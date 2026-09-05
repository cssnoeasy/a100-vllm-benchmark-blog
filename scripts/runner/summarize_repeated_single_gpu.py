#!/usr/bin/env python3
import csv
import re
from collections import defaultdict
from statistics import mean, stdev

CSV_PATH = "results/baseline_matrix.csv"
PATTERN = re.compile(r"20260801_single_gpu_2048x512_c(8|16|32)_(trial1b|trial2|trial3)$")

METRICS = [
    "output_throughput",
    "request_throughput",
    "p99_ttft_ms",
    "p99_tpot_ms",
    "gpu0_avg_utilization",
]

def f(value: str) -> float:
    return float(value)

def avg(values):
    return mean(values)

def sd(values):
    return stdev(values) if len(values) >= 2 else 0.0

groups = defaultdict(list)

with open(CSV_PATH, newline="", encoding="utf-8") as file:
    for row in csv.DictReader(file):
        m = PATTERN.match(row["run_id"])
        if not m:
            continue
        groups[int(row["max_concurrency"])].append(row)

print("| 并发 | 次数 | 输出 tok/s 均值 | 输出 tok/s 标准差 | p99 TTFT 均值 | p99 TTFT 标准差 | p99 TPOT 均值 | p99 TPOT 标准差 | GPU0 平均利用率 |")
print("| ---- | ---- | --------------- | ----------------- | ------------- | --------------- | ------------- | --------------- | --------------- |")

for concurrency in sorted(groups):
    rows = groups[concurrency]
    output_values = [f(r["output_throughput"]) for r in rows]
    ttft_values = [f(r["p99_ttft_ms"]) for r in rows]
    tpot_values = [f(r["p99_tpot_ms"]) for r in rows]
    gpu_values = [f(r["gpu0_avg_utilization"]) for r in rows]

    print(
        f"| {concurrency} "
        f"| {len(rows)} "
        f"| {avg(output_values):.2f} "
        f"| {sd(output_values):.2f} "
        f"| {avg(ttft_values):.2f} "
        f"| {sd(ttft_values):.2f} "
        f"| {avg(tpot_values):.2f} "
        f"| {sd(tpot_values):.2f} "
        f"| {avg(gpu_values):.2f}% |"
    )
