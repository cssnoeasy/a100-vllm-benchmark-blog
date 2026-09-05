#!/usr/bin/env python3
"""Shared, dependency-free runner state and process helpers."""

from __future__ import annotations

import json
import hashlib
import os
import re
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_ORDER = ("created", "queued", "waiting_resources", "starting", "ready", "warming", "warmed", "benchmarking", "benchmark_completed", "completed", "validating", "validated", "retrying", "recovering")
TERMINAL_STATUSES = {"validated", "failed", "validation_failed", "cancelled", "stopped"}
ACTIVE_STATUSES = {"created", "planned", "queued", "waiting_resources", "starting", "ready", "warming", "warmed", "benchmarking", "benchmark_completed", "completed", "validating", "retrying", "recovering"}
ALLOWED_TRANSITIONS = {
    "created": {"queued", "starting", "recovering", "failed", "cancelled"},
    "planned": {"created", "queued", "starting", "failed", "cancelled"},
    "queued": {"waiting_resources", "starting", "retrying", "recovering", "failed", "cancelled"},
    "waiting_resources": {"queued", "starting", "retrying", "failed", "cancelled"},
    "starting": {"ready", "failed", "cancelled", "recovering"},
    "ready": {"warming", "failed", "cancelled", "recovering"},
    "warming": {"warmed", "failed", "cancelled", "recovering"},
    "warmed": {"benchmarking", "failed", "cancelled", "recovering"},
    "benchmarking": {"benchmark_completed", "failed", "cancelled", "recovering"},
    "benchmark_completed": {"completed", "failed", "retrying", "recovering"},
    "completed": {"validating", "failed", "recovering"},
    "validating": {"validated", "validation_failed", "failed", "retrying", "recovering"},
    "validated": {"created", "queued"},
    "failed": {"created", "queued", "retrying", "recovering", "cancelled"},
    "validation_failed": {"created", "queued", "retrying", "cancelled"},
    "cancelled": {"created", "queued"},
    "stopped": {"created", "queued"},
    "retrying": {"queued", "starting", "failed", "cancelled"},
    "recovering": {"queued", "failed", "cancelled"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def config_hash(config: dict[str, Any]) -> str:
    """Create a stable hash for the effective experiment configuration."""
    ignored = {"config_path", "requested_port", "allocated_port"}
    payload = {key: value for key, value in config.items() if key not in ignored}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def schema_errors(config: dict[str, Any]) -> list[str]:
    errors = []
    for name in ("experiment", "service_mode", "feature", "feature_variant", "model_path", "model_name", "cuda_visible_devices", "dataset", "trial"):
        if not isinstance(config.get(name), str) or not str(config.get(name)).strip():
            errors.append(f"{name} must be a non-empty string")
    for name in ("port", "tensor_parallel_size", "max_model_len", "input_len", "output_len", "num_prompts", "max_concurrency", "warmup_requests"):
        if not isinstance(config.get(name), int) or config[name] < 1:
            errors.append(f"{name} must be a positive integer")
    request_rate = config.get("request_rate")
    if isinstance(request_rate, str):
        if request_rate.lower() != "inf":
            try:
                if float(request_rate) <= 0:
                    errors.append("request_rate must be positive or 'inf'")
            except ValueError:
                errors.append("request_rate must be numeric or 'inf'")
    elif not isinstance(request_rate, (int, float)) or request_rate <= 0:
        errors.append("request_rate must be positive or 'inf'")
    for name in ("readiness_timeout_seconds", "benchmark_timeout_seconds", "run_timeout_seconds"):
        if name in config and (not isinstance(config[name], (int, float)) or config[name] <= 0):
            errors.append(f"{name} must be positive when provided")
    return errors


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "manifest.json"
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def transition_allowed(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set()) or current == target


def validate_transition(current: str, target: str) -> None:
    if not transition_allowed(current, target):
        raise ValueError(f"invalid runner state transition: {current} -> {target}")


@contextmanager
def exclusive_lock(path: Path, timeout: float = 0.0, poll: float = 0.1, stale_after: float = 300):
    """Acquire a process-wide lock using atomic file creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    handle = None
    while handle is None:
        try:
            handle = path.open("x", encoding="utf-8")
            handle.write(json.dumps({"pid": os.getpid(), "created_at": utc_now()}))
            handle.flush()
        except FileExistsError:
            try:
                lock_data = json.loads(path.read_text(encoding="utf-8"))
                lock_pid = int(lock_data.get("pid", 0))
                lock_created = datetime.fromisoformat(lock_data.get("created_at", "")).timestamp()
                if (not pid_alive(lock_pid)) and time.time() - lock_created > stale_after:
                    path.unlink(missing_ok=True)
                    continue
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"lock is busy: {path}")
            time.sleep(poll)
    try:
        yield handle
    finally:
        handle.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def append_event(run_dir: Path, event: str, status: str | None = None, **details: Any) -> dict[str, Any]:
    event_record: dict[str, Any] = {
        "ts": utc_now(),
        "event": event,
    }
    if status:
        event_record["status"] = status
    event_record.update(details)
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event_record, ensure_ascii=False, sort_keys=True) + "\n")
    return event_record


def update_manifest(run_dir: Path, status: str | None = None, **fields: Any) -> dict[str, Any]:
    manifest = load_manifest(run_dir)
    if status:
        validate_transition(str(manifest.get("status", "created")), status)
        manifest["status"] = status
        manifest.setdefault("status_history", []).append({"status": status, "at": utc_now()})
    manifest.update(fields)
    manifest["updated_at"] = utc_now()
    atomic_write_json(run_dir / "manifest.json", manifest)
    append_event(run_dir, "manifest_updated", status=manifest.get("status"), fields=list(fields))
    return manifest


def heartbeat(run_dir: Path, **fields: Any) -> dict[str, Any]:
    payload = {"ts": utc_now(), "pid": os.getpid(), **fields}
    atomic_write_json(run_dir / "heartbeat.json", payload)
    append_event(run_dir, "heartbeat", **fields)
    return payload


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            state = stat_path.read_text(encoding="utf-8", errors="replace").split()[2]
            if state == "Z":
                return False
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def stop_process_group(pid: int, timeout: float = 30.0) -> bool:
    """Stop only the process group owned by pid; never use broad pkill."""
    if not pid_alive(pid):
        return True
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.25)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    return not pid_alive(pid)


def process_command(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def stale_lock_paths(root: Path, stale_after: float = 300) -> list[Path]:
    """Return only lock files whose recorded owner is definitely dead and old."""
    stale = []
    for path in root.glob("*.lock"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            created = datetime.fromisoformat(data.get("created_at", "")).timestamp()
            if not pid_alive(pid) and time.time() - created > stale_after:
                stale.append(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return stale


def inspect_run(run_dir: Path, stale_after: int = 120) -> dict[str, Any]:
    result: dict[str, Any] = {"run_dir": str(run_dir), "exists": run_dir.is_dir()}
    if not run_dir.is_dir():
        return result
    try:
        manifest = load_manifest(run_dir)
    except (OSError, json.JSONDecodeError) as exc:
        return {**result, "status": "corrupt", "error": str(exc)}
    result.update({"run_id": manifest.get("run_id", run_dir.name), "status": manifest.get("status", "legacy")})
    heartbeat_path = run_dir / "heartbeat.json"
    result["heartbeat"] = None
    result["stale"] = False
    if heartbeat_path.exists():
        try:
            heartbeat_data = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            result["heartbeat"] = heartbeat_data
            ts = datetime.fromisoformat(heartbeat_data["ts"]).timestamp()
            result["stale"] = time.time() - ts > stale_after and manifest.get("status") in ACTIVE_STATUSES
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            result["stale"] = True
    elif manifest.get("status") in ACTIVE_STATUSES:
        result["stale"] = True
    pid_path = run_dir / "server.pid"
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            result["server_pid"] = pid
            result["server_alive"] = pid_alive(pid)
        except ValueError:
            result["server_pid"] = None
            result["server_alive"] = False
    return result


def parse_gpu_ids(value: Any) -> list[int]:
    ids = []
    for token in str(value).split(","):
        token = token.strip()
        if token and token.isdigit():
            ids.append(int(token))
    return ids


def validate_config(config: dict[str, Any], project_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    errors.extend(schema_errors(config))
    required = [
        "experiment", "service_mode", "feature", "feature_variant", "model_path", "model_name", "port",
        "cuda_visible_devices", "tensor_parallel_size", "max_model_len", "gpu_memory_utilization", "dataset",
        "input_len", "output_len", "num_prompts", "max_concurrency", "request_rate", "warmup_requests", "trial",
    ]
    missing = [name for name in required if name not in config]
    errors.extend(f"missing required field: {name}" for name in missing)
    if missing:
        return errors
    if not isinstance(config["port"], int) or not 1 <= config["port"] <= 65535:
        errors.append("port must be an integer between 1 and 65535")
    for field in ("port_candidates",):
        raw = config.get(field, [])
        if isinstance(raw, str):
            raw = [item.strip() for item in raw.split(",") if item.strip()]
        if not isinstance(raw, (list, tuple)):
            errors.append(f"{field} must be a list of ports")
        else:
            for value in raw:
                try:
                    if not 1 <= int(value) <= 65535:
                        errors.append(f"{field} contains invalid port: {value}")
                except (TypeError, ValueError):
                    errors.append(f"{field} contains non-integer port: {value}")
    port_range = config.get("port_range")
    if port_range is not None:
        if not isinstance(port_range, (list, tuple)) or len(port_range) != 2:
            errors.append("port_range must contain [start, end]")
        else:
            try:
                start, end = (int(item) for item in port_range)
                if not (1 <= start <= end <= 65535):
                    errors.append("port_range must be within 1..65535 and start <= end")
            except (TypeError, ValueError):
                errors.append("port_range values must be integers")
    gpu_ids = parse_gpu_ids(config["cuda_visible_devices"])
    if not gpu_ids:
        errors.append("cuda_visible_devices must contain at least one GPU id")
    tp = config["tensor_parallel_size"]
    if not isinstance(tp, int) or tp < 1:
        errors.append("tensor_parallel_size must be a positive integer")
    elif gpu_ids and tp != len(gpu_ids):
        errors.append("tensor_parallel_size must match the number of visible GPU ids")
    for name in ("input_len", "output_len", "num_prompts", "max_concurrency", "warmup_requests"):
        if not isinstance(config[name], int) or config[name] < 1:
            errors.append(f"{name} must be a positive integer")
    try:
        utilization = float(config["gpu_memory_utilization"])
        if not 0 < utilization <= 1:
            errors.append("gpu_memory_utilization must be in (0, 1]")
    except (TypeError, ValueError):
        errors.append("gpu_memory_utilization must be numeric")
    model_path = Path(str(config["model_path"]))
    if project_root and not model_path.is_absolute():
        model_path = project_root / model_path
    if not model_path.is_dir():
        errors.append(f"model_path does not exist: {model_path}")
    return errors


def gpu_snapshot() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            rows.append({"index": int(parts[0]), "name": parts[1], "memory_total_mib": int(parts[2]), "memory_used_mib": int(parts[3]), "utilization_gpu": int(parts[4])})
        except ValueError:
            continue
    return rows


def listening_ports() -> set[int]:
    try:
        result = subprocess.run(["ss", "-ltnH"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    ports = set()
    for line in result.stdout.splitlines():
        address = line.split()[3] if len(line.split()) > 3 else ""
        match = re.search(r":(\d+)$", address)
        if match:
            ports.add(int(match.group(1)))
    return ports


def choose_port(preferred: int, candidates: list[int] | None = None) -> int:
    occupied = listening_ports()
    for port in [preferred, *(candidates or [])]:
        if port not in occupied:
            return port
    raise RuntimeError(f"no available port among {[preferred, *(candidates or [])]}")


def port_candidates(config: dict[str, Any]) -> list[int]:
    """Return deterministic fallback ports without mutating the source config."""
    values = [int(config["port"])]
    raw = config.get("port_candidates", [])
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, (list, tuple)):
        values.extend(int(item) for item in raw)
    port_range = config.get("port_range")
    if isinstance(port_range, (list, tuple)) and len(port_range) == 2:
        values.extend(range(int(port_range[0]), int(port_range[1]) + 1))
    result = []
    for port in values:
        if 1 <= port <= 65535 and port not in result:
            result.append(port)
    return result
