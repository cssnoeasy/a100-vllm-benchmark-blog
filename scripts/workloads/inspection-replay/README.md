# 四足机器人巡检业务回放

本目录提供一个独立的 Python Replay 客户端，用于向 OpenAI 兼容的
`/v1/chat/completions` 接口回放脱敏后的巡检业务请求。

它模拟的是视觉识别和知识图谱检索完成之后的文本推理阶段，不连接摄像头、
Neo4j、MySQL 或真实机器人，也不会发送 UDP 控制指令。因此它属于“知识图谱增强
巡检场景的离线业务回放”，不是完整的 GraphRAG 或 VLA 端到端测试。

## 目录

```text
configs/
  smoke.yaml          6 请求冒烟
  preflight_5m.yaml   5 分钟预检
  soak_60m.yaml       60 分钟长稳
scenarios.jsonl       30 个固定脱敏场景
run_inspection_replay.py
tests/test_replay.py
```

配置文件采用 JSON 语法编写。JSON 是 YAML 1.2 的子集，因此文件既可以作为 YAML
阅读，也能由 Python 标准库直接解析，不需要额外安装 PyYAML。

## 运行要求

- Python 3.10 或更高版本。
- vLLM 或其他 OpenAI 兼容服务已经启动。
- 客户端所在主机能够访问配置中的 `base_url`。

工具只使用 Python 标准库。

## 常用命令

先做离线检查，不发送请求：

```bash
python run_inspection_replay.py --config configs/smoke.yaml --dry-run
python -m unittest discover -s tests -v
```

执行 6 请求冒烟：

```bash
python run_inspection_replay.py --config configs/smoke.yaml
```

执行 5 分钟预检：

```bash
python run_inspection_replay.py --config configs/preflight_5m.yaml
```

执行 60 分钟长稳：

```bash
python run_inspection_replay.py --config configs/soak_60m.yaml
```

可用环境变量覆盖敏感或环境相关配置：

```bash
export REPLAY_BASE_URL=http://127.0.0.1:8000/v1
export REPLAY_API_KEY="<set-in-shell-only>"
export REPLAY_MODEL=Qwen2.5-7B-Instruct
```

也可以使用参数覆盖输出目录或限制请求数：

```bash
python run_inspection_replay.py \
  --config configs/preflight_5m.yaml \
  --output-dir results/manual_preflight \
  --max-requests 10
```

## 结果文件

每次运行会创建独立目录：

```text
results/<run_id>/
  command.txt
  environment.txt
  requests.jsonl
  resolved_config.json
  summary.json
  summary.md
```

逐请求结果会记录场景、状态、HTTP 状态、TTFT、端到端延迟、Token、格式校验、
错误分类和有限长度的模型响应。API Key 不会写入结果。

`summary.json` 和 `summary.md` 还会根据配置中的 `quality_gates` 输出最终 PASS/FAIL。
当前门禁同时检查请求成功率、业务格式通过率和 P99 端到端延迟；程序退出码也以
门禁结果为准，便于后续接入自动化任务。

按一次 `Ctrl+C` 后，程序会停止安排新请求，等待已经在途的请求结束，并保存已有
结果。再次中断才会立即退出。

## 验收重点

- `hazard_decision` 必须返回合法 JSON，且动作码与风险等级一致。
- `fault_diagnosis` 必须返回含原因、排查步骤和处置建议的 JSON。
- `inspection_summary` 必须包含约定的三个 Markdown 章节。
- `invalid_or_edge` 必须识别信息冲突或缺失，给出澄清/安全停机 JSON。
- 不能只依据 HTTP 200 判断业务成功，应同时查看 `output_parse_ok`。
- 流式 TTFT 是收到第一段非空文本的时间；非流式请求不伪造 TTFT，保持为空。
- E2E 是客户端端到端时间，不能冒充 TPOT。
