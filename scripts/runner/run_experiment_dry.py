#!/usr/bin/env python3
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

REQUIRED_FIELDS = [
    "experiment",
    "service_mode",
    "feature",
    "feature_variant",
    "model_path",
    "model_name",
    "port",
    "cuda_visible_devices",
    "tensor_parallel_size",
    "max_model_len",
    "gpu_memory_utilization",
    "dataset",
    "input_len",
    "output_len",
    "num_prompts",
    "max_concurrency",
    "request_rate",
    "warmup_requests",
    "trial",
]

def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    missing = [field for field in REQUIRED_FIELDS if field not in config]
    if missing:
        raise SystemExit(f"Missing required fields: {', '.join(missing)}")

    return config

def build_run_id(config: dict) -> str:
    workload = f'{config["input_len"]}x{config["output_len"]}'
    return (
        f'week5_{config["service_mode"]}_{workload}_'
        f'c{config["max_concurrency"]}_{config["trial"]}'
    )

def build_vllm_command(config: dict) -> list[str]:
    return [
        "vllm", "serve", config["model_path"],
        "--host", "0.0.0.0",
        "--port", str(config["port"]),
        "--served-model-name", config["model_name"],
        "--dtype", "float16",
        "--tensor-parallel-size", str(config["tensor_parallel_size"]),
        "--max-model-len", str(config["max_model_len"]),
        "--gpu-memory-utilization", str(config["gpu_memory_utilization"]),
    ]

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: scripts/run_experiment_dry.py <config.yaml>")

    config_path = Path(sys.argv[1])
    config = load_config(config_path)
    run_id = build_run_id(config)
    run_dir = Path("results/runs") / run_id
    workload = f'{config["input_len"]}x{config["output_len"]}'

    run_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(config_path, run_dir / "config.yaml")

    command = build_vllm_command(config)
    with (run_dir / "command.txt").open("w", encoding="utf-8") as f:
        f.write("CUDA_VISIBLE_DEVICES=" + str(config["cuda_visible_devices"]) + " ")
        f.write(" ".join(command) + "\n")

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "status": "planned",
        "experiment": config["experiment"],
        "service_mode": config["service_mode"],
        "feature": config["feature"],
        "feature_variant": config["feature_variant"],
        "dataset": config["dataset"],
        "workload": workload,
        "trial": config["trial"],
        "model_id": config["model_name"],
        "model_path": config["model_path"],
        "num_prompts": config["num_prompts"],
        "max_concurrency": config["max_concurrency"],
        "request_rate": config["request_rate"],
        "warmup_requests": config["warmup_requests"],
        "artifacts": {
            "config_yaml": "config.yaml",
            "command_txt": "command.txt",
            "manifest_json": "manifest.json",
        },
    }

    with (run_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Created dry run archive: {run_dir}")
    print(f"status: {manifest['status']}")
    print(f"command: {run_dir / 'command.txt'}")
    print(f"manifest: {run_dir / 'manifest.json'}")

if __name__ == "__main__":
    main()
