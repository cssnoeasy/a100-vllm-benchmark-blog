# Week6 阶段 A：轻量可观测性、故障注入与稳定性 Smoke 记录

本记录用于保存第 6 周阶段 A 的工程状态、关键脚本、产物位置和后续验证边界。

## 阶段定位

第六周阶段 A 承接第五周的 vLLM 自动化 runner。第五周已经完成实验执行、profile/default 配置、质量门禁、stale run 清理和 Week5 报告。第六周 A 阶段先做离线轻量闭环：让已有 benchmark 结果可观测、失败可解释、短时重复性可复查，再把实时监控留给阶段 B。

本文中的“稳定性 Smoke”特指两轮相同配置的短时重复性验证，不等同于原计划中至少 60 分钟的业务长稳测试。后者已明确延期到第七、八周完成后，结合受控的真实业务链路执行。

当前阶段已经完成三个模块：

- 轻量静态 dashboard：单文件 HTML，离线可打开。
- 故障注入与诊断闭环：失败样本、诊断规则、summary 汇总。
- 稳定性 smoke 最小实现：两轮 smoke benchmark，计算短时重复性波动。

统一刷新入口已经完成：

```bash
cd <PROJECT_ROOT>
python scripts/refresh_observability.py
```

## 服务器环境

- 服务器：受控 GPU 实验机
- 项目目录：实验机上的项目根目录
- Conda 环境：`vllm_env`
- 模型：实验机上的 Qwen2.5-7B-Instruct 本地目录
- 主要端口：`8000`
- GPU：A100 80GB，当前实验主要使用 `CUDA_VISIBLE_DEVICES=0`

## 新增/关键脚本

### `scripts/build_dashboard.py`

读取 `results/week5/week5_matrix.csv`、`results/week5/week5_quality_gate.md`、各 run 的 `manifest.json`、`diagnosis.md`、`gpu_metrics.csv`，生成单文件 dashboard：

```text
results/dashboard/index.html
```

特性：

- 无 CDN、无外部 JS/CSS 依赖。
- 数据以内嵌 JSON 方式写入 HTML。
- 已修复 JSON 被 HTML escape 成 `&quot;` 导致浏览器无法 `JSON.parse` 的问题。
- 展示 Runs、Validated、Failed、Quality WARN 统计。
- 提供 Status/Profile/Mode/Feature/Workload/Search 筛选。
- 性能图只展示 validated run 的 Output Throughput。
- 故障中心展示 failed/validation_failed/stale 等非成功样本。
- 完整结果表展示吞吐、p99 TTFT、p99 TPOT、GPU samples/max util。

### `scripts/diagnose_run.py`

读取单个 run 目录下的：

- `manifest.json`
- `server.log`
- `server_tail.log`
- `validation.txt`
- `result.json`
- `benchmark_command.txt`

输出：

```text
results/runs/<run_id>/diagnosis.md
```

当前分类包含：

- `MODEL_PATH_ERROR`
- `PORT_IN_USE`
- `GPU_OOM`
- `CUDA_ERROR`
- `SERVICE_EXITED`
- `READINESS_TIMEOUT`
- `BENCHMARK_FAILED`
- `VALIDATION_FAILED`
- `NO_FAILURE_DETECTED`
- `UNKNOWN`

### `scripts/run_failure_injection_suite.py`

故障注入 suite，当前覆盖三个稳定样本：

- `MODEL_PATH_ERROR`
- `PORT_IN_USE`
- `READINESS_TIMEOUT`

输出：

```text
results/week6/failure_injection_summary.md
```

脚本默认不会重跑已有 diagnosis 的样本，避免重复制造失败 run。需要重跑时使用：

```bash
python scripts/run_failure_injection_suite.py --rerun-existing
```

端口占用类样本默认是 unsafe，需要显式参数和手动端口占用配合，不建议自动化直接重跑。

### `scripts/run_soak_suite.py`

稳定性 smoke suite，当前使用：

- `configs/experiments/soak_single_gpu_smoke_trial2.yaml`
- `configs/experiments/soak_single_gpu_smoke_trial3.yaml`

输出：

```text
results/week6/soak_summary.md
```

当前两轮结果：

- output throughput avg：约 `329.21 tok/s`
- output throughput spread：`0.29%`
- p99 TTFT spread：`6.21%`
- p99 TPOT spread：`0.15%`

注意：`trial_soak1` 曾因残留 vLLM 服务影响处于 `starting`，已用 `mark_week5_stale_runs.py` 标为 `stale`，保留为中间态样本。

### `scripts/refresh_observability.py`

统一刷新入口，当前串起：

1. `scripts/make_week5_matrix.py`
2. `scripts/list_week5_status.py`
3. `scripts/summarize_week5_validated_runs.py`
4. `scripts/check_week5_quality_gate.py`
5. `scripts/run_failure_injection_suite.py`
6. `scripts/run_soak_suite.py`
7. 对 failed/validation_failed run 补 `diagnosis.md`
8. `scripts/build_dashboard.py`
9. 输出 `results/week6/observability_refresh.md`

## 新增配置

故障注入配置：

- `configs/experiments/failure_port_in_use_trial1.yaml`
- `configs/experiments/failure_readiness_timeout_trial1.yaml`
- 复用已有 `configs/experiments/diagnosis_bad_model_trial2.yaml`

长稳 smoke 配置：

- `configs/experiments/soak_single_gpu_smoke_trial1.yaml`：已 stale，不作为稳定性统计样本。
- `configs/experiments/soak_single_gpu_smoke_trial2.yaml`：validated。
- `configs/experiments/soak_single_gpu_smoke_trial3.yaml`：validated。

## 当前关键产物

- `results/dashboard/index.html`
- `results/week6/observability_refresh.md`
- `results/week6/failure_injection_summary.md`
- `results/week6/soak_summary.md`
- `results/week5/week5_matrix.csv`
- `results/week5/week5_quality_gate.md`
- `results/week5/week5_status_snapshot.md`
- `results/week5/week5_validated_summary.md`

Windows 本地曾保存 dashboard（原始归档文件名，路径已省略）：

```text
Week6_静态可观测性Dashboard_Week5数据.html.html
```

建议后续重命名为：

```text
Week6_静态可观测性Dashboard_Week5数据.html
```

## 最近一次健康状态

最近一次 `refresh_observability.py` 后：

```text
runs: 22
status_counts: {'failed': 4, 'stale': 1, 'validated': 16, 'validation_failed': 1}
quality_counts: {'NONE': 6, 'PASS': 11, 'SKIP': 3, 'WARN': 2}
contains_html_escaped_quotes: False
external_dependency_matches: 0
```

## 已验证故障样本

| 故障 | 预期分类 | 当前状态 | run |
| ---- | ---- | ---- | ---- |
| 模型路径错误 | `MODEL_PATH_ERROR` | pass_existing | `week5_single_gpu_baseline_diagnosis_bad_model_32x8_c1_trial2` |
| 端口占用 | `PORT_IN_USE` | pass_existing | `week5_single_gpu_baseline_port_in_use_32x8_c1_trial1` |
| readiness 超时 | `READINESS_TIMEOUT` | pass_existing | `week5_single_gpu_baseline_readiness_timeout_32x8_c1_trial1` |

## 阶段 B 衔接决策

阶段 A 完成后，没有把 Grafana 内容混入离线实验报告，而是先明确以下边界，再在阶段 B 单独实施：

- 明确数据源：vLLM `/metrics`、Prometheus scrape、还是历史 benchmark 导入。
- 明确部署边界：只在推理服务节点做观测，不触碰上游网关、数据库或其他业务组件。
- 明确 dashboard 面板：QPS、吞吐、TTFT/TPOT、GPU util/memory、失败分类、质量门禁。
- 明确安全策略：端口、防火墙、仅本机访问，以及组织批准的远程访问通道。

这些规划已经在阶段 B 落地：vLLM 原生指标由 Prometheus 抓取，GPU/System 指标由独立 exporter 提供，Grafana 通过受控访问通道访问；历史 benchmark 仍保留静态 Dashboard，不强行并入在线时序监控。

## 最终验收口径

阶段 A 的最终状态是“离线观测、诊断规则和短时重复性验证完成”，而不是“完整长稳测试完成”。

| 验收项 | 状态 | 证据 |
| ---- | ---- | ---- |
| 静态 Dashboard 可离线打开 | 完成 | `results/dashboard/index.html` |
| HTML 不依赖 CDN，内嵌 JSON 可解析 | 完成 | `external_dependency_matches: 0`、`contains_html_escaped_quotes: False` |
| 失败 run 自动生成诊断 | 完成 | 各 run 的 `diagnosis.md` |
| 三类故障注入匹配预期 | 完成 | `results/week6/failure_injection_summary.md` |
| 两轮相同配置短时重复性验证 | 完成 | `results/week6/soak_summary.md` |
| 至少 60 分钟真实业务长稳 | 延期 | 第七、八周后结合双机业务链路执行 |

## 本地归档

第六周最终服务器工程快照位于原始暑期任务目录；公开副本只保留经过筛选的工程文件：

```text
服务器工程快照/<快照目录>
```

静态 Dashboard、Grafana JSON 和截图分别归档在本目录的 `dashboards/` 与 `assets/`。服务器仍是运行环境，本地快照是 2026-08-02 的阶段性可追溯副本。
