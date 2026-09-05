#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

OUT_DIR = Path("results/week6/process_exit_recovery")
PROM_URL = "http://127.0.0.1:9090/api/v1/query"
VLLM_LOG = OUT_DIR / "vllm_restarted.log"

MODEL_PATH = os.environ.get("MODEL_PATH", "")
MODEL_NAME = "Qwen2.5-7B-Instruct"

QUERIES = {
    "vllm_up": 'up{job="vllm"}',
    "running": 'vllm:num_requests_running{job="vllm"} or vector(0)',
    "waiting": 'vllm:num_requests_waiting{job="vllm"} or vector(0)',
    "gpu0_util": 'week6_gpu_utilization_percent{job="week6_gpu_system",gpu="0"} or vector(0)',
    "gpu0_memory_mib": 'week6_gpu_memory_used_mib{job="week6_gpu_system",gpu="0"} or vector(0)',
}


def now():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def run(command, **kwargs):
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        **kwargs,
    )


def query(expr):
    url = PROM_URL + "?" + urlencode({"query": expr})
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def compact(payload):
    rows = []
    for item in payload.get("data", {}).get("result", []):
        metric = item.get("metric", {})
        rows.append({
            "metric": {k: metric.get(k) for k in ["job", "instance", "engine", "gpu"] if k in metric},
            "value": item.get("value", [None, None])[1],
        })
    return rows


def snapshot(label):
    data = {"label": label, "timestamp": now(), "queries": {}}
    for name, expr in QUERIES.items():
        try:
            data["queries"][name] = compact(query(expr))
        except Exception as exc:
            data["queries"][name] = [{"error": str(exc)}]
    return data


def find_vllm_pids():
    result = run(["bash", "-lc", "ps -ef | grep 'vllm serve' | grep -v grep | awk '{print $2}'"])
    pids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def wait_up(expected, timeout=180):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        snap = snapshot(f"wait_up_{expected}")
        seen.append(snap)
        rows = snap["queries"].get("vllm_up", [])
        values = [row.get("value") for row in rows]
        if expected == "0":
            if not rows or all(value == "0" for value in values):
                return True, seen
        else:
            if any(value == "1" for value in values):
                return True, seen
        time.sleep(5)
    return False, seen


def start_vllm():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log = open(VLLM_LOG, "ab")
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "0"}
    command = [
        "vllm",
        "serve",
        MODEL_PATH,
        "--host", "0.0.0.0",
        "--port", "8000",
        "--served-model-name", MODEL_NAME,
        "--dtype", "float16",
        "--tensor-parallel-size", "1",
        "--max-model-len", "4096",
        "--gpu-memory-utilization", "0.8",
    ]
    process = subprocess.Popen(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    return process.pid


def validate_model_endpoint():
    result = run(["bash", "-lc", "curl -s http://127.0.0.1:8000/v1/models | head -c 500"])
    return result.stdout


def run_smoke_benchmark():
    case_dir = OUT_DIR / "post_recovery_smoke"
    case_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PORT": "8000",
        "MODEL": MODEL_NAME,
        "TOKENIZER": MODEL_PATH,
        "RESULT_DIR": str(case_dir),
        "RESULT_FILE": "result.json",
        "INPUT_LEN": "256",
        "OUTPUT_LEN": "64",
        "NUM_PROMPTS": "16",
        "MAX_CONCURRENCY": "4",
        "REQUEST_RATE": "inf",
        "DATASET_NAME": "random",
        "BENCHMARK_EXTRA_ARGS": "",
    }
    result = run(["bash", "scripts/run_benchmark_once.sh"], env=env)
    (case_dir / "benchmark_stdout.txt").write_text(result.stdout, encoding="utf-8")
    return result.returncode


def write_summary(summary):
    lines = [
        "# Week6 Process Exit Recovery Summary",
        "",
        f"generated_at: {summary['generated_at']}",
        "",
        "## Purpose",
        "",
        "Terminate the running vLLM service, verify Prometheus detects the outage, restart the service, and verify health and request handling recover.",
        "",
        "## Events",
        "",
        f"- initial_pids: {summary.get('initial_pids')}",
        f"- killed_pids: {summary.get('killed_pids')}",
        f"- down_detected: {summary.get('down_detected')}",
        f"- restarted_pid: {summary.get('restarted_pid')}",
        f"- up_detected: {summary.get('up_detected')}",
        f"- model_endpoint_ok: {summary.get('model_endpoint_ok')}",
        f"- post_recovery_smoke_returncode: {summary.get('post_recovery_smoke_returncode')}",
        "",
        "## Prometheus Snapshots",
        "",
    ]

    for snap in summary["snapshots"]:
        lines.append(f"### {snap['label']} - {snap['timestamp']}")
        for name, rows in snap["queries"].items():
            values = ", ".join(row.get("value", str(row)) for row in rows) or "no data"
            lines.append(f"- {name}: {values}")
        lines.append("")

    lines.extend([
        "## Interpretation",
        "",
        "- During process exit, Prometheus should mark the vLLM target down after scrape failure.",
        "- After restart and model readiness, `up{job=\"vllm\"}` should return to 1.",
        "- A successful post-recovery smoke benchmark proves the endpoint is not only listening but serving requests.",
    ])

    (OUT_DIR / "process_exit_recovery_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at": now(),
        "snapshots": [],
    }

    summary["snapshots"].append(snapshot("before_kill"))
    pids = find_vllm_pids()
    summary["initial_pids"] = pids

    if not pids:
        summary["killed_pids"] = []
        summary["down_detected"] = False
        summary["error"] = "No running vllm serve process found before test."
        write_summary(summary)
        raise SystemExit(summary["error"])

    killed = []
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
        killed.append(pid)
    summary["killed_pids"] = killed

    time.sleep(10)
    summary["snapshots"].append(snapshot("after_sigterm"))

    down_detected, down_snaps = wait_up("0", timeout=90)
    summary["down_detected"] = down_detected
    summary["snapshots"].extend(down_snaps[-3:])

    restarted_pid = start_vllm()
    summary["restarted_pid"] = restarted_pid

    up_detected, up_snaps = wait_up("1", timeout=240)
    summary["up_detected"] = up_detected
    summary["snapshots"].extend(up_snaps[-5:])

    endpoint = validate_model_endpoint()
    (OUT_DIR / "model_endpoint_after_recovery.txt").write_text(endpoint, encoding="utf-8")
    summary["model_endpoint_ok"] = MODEL_NAME in endpoint

    if summary["model_endpoint_ok"]:
        summary["post_recovery_smoke_returncode"] = run_smoke_benchmark()
    else:
        summary["post_recovery_smoke_returncode"] = None

    summary["snapshots"].append(snapshot("final"))
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_summary(summary)

    print((OUT_DIR / "process_exit_recovery_summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
