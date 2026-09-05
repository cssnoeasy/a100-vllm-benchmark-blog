#!/usr/bin/env python3
import json
import sys
from pathlib import Path


RULES = [
    (
        "MODEL_PATH_ERROR",
        [
            "no such file or directory",
            "does not exist",
            "model path",
            "can't load the configuration",
            "correct path to a directory containing config.json",
        ],
        "检查 model_path 是否存在，确认模型目录和权限。",
        "ls -lh <model_path>",
    ),
    (
        "PORT_IN_USE",
        ["address already in use", "bind failed"],
        "检查端口是否被旧服务占用。",
        "lsof -i :<port> 或 ss -ltnp | grep <port>",
    ),
    (
        "GPU_OOM",
        ["out of memory", "cuda out of memory", "显存不足"],
        "检查 GPU 显存，降低 max_model_len、gpu_memory_utilization 或 max_concurrency。",
        "nvidia-smi",
    ),
    (
        "CUDA_ERROR",
        ["cuda error", "cuda out of memory", "cuda driver error", "cublas error", "nccl error"],
        "检查 CUDA、驱动、GPU 可见性和 TP 配置。",
        "nvidia-smi",
    ),
    (
        "SERVICE_EXITED",
        ["service exited before readiness", "process exited"],
        "服务在 readiness 前退出，优先查看 server_tail.log 和模型加载错误。",
        "cat server_tail.log",
    ),
    (
        "READINESS_TIMEOUT",
        ["readiness timeout", "connection refused", "timed out"],
        "服务未在规定时间内 ready，检查启动日志、模型加载时间和端口。",
        "cat server_tail.log",
    ),
    (
        "BENCHMARK_FAILED",
        ["benchmark failed", "request failed"],
        "检查 benchmark endpoint、model_name、dataset 和额外参数。",
        "cat benchmark_command.txt && cat result.json",
    ),
    (
        "VALIDATION_FAILED",
        ["run validation failed", "validation failed", "manifest status"],
        "检查 validation.txt，确认结果文件、请求数和性能指标是否完整。",
        "cat validation.txt",
    ),
]


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def load_manifest(run_dir: Path) -> dict:
    path = run_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_result(run_dir: Path) -> dict:
    path = run_dir / "result.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def classify(manifest: dict, result: dict, combined: str) -> tuple[str, str, str]:
    status = manifest.get("status", "")
    failed_stage = manifest.get("failed_stage", "")
    lower = combined.lower()

    result_failed = int(result.get("failed", 0) or 0)

    if status == "validated" and result_failed == 0:
        return (
            "NO_FAILURE_DETECTED",
            "run 已通过校验，result.json 中 failed=0，没有明确失败信息。",
            "继续查看 benchmark 指标、GPU 指标和质量门禁。",
        )

    if status == "validation_failed" or failed_stage == "validating":
        return (
            "VALIDATION_FAILED",
            "校验阶段失败。通常说明 benchmark 已完成，但 manifest 状态、结果文件或校验规则之间存在不一致。",
            "cat validation.txt",
        )

    if result_failed > 0:
        return (
            "BENCHMARK_FAILED",
            "benchmark 已返回失败请求，需要检查 endpoint、model_name、dataset 和额外参数。",
            "cat benchmark_command.txt && cat result.json",
        )

    for category, keywords, explanation, action in RULES:
        if category == "BENCHMARK_FAILED":
            continue
        if any(keyword in lower for keyword in keywords):
            return category, explanation, action

    if status in {"failed", "validation_failed"}:
        return (
            "UNKNOWN",
            "run 已失败，但现有日志没有匹配到已知错误模式。",
            "查看 server.log、server_tail.log、validation.txt 和 manifest.json。",
        )

    return (
        "NO_FAILURE_DETECTED",
        "当前文件中没有发现明确失败信息。",
        "如果这是一个成功 run，可继续查看 benchmark 指标和质量门禁。",
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: scripts/diagnose_run.py <run_dir>")

    run_dir = Path(sys.argv[1])
    if not run_dir.is_dir():
        raise SystemExit(f"Run directory not found: {run_dir}")

    manifest = load_manifest(run_dir)

    names = [
        "manifest.json",
        "server.log",
        "server_tail.log",
        "validation.txt",
        "result.json",
        "benchmark_command.txt",
    ]
    texts = {name: read_text(run_dir / name) for name in names}
    combined = "\n".join(texts.values())

    result = load_result(run_dir)
    category, explanation, action = classify(manifest, result, combined)
    failed_stage = manifest.get("failed_stage", "")
    status = manifest.get("status", "unknown")
    run_id = manifest.get("run_id", run_dir.name)

    matched_lines = []
    for line in combined.splitlines():
        lower = line.lower()
        if any(
            keyword in lower
            for _, keywords, _, _ in RULES
            for keyword in keywords
        ):
            matched_lines.append(line.strip())

    lines = [
        "# Run Diagnosis",
        "",
        f"run_id: `{run_id}`",
        f"status: `{status}`",
        f"failed_stage: `{failed_stage or 'not recorded'}`",
        "",
        "## Classification",
        "",
        f"**{category}**",
        "",
        explanation,
        "",
        "## Recommended Action",
        "",
        f"```bash\n{action}\n```",
        "",
        "## Matched Log Evidence",
        "",
    ]

    if matched_lines:
        lines.extend(["```text", *matched_lines[:40], "```"])
    else:
        lines.append("No known error keywords matched.")

    lines.extend([
        "",
        "## Files Inspected",
        "",
        *[f"- `{name}`: {'present' if texts[name] else 'missing or empty'}" for name in names],
        "",
    ])

    output = run_dir / "diagnosis.md"
    output.write_text("\n".join(lines), encoding="utf-8")

    print(f"Diagnosis: {category}")
    print(f"Run: {run_id}")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
