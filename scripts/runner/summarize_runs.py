#!/usr/bin/env python3
import csv
import json
from pathlib import Path

RUNS_DIR = Path("results/runs")
OUTPUT_CSV = Path("results/baseline_matrix.csv")

MANIFEST_FIELDS = [
    "experiment",
    "service_mode",
    "feature",
    "feature_variant",
    "dataset",
    "workload",
    "trial",
]

BASE_FIELDS = [
    "run_id",
    "date",
    "model_id",
    "backend",
    "num_prompts",
    "request_rate",
    "max_concurrency",
    "duration",
    "completed",
    "failed",
    "total_input_tokens",
    "total_output_tokens",
    "request_throughput",
    "output_throughput",
    "total_token_throughput",
    "mean_ttft_ms",
    "median_ttft_ms",
    "p99_ttft_ms",
    "mean_tpot_ms",
    "median_tpot_ms",
    "p99_tpot_ms",
    "mean_itl_ms",
    "median_itl_ms",
    "p99_itl_ms",
]

GPU_FIELDS = [
    "gpu0_avg_utilization",
    "gpu0_max_utilization",
    "gpu0_avg_memory_used_mb",
    "gpu0_max_memory_used_mb",
    "gpu0_avg_power_w",
    "gpu0_max_power_w",
    "gpu1_max_utilization",
    "gpu1_max_memory_used_mb",
]

FIELDS = MANIFEST_FIELDS + BASE_FIELDS + GPU_FIELDS

def to_float(value: str) -> float:
    return float(value.strip())

def load_manifest(run_dir: Path) -> dict:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    with manifest_path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_result(run_dir: Path) -> dict:
    result_path = run_dir / "result.json"
    with result_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["run_id"] = run_dir.name
    return data

def summarize_gpu_metrics(run_dir: Path) -> dict:
    path = run_dir / "gpu_metrics.csv"
    if not path.exists():
        return {}

    by_gpu = {}

    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gpu_index = row["index"].strip()
            by_gpu.setdefault(gpu_index, {
                "utilization": [],
                "memory_used_mb": [],
                "power_w": [],
            })

            by_gpu[gpu_index]["utilization"].append(to_float(row["utilization_gpu"]))
            by_gpu[gpu_index]["memory_used_mb"].append(to_float(row["memory_used_mb"]))
            by_gpu[gpu_index]["power_w"].append(to_float(row["power_draw_w"]))

    summary = {}

    if "0" in by_gpu:
        gpu0 = by_gpu["0"]
        summary["gpu0_avg_utilization"] = sum(gpu0["utilization"]) / len(gpu0["utilization"])
        summary["gpu0_max_utilization"] = max(gpu0["utilization"])
        summary["gpu0_avg_memory_used_mb"] = sum(gpu0["memory_used_mb"]) / len(gpu0["memory_used_mb"])
        summary["gpu0_max_memory_used_mb"] = max(gpu0["memory_used_mb"])
        summary["gpu0_avg_power_w"] = sum(gpu0["power_w"]) / len(gpu0["power_w"])
        summary["gpu0_max_power_w"] = max(gpu0["power_w"])

    if "1" in by_gpu:
        gpu1 = by_gpu["1"]
        summary["gpu1_max_utilization"] = max(gpu1["utilization"])
        summary["gpu1_max_memory_used_mb"] = max(gpu1["memory_used_mb"])

    return summary

def main() -> None:
    rows = []

    for run_dir in sorted(RUNS_DIR.iterdir()):
        if not run_dir.is_dir():
            continue

        result_path = run_dir / "result.json"
        if not result_path.exists():
            continue

        manifest = load_manifest(run_dir)
        data = load_result(run_dir)
        for field in MANIFEST_FIELDS:
            data[field] = manifest.get(field, "")
        data.update(summarize_gpu_metrics(run_dir))
        rows.append({field: data.get(field, "") for field in FIELDS})

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
