#!/usr/bin/env python3
import csv
import re
import statistics
from collections import defaultdict
from pathlib import Path

MATRIX = Path("results/baseline_matrix.csv")

CASE_ORDER = {
    "random_1024x128": 0,
    "prefix256_768x128": 1,
    "prefix512_512x128": 2,
    "prefix768_256x128": 3,
}

CASE_LABEL = {
    "random_1024x128": "random 1024+0",
    "prefix256_768x128": "prefix 256 + suffix 768",
    "prefix512_512x128": "prefix 512 + suffix 512",
    "prefix768_256x128": "prefix 768 + suffix 256",
}

PATTERN = re.compile(r"20260801_prefix_caching_(.*)_c16_trial([123])$")


def f(row, key):
    value = row.get(key, "")
    return float(value) if value not in ("", None) else 0.0


def mean(values):
    return statistics.mean(values) if values else 0.0


def stdev(values):
    return statistics.stdev(values) if len(values) >= 2 else 0.0


with MATRIX.open(newline="", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))

groups = defaultdict(list)
for row in rows:
    m = PATTERN.match(row["run_id"])
    if not m:
        continue
    case_name, trial = m.group(1), int(m.group(2))
    groups[case_name].append((trial, row))

print("| Case | Runs | Warm runs | Output tok/s mean | Output tok/s warm mean | p99 TTFT mean | p99 TTFT warm mean | p99 TPOT mean | p99 TPOT warm mean | Output tokens mean | GPU0 avg util warm |")
print("| ---- | ---- | --------- | ----------------- | ---------------------- | ------------- | ------------------ | ------------- | ------------------ | ------------------ | ------------------ |")

for case_name in sorted(groups, key=lambda name: CASE_ORDER.get(name, 99)):
    entries = sorted(groups[case_name], key=lambda item: item[0])
    all_rows = [row for _, row in entries]
    warm_rows = [row for trial, row in entries if trial in (2, 3)]

    out_all = [f(row, "output_throughput") for row in all_rows]
    out_warm = [f(row, "output_throughput") for row in warm_rows]
    ttft_all = [f(row, "p99_ttft_ms") for row in all_rows]
    ttft_warm = [f(row, "p99_ttft_ms") for row in warm_rows]
    tpot_all = [f(row, "p99_tpot_ms") for row in all_rows]
    tpot_warm = [f(row, "p99_tpot_ms") for row in warm_rows]
    tokens_all = [f(row, "total_output_tokens") for row in all_rows]
    gpu_warm = [f(row, "gpu0_avg_utilization") for row in warm_rows]

    print(
        f"| {CASE_LABEL.get(case_name, case_name)} "
        f"| {len(all_rows)} "
        f"| {len(warm_rows)} "
        f"| {mean(out_all):.2f} +/- {stdev(out_all):.2f} "
        f"| {mean(out_warm):.2f} "
        f"| {mean(ttft_all):.2f} +/- {stdev(ttft_all):.2f} "
        f"| {mean(ttft_warm):.2f} "
        f"| {mean(tpot_all):.2f} +/- {stdev(tpot_all):.2f} "
        f"| {mean(tpot_warm):.2f} "
        f"| {mean(tokens_all):.0f} "
        f"| {mean(gpu_warm):.2f}% |"
    )
