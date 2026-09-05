#!/usr/bin/env python3
import csv

CSV_PATH = "results/baseline_matrix.csv"

SINGLE_IDS = {
    "8": "20260801_single_gpu_2048x512_c8_trial1b",
    "16": "20260801_single_gpu_2048x512_c16_trial1b",
    "32": "20260801_single_gpu_2048x512_c32_trial1b",
}

TP2_IDS = {
    "8": "20260801_tp2_2048x512_c8_trial1",
    "16": "20260801_tp2_2048x512_c16_trial1",
    "32": "20260801_tp2_2048x512_c32_trial1",
}

def f(row, key):
    return float(row[key])

with open(CSV_PATH, newline="", encoding="utf-8") as file:
    rows = {row["run_id"]: row for row in csv.DictReader(file)}

print("| 并发 | 单卡 out tok/s | TP=2 out tok/s | 吞吐比 TP2/单卡 | 单卡 p99 TTFT | TP=2 p99 TTFT | 单卡 p99 TPOT | TP=2 p99 TPOT |")
print("| ---- | -------------- | -------------- | ---------------- | ------------- | ------------- | ------------- | ------------- |")

for c in ["8", "16", "32"]:
    single = rows[SINGLE_IDS[c]]
    tp2 = rows[TP2_IDS[c]]

    single_out = f(single, "output_throughput")
    tp2_out = f(tp2, "output_throughput")

    print(
        f"| {c} "
        f"| {single_out:.2f} "
        f"| {tp2_out:.2f} "
        f"| {tp2_out / single_out:.2f}x "
        f"| {f(single, 'p99_ttft_ms'):.2f} "
        f"| {f(tp2, 'p99_ttft_ms'):.2f} "
        f"| {f(single, 'p99_tpot_ms'):.2f} "
        f"| {f(tp2, 'p99_tpot_ms'):.2f} |"
    )
