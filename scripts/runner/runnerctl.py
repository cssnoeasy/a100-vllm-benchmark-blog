#!/usr/bin/env python3
"""Operational CLI for experiment runs and persistent batch plans."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from runner_core import ACTIVE_STATUSES, TERMINAL_STATUSES, append_event, choose_port, config_hash, exclusive_lock, gpu_snapshot, inspect_run, listening_ports, load_manifest, parse_gpu_ids, port_candidates, process_command, stale_lock_paths, stop_process_group, update_manifest, utc_now, validate_config

RETRYABLE_FAILURES = {
    "PORT_IN_USE",
    "READINESS_TIMEOUT",
    "SERVICE_EXITED",
    "CUDA_ERROR",
    "BENCHMARK_FAILED",
    "BENCHMARK_TIMEOUT",
}
FAILURE_KEYWORDS = {
    "MODEL_PATH_ERROR": ("no such file or directory", "model path", "config.json"),
    "PORT_IN_USE": ("address already in use", "bind failed"),
    "GPU_OOM": ("out of memory", "cuda out of memory", "显存不足"),
    "CUDA_ERROR": ("cuda error", "cuda driver error", "cublas error", "nccl error"),
    "SERVICE_EXITED": ("service exited before readiness", "process exited"),
    "READINESS_TIMEOUT": ("readiness timeout", "connection refused", "timed out"),
    "BENCHMARK_FAILED": ("benchmark failed", "request failed"),
    "BENCHMARK_TIMEOUT": ("benchmark_timeout", "benchmark exceeded"),
    "VALIDATION_FAILED": ("validation failed", "manifest status"),
}


def classify_task_failure(task: dict) -> str:
    text = "\n".join(str(task.get(key, "")) for key in ("error", "output")).lower()
    for category, keywords in FAILURE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "UNKNOWN"


def run_dirs(root: Path) -> list[Path]:
    return sorted((path for path in root.glob("*") if path.is_dir()), key=lambda path: path.name)


def cmd_list(args: argparse.Namespace) -> int:
    for run_dir in run_dirs(args.root):
        info = inspect_run(run_dir, args.stale_after)
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    info = inspect_run(args.run_dir, args.stale_after)
    validation_path = args.run_dir / "validation.txt"
    diagnosis_path = args.run_dir / "diagnosis.md"
    if validation_path.exists():
        info["validation"] = validation_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    if diagnosis_path.exists():
        for line in diagnosis_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("**") and line.endswith("**"):
                info["diagnosis"] = line.strip("*")
                break
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 2 if info.get("stale") else 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    changed = 0
    for run_dir in run_dirs(args.root):
        info = inspect_run(run_dir, args.stale_after)
        if not info.get("stale"):
            continue
        update_manifest(run_dir, "failed", failed_stage="heartbeat", error="runner heartbeat is stale", reconciled_at=utc_now())
        append_event(run_dir, "stale_run_reconciled", reason="heartbeat_timeout")
        changed += 1
        print(f"reconciled {run_dir}")
    print(f"reconciled={changed}")
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    recovered = 0
    for run_dir in run_dirs(args.root):
        info = inspect_run(run_dir, args.stale_after)
        if not info.get("exists") or info.get("status") not in ACTIVE_STATUSES:
            continue
        dead_server = "server_pid" in info and not info.get("server_alive", True)
        if not info.get("stale") and not dead_server:
            continue
        stopped = True
        update_manifest(run_dir, "recovering", recovery_started_at=utc_now())
        if info.get("server_pid"):
            stopped = stop_process_group(int(info["server_pid"]), args.timeout)
        update_manifest(
            run_dir,
            "failed",
            failed_stage="recovery",
            error="active run recovered after stale heartbeat or exited server",
            recovered_at=utc_now(),
            process_group_stopped=stopped,
        )
        append_event(run_dir, "run_recovered", reason="stale_or_exited_server", process_group_stopped=stopped)
        for lock_path in stale_lock_paths(args.lock_root, args.lock_stale_after):
            lock_path.unlink(missing_ok=True)
            append_event(run_dir, "stale_lock_removed", lock_path=str(lock_path))
        recovered += 1
        print(f"recovered {run_dir}")
    print(f"recovered={recovered}")
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.run_dir)
    status = str(manifest.get("status", "created"))
    if status in TERMINAL_STATUSES:
        print(f"already terminal: {manifest.get('run_id', args.run_dir.name)} ({status})")
        return 0
    pid_path = args.run_dir / "server.pid"
    stopped = True
    if pid_path.exists():
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        command = process_command(pid)
        if command and "vllm" not in command:
            raise SystemExit(f"refusing to stop unexpected process {pid}: {command}")
        stopped = stop_process_group(pid, args.timeout)
    if status not in TERMINAL_STATUSES:
        update_manifest(args.run_dir, "cancelled", cancelled_at=utc_now(), cancel_reason=args.reason, process_group_stopped=stopped)
    else:
        update_manifest(args.run_dir, None, cancelled_at=utc_now(), cancel_reason=args.reason, process_group_stopped=stopped)
    append_event(args.run_dir, "run_cancelled", reason=args.reason, process_group_stopped=stopped)
    args.lock_root.mkdir(parents=True, exist_ok=True)
    removed = []
    for lock_path in stale_lock_paths(args.lock_root, args.lock_stale_after):
        lock_path.unlink(missing_ok=True)
        removed.append(str(lock_path))
    append_event(args.run_dir, "cancel_cleanup", stale_locks_removed=removed)
    print(f"cancelled {manifest.get('run_id', args.run_dir.name)}")
    return 0 if stopped else 1


def cmd_retry(args: argparse.Namespace) -> int:
    source = args.run_dir
    manifest = load_manifest(source)
    if manifest.get("status") not in TERMINAL_STATUSES:
        raise SystemExit(f"run is not terminal: {manifest.get('status')}")
    target = source.with_name(f"{source.name}__retry{args.attempt}")
    if target.exists():
        raise SystemExit(f"target already exists: {target}")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("server.log", "server_tail.log", "gpu_metrics.csv", "result.json", "validation.txt", "diagnosis.md", "events.jsonl", "heartbeat.json", "server.pid"))
    update_manifest(target, "created", retry_of=manifest.get("run_id", source.name), retry_attempt=args.attempt, created_at=utc_now(), error=None, failed_stage=None)
    append_event(target, "retry_created", source_run=manifest.get("run_id", source.name), attempt=args.attempt)
    print(target)
    return 0


def run_helper(args: argparse.Namespace, script: str) -> int:
    if not args.run_dir.is_dir():
        raise SystemExit(f"run directory not found: {args.run_dir}")
    result = subprocess.run(
        [sys.executable, f"scripts/{script}", str(args.run_dir)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(result.stdout, end="")
    return result.returncode


def cmd_validate(args: argparse.Namespace) -> int:
    return run_helper(args, "validate_run.py")


def cmd_diagnose(args: argparse.Namespace) -> int:
    return run_helper(args, "diagnose_run.py")


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_run(run_dir: Path) -> dict:
    manifest = load_manifest(run_dir) if (run_dir / "manifest.json").exists() else {}
    result = {}
    if (run_dir / "result.json").exists():
        try:
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {}
    gpu_rows = []
    metrics_path = run_dir / "gpu_metrics.csv"
    if metrics_path.exists():
        with metrics_path.open(encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                normalized = {str(key).strip(): value.strip() for key, value in row.items() if key}
                gpu_rows.append(normalized)
    def metric(name):
        values = [_number(row.get(name)) for row in gpu_rows]
        values = [value for value in values if value is not None]
        return {"avg": round(sum(values) / len(values), 2), "peak": round(max(values), 2), "samples": len(values)} if values else {}
    category = ""
    diagnosis = run_dir / "diagnosis.md"
    if diagnosis.exists():
        for line in diagnosis.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("**") and line.endswith("**"):
                category = line.strip("*")
                break
    return {
        "run_id": manifest.get("run_id", run_dir.name),
        "run_dir": str(run_dir),
        "status": manifest.get("status", "legacy"),
        "diagnosis_category": category,
        "model_id": manifest.get("model_id", result.get("model_id", "")),
        "dtype": manifest.get("dtype", ""),
        "quantization": manifest.get("quantization", ""),
        "cuda_visible_devices": manifest.get("cuda_visible_devices", ""),
        "tensor_parallel_size": manifest.get("tensor_parallel_size", ""),
        "workload": manifest.get("workload", ""),
        "max_concurrency": manifest.get("max_concurrency", result.get("max_concurrency", "")),
        "completed": result.get("completed", manifest.get("completed", "")),
        "failed": result.get("failed", manifest.get("failed", "")),
        "output_throughput": result.get("output_throughput", ""),
        "median_ttft_ms": result.get("median_ttft_ms", ""),
        "p99_ttft_ms": result.get("p99_ttft_ms", ""),
        "median_tpot_ms": result.get("median_tpot_ms", ""),
        "p99_tpot_ms": result.get("p99_tpot_ms", ""),
        "gpu_utilization": metric("utilization_gpu"),
        "gpu_memory_used_mb": metric("memory_used_mb"),
        "gpu_power_draw_w": metric("power_draw_w"),
        "gpu_samples": len(gpu_rows),
    }


def write_report_files(records: list[dict], output: Path, title: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    csv_path = output.with_suffix(".csv")
    fields = sorted({key for record in records for key, value in record.items() if not isinstance(value, dict)})
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: value for key, value in record.items() if not isinstance(value, dict)} for record in records)
    md_path = output.with_suffix(".md")
    columns = ["run_id", "status", "model_id", "tensor_parallel_size", "max_concurrency", "output_throughput", "p99_ttft_ms", "p99_tpot_ms", "failed", "diagnosis_category"]
    lines = [f"# {title}", "", "| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for record in records:
        lines.append("| " + " | ".join(str(record.get(column, "")) for column in columns) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"JSON: {output}\nCSV: {csv_path}\nMarkdown: {md_path}")


def cmd_summarize(args: argparse.Namespace) -> int:
    records = [summarize_run(path) for path in run_dirs(args.root) if (path / "manifest.json").exists() or (path / "result.json").exists()]
    write_report_files(records, args.output, "Runner Run Summary")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    records = [summarize_run(path) for path in run_dirs(args.root) if (path / "manifest.json").exists() or (path / "result.json").exists()]
    groups = {}
    for record in records:
        key = (record.get("model_id"), record.get("dtype"), record.get("quantization"), record.get("tensor_parallel_size"), record.get("max_concurrency"), record.get("workload"))
        groups.setdefault(key, []).append(record)
    comparisons = []
    for key, items in groups.items():
        throughputs = [_number(item.get("output_throughput")) for item in items]
        throughputs = [item for item in throughputs if item is not None]
        comparisons.append({"configuration": dict(zip(("model_id", "dtype", "quantization", "tensor_parallel_size", "max_concurrency", "workload"), key)), "runs": len(items), "validated_runs": sum(item.get("status") == "validated" for item in items), "mean_output_throughput": round(sum(throughputs) / len(throughputs), 2) if throughputs else None, "run_ids": [item["run_id"] for item in items]})
    write_report_files(comparisons, args.output, "Runner Configuration Comparison")
    return 0


def load_yaml_config(path: Path) -> dict:
    try:
        import yaml
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception as exc:
        raise SystemExit(f"cannot load config {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"config must be a mapping: {path}")
    defaults = data.get("default_config")
    if defaults:
        base = Path("configs/defaults") / f"{defaults}.yaml"
        if base.exists():
            with base.open("r", encoding="utf-8") as handle:
                merged = yaml.safe_load(handle) or {}
            merged.update(data)
            data = merged
    profile = data.get("profile")
    if profile:
        profile_path = Path("configs/profiles") / f"{profile}.yaml"
        if profile_path.exists():
            with profile_path.open("r", encoding="utf-8") as handle:
                merged = yaml.safe_load(handle) or {}
            merged.update(data)
            data = merged
    return data


def cmd_preflight(args: argparse.Namespace) -> int:
    config = load_yaml_config(args.config)
    errors = validate_config(config, Path.cwd())
    if errors:
        print("PREFLIGHT FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PREFLIGHT PASSED")
    print(f"model_path: {config['model_path']}")
    print(f"port: {config['port']}")
    print(f"gpu_ids: {','.join(str(item) for item in parse_gpu_ids(config['cuda_visible_devices']))}")
    print(f"tensor_parallel_size: {config['tensor_parallel_size']}")
    return 0


def command_output(command: list[str], timeout: float = 10) -> tuple[int, str]:
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = []
    checks.append(("python", sys.executable, 0))
    code, output = command_output(["vllm", "--version"])
    checks.append(("vllm", output or "unavailable", code))
    gpu_code, gpu_output = command_output(["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader"])
    checks.append(("gpu", gpu_output or "unavailable", gpu_code))
    port_code, port_output = command_output(["ss", "-ltnH"])
    checks.append(("ports", port_output or "unavailable", port_code))
    failures = 0
    for name, value, code in checks:
        print(f"[{ 'PASS' if code == 0 else 'FAIL' }] {name}: {value}")
        failures += code != 0
    if gpu_code == 0 and gpu_output:
        gpu_lines = [line for line in gpu_output.splitlines() if line.strip()]
        if len(gpu_lines) != 2:
            print(f"[WARN] expected 2 GPUs, detected {len(gpu_lines)}")
    return 1 if failures else 0


def cmd_batch(args: argparse.Namespace) -> int:
    configs = [str(path) for path in args.configs]
    tasks = [
        {"id": index, "config": config, "status": "queued", "attempts": 0, "priority": args.priority}
        for index, config in enumerate(configs, start=1)
    ]
    plan = {
        "schema_version": 2,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "status": "planned",
        "tasks": tasks,
        "summary": {"total": len(tasks), "pending": 0, "queued": len(tasks), "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "skipped": 0},
    }
    save_plan(args.output, plan)
    print(f"planned {len(configs)} configs: {args.output}")
    return 0


def task_resource_info(task: dict) -> tuple[list[int], list[int], int, str]:
    config = load_yaml_config(Path(task["config"]))
    gpu_ids = parse_gpu_ids(config.get("cuda_visible_devices", ""))
    return gpu_ids, port_candidates(config), int(config.get("priority", task.get("priority", 0))), config_hash(config)


def acquire_task_resources(task: dict, args: argparse.Namespace):
    gpu_ids, ports, priority, effective_hash = task_resource_info(task)
    available = {row["index"]: row for row in gpu_snapshot()}
    missing = [gpu for gpu in gpu_ids if gpu not in available]
    if missing:
        raise RuntimeError(f"requested GPU ids are unavailable: {missing}")
    low_memory = [gpu for gpu in gpu_ids if available[gpu]["memory_total_mib"] - available[gpu]["memory_used_mib"] < args.min_free_memory_mib]
    if low_memory:
        raise RuntimeError(f"requested GPUs do not have enough free memory: {low_memory}")
    port = choose_port(ports[0], ports[1:])
    stack = ExitStack()
    try:
        for gpu in sorted(gpu_ids):
            stack.enter_context(exclusive_lock(args.resource_root / f"gpu-{gpu}.lock", timeout=0))
        stack.enter_context(exclusive_lock(args.resource_root / f"port-{port}.lock", timeout=0))
        return stack, port, effective_hash
    except Exception:
        stack.close()
        raise


def acquire_task_resources_waiting(task: dict, args: argparse.Namespace, on_wait=None):
    deadline = time.monotonic() + args.resource_wait_timeout
    while True:
        try:
            return acquire_task_resources(task, args)
        except RuntimeError as exc:
            if not args.wait_for_resources or time.monotonic() >= deadline:
                raise RuntimeError(f"RESOURCE_WAIT_TIMEOUT: {exc}") from exc
            task["status"] = "waiting_resources"
            task["waiting_since"] = task.get("waiting_since", utc_now())
            task["waiting_reason"] = str(exc)
            task["waiting_elapsed_seconds"] = max(0, int(args.resource_wait_timeout - max(0, deadline - time.monotonic())))
            if on_wait is not None:
                on_wait(task)
            time.sleep(args.resource_poll_interval)


def save_plan(path: Path, plan: dict) -> None:
    plan["updated_at"] = utc_now()
    counts = {"pending": 0, "queued": 0, "waiting_resources": 0, "retrying": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "skipped": 0}
    for task in plan.get("tasks", []):
        status = task.get("status", "pending")
        if status in counts:
            counts[status] += 1
    plan["summary"] = {"total": len(plan.get("tasks", [])), **counts}
    actionable = counts["completed"] + counts["skipped"] + counts["cancelled"]
    plan["status"] = "completed" if actionable == len(plan.get("tasks", [])) and counts["cancelled"] == 0 else "cancelled" if actionable == len(plan.get("tasks", [])) else "failed" if counts["failed"] else "running" if counts["running"] else "planned"
    temp = path.with_name(f".{path.name}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read batch plan {path}: {exc}")
    if not isinstance(plan.get("tasks"), list):
        raise SystemExit(f"unsupported batch plan format: {path}")
    return plan


def cmd_batch_status(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    save_plan(args.plan, plan)
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0 if plan["status"] in {"planned", "running", "completed", "cancelled"} else 1


def cmd_batch_cancel(args: argparse.Namespace) -> int:
    lock_path = args.plan.with_suffix(args.plan.suffix + ".cancel.lock")
    with exclusive_lock(lock_path, timeout=args.lock_timeout):
        plan = load_plan(args.plan)
        requested = {int(value) for value in args.task_id} if args.task_id else None
        changed = 0
        for task in plan.get("tasks", []):
            if requested is not None and int(task.get("id", -1)) not in requested:
                continue
            if task.get("status") in {"completed", "failed", "cancelled", "skipped"}:
                continue
            task["status"] = "cancelled"
            task["cancel_reason"] = args.reason
            task["cancelled_at"] = utc_now()
            task["cancel_requested"] = True
            changed += 1
        save_plan(args.plan, plan)
    print(f"cancelled {changed} task(s): {args.plan}")
    return 0


def cmd_batch_run(args: argparse.Namespace) -> int:
    lock_path = args.plan.with_suffix(args.plan.suffix + ".exec.lock")
    with exclusive_lock(lock_path, timeout=args.lock_timeout):
        return cmd_batch_run_locked(args)


def cmd_batch_run_locked(args: argparse.Namespace) -> int:
    plan = load_plan(args.plan)
    executor = args.executor or [sys.executable, "scripts/run_experiment_once.py"]
    failures = 0
    args.resource_root.mkdir(parents=True, exist_ok=True)
    pending_tasks = sorted(
        [task for task in plan["tasks"] if task.get("status") not in {"completed", "skipped", "cancelled"}],
        key=lambda task: (-int(task.get("priority", 0)), int(task.get("id", 0))),
    )
    task_order = {id(task): index for index, task in enumerate(pending_tasks)}
    plan["execution_order"] = [task["id"] for task in pending_tasks]
    save_plan(args.plan, plan)
    for task in pending_tasks:
        if task.get("status") in {"completed", "skipped", "cancelled"}:
            continue
        if task.get("status") == "running":
            task["status"] = "queued"
            task["recovered_at"] = utc_now()
        while task.get("attempts", 0) < args.max_attempts:
            if task.get("status") == "cancelled":
                break
            task["status"] = "running"
            task["attempts"] = task.get("attempts", 0) + 1
            task["started_at"] = utc_now()
            try:
                task["config_hash"] = task_resource_info(task)[3]
            except Exception as exc:
                task["config_hash_error"] = str(exc)
            save_plan(args.plan, plan)
            command = [*executor, task["config"]]
            task["command"] = command
            if args.dry_run:
                task["status"] = "skipped"
                task["finished_at"] = utc_now()
                task["output"] = "dry-run: " + " ".join(command)
                save_plan(args.plan, plan)
                print(f"task {task['id']} skipped (dry-run): {task['config']}")
                break
            resource_stack = None
            try:
                if not args.dry_run and args.resource_check:
                    resource_stack, allocated_port, effective_hash = acquire_task_resources_waiting(
                        task, args, on_wait=lambda _task: save_plan(args.plan, plan)
                    )
                    task["status"] = "running"
                    task["resources_acquired_at"] = utc_now()
                    task["config_hash"] = effective_hash
                else:
                    allocated_port = None
                task["allocated_port"] = allocated_port
                task_env = os.environ.copy()
                if allocated_port is not None:
                    task_env["RUNNER_ALLOCATED_PORT"] = str(allocated_port)
                result = subprocess.run(command, env=task_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=args.task_timeout or None)
                task["returncode"] = result.returncode
                task["output"] = result.stdout[-4000:]
                task["finished_at"] = utc_now()
                latest = load_plan(args.plan)
                latest_task = next((item for item in latest.get("tasks", []) if int(item.get("id", -1)) == int(task.get("id", -2))), {})
                if latest_task.get("cancel_requested") or latest_task.get("status") == "cancelled":
                    task["status"] = "cancelled"
                    task["cancelled_at"] = latest_task.get("cancelled_at", utc_now())
                    task["cancel_reason"] = latest_task.get("cancel_reason", "operator requested batch cancellation")
                else:
                    task["status"] = "completed" if result.returncode == 0 else "failed"
            except subprocess.TimeoutExpired as exc:
                task["status"] = "failed"
                task["error"] = f"TASK_TIMEOUT: task timeout after {args.task_timeout}s"
                task["timeout_category"] = "TASK_TIMEOUT"
                task["output"] = (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else ""
                task["finished_at"] = utc_now()
            except OSError as exc:
                task["status"] = "failed"
                task["error"] = str(exc)
                task["finished_at"] = utc_now()
            except RuntimeError as exc:
                task["status"] = "failed"
                task["error"] = str(exc)
                task["timeout_category"] = "RESOURCE_WAIT_TIMEOUT" if str(exc).startswith("RESOURCE_WAIT_TIMEOUT:") else None
                task["finished_at"] = utc_now()
            finally:
                if resource_stack is not None:
                    resource_stack.close()
            save_plan(args.plan, plan)
            if task["status"] == "completed":
                print(f"task {task['id']} completed: {task['config']}")
                break
            if task["status"] == "cancelled":
                print(f"task {task['id']} cancelled: {task['config']}")
                break
            task["diagnosis_category"] = classify_task_failure(task)
            task["retry_allowed"] = task["diagnosis_category"] in args.retry_categories
            task["retry_policy"] = {
                "category": task["diagnosis_category"],
                "allowed_categories": sorted(args.retry_categories),
                "decision": "retry" if task["retry_allowed"] else "fail",
            }
            save_plan(args.plan, plan)
            if task.get("attempts", 0) < args.max_attempts and task["retry_allowed"]:
                task["status"] = "retrying"
                task["retry_scheduled_at"] = utc_now()
                task["retry_reason"] = task.get("error") or f"returncode={task.get('returncode')}"
                save_plan(args.plan, plan)
                if args.retry_backoff:
                    time.sleep(args.retry_backoff)
                task["status"] = "queued"
                save_plan(args.plan, plan)
                continue
            failures += 1
            print(f"task {task['id']} failed: {task['config']}")
            break
        if failures and not args.continue_on_error:
            break
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect and operate experiment runs")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list"); p.add_argument("root", type=Path, default=Path("results/runs"), nargs="?"); p.add_argument("--stale-after", type=int, default=120); p.set_defaults(func=cmd_list)
    p = sub.add_parser("status"); p.add_argument("run_dir", type=Path); p.add_argument("--stale-after", type=int, default=120); p.set_defaults(func=cmd_status)
    p = sub.add_parser("validate"); p.add_argument("run_dir", type=Path); p.set_defaults(func=cmd_validate)
    p = sub.add_parser("diagnose"); p.add_argument("run_dir", type=Path); p.set_defaults(func=cmd_diagnose)
    p = sub.add_parser("summarize"); p.add_argument("root", type=Path, default=Path("results/runs"), nargs="?"); p.add_argument("--output", type=Path, default=Path("results/reports/summary.json")); p.set_defaults(func=cmd_summarize)
    p = sub.add_parser("compare"); p.add_argument("root", type=Path, default=Path("results/runs"), nargs="?"); p.add_argument("--output", type=Path, default=Path("results/reports/comparison.json")); p.set_defaults(func=cmd_compare)
    p = sub.add_parser("preflight"); p.add_argument("config", type=Path); p.set_defaults(func=cmd_preflight)
    p = sub.add_parser("doctor"); p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("reconcile"); p.add_argument("root", type=Path, default=Path("results/runs"), nargs="?"); p.add_argument("--stale-after", type=int, default=120); p.set_defaults(func=cmd_reconcile)
    p = sub.add_parser("recover"); p.add_argument("root", type=Path, default=Path("results/runs"), nargs="?"); p.add_argument("--stale-after", type=int, default=120); p.add_argument("--timeout", type=float, default=30); p.add_argument("--lock-root", type=Path, default=Path("results/locks")); p.add_argument("--lock-stale-after", type=float, default=300); p.set_defaults(func=cmd_recover)
    p = sub.add_parser("cancel"); p.add_argument("run_dir", type=Path); p.add_argument("--reason", default="operator requested"); p.add_argument("--timeout", type=float, default=30); p.add_argument("--lock-root", type=Path, default=Path("results/locks")); p.add_argument("--lock-stale-after", type=float, default=300); p.set_defaults(func=cmd_cancel)
    p = sub.add_parser("retry"); p.add_argument("run_dir", type=Path); p.add_argument("--attempt", type=int, default=1); p.set_defaults(func=cmd_retry)
    p = sub.add_parser("plan-batch"); p.add_argument("configs", nargs="+", type=Path); p.add_argument("--output", type=Path, default=Path("results/batches/plan.json")); p.add_argument("--priority", type=int, default=0); p.set_defaults(func=cmd_batch)
    p = sub.add_parser("batch-status"); p.add_argument("plan", type=Path); p.set_defaults(func=cmd_batch_status)
    p = sub.add_parser("batch-cancel"); p.add_argument("plan", type=Path); p.add_argument("--task-id", action="append"); p.add_argument("--reason", default="operator requested batch cancellation"); p.add_argument("--lock-timeout", type=float, default=30); p.set_defaults(func=cmd_batch_cancel)
    p = sub.add_parser("batch-run"); p.add_argument("plan", type=Path); p.add_argument("--executor", nargs="+", help="executor command before the config path"); p.add_argument("--max-attempts", type=int, default=1); p.add_argument("--retry-backoff", type=float, default=0); p.add_argument("--retry-on", default=",".join(sorted(RETRYABLE_FAILURES)), help="comma-separated diagnosis categories eligible for retry"); p.add_argument("--task-timeout", type=float, default=0); p.add_argument("--continue-on-error", action="store_true"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--lock-timeout", type=float, default=0); p.add_argument("--resource-check", action="store_true"); p.add_argument("--wait-for-resources", action="store_true"); p.add_argument("--resource-wait-timeout", type=float, default=300); p.add_argument("--resource-poll-interval", type=float, default=2); p.add_argument("--min-free-memory-mib", type=int, default=1024); p.add_argument("--resource-root", type=Path, default=Path("results/locks")); p.set_defaults(func=cmd_batch_run)
    args = parser.parse_args()
    if hasattr(args, "retry_on"):
        args.retry_categories = {item.strip().upper() for item in args.retry_on.split(",") if item.strip()}
    try:
        return args.func(args)
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
