#!/usr/bin/env python3
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

import yaml

from runner_core import append_event, atomic_write_json, choose_port, config_hash, exclusive_lock, gpu_snapshot, heartbeat, listening_ports, parse_gpu_ids, port_candidates, stop_process_group, utc_now, validate_config, validate_transition

REQUIRED_FIELDS = [
    "experiment", "service_mode", "feature", "feature_variant",
    "model_path", "model_name", "port", "cuda_visible_devices",
    "tensor_parallel_size", "max_model_len", "gpu_memory_utilization",
    "dataset", "input_len", "output_len", "num_prompts",
    "max_concurrency", "request_rate", "warmup_requests", "trial",
]

def now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")

def load_yaml_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Config must be a mapping: {path}")
    return data

def load_named_config(base_dir: Path, name: str) -> dict:
    path = base_dir / f"{name}.yaml"
    if not path.exists():
        raise SystemExit(f"Named config not found: {path}")
    return load_yaml_file(path)

def load_config(path: Path) -> dict:
    experiment_config = load_yaml_file(path)
    config = {}

    default_name = experiment_config.get("default_config")
    if default_name:
        config.update(load_named_config(Path("configs/defaults"), str(default_name)))

    profile_name = experiment_config.get("profile")
    if profile_name:
        config.update(load_named_config(Path("configs/profiles"), str(profile_name)))

    config.update(experiment_config)
    allocated_port = os.environ.get("RUNNER_ALLOCATED_PORT")
    if allocated_port:
        config["requested_port"] = int(config["port"])
        config["port"] = int(allocated_port)
    config["config_path"] = str(path)

    missing = [field for field in REQUIRED_FIELDS if field not in config]
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(missing)}")
    errors = validate_config(config, Path.cwd())
    if errors:
        raise SystemExit("Config preflight failed:\n" + "\n".join(f"- {error}" for error in errors))
    return config

def build_run_id(config: dict) -> str:
    workload = f'{config["input_len"]}x{config["output_len"]}'
    feature = config.get("feature", "baseline")
    variant = config.get("feature_variant", "default")
    precision = config.get("quantization") or config.get("dtype", "float16")
    kv_cache_dtype = config.get("kv_cache_dtype", "auto")
    if feature == "baseline" and variant == "default":
        feature_part = config["service_mode"]
    else:
        feature_part = f'{config["service_mode"]}_{feature}_{variant}'
    precision_part = f'{precision}_kv-{kv_cache_dtype}'.replace("/", "-")
    return f'week5_{feature_part}_{precision_part}_{workload}_c{config["max_concurrency"]}_{config["trial"]}'

def build_vllm_command(config: dict) -> list[str]:
    command = [
        "vllm", "serve", config["model_path"],
        "--host", "0.0.0.0",
        "--port", str(config["port"]),
        "--served-model-name", config["model_name"],
        "--dtype", str(config.get("dtype", "float16")),
        "--tensor-parallel-size", str(config["tensor_parallel_size"]),
        "--max-model-len", str(config["max_model_len"]),
        "--gpu-memory-utilization", str(config["gpu_memory_utilization"]),
    ]

    if config.get("quantization"):
        command.extend(["--quantization", str(config["quantization"])])
    if config.get("kv_cache_dtype"):
        command.extend(["--kv-cache-dtype", str(config["kv_cache_dtype"])])

    for arg in config.get("vllm_extra_args", []):
        command.append(str(arg))

    return command

def write_manifest(run_dir: Path, manifest: dict) -> None:
    atomic_write_json(run_dir / "manifest.json", manifest)

def set_status(manifest: dict, run_dir: Path, status: str, timestamp_field: str | None = None) -> None:
    validate_transition(str(manifest.get("status", "created")), status)
    manifest["status"] = status
    if timestamp_field:
        manifest[timestamp_field] = now()
    manifest["updated_at"] = utc_now()
    manifest.setdefault("status_history", []).append({"status": status, "at": manifest["updated_at"]})
    write_manifest(run_dir, manifest)
    append_event(run_dir, "status_changed", status=status, timestamp_field=timestamp_field)
    heartbeat(run_dir, stage=status, status=status)
def command_output(command: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout,
        )
        return result.stdout.strip()
    except Exception as exc:
        return f"<failed to collect: {exc}>"

def run_diagnosis(run_dir: Path, manifest: dict) -> None:
    diagnose_script = Path("scripts/diagnose_run.py")
    if not diagnose_script.exists():
        return

    try:
        output = subprocess.check_output(
            [sys.executable, str(diagnose_script), str(run_dir)],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        manifest.setdefault("artifacts", {})["diagnosis_md"] = "diagnosis.md"
        manifest["diagnosis_output"] = output.strip()
    except Exception as exc:
        manifest["diagnosis_error"] = str(exc)


def write_environment_snapshot(run_dir: Path, config: dict, command: list[str]) -> None:
    freeze = command_output(["python", "-m", "pip", "freeze"], timeout=60)
    important_packages = []
    for line in freeze.splitlines():
        lower = line.lower()
        if any(name in lower for name in ["vllm", "torch", "transformers", "tokenizers", "cuda", "triton", "ray"]):
            important_packages.append(line)

    lines = [
        "# Environment Snapshot",
        "",
        "## Run Context",
        f"captured_at: {now()}",
        f"run_config_experiment: {config.get('experiment', '')}",
        f"service_mode: {config.get('service_mode', '')}",
        f"profile: {config.get('profile', 'unspecified')}",
        f"model_name: {config.get('model_name', '')}",
        f"model_path: {config.get('model_path', '')}",
        f"model_revision: {config.get('model_revision', 'unspecified')}",
        f"model_hash: {config.get('model_hash', 'unspecified')}",
        f"dtype: {config.get('dtype', 'float16')}",
        f"quantization: {config.get('quantization', 'none')}",
        f"kv_cache_dtype: {config.get('kv_cache_dtype', 'auto')}",
        f"enable_thinking: {config.get('enable_thinking', 'unspecified')}",
        f"cuda_visible_devices: {config.get('cuda_visible_devices', '')}",
        f"tensor_parallel_size: {config.get('tensor_parallel_size', '')}",
        "",
        "## vLLM Command",
        "```bash",
        "CUDA_VISIBLE_DEVICES=" + str(config["cuda_visible_devices"]) + " " + " ".join(command),
        "```",
        "",
        "## Host",
        "```text",
        command_output(["hostname"]),
        "```",
        "",
        "## Date",
        "```text",
        command_output(["date", "-Is"]),
        "```",
        "",
        "## Working Directory",
        "```text",
        command_output(["pwd"]),
        "```",
        "",
        "## Python",
        "```text",
        command_output(["which", "python"]),
        command_output(["python", "--version"]),
        "```",
        "",
        "## vLLM",
        "```text",
        command_output(["which", "vllm"]),
        command_output(["vllm", "--version"]),
        "```",
        "",
        "## Important Python Packages",
        "```text",
        "\n".join(important_packages) if important_packages else "<none matched>",
        "```",
        "",
        "## GPU",
        "```text",
        command_output(["nvidia-smi"], timeout=60),
        "```",
    ]

    (run_dir / "environment.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def wait_ready(
    port: int,
    process: subprocess.Popen,
    model_name: str,
    enable_thinking: object = None,
    timeout: int = 300,
) -> None:
    models_url = f"http://127.0.0.1:{port}/v1/models"
    chat_url = f"http://127.0.0.1:{port}/v1/chat/completions"
    deadline = time.time() + timeout
    last_error = ""

    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"service exited before readiness, returncode={process.returncode}")

        try:
            with urllib.request.urlopen(models_url, timeout=5) as response:
                models = json.loads(response.read().decode("utf-8"))
            model_ids = {item.get("id") for item in models.get("data", []) if isinstance(item, dict)}
            if model_name not in model_ids:
                last_error = f"model {model_name!r} not in /v1/models: {sorted(model_ids)}"
                time.sleep(2)
                continue

            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": "只回答 OK"}],
                "temperature": 0,
                "max_tokens": 1,
            }
            if isinstance(enable_thinking, bool):
                payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}

            request = urllib.request.Request(
                chat_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
                return
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                body = ""
            last_error = f"HTTP {exc.code}: {body}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)

    raise RuntimeError(f"readiness timeout after {timeout}s: {last_error}")

def stop_process(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    if process.poll() is not None:
        return
    stop_process_group(process.pid, timeout=60)

def write_server_tail(run_dir: Path) -> None:
    server_log = run_dir / "server.log"
    tail_log = run_dir / "server_tail.log"
    if not server_log.exists():
        return
    lines = server_log.read_text(encoding="utf-8", errors="replace").splitlines()
    tail_log.write_text("\n".join(lines[-120:]) + "\n", encoding="utf-8")

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: scripts/run_experiment_once.py <config.yaml>")

    config_path = Path(sys.argv[1])
    config = load_config(config_path)
    gpu_ids = parse_gpu_ids(config["cuda_visible_devices"])
    available_gpus = {row["index"] for row in gpu_snapshot()}
    missing_gpus = [gpu for gpu in gpu_ids if gpu not in available_gpus]
    if missing_gpus:
        raise SystemExit(f"Requested GPU ids are unavailable: {missing_gpus}")
    requested_port = int(config["port"])
    try:
        port = choose_port(requested_port, port_candidates(config)[1:])
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    config["requested_port"] = requested_port
    config["port"] = port
    run_id = build_run_id(config)
    run_dir = Path("results/runs") / run_id
    workload = f'{config["input_len"]}x{config["output_len"]}'

    if run_dir.exists():
        raise SystemExit(f"Run dir already exists: {run_dir}")

    resource_stack = ExitStack()
    resource_root = Path("results/locks")
    try:
        for gpu in sorted(gpu_ids):
            resource_stack.enter_context(exclusive_lock(resource_root / f"gpu-{gpu}.lock", timeout=0))
        resource_stack.enter_context(exclusive_lock(resource_root / f"port-{port}.lock", timeout=0))
    except Exception:
        resource_stack.close()
        raise

    run_dir.mkdir(parents=True)
    shutil.copy2(config_path, run_dir / "config.yaml")

    command = build_vllm_command(config)
    (run_dir / "command.txt").write_text(
        "CUDA_VISIBLE_DEVICES=" + str(config["cuda_visible_devices"]) + " " + " ".join(command) + "\n",
        encoding="utf-8",
    )
    write_environment_snapshot(run_dir, config, command)

    manifest = {
        "run_id": run_id,
        "runner_version": "runner-v3-quality-gates",
        "config_hash": config_hash(config),
        "created_at": now(),
        "status": "created",
        "profile": config.get("profile", "unspecified"),
        "experiment": config["experiment"],
        "service_mode": config["service_mode"],
        "feature": config["feature"],
        "feature_variant": config["feature_variant"],
        "dataset": config["dataset"],
        "workload": workload,
        "trial": config["trial"],
        "model_id": config["model_name"],
        "model_path": config["model_path"],
        "cuda_visible_devices": config["cuda_visible_devices"],
        "tensor_parallel_size": config["tensor_parallel_size"],
        "requested_port": requested_port,
        "allocated_port": port,
        "port_allocation": "preferred" if requested_port == port else "fallback",
        "model_revision": config.get("model_revision", "unspecified"),
        "model_hash": config.get("model_hash", "unspecified"),
        "dtype": config.get("dtype", "float16"),
        "quantization": config.get("quantization"),
        "kv_cache_dtype": config.get("kv_cache_dtype", "auto"),
        "enable_thinking": config.get("enable_thinking"),
        "num_prompts": config["num_prompts"],
        "max_concurrency": config["max_concurrency"],
        "request_rate": config["request_rate"],
        "warmup_requests": config["warmup_requests"],
        "artifacts": {
            "config_yaml": "config.yaml",
            "command_txt": "command.txt",
            "manifest_json": "manifest.json",
            "environment_txt": "environment.txt",
            "server_log": "server.log",
            "server_tail_log": "server_tail.log",
            "server_pid": "server.pid",
            "gpu_metrics_csv": "gpu_metrics.csv",
            "result_json": "result.json",
        },
    }
    write_manifest(run_dir, manifest)

    service = None
    metrics = None
    current_stage = "created"

    timeout_seconds = int(config.get("run_timeout_seconds", 0) or 0)
    def raise_task_timeout(signum, frame):
        raise TimeoutError(f"TASK_TIMEOUT: run timeout after {timeout_seconds}s")

    previous_alarm = signal.signal(signal.SIGALRM, raise_task_timeout) if timeout_seconds > 0 else None
    if timeout_seconds > 0:
        signal.alarm(timeout_seconds)

    try:
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(config["cuda_visible_devices"])

        current_stage = "starting"
        set_status(manifest, run_dir, "starting", "starting_at")

        with (run_dir / "server.log").open("w", encoding="utf-8") as server_log:
            service = subprocess.Popen(
                command,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                start_new_session=True,
            )

            (run_dir / "server.pid").write_text(str(service.pid) + "\n", encoding="utf-8")

            readiness_timeout = int(config.get("readiness_timeout_seconds", 300))
            try:
                wait_ready(
                    int(config["port"]), service, config["model_name"],
                    config.get("enable_thinking"), readiness_timeout,
                )
            except RuntimeError as exc:
                text = str(exc)
                manifest["timeout_category"] = "READINESS_TIMEOUT" if "readiness timeout" in text.lower() else None
                manifest["failure_category"] = "SERVICE_EXITED" if "service exited" in text.lower() else "READINESS_FAILED"
                raise
            current_stage = "ready"
            set_status(manifest, run_dir, "ready", "ready_at")

            current_stage = "warming"
            set_status(manifest, run_dir, "warming", "warming_started_at")

            subprocess.run(
                [
                    "bash", "scripts/warmup.sh", str(config["port"]),
                    config["model_name"], str(config["warmup_requests"]),
                    str(config.get("enable_thinking", "unspecified")).lower(),
                ],
                check=True,
            )
            current_stage = "warmed"
            set_status(manifest, run_dir, "warmed", "warmed_at")

            metrics_env = os.environ.copy()
            metrics_env.update({"RUN_ID": run_id, "GPU_IDS": str(config["cuda_visible_devices"])})
            manifest["metrics_started_at"] = utc_now()
            manifest["metrics_run_id"] = run_id
            write_manifest(run_dir, manifest)
            metrics = subprocess.Popen(
                ["bash", "scripts/collect_gpu_metrics.sh", str(run_dir / "gpu_metrics.csv"), "1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=metrics_env,
                start_new_session=True,
            )

            bench_env = os.environ.copy()
            bench_env.update({
                "PORT": str(config["port"]),
                "MODEL": config["model_name"],
                "TOKENIZER": config["model_path"],
                "RESULT_DIR": str(run_dir),
                "RESULT_FILE": "result.json",
                "INPUT_LEN": str(config["input_len"]),
                "OUTPUT_LEN": str(config["output_len"]),
                "NUM_PROMPTS": str(config["num_prompts"]),
                "MAX_CONCURRENCY": str(config["max_concurrency"]),
                "REQUEST_RATE": str(config["request_rate"]),
                "DATASET_NAME": str(config["dataset"]),
                "ENABLE_THINKING": str(config.get("enable_thinking", "unspecified")).lower(),
            })

            current_stage = "benchmarking"
            set_status(manifest, run_dir, "benchmarking", "benchmark_started_at")

            benchmark_cmd = ["bash", "scripts/run_benchmark_once.sh"]
            extra_args = [str(arg) for arg in config.get("benchmark_extra_args", [])]

            if extra_args:
                bench_env["BENCHMARK_EXTRA_ARGS"] = " ".join(extra_args)

            benchmark_command_text = (
                f'PORT={bench_env["PORT"]} '
                f'MODEL={bench_env["MODEL"]} '
                f'TOKENIZER={bench_env["TOKENIZER"]} '
                f'RESULT_DIR={bench_env["RESULT_DIR"]} '
                f'RESULT_FILE={bench_env["RESULT_FILE"]} '
                f'INPUT_LEN={bench_env["INPUT_LEN"]} '
                f'OUTPUT_LEN={bench_env["OUTPUT_LEN"]} '
                f'NUM_PROMPTS={bench_env["NUM_PROMPTS"]} '
                f'MAX_CONCURRENCY={bench_env["MAX_CONCURRENCY"]} '
                f'REQUEST_RATE={bench_env["REQUEST_RATE"]} '
                f'DATASET_NAME={bench_env["DATASET_NAME"]} '
                f'ENABLE_THINKING={bench_env["ENABLE_THINKING"]} '
                f'BENCHMARK_EXTRA_ARGS="{bench_env.get("BENCHMARK_EXTRA_ARGS", "")}" '
                + " ".join(benchmark_cmd)
            )
            (run_dir / "benchmark_command.txt").write_text(
                benchmark_command_text + "\n",
                encoding="utf-8",
            )
            manifest["artifacts"]["benchmark_command_txt"] = "benchmark_command.txt"
            write_manifest(run_dir, manifest)

            benchmark_timeout = config.get("benchmark_timeout_seconds")
            try:
                subprocess.run(
                    benchmark_cmd,
                    env=bench_env,
                    check=True,
                    timeout=float(benchmark_timeout) if benchmark_timeout else None,
                )
            except subprocess.TimeoutExpired as exc:
                manifest["timeout_category"] = "BENCHMARK_TIMEOUT"
                manifest["failure_category"] = "BENCHMARK_TIMEOUT"
                manifest["error"] = f"BENCHMARK_TIMEOUT: benchmark exceeded {benchmark_timeout}s"
                write_manifest(run_dir, manifest)
                raise RuntimeError(manifest["error"]) from exc
            except subprocess.CalledProcessError as exc:
                manifest["failure_category"] = "BENCHMARK_FAILED"
                raise RuntimeError(f"BENCHMARK_FAILED: benchmark exited with {exc.returncode}") from exc

            current_stage = "benchmark_completed"
            set_status(manifest, run_dir, "benchmark_completed", "benchmark_completed_at")

    except Exception as exc:
        if manifest.get("status") not in {"failed", "validation_failed", "cancelled"}:
            set_status(manifest, run_dir, "failed")
        manifest["failed_stage"] = current_stage
        manifest["error"] = str(exc)
        if isinstance(exc, TimeoutError) or str(exc).startswith("TASK_TIMEOUT:"):
            manifest["timeout_category"] = "TASK_TIMEOUT"
            manifest["failure_category"] = "TASK_TIMEOUT"
        elif "failure_category" not in manifest:
            manifest["failure_category"] = "RUN_FAILED"
        manifest["failed_at"] = now()
        manifest["updated_at"] = utc_now()
        write_manifest(run_dir, manifest)
        append_event(run_dir, "run_failed", status="failed", failed_stage=current_stage, error=str(exc))
        heartbeat(run_dir, stage=current_stage, status="failed")
        raise
    finally:
        if timeout_seconds > 0:
            signal.alarm(0)
            if previous_alarm is not None:
                signal.signal(signal.SIGALRM, previous_alarm)
        stop_process(metrics)
        if metrics is not None:
            manifest["metrics_finished_at"] = utc_now()
        stop_process(service)
        write_server_tail(run_dir)

        if manifest.get("status") in {"failed", "validation_failed"}:
            run_diagnosis(run_dir, manifest)
            write_manifest(run_dir, manifest)

        if manifest.get("status") == "benchmark_completed":
            current_stage = "completed"
            set_status(manifest, run_dir, "completed", "stopped_at")

            current_stage = "validating"
            set_status(manifest, run_dir, "validating", "validating_at")

            validation = subprocess.run(
                ["python", "scripts/validate_run.py", str(run_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            (run_dir / "validation.txt").write_text(validation.stdout, encoding="utf-8")

            if validation.returncode == 0:
                current_stage = "validated"
                set_status(manifest, run_dir, "validated")
                manifest["validated_at"] = now()
                manifest["updated_at"] = utc_now()
                append_event(run_dir, "validation_passed", status="validated")
                heartbeat(run_dir, stage="validated", status="validated")
            else:
                current_stage = "validation_failed"
                set_status(manifest, run_dir, "validation_failed")
                manifest["failed_stage"] = "validating"
                manifest["validation_error"] = validation.stdout[-2000:]
                manifest["artifacts"]["validation_txt"] = "validation.txt"
            if "validation_txt" not in manifest["artifacts"]:
                manifest["artifacts"]["validation_txt"] = "validation.txt"

            if manifest.get("status") == "validation_failed":
                run_diagnosis(run_dir, manifest)

            write_manifest(run_dir, manifest)

        resource_stack.close()

    print(f"Experiment completed: {run_id}")
    print(f"Run dir: {run_dir}")

if __name__ == "__main__":
    main()
