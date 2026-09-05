#!/usr/bin/env python3
import csv
import re
import statistics
from collections import defaultdict
from pathlib import Path

MATRIX = Path("results/baseline_matrix.csv")
PATTERN = re.compile(r"20260801_chunked_prefill_(on|off)_2048x512_c16_trial([12])$")

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
    mode, trial = m.group(1), int(m.group(2))
    groups[mode].append((trial, row))

print("| Chunked Prefill | Runs | Warm runs | Output tok/s mean | Output tok/s warm | p99 TTFT mean | p99 TTFT warm | p99 TPOT mean | p99 TPOT warm | GPU0 avg util warm |")
print("| --------------- | ---- | --------- | ----------------- | ---------------- | ------------- | ------------- | ------------- | ------------- | ------------------ |")

for mode in ["on", "off"]:
    entries = sorted(groups[mode], key=lambda item: item[0])
    all_rows = [row for _, row in entries]
    warm_rows = [row for trial, row in entries if trial == 2]

    out_all = [f(row, "output_throughput") for row in all_rows]
    out_warm = [f(row, "output_throughput") for row in warm_rows]
    ttft_all = [f(row, "p99_ttft_ms") for row in all_rows]
    ttft_warm = [f(row, "p99_ttft_ms") for row in warm_rows]
    tpot_all = [f(row, "p99_tpot_ms") for row in all_rows]
    tpot_warm = [f(row, "p99_tpot_ms") for row in warm_rows]
    gpu_warm = [f(row, "gpu0_avg_utilization") for row in warm_rows]

    print(
        f"| {mode} "
        f"| {len(all_rows)} "
        f"| {len(warm_rows)} "
        f"| {mean(out_all):.2f} +/- {stdev(out_all):.2f} "
        f"| {mean(out_warm):.2f} "
        f"| {mean(ttft_all):.2f} +/- {stdev(ttft_all):.2f} "
        f"| {mean(ttft_warm):.2f} "
        f"| {mean(tpot_all):.2f} +/- {stdev(tpot_all):.2f} "
        f"| {mean(tpot_warm):.2f} "
        f"| {mean(gpu_warm):.2f}% |"
    )
