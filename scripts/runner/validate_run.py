#!/usr/bin/env python3
import csv
import json
import math
import sys
from pathlib import Path

REQUIRED_FILES = [
    "config.yaml", "command.txt", "manifest.json", "server.log",
    "server_tail.log", "server.pid", "gpu_metrics.csv", "result.json",
]

def number(value, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is not numeric")
    if not math.isfinite(result):
        raise ValueError(f"{name} is not finite")
    return result

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: scripts/validate_run.py <run_dir>")
    run_dir = Path(sys.argv[1])
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit("Missing files: manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing = [name for name in REQUIRED_FILES if not (run_dir / name).exists()]
    for artifact in manifest.get("artifacts", {}).values():
        if artifact and not (run_dir / artifact).exists():
            missing.append(artifact)
    if missing:
        raise SystemExit("Missing files: " + ", ".join(dict.fromkeys(missing)))

    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    errors = []
    if manifest.get("status") not in {"completed", "validating", "validated"}:
        errors.append(f"manifest status is {manifest.get('status')}")
    try:
        if int(result.get("completed", -1)) != int(manifest.get("num_prompts", -2)):
            errors.append("completed requests do not match num_prompts")
        if int(result.get("failed", -1)) != 0:
            errors.append("failed requests is not 0")
    except (TypeError, ValueError):
        errors.append("completed/failed counts are invalid")
    for field in ("output_throughput", "p99_ttft_ms", "p99_tpot_ms"):
        try:
            if number(result.get(field), field) <= 0:
                errors.append(f"{field} is not positive")
        except ValueError as exc:
            errors.append(str(exc))

    thresholds = {
        "min_output_throughput": ("output_throughput", "min"),
        "max_p99_ttft_ms": ("p99_ttft_ms", "max"),
        "max_p99_tpot_ms": ("p99_tpot_ms", "max"),
    }
    config = {}
    try:
        import yaml
        config = yaml.safe_load((run_dir / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception as exc:
        errors.append(f"config.yaml cannot be read: {exc}")
    for gate, (field, mode) in thresholds.items():
        if gate not in config:
            continue
        try:
            actual, limit = number(result.get(field), field), number(config[gate], gate)
            if (mode == "min" and actual < limit) or (mode == "max" and actual > limit):
                errors.append(f"quality gate {gate} failed: {field}={actual} limit={limit}")
        except ValueError as exc:
            errors.append(str(exc))

    rows = []
    with (run_dir / "gpu_metrics.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    min_samples = int(config.get("min_gpu_samples", 1))
    if len(rows) < min_samples:
        errors.append(f"gpu_metrics.csv has {len(rows)} samples, requires {min_samples}")
    expected_run_id = manifest.get("run_id")
    expected_gpu_ids = {str(item).strip() for item in str(manifest.get("cuda_visible_devices", "")).split(",") if str(item).strip()}
    observed_run_ids = {row.get("run_id", "") for row in rows}
    if expected_run_id and observed_run_ids - {expected_run_id}:
        errors.append(f"gpu metrics run_id mismatch: {sorted(observed_run_ids)}")
    observed_gpu_ids = {str(row.get("gpu_id", row.get("index", ""))).strip() for row in rows}
    if expected_gpu_ids and not expected_gpu_ids.issubset(observed_gpu_ids):
        errors.append(f"missing GPU samples for: {sorted(expected_gpu_ids - observed_gpu_ids)}")
    for row_number, row in enumerate(rows, start=1):
        for key, value in row.items():
            if key in {"run_id", "gpu_ids", "gpu_id", "index", "name", "timestamp", "ts"} or value in {None, ""}:
                continue
            try:
                number(value, f"gpu_metrics row {row_number} {key}")
            except ValueError as exc:
                errors.append(str(exc))
                break

    report = {
        "run_id": manifest.get("run_id"),
        "passed": not errors,
        "errors": errors,
        "quality_gates": {key: config[key] for key in thresholds if key in config},
        "gpu_samples": len(rows),
    }
    (run_dir / "quality_gate.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if errors:
        print("Run validation FAILED")
        for error in errors:
            print("-", error)
        raise SystemExit(1)
    print("Run validation PASSED")
    print(f"run_id: {manifest.get('run_id')}")
    print(f"completed: {result.get('completed')}")
    print(f"output_throughput: {float(result['output_throughput']):.2f}")
    print(f"gpu_samples: {len(rows)}")

if __name__ == "__main__":
    main()
