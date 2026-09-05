#!/usr/bin/env python3
"""Deterministic, GPU-free fault matrix for Runner control and validation paths."""

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
VALIDATOR = ROOT / ("validate_run.py" if (ROOT / "validate_run.py").exists() else "validate_run.new.py")
RUNNERCTL = ROOT / ("runnerctl.py" if (ROOT / "runnerctl.py").exists() else "runnerctl.new.py")
CORE = ROOT / ("runner_core.py" if (ROOT / "runner_core.py").exists() else "runner_core.new.py")


def write_run(run_dir: Path, *, run_id="fault-run", status="completed", gpu_run_id=None, gpu_ids="0", completed=4, failed=0, throughput=10.0):
    run_dir.mkdir(parents=True)
    (run_dir / "config.yaml").write_text(yaml.safe_dump({
        "num_prompts": 4,
        "min_gpu_samples": 1,
        "min_output_throughput": 1,
    }, sort_keys=False), encoding="utf-8")
    manifest = {"run_id": run_id, "status": status, "num_prompts": 4, "cuda_visible_devices": gpu_ids,
                "artifacts": {"manifest_json": "manifest.json"}}
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps({"completed": completed, "failed": failed,
        "output_throughput": throughput, "p99_ttft_ms": 5, "p99_tpot_ms": 2}), encoding="utf-8")
    for name in ("command.txt", "server.log", "server_tail.log", "server.pid"):
        (run_dir / name).write_text("\n", encoding="utf-8")
    with (run_dir / "gpu_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["run_id", "gpu_id", "utilization"])
        writer.writeheader()
        writer.writerow({"run_id": gpu_run_id or run_id, "gpu_id": "0", "utilization": "20"})


def expect_failure(label: str, command: list[str], expected: str) -> None:
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode == 0 or expected not in result.stdout:
        raise AssertionError(f"{label}: expected {expected!r}, got rc={result.returncode}: {result.stdout}")
    print(f"PASS {label}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="runner-fault-") as temp:
        root = Path(temp)
        valid = root / "valid"
        write_run(valid)
        validator = [sys.executable, str(VALIDATOR), str(valid)]
        valid_result = subprocess.run(validator, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if valid_result.returncode != 0:
            raise AssertionError("valid quality gate did not pass: " + valid_result.stdout)
        print("PASS quality_gate_pass")

        mismatch = root / "mismatch"
        write_run(mismatch, gpu_run_id="another-run")
        expect_failure("gpu_run_id_mismatch", [sys.executable, str(VALIDATOR), str(mismatch)], "run_id mismatch")

        incomplete = root / "incomplete"
        write_run(incomplete, completed=3)
        expect_failure("incomplete_result", [sys.executable, str(VALIDATOR), str(incomplete)], "completed requests")

        plan = root / "plan.json"
        plan.write_text(json.dumps({"tasks": [{"id": 1, "status": "queued"}, {"id": 2, "status": "completed"}]}), encoding="utf-8")
        cancel_env = dict(__import__("os").environ)
        import_dir = root / "imports"
        import_dir.mkdir()
        shutil.copy2(CORE, import_dir / "runner_core.py")
        cancel_env["PYTHONPATH"] = str(import_dir)
        cancel = subprocess.run([sys.executable, str(RUNNERCTL), "batch-cancel", str(plan), "--task-id", "1"], cwd=ROOT, env=cancel_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if cancel.returncode != 0:
            raise AssertionError(cancel.stdout)
        plan_data = json.loads(plan.read_text(encoding="utf-8"))
        assert plan_data["tasks"][0]["status"] == "cancelled"
        assert plan_data["status"] == "cancelled"
        print("PASS batch_cancel")

        import importlib.util
        core_spec = importlib.util.spec_from_file_location("fault_runner_core", CORE)
        core = importlib.util.module_from_spec(core_spec)
        core_spec.loader.exec_module(core)
        config_hash, schema_errors = core.config_hash, core.schema_errors
        config = {"experiment": "x", "service_mode": "s", "feature": "f", "feature_variant": "v",
                  "model_path": "/m", "model_name": "m", "cuda_visible_devices": "0", "dataset": "d", "trial": "1",
                  "port": 1, "tensor_parallel_size": 1, "max_model_len": 1, "input_len": 1, "output_len": 1,
                  "num_prompts": 1, "max_concurrency": 1, "warmup_requests": 1, "request_rate": "inf"}
        assert not schema_errors(config)
        assert config_hash({**config, "requested_port": 9999}) == config_hash(config)
        assert config_hash({**config, "input_len": 2}) != config_hash(config)
        print("PASS schema_and_hash")
    print("FAULT MATRIX PASSED: 6 cases")


if __name__ == "__main__":
    main()
