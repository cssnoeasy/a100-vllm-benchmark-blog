#!/usr/bin/env python3
import csv
import re
from collections import defaultdict
from statistics import mean

CSV_PATH = "results/baseline_matrix.csv"

SINGLE_PATTERN = re.compile(r"20260801_single_gpu_2048x512_c(8|16|32)_(trial1b|trial2|trial3)$")
TP2_PATTERN = re.compile(r"20260801_tp2_2048x512_c(8|16|32)_(trial1|trial2|trial3)$")

def f(row, key):
    return float(row[key])

def collect(pattern):
    groups = defaultdict(list)
    with open(CSV_PATH, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            m = pattern.match(row["run_id"])
            if not m:
                continue
            groups[int(row["max_concurrency"])].append(row)
    return groups

def avg(rows, key):
    return mean(f(row, key) for row in rows)

single = collect(SINGLE_PATTERN)
tp2 = collect(TP2_PATTERN)

print("| 并发 | 单卡输出 tok/s | TP=2 输出 tok/s | TP=2/单卡 | 单卡 p99 TTFT | TP=2 p99 TTFT | 单卡 p99 TPOT | TP=2 p99 TPOT |")
print("| ---- | -------------- | --------------- | ---------- | ------------- | ------------- | ------------- | ------------- |")

for c in [8, 16, 32]:
    s_rows = single[c]
    t_rows = tp2[c]

    s_out = avg(s_rows, "output_throughput")
    t_out = avg(t_rows, "output_throughput")

    print(
        f"| {c} "
        f"| {s_out:.2f} "
        f"| {t_out:.2f} "
        f"| {t_out / s_out:.2f}x "
        f"| {avg(s_rows, 'p99_ttft_ms'):.2f} "
        f"| {avg(t_rows, 'p99_ttft_ms'):.2f} "
        f"| {avg(s_rows, 'p99_tpot_ms'):.2f} "
        f"| {avg(t_rows, 'p99_tpot_ms'):.2f} |"
    )
