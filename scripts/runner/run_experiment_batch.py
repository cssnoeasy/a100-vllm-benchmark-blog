#!/usr/bin/env python3
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def run_inspect(config_path: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["python", "scripts/inspect_experiment_config.py", str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return result.returncode == 0, result.stdout.strip()


def run_one(config_path: Path) -> tuple[str, str]:
    started_at = now()
    result = subprocess.run(
        ["python", "scripts/run_experiment_once.py", str(config_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    status = "completed" if result.returncode == 0 else "failed"
    return status, (
        f"started_at: {started_at}\n"
        f"returncode: {result.returncode}\n"
        f"output:\n{result.stdout.strip()}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run multiple vLLM experiment configs sequentially."
    )
    parser.add_argument("configs", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/week6/batch_summary.md"),
    )
    args = parser.parse_args()

    configs = args.configs
    if not configs:
        raise SystemExit("No config files supplied")

    args.output.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    details = []

    for config_path in configs:
        if not config_path.exists():
            status = "missing"
            output = f"Config not found: {config_path}"
        elif args.dry_run:
            ok, output = run_inspect(config_path)
            status = "ready" if ok else "invalid"
        else:
            status, output = run_one(config_path)

        rows.append((status, str(config_path)))
        details.append((status, str(config_path), output))

        print(f"[{status}] {config_path}")

        if status in {"failed", "invalid", "missing"} and args.stop_on_fail:
            print("Stopping because --stop-on-fail was requested")
            break

    lines = [
        "# Batch Experiment Summary",
        "",
        f"generated_at: {now()}",
        f"dry_run: {str(args.dry_run).lower()}",
        "",
        "| Status | Config |",
        "| ---- | ---- |",
    ]

    for status, config_path in rows:
        lines.append(f"| {status} | `{config_path}` |")

    lines.extend(["", "## Details", ""])

    for status, config_path, output in details:
        lines.extend([
            f"### {status}: `{config_path}`",
            "",
            "```text",
            output,
            "```",
            "",
        ])

    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")

    if any(status in {"failed", "invalid", "missing"} for status, _ in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
