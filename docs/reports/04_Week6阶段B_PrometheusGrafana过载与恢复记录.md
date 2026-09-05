# Week6 阶段 B：Prometheus、Grafana、过载与恢复工程记录

本文记录第 6 周阶段 B 的运行架构、实验依据、工程判断和验证边界。阶段 B 把阶段 A 的离线实验观测扩展成在线时序观测，并补齐受控过载和推理进程退出/恢复验证。

## 1. 完成结论

阶段 B 已完成：

- Prometheus 在线采集 vLLM `/metrics`。
- Grafana 展示服务、调度器、KV Cache、GPU 和主机指标。
- 自定义 GPU/System exporter 以 systemd 服务运行。
- 受控高并发探针验证压力和恢复。
- 推理进程退出/恢复探针验证 `up: 1 -> 0 -> 1` 和恢复后真实请求。
- 可公开的配置、Dashboard JSON、exporter 和脱敏报告归档进仓库。

未完成项：至少 60 分钟真实业务长稳测试。按第六周当时的计划，该项延期到第七、八周后，在受控的真实业务链路上执行。

后续进展：第七周已另行完成 [60 分钟离线巡检文本推理回放](inspection-replay-60m.md)。该回放是脱敏离线文本验证，不含真实业务链路或机器人端到端组件；它补充业务形态输入的工程验证，不把本报告的短时 Smoke、过载和进程恢复证据扩展为真实业务长稳结论。

## 2. 运行架构

```text
受控浏览器
    |
    | 组织批准的本地转发或安全访问通道
    v
推理服务节点
    +-- Grafana :3000
    |      |
    |      v
    +-- Prometheus :9090
           +-- job=vllm -> 127.0.0.1:8000/metrics
           +-- job=week6_gpu_system -> 127.0.0.1:9400/metrics
           +-- job=node -> 127.0.0.1:9100/metrics
           +-- job=prometheus -> 127.0.0.1:9090/metrics

vLLM :8000 -> Qwen2.5-7B-Instruct, CUDA_VISIBLE_DEVICES=0
GPU/System exporter :9400 -> nvidia-smi + host memory/load
```

最终检查时四个 Prometheus target 均为 `up`：`prometheus`、`node`、`vllm`、`week6_gpu_system`。

## 3. 可复现资产

当前公开副本结构：

```text
observability/
├── README.md
├── prometheus/prometheus.yml
├── grafana/vllm_engineered_dashboard.json
└── exporters/
    ├── week6_gpu_system_exporter.py
    └── week6-gpu-system-exporter.service
```

下列路径属于受控 Linux GPU 实验环境的运行目录，不是仓库目录，也不能在 Windows 本地直接套用：

- `/etc/prometheus/prometheus.yml`
- `/etc/grafana/`
- `/etc/systemd/system/week6-gpu-system-exporter.service`
- `/var/lib/prometheus/`
- `/var/lib/grafana/`

仓库副本用于复现和审查，不会自动同步到 `/etc`。TSDB 和 Grafana 数据库不进入项目目录。

## 4. Grafana Dashboard

正式 JSON：

```text
observability/grafana/vllm_engineered_dashboard.json
```

面板分三组：

1. Service SLO and Throughput：目标存活、Prompt/Generation tok/s、成功请求、running/waiting、P99 TTFT、P99 TPOT。
2. vLLM Scheduler and KV Cache：KV Cache、preemption、按 engine 的 running/waiting、1 分钟 token rate。
3. GPU and System Resources：exporter 存活、GPU 利用率/显存/功耗/温度/SM 时钟、主机负载和内存。

最终查询修正：

- token rate 和 success 使用 `sum(...)` 聚合，避免重复 series。
- Running vs Waiting 面板同时保留两条 target。
- 删除不存在的 `vllm:request_failure_total` 查询。
- 用 histogram bucket 计算 P99 TTFT/TPOT。

验证流量后的结果：P99 TTFT `490 ms`，P99 TPOT `24.85 ms`；请求结束后 running 和 waiting 均恢复为 `0`。

## 5. 受控过载证据

当前公开副本入口：`scripts/runner/run_overload_probe.py`。下表数字来自原工程快照中的已完成受控测试；当前仓库未迁入其 `results/week6/` 原始结果目录。

| 场景 | 请求配置 | 完成/失败 | Output tok/s | P99 TTFT | P99 TPOT |
| ---- | ---- | ---- | ---- | ---- | ---- |
| baseline_medium | 512x128，64 prompts，C=8 | 64/0 | 699.05 | 125.37 ms | 10.87 ms |
| overload_high_concurrency | 1024x256，160 prompts，C=48 | 160/0 | 2432.81 | 763.43 ms | 22.99 ms |
| recovery_medium | 512x128，64 prompts，C=8 | 64/0 | 691.85 | 121.54 ms | 10.85 ms |

高并发观测快照：running `48`、GPU utilization `100%`、GPU power `294.1 W`、KV Cache `6.17%`。最终 `up=1`，running/waiting 回到 `0`。

解释：吞吐提高并不代表体验更好。高并发时批处理效率提高，但 P99 TTFT 和 TPOT 同时恶化。恢复场景的吞吐和延迟接近基线，说明受控压力后服务没有持续性退化。

## 6. 进程退出与恢复证据

当前公开副本入口：`scripts/runner/run_process_exit_recovery_probe.py`。以下结果来自原工程快照中的已完成受控测试；当前仓库未迁入其 `results/week6/` 原始结果目录。

- 初始进程：原推理进程
- SIGTERM 后 Prometheus 检测到 down：`True`
- 重启进程：重启后的推理进程
- Prometheus 检测恢复：`True`
- `/v1/models` 验证成功：`True`
- 恢复后 benchmark：16 completed、0 failed
- 恢复后吞吐：`318.23 tok/s`
- 恢复后 P99 TTFT：`225.56 ms`
- 恢复后 P99 TPOT：`11.41 ms`

显存变化提供了独立证据：约 `65945 MiB -> 0 -> 16015 MiB（加载中）-> 65943 MiB（恢复）`。这比只看端口更能说明模型进程确实退出并重新加载。

## 7. 已知现象与解释

- SIGTERM 后短时间 `up` 仍为 1：Prometheus 展示最近一次抓取结果，下一次 scrape 失败后才变 0。
- histogram P99 在无流量窗口返回 `NaN`：bucket 没有增长，无法计算分位数，不代表采集失败。
- `increase()` 出现小数：Prometheus 对窗口边界外推的正常结果。
- waiting 在某次采样为 0：采样周期可能错过短队列；running、延迟、GPU 与 KV Cache 仍共同证明压力存在。
- GPU 显存在空闲时仍约 65 GB：模型常驻显存，不等于 GPU 正在计算。

## 8. 清理与最终状态

删除了脚本备份、`.save`、Python/cache 目录、临时下载和中间 Dashboard 产物；重复 exporter 已删除，只保留 `observability/exporters/` 中的正式版本。项目最终约 `6.3M`，其中 `results` 约 `5.5M`，`observability` 约 `68K`。

最终静态检查通过：Python compile、Shell syntax、Dashboard JSON、Prometheus config。服务检查通过：Prometheus、Grafana、exporter active，vLLM 模型端点可用。

## 9. 历史证据与当前公开资产

以下 `results/week6/` 路径是原工程快照中的历史证据定位，不是当前公开副本中的文件。公开副本保留可审查的探针脚本、Prometheus 配置、Grafana JSON 和筛选后的截图。

```text
results/week6/overload_probe/overload_summary.md
results/week6/overload_probe/summary.json
results/week6/process_exit_recovery/process_exit_recovery_summary.md
results/week6/process_exit_recovery/summary.json
results/week6/process_exit_recovery/post_recovery_smoke/result.json
observability/grafana/vllm_engineered_dashboard.json
```

本地截图：

```text
assets/figures/week6-observability-overview.png
assets/figures/week6-overload.png
assets/figures/week6-process-recovery.png
```

## 10. 后续接续点

第六周当时的计划是：第七、八周完成业务服务后，再设计跨主机长稳，固定版本和配置，定义至少 60 分钟时长、业务请求模型、SLO、错误率阈值、资源泄漏判定、恢复检查和证据保留周期。

实际后续已完成的是独立的 [60 分钟离线巡检文本推理回放](inspection-replay-60m.md)，并非上述跨主机真实业务长稳。不要把本周短时 Smoke、压力探针或该离线回放冒充为真实业务长稳结论。
