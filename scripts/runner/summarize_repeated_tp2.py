#!/usr/bin/env python3
import csv
import re
from collections import defaultdict
from statistics import mean, stdev

CSV_PATH = "results/baseline_matrix.csv"
PATTERN = re.compile(r"20260801_tp2_2048x512_c(8|16|32)_(trial1|trial2|trial3)$")

def f(value: str) -> float:
    return float(value)

def avg(values):
    return mean(values)

def sd(values):
    return stdev(values) if len(values) >= 2 else 0.0

groups = defaultdict(list)

with open(CSV_PATH, newline="", encoding="utf-8") as file:
    for row in csv.DictReader(file):
        if PATTERN.match(row["run_id"]):
            groups[int(row["max_concurrency"])].append(row)

print("| 并发 | 次数 | 输出 tok/s 均值 | 输出 tok/s 标准差 | p99 TTFT 均值 | p99 TTFT 标准差 | p99 TPOT 均值 | p99 TPOT 标准差 | GPU0 平均利用率 | GPU1 峰值利用率 |")
print("| ---- | ---- | --------------- | ----------------- | ------------- | --------------- | ------------- | --------------- | --------------- | --------------- |")

for concurrency in sorted(groups):
    rows = groups[concurrency]
    output_values = [f(r["output_throughput"]) for r in rows]
    ttft_values = [f(r["p99_ttft_ms"]) for r in rows]
    tpot_values = [f(r["p99_tpot_ms"]) for r in rows]
    gpu0_values = [f(r["gpu0_avg_utilization"]) for r in rows]
    gpu1_max_values = [f(r["gpu1_max_utilization"]) for r in rows]

    print(
        f"| {concurrency} "
        f"| {len(rows)} "
        f"| {avg(output_values):.2f} "
        f"| {sd(output_values):.2f} "
        f"| {avg(ttft_values):.2f} "
        f"| {sd(ttft_values):.2f} "
        f"| {avg(tpot_values):.2f} "
        f"| {sd(tpot_values):.2f} "
        f"| {avg(gpu0_values):.2f}% "
        f"| {max(gpu1_max_values):.2f}% |"
    )
