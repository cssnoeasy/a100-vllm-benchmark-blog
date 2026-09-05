#!/usr/bin/env python3
import json
import subprocess
import sys
import time
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(".")
OUT_DIR = ROOT / "results" / "week6" / "overload_probe"
PROM_URL = "http://127.0.0.1:9090/api/v1/query"

CASES = [
    {
        "name": "baseline_medium",
        "input_len": 512,
        "output_len": 128,
        "num_prompts": 64,
        "max_concurrency": 8,
    },
    {
        "name": "overload_high_concurrency",
        "input_len": 1024,
        "output_len": 256,
        "num_prompts": 160,
        "max_concurrency": 48,
    },
    {
        "name": "recovery_medium",
        "input_len": 512,
        "output_len": 128,
        "num_prompts": 64,
        "max_concurrency": 8,
    },
]

QUERIES = {
    "vllm_up": 'up{job="vllm"}',
    "running": 'vllm:num_requests_running{job="vllm"} or vector(0)',
    "waiting": 'vllm:num_requests_waiting{job="vllm"} or vector(0)',
    "kv_cache_percent": 'vllm:kv_cache_usage_perc{job="vllm"} * 100 or vector(0)',
    "prompt_tps_5m": 'rate(vllm:prompt_tokens_total{job="vllm"}[5m]) or vector(0)',
    "generation_tps_5m": 'rate(vllm:generation_tokens_total{job="vllm"}[5m]) or vector(0)',
    "gpu0_util": 'week6_gpu_utilization_percent{job="week6_gpu_system",gpu="0"} or vector(0)',
    "gpu0_memory_mib": 'week6_gpu_memory_used_mib{job="week6_gpu_system",gpu="0"} or vector(0)',
    "gpu0_power_watts": 'week6_gpu_power_watts{job="week6_gpu_system",gpu="0"} or vector(0)',
}


def now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def run(command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        check=False,
    )


def query_prometheus(expr: str) -> dict:
    url = PROM_URL + "?" + urlencode({"query": expr})
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def compact_query_result(payload: dict) -> list[dict]:
    rows = []
    for item in payload.get("data", {}).get("result", []):
        metric = item.get("metric", {})
        value = item.get("value", [None, None])[1]
        rows.append({
            "metric": {
                key: metric.get(key)
                for key in ["job", "instance", "engine", "gpu", "model_name"]
                if key in metric
            },
            "value": value,
        })
    return rows


def snapshot(label: str) -> dict:
    data = {"label": label, "timestamp": now(), "queries": {}}
    for name, expr in QUERIES.items():
        try:
            data["queries"][name] = compact_query_result(query_prometheus(expr))
        except Exception as exc:
            data["queries"][name] = [{"error": str(exc)}]
    return data


def read_result(path: Path) -> dict:
    result = path / "result.json"
    if not result.exists():
        return {}
    return json.loads(result.read_text(encoding="utf-8"))


def write_summary(summary: dict) -> None:
    lines = [
        "# Week6 Overload Probe Summary",
        "",
        f"generated_at: {summary['generated_at']}",
        "",
        "## Purpose",
        "",
        "Use controlled high-concurrency traffic to create observable queueing pressure, then verify the vLLM service and metrics recover.",
        "",
        "## Benchmark Cases",
        "",
        "| Case | Completed | Failed | Output tok/s | p99 TTFT ms | p99 TPOT ms | Duration s |",
        "| ---- | ---- | ---- | ---- | ---- | ---- | ---- |",
    ]

    for case in summary["cases"]:
        result = case.get("result", {})
        lines.append(
            f"| {case['name']} | {result.get('completed')} | {result.get('failed')} | "
            f"{fmt(result.get('output_throughput'))} | {fmt(result.get('p99_ttft_ms'))} | "
            f"{fmt(result.get('p99_tpot_ms'))} | {fmt(result.get('duration'))} |"
        )

    lines.extend([
        "",
        "## Prometheus Snapshots",
        "",
    ])

    for snap in summary["snapshots"]:
        lines.append(f"### {snap['label']} - {snap['timestamp']}")
        lines.append("")
        for name, rows in snap["queries"].items():
            values = ", ".join(row.get("value", str(row)) for row in rows) or "no data"
            lines.append(f"- {name}: {values}")
        lines.append("")

    lines.extend([
        "## Interpretation",
        "",
        "- `overload_high_concurrency` is expected to raise running/waiting requests and GPU utilization compared with the medium baseline.",
        "- A healthy recovery means `up{job=\"vllm\"}` remains 1 and queue-related gauges return to low values after the probe.",
        "- This is a pressure probe, not a destructive OOM test.",
    ])

    (OUT_DIR / "overload_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def fmt(value):
    if value is None:
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    summary = {
        "generated_at": now(),
        "cases": [],
        "snapshots": [],
    }

    summary["snapshots"].append(snapshot("before"))

    for case in CASES:
        case_dir = OUT_DIR / case["name"]
        case_dir.mkdir(parents=True, exist_ok=True)

        env = {
            **dict(**__import__("os").environ),
            "PORT": "8000",
            "MODEL": "Qwen2.5-7B-Instruct",
            "TOKENIZER": os.environ.get("MODEL_PATH", ""),
            "RESULT_DIR": str(case_dir),
            "RESULT_FILE": "result.json",
            "INPUT_LEN": str(case["input_len"]),
            "OUTPUT_LEN": str(case["output_len"]),
            "NUM_PROMPTS": str(case["num_prompts"]),
            "MAX_CONCURRENCY": str(case["max_concurrency"]),
            "REQUEST_RATE": "inf",
            "DATASET_NAME": "random",
            "BENCHMARK_EXTRA_ARGS": "",
        }

        summary["snapshots"].append(snapshot(f"before_{case['name']}"))
        result = run(["bash", "scripts/run_benchmark_once.sh"], env=env)
        (case_dir / "benchmark_stdout.txt").write_text(result.stdout, encoding="utf-8")
        summary["snapshots"].append(snapshot(f"after_{case['name']}"))

        case_record = {
            **case,
            "returncode": result.returncode,
            "result": read_result(case_dir),
        }
        summary["cases"].append(case_record)
        write_summary(summary)
        time.sleep(20)

    summary["snapshots"].append(snapshot("final_recovery"))
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_summary(summary)

    print((OUT_DIR / "overload_summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
