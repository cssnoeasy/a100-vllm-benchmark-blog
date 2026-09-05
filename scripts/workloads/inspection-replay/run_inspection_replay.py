#!/usr/bin/env python3
"""Replay sanitized robot-inspection workloads against an OpenAI-compatible API."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import platform
import random
import re
import signal
import socket
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REQUIRED_CONFIG = {
    "run_name",
    "server_role",
    "base_url",
    "model",
    "scenarios_file",
    "output_root",
    "timeout_seconds",
    "stream",
    "seed",
    "selection_strategy",
    "temperature",
    "max_tokens",
    "scenario_weights",
}
SCENARIO_TYPES = {
    "hazard_decision",
    "fault_diagnosis",
    "inspection_summary",
    "invalid_or_edge",
}
MAX_CAPTURE_CHARS = 12000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON/YAML-subset file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Configuration must be an object: {path}")
    return value


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
        required = {"scenario_id", "scenario_type", "system_prompt", "user_prompt", "expected"}
        missing = required - set(item)
        if missing:
            raise ValueError(f"{path}:{line_number} missing fields: {sorted(missing)}")
        if item["scenario_id"] in seen:
            raise ValueError(f"Duplicate scenario_id: {item['scenario_id']}")
        if item["scenario_type"] not in SCENARIO_TYPES:
            raise ValueError(f"Unknown scenario_type: {item['scenario_type']}")
        seen.add(item["scenario_id"])
        scenarios.append(item)
    if not scenarios:
        raise ValueError(f"No scenarios found in {path}")
    return scenarios


def resolve_config(config_path: Path) -> dict[str, Any]:
    config = load_json(config_path)
    missing = REQUIRED_CONFIG - set(config)
    if missing:
        raise ValueError(f"Missing configuration fields: {sorted(missing)}")

    config["base_url"] = os.getenv("REPLAY_BASE_URL", str(config["base_url"])).rstrip("/")
    config["api_key"] = os.getenv("REPLAY_API_KEY", str(config.get("api_key", "")))
    config["model"] = os.getenv("REPLAY_MODEL", str(config["model"]))
    for field in ("scenarios_file", "output_root"):
        path = Path(config[field])
        if not path.is_absolute():
            path = (config_path.parent / path).resolve()
        config[field] = str(path)

    phases = config.get("phases")
    if phases:
        if not isinstance(phases, list) or not phases:
            raise ValueError("phases must be a non-empty list")
        for phase in phases:
            validate_phase(phase)
    else:
        validate_phase(config)

    weights = config["scenario_weights"]
    if set(weights) != SCENARIO_TYPES:
        raise ValueError(f"scenario_weights must contain exactly: {sorted(SCENARIO_TYPES)}")
    if any(float(value) < 0 for value in weights.values()) or sum(map(float, weights.values())) <= 0:
        raise ValueError("scenario_weights must be non-negative with a positive sum")
    if config["selection_strategy"] not in {"coverage", "weighted_random"}:
        raise ValueError("selection_strategy must be coverage or weighted_random")
    if int(config["max_tokens"]) <= 0 or float(config["timeout_seconds"]) <= 0:
        raise ValueError("max_tokens and timeout_seconds must be positive")
    quality_gates = config.get("quality_gates", {})
    if not isinstance(quality_gates, dict):
        raise ValueError("quality_gates must be an object")
    allowed_gates = {"min_success_rate", "min_business_parse_rate", "max_p99_e2e_ms"}
    unknown_gates = set(quality_gates) - allowed_gates
    if unknown_gates:
        raise ValueError(f"Unknown quality gates: {sorted(unknown_gates)}")
    for field in ("min_success_rate", "min_business_parse_rate"):
        if field in quality_gates and not 0 <= float(quality_gates[field]) <= 1:
            raise ValueError(f"{field} must be between 0 and 1")
    if "max_p99_e2e_ms" in quality_gates and float(quality_gates["max_p99_e2e_ms"]) <= 0:
        raise ValueError("max_p99_e2e_ms must be positive")
    return config


def validate_phase(phase: dict[str, Any]) -> None:
    duration = float(phase.get("duration_seconds", 0))
    max_requests = int(phase.get("max_requests", 0))
    rate = float(phase.get("request_rate", 0))
    concurrency = int(phase.get("max_concurrency", 0))
    if duration <= 0 and max_requests <= 0:
        raise ValueError("Each run/phase needs duration_seconds > 0 or max_requests > 0")
    if rate <= 0 or concurrency <= 0:
        raise ValueError("request_rate and max_concurrency must be positive")


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config)
    result["api_key"] = "***" if config.get("api_key") else ""
    return result


class ScenarioSelector:
    def __init__(self, scenarios: list[dict[str, Any]], config: dict[str, Any]) -> None:
        self.by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for scenario in scenarios:
            self.by_type[scenario["scenario_type"]].append(scenario)
        missing = SCENARIO_TYPES - set(self.by_type)
        if missing:
            raise ValueError(f"Scenario dataset lacks types: {sorted(missing)}")
        self.random = random.Random(int(config["seed"]))
        self.strategy = config["selection_strategy"]
        self.weights = {key: float(value) for key, value in config["scenario_weights"].items()}
        self.type_order = sorted(SCENARIO_TYPES)
        self.type_indexes = Counter()
        self.coverage_index = 0

    def next(self) -> dict[str, Any]:
        if self.strategy == "coverage" and self.coverage_index < len(self.type_order):
            scenario_type = self.type_order[self.coverage_index]
            self.coverage_index += 1
        else:
            scenario_type = self.random.choices(
                self.type_order,
                weights=[self.weights[item] for item in self.type_order],
                k=1,
            )[0]
        pool = self.by_type[scenario_type]
        if self.strategy == "coverage":
            index = self.type_indexes[scenario_type] % len(pool)
            self.type_indexes[scenario_type] += 1
            return pool[index]
        return self.random.choice(pool)


def strip_code_fence(text: str) -> str:
    value = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def parse_json_object(text: str) -> dict[str, Any]:
    raw = strip_code_fence(text)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        # Some small models occasionally escape field quotes in otherwise valid JSON.
        # Keep the repair narrow so genuinely malformed business output still fails.
        repaired = re.sub(r'(?<=[{,])\s*\\"([A-Za-z][A-Za-z0-9_]*)\\"\s*:', r'"\1":', raw)
        if repaired == raw:
            raise
        value = json.loads(repaired)
    if not isinstance(value, dict):
        raise ValueError("output is not a JSON object")
    return value


def validate_output(scenario: dict[str, Any], content: str) -> tuple[bool, str | None]:
    expected = scenario["expected"]
    output_format = expected["format"]
    try:
        if output_format == "action_json":
            value = parse_json_object(content)
            if set(value) != {"thought", "actionCode"}:
                raise ValueError("action JSON must contain only thought and actionCode")
            if not isinstance(value["thought"], str) or not value["thought"].strip():
                raise ValueError("thought must be a non-empty string")
            if len(value["thought"]) > 50:
                raise ValueError("thought must not exceed 50 characters")
            if value["actionCode"] != expected["action_code"]:
                raise ValueError(
                    f"actionCode={value['actionCode']} expected={expected['action_code']}"
                )
        elif output_format == "diagnosis_json":
            value = parse_json_object(content)
            required = {"probableCauses", "inspectionSteps", "safetyAction"}
            if set(value) != required:
                raise ValueError(f"diagnosis JSON keys must be {sorted(required)}")
            if not isinstance(value["probableCauses"], list) or not value["probableCauses"]:
                raise ValueError("probableCauses must be a non-empty list")
            if not isinstance(value["inspectionSteps"], list) or not value["inspectionSteps"]:
                raise ValueError("inspectionSteps must be a non-empty list")
            if not all(isinstance(item, str) and item.strip() for item in value["probableCauses"]):
                raise ValueError("probableCauses entries must be non-empty strings")
            if not all(isinstance(item, str) and item.strip() for item in value["inspectionSteps"]):
                raise ValueError("inspectionSteps entries must be non-empty strings")
            if not isinstance(value["safetyAction"], str) or not value["safetyAction"].strip():
                raise ValueError("safetyAction must be a non-empty string")
        elif output_format == "markdown_sections":
            positions = [content.find(section) for section in expected["required_sections"]]
            if any(position < 0 for position in positions):
                raise ValueError("one or more required Markdown sections are missing")
            if positions != sorted(positions):
                raise ValueError("Markdown sections are out of order")
        elif output_format == "clarification_json":
            value = parse_json_object(content)
            required = {"status", "missingOrConflictingFields", "safeAction"}
            if set(value) != required:
                raise ValueError(f"clarification JSON keys must be {sorted(required)}")
            if value["status"] != "NEEDS_CLARIFICATION":
                raise ValueError("status must be NEEDS_CLARIFICATION")
            if not isinstance(value["missingOrConflictingFields"], list) or not value["missingOrConflictingFields"]:
                raise ValueError("missingOrConflictingFields must be a non-empty list")
            if not isinstance(value["safeAction"], str) or not value["safeAction"].strip():
                raise ValueError("safeAction must be a non-empty string")
            safe_action = value["safeAction"]
            if "停止" not in safe_action or not any(word in safe_action for word in ("人工", "确认")):
                raise ValueError("safeAction must explicitly stop and wait for human confirmation")
        else:
            raise ValueError(f"unknown expected format: {output_format}")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return False, str(exc)
    return True, None


def classify_error(exc: BaseException) -> tuple[str, int | None, str]:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read(2000).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return "http_error", exc.code, body or str(exc)
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "timeout", None, str(reason)
        return "connection_error", None, str(reason)
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout", None, str(exc)
    return "client_error", None, f"{type(exc).__name__}: {exc}"


def build_payload(config: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": scenario["system_prompt"]},
            {"role": "user", "content": scenario["user_prompt"]},
        ],
        "temperature": float(config["temperature"]),
        "max_tokens": int(config["max_tokens"]),
        "stream": bool(config["stream"]),
    }
    if config["stream"]:
        payload["stream_options"] = {"include_usage": True}
    return payload


def execute_request(
    config: dict[str, Any], scenario: dict[str, Any], phase_name: str, sequence: int
) -> dict[str, Any]:
    request_id = f"inspection-{uuid.uuid4().hex[:16]}"
    started_wall = utc_now()
    started = time.perf_counter()
    result: dict[str, Any] = {
        "request_id": request_id,
        "sequence": sequence,
        "phase": phase_name,
        "scenario_id": scenario["scenario_id"],
        "scenario_type": scenario["scenario_type"],
        "server_role": config["server_role"],
        "model": config["model"],
        "started_at": started_wall,
        "status": "failed",
        "http_status": None,
        "ttft_ms": None,
        "e2e_latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "output_parse_ok": False,
        "parse_error": None,
        "error_type": None,
        "error_message": None,
        "response_text": "",
    }
    payload = build_payload(config, scenario)
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if config["stream"] else "application/json",
        "X-Request-ID": request_id,
    }
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    request = urllib.request.Request(
        f"{config['base_url']}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=float(config["timeout_seconds"])) as response:
            result["http_status"] = response.status
            if config["stream"]:
                content, usage, ttft_ms = read_stream(response, started)
                result["ttft_ms"] = ttft_ms
            else:
                data = json.loads(response.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage") or {}
            result["response_text"] = content[:MAX_CAPTURE_CHARS]
            result["input_tokens"] = usage.get("prompt_tokens")
            result["output_tokens"] = usage.get("completion_tokens")
            result["total_tokens"] = usage.get("total_tokens")
            parse_ok, parse_error = validate_output(scenario, content)
            result["output_parse_ok"] = parse_ok
            result["parse_error"] = parse_error
            result["status"] = "success" if parse_ok else "business_validation_failed"
    except BaseException as exc:
        error_type, http_status, message = classify_error(exc)
        result["error_type"] = error_type
        result["http_status"] = http_status
        result["error_message"] = message[:2000]
    finally:
        result["e2e_latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result["finished_at"] = utc_now()
    return result


def read_stream(response: Any, started: float) -> tuple[str, dict[str, Any], float | None]:
    fragments: list[str] = []
    usage: dict[str, Any] = {}
    ttft_ms: float | None = None
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue
        data_text = line[5:].strip()
        if data_text == "[DONE]":
            break
        try:
            event = json.loads(data_text)
        except json.JSONDecodeError:
            continue
        if event.get("usage"):
            usage = event["usage"]
        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content:
            if ttft_ms is None:
                ttft_ms = round((time.perf_counter() - started) * 1000, 3)
            fragments.append(content)
    return "".join(fragments), usage, ttft_ms


def percentile(values: Iterable[float], percent: float) -> float | None:
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return None
    rank = (len(ordered) - 1) * percent / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 3)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)
    return round(interpolated, 3)


def metric_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    data = [float(value) for value in values if value is not None]
    return {
        "count": len(data),
        "mean": round(statistics.fmean(data), 3) if data else None,
        "p50": percentile(data, 50),
        "p95": percentile(data, 95),
        "p99": percentile(data, 99),
        "max": round(max(data), 3) if data else None,
    }


def summarize(
    results: list[dict[str, Any]], started: float, finished: float, config: dict[str, Any]
) -> dict[str, Any]:
    status_counts = Counter(item["status"] for item in results)
    type_counts = Counter(item["scenario_type"] for item in results)
    type_success = Counter(
        item["scenario_type"] for item in results if item["status"] == "success"
    )
    error_counts = Counter(
        item["error_type"] or item["parse_error"] or "unknown"
        for item in results
        if item["status"] != "success"
    )
    elapsed = max(finished - started, 0.000001)
    successful = status_counts["success"]
    summary = {
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds"),
        "finished_at": datetime.fromtimestamp(finished, timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": round(elapsed, 3),
        "total_requests": len(results),
        "successful_requests": successful,
        "failed_requests": len(results) - successful,
        "success_rate": round(successful / len(results), 6) if results else 0,
        "business_parse_rate": round(
            sum(bool(item["output_parse_ok"]) for item in results) / len(results), 6
        ) if results else 0,
        "requests_per_second": round(len(results) / elapsed, 6),
        "status_counts": dict(sorted(status_counts.items())),
        "scenario_counts": dict(sorted(type_counts.items())),
        "scenario_success_counts": dict(sorted(type_success.items())),
        "error_counts": dict(error_counts.most_common()),
        "e2e_latency_ms": metric_summary(item["e2e_latency_ms"] for item in results),
        "ttft_ms": metric_summary(item["ttft_ms"] for item in results),
        "input_tokens": metric_summary(item["input_tokens"] for item in results),
        "output_tokens": metric_summary(item["output_tokens"] for item in results),
    }
    gates = config.get("quality_gates", {})
    gate_checks: dict[str, dict[str, Any]] = {}
    if "min_success_rate" in gates:
        actual = summary["success_rate"]
        threshold = float(gates["min_success_rate"])
        gate_checks["min_success_rate"] = {
            "actual": actual,
            "threshold": threshold,
            "passed": actual >= threshold,
        }
    if "min_business_parse_rate" in gates:
        actual = summary["business_parse_rate"]
        threshold = float(gates["min_business_parse_rate"])
        gate_checks["min_business_parse_rate"] = {
            "actual": actual,
            "threshold": threshold,
            "passed": actual >= threshold,
        }
    if "max_p99_e2e_ms" in gates:
        actual = summary["e2e_latency_ms"]["p99"]
        threshold = float(gates["max_p99_e2e_ms"])
        gate_checks["max_p99_e2e_ms"] = {
            "actual": actual,
            "threshold": threshold,
            "passed": actual is not None and actual <= threshold,
        }
    summary["quality_gate"] = {
        "passed": bool(gate_checks) and all(item["passed"] for item in gate_checks.values()),
        "checks": gate_checks,
    }
    return summary


def render_summary(summary: dict[str, Any], config: dict[str, Any]) -> str:
    def value(metric: str, key: str) -> str:
        item = summary[metric][key]
        return "N/A" if item is None else str(item)

    lines = [
        f"# {config['run_name']} Summary",
        "",
        f"- Server role: `{config['server_role']}`",
        f"- Model: `{config['model']}`",
        f"- Stream: `{str(config['stream']).lower()}`",
        f"- Elapsed: `{summary['elapsed_seconds']} s`",
        f"- Requests: `{summary['total_requests']}`",
        f"- Success: `{summary['successful_requests']}`",
        f"- Failed/business-invalid: `{summary['failed_requests']}`",
        f"- Success rate: `{summary['success_rate'] * 100:.2f}%`",
        f"- Business parse rate: `{summary['business_parse_rate'] * 100:.2f}%`",
        f"- Quality gate: `{'PASS' if summary['quality_gate']['passed'] else 'FAIL'}`",
        "",
        "## Latency",
        "",
        "| Metric | P50 | P95 | P99 | Max |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| E2E ms | {value('e2e_latency_ms', 'p50')} | {value('e2e_latency_ms', 'p95')} | {value('e2e_latency_ms', 'p99')} | {value('e2e_latency_ms', 'max')} |",
        f"| TTFT ms | {value('ttft_ms', 'p50')} | {value('ttft_ms', 'p95')} | {value('ttft_ms', 'p99')} | {value('ttft_ms', 'max')} |",
        "",
        "## Scenarios",
        "",
        "| Type | Requests | Success |",
        "| --- | ---: | ---: |",
    ]
    for scenario_type in sorted(SCENARIO_TYPES):
        lines.append(
            f"| {scenario_type} | {summary['scenario_counts'].get(scenario_type, 0)} | "
            f"{summary['scenario_success_counts'].get(scenario_type, 0)} |"
        )
    lines.extend(["", "## Errors", ""])
    if summary["error_counts"]:
        for error, count in summary["error_counts"].items():
            lines.append(f"- `{error}`: {count}")
    else:
        lines.append("- None")
    lines.extend(["", "## Quality Gate", ""])
    for name, check in summary["quality_gate"]["checks"].items():
        state = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"- `{name}`: {state} (actual={check['actual']}, threshold={check['threshold']})"
        )
    lines.extend(
        [
            "",
            "TTFT is recorded only for streaming responses. E2E is not TPOT.",
            "",
        ]
    )
    return "\n".join(lines)


class ResultWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def append(self, result: dict[str, Any]) -> None:
        line = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()


def run_replay(
    config: dict[str, Any], scenarios: list[dict[str, Any]], output_dir: Path
) -> tuple[list[dict[str, Any]], float, float, bool]:
    selector = ScenarioSelector(scenarios, config)
    writer = ResultWriter(output_dir / "requests.jsonl")
    results: list[dict[str, Any]] = []
    stop_event = threading.Event()
    interrupted = False
    sequence = 0
    run_started_wall = time.time()

    def handle_signal(signum: int, frame: Any) -> None:
        nonlocal interrupted
        if interrupted:
            raise KeyboardInterrupt
        interrupted = True
        stop_event.set()
        print("\nInterrupt received: stopping new requests and draining in-flight work...", flush=True)

    previous_handler = signal.signal(signal.SIGINT, handle_signal)
    phases = config.get("phases") or [
        {
            "name": "default",
            "duration_seconds": config.get("duration_seconds", 0),
            "max_requests": config.get("max_requests", 0),
            "request_rate": config["request_rate"],
            "max_concurrency": config["max_concurrency"],
        }
    ]
    try:
        for phase in phases:
            if stop_event.is_set():
                break
            phase_name = str(phase.get("name", "default"))
            duration = float(phase.get("duration_seconds", 0))
            max_requests = int(phase.get("max_requests", 0))
            rate = float(phase["request_rate"])
            concurrency = int(phase["max_concurrency"])
            phase_started = time.monotonic()
            next_release = phase_started
            submitted = 0
            futures: set[concurrent.futures.Future[dict[str, Any]]] = set()
            print(
                f"Phase {phase_name}: duration={duration}s max_requests={max_requests or 'unlimited'} "
                f"rate={rate}/s concurrency={concurrency}",
                flush=True,
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                while not stop_event.is_set():
                    now = time.monotonic()
                    duration_done = duration > 0 and now - phase_started >= duration
                    count_done = max_requests > 0 and submitted >= max_requests
                    if duration_done or count_done:
                        break
                    if now < next_release:
                        time.sleep(min(next_release - now, 0.1))
                        continue
                    if len(futures) >= concurrency:
                        done, futures = concurrent.futures.wait(
                            futures,
                            timeout=0.1,
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        for future in done:
                            result = future.result()
                            writer.append(result)
                            results.append(result)
                            print_result(result, len(results))
                        continue
                    sequence += 1
                    submitted += 1
                    scenario = selector.next()
                    futures.add(
                        executor.submit(execute_request, config, scenario, phase_name, sequence)
                    )
                    next_release += 1.0 / rate
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    writer.append(result)
                    results.append(result)
                    print_result(result, len(results))
    finally:
        signal.signal(signal.SIGINT, previous_handler)
    return results, run_started_wall, time.time(), interrupted


def print_result(result: dict[str, Any], completed: int) -> None:
    print(
        f"[{completed}] {result['phase']} {result['scenario_id']} "
        f"status={result['status']} e2e={result['e2e_latency_ms']}ms "
        f"ttft={result['ttft_ms'] if result['ttft_ms'] is not None else 'N/A'}ms",
        flush=True,
    )


def create_output_dir(config: dict[str, Any], override: str | None) -> Path:
    if override:
        path = Path(override).resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(config["output_root"]) / f"{config['run_name']}_{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_metadata(output_dir: Path, config: dict[str, Any], argv: list[str]) -> None:
    (output_dir / "resolved_config.json").write_text(
        json.dumps(public_config(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "command.txt").write_text(" ".join(argv) + "\n", encoding="utf-8")
    environment = {
        "captured_at": utc_now(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.replace("\n", " "),
        "executable": sys.executable,
    }
    (output_dir / "environment.txt").write_text(
        "\n".join(f"{key}: {value}" for key, value in environment.items()) + "\n",
        encoding="utf-8",
    )


def dry_run(config: dict[str, Any], scenarios: list[dict[str, Any]]) -> int:
    counts = Counter(item["scenario_type"] for item in scenarios)
    phases = config.get("phases") or [config]
    estimated = 0.0
    for phase in phases:
        duration = float(phase.get("duration_seconds", 0))
        max_requests = int(phase.get("max_requests", 0))
        estimated += max_requests if max_requests else duration * float(phase["request_rate"])
    print("Configuration OK")
    print(f"endpoint: {config['base_url']}/chat/completions")
    print(f"model: {config['model']}")
    print(f"stream: {config['stream']}")
    print(f"scenarios: {len(scenarios)} {dict(sorted(counts.items()))}")
    print(f"estimated scheduled requests: {round(estimated)}")
    print("No request was sent.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="JSON-compatible YAML configuration")
    parser.add_argument("--output-dir", help="Override the generated output directory")
    parser.add_argument("--max-requests", type=int, help="Limit the whole run (single-phase configs only)")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without sending requests")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config).resolve()
    try:
        config = resolve_config(config_path)
        if args.max_requests is not None:
            if config.get("phases"):
                raise ValueError("--max-requests cannot override a multi-phase config")
            if args.max_requests <= 0:
                raise ValueError("--max-requests must be positive")
            config["max_requests"] = args.max_requests
            config["duration_seconds"] = 0
        scenarios = load_scenarios(Path(config["scenarios_file"]))
    except (OSError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        return dry_run(config, scenarios)

    try:
        output_dir = create_output_dir(config, args.output_dir)
    except OSError as exc:
        print(f"Cannot create output directory: {exc}", file=sys.stderr)
        return 2
    write_metadata(output_dir, config, [sys.executable, *sys.argv])
    print(f"Output: {output_dir}", flush=True)

    results, started, finished, interrupted = run_replay(config, scenarios, output_dir)
    summary = summarize(results, started, finished, config)
    summary["interrupted"] = interrupted
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.md").write_text(
        render_summary(summary, config), encoding="utf-8"
    )
    print(
        f"Finished: success={summary['successful_requests']} failed={summary['failed_requests']} "
        f"summary={output_dir / 'summary.md'}",
        flush=True,
    )
    return 0 if results and summary["quality_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
