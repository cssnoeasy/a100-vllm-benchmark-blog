#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(".")
RESULTS_DIR = ROOT / "results" / "week6"
RUNS_DIR = ROOT / "results" / "runs"

CASES = [
    {
        "name": "model_path_error",
        "config": "configs/experiments/diagnosis_bad_model_trial2.yaml",
        "expected": "MODEL_PATH_ERROR",
        "safe_to_run": True,
        "note": "模型路径不存在，验证启动阶段模型路径错误诊断。",
    },
    {
        "name": "port_in_use",
        "config": "configs/experiments/failure_port_in_use_trial1.yaml",
        "expected": "PORT_IN_USE",
        "safe_to_run": False,
        "note": "需要先临时占用 8000 端口，再运行实验。",
    },
    {
        "name": "readiness_timeout",
        "config": "configs/experiments/failure_readiness_timeout_trial1.yaml",
        "expected": "READINESS_TIMEOUT",
        "safe_to_run": True,
        "note": "readiness_timeout_seconds=1，验证 readiness 超时诊断。",
    },
]


def now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def run(command: list[str], check: bool = False) -> subprocess.CompletedProcess:
    print("$ " + " ".join(command))
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=check,
    )


def inspect_config(config: str) -> tuple[str, str]:
    result = run([sys.executable, "scripts/inspect_experiment_config.py", config])
    run_id = ""
    for line in result.stdout.splitlines():
        if line.startswith("run_id:"):
            run_id = line.split(":", 1)[1].strip()
    return run_id, result.stdout


def read_diagnosis_category(run_id: str) -> str:
    diagnosis_path = RUNS_DIR / run_id / "diagnosis.md"
    if not diagnosis_path.exists():
        return ""

    text = diagnosis_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == "## Classification":
            for next_line in lines[idx + 1 : idx + 8]:
                value = next_line.strip().strip("*").replace("\\_", "_")
                if value and not value.startswith("#"):
                    return value
    return ""


def write_summary(rows: list[dict], dry_run: bool) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "failure_injection_summary.md"

    lines = [
        "# Week6 Failure Injection Summary",
        "",
        f"generated_at: {now()}",
        f"dry_run: {str(dry_run).lower()}",
        "",
        "| Case | Status | Expected | Actual | Run ID | Note |",
        "| ---- | ---- | ---- | ---- | ---- | ---- |",
    ]

    for row in rows:
        lines.append(
            "| {case} | {status} | {expected} | {actual} | `{run_id}` | {note} |".format(
                case=row["case"],
                status=row["status"],
                expected=row["expected"],
                actual=row["actual"] or "",
                run_id=row["run_id"] or "",
                note=row["note"].replace("|", "/"),
            )
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-unsafe", action="store_true")
    parser.add_argument("--rerun-existing", action="store_true")
    args = parser.parse_args()

    rows = []

    for case in CASES:
        run_id, inspect_output = inspect_config(case["config"])
        if not run_id:
            rows.append({
                "case": case["name"],
                "status": "inspect_failed",
                "expected": case["expected"],
                "actual": "",
                "run_id": "",
                "note": inspect_output.strip()[-200:],
            })
            continue

        if args.dry_run:
            rows.append({
                "case": case["name"],
                "status": "ready",
                "expected": case["expected"],
                "actual": read_diagnosis_category(run_id),
                "run_id": run_id,
                "note": case["note"],
            })
            continue

        existing_actual = read_diagnosis_category(run_id)
        if existing_actual and not args.rerun_existing:
            status = "pass_existing" if existing_actual == case["expected"] else "mismatch_existing"
            rows.append({
                "case": case["name"],
                "status": status,
                "expected": case["expected"],
                "actual": existing_actual,
                "run_id": run_id,
                "note": "已有 diagnosis.md，默认跳过；如需重跑加 --rerun-existing。",
            })
            continue

        if not case["safe_to_run"] and not args.include_unsafe:
            rows.append({
                "case": case["name"],
                "status": "skipped_unsafe",
                "expected": case["expected"],
                "actual": read_diagnosis_category(run_id),
                "run_id": run_id,
                "note": "需要 --include-unsafe；" + case["note"],
            })
            continue

        result = run([sys.executable, "scripts/run_experiment_once.py", case["config"]])
        actual = read_diagnosis_category(run_id)
        status = "pass" if actual == case["expected"] else "mismatch"

        rows.append({
            "case": case["name"],
            "status": status,
            "expected": case["expected"],
            "actual": actual,
            "run_id": run_id,
            "note": case["note"] if result.returncode != 0 else "实验未失败，请检查配置是否仍能触发故障。",
        })

    summary = write_summary(rows, args.dry_run)
    print(f"Wrote {summary}")


if __name__ == "__main__":
    main()
