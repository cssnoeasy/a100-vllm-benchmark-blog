# 大模型推理服务平台

围绕双 A100 与单 A100 的大模型推理服务基准测试、容量规划与工程实践。项目包含两条模型独立、方法一致的核心实验线：Qwen2.5-7B 的双 A100 并行策略，以及 Qwen3-32B 的单 A100 量化与容量决策。

## 项目目标

本项目围绕一个可审计的问题展开：在给定硬件、模型、软件版本和业务负载下，如何部署、测量、监控并改进大模型推理服务。主线是 `vLLM + A100 + Benchmark + 可观测性 + 故障注入 + 运行验证 + 业务负载`。

项目不把单次模型启动、截图或框架名列表当作结论。每一项重要结论都应能回到实验配置、原始结果、派生过程和局限说明。

## 当前可引用结论

- [部署优化结果总览](docs/reports/deployment-optimization-summary.md)按“优化动作 -> 基线 -> 量化结果 -> 质量或资源代价 -> 部署建议”汇总了本项目最重要的真实证据。
- [A100 推理部署实验主文章](docs/articles/a100-inference-experiment.md)把旧博客的探索过程和后续正式实验融合到一条工程主线中，适合作为对外阅读入口。
- 双 A100 PCIe、无 NVLink、跨 NUMA 的 TP1/TP2 正式基线完成 18 轮受控实验。TP2 在三类合成负载中提高绝对吞吐约 33.42% 至 44.71%；在长输入、长输出、高并发负载中，TPOT p99 和 E2E p99 分别降低 31.15% 和 28.37%。TP2 使用两张 GPU，轻负载尾延迟存在反例，不能据此宣称单位 GPU 效率更高或双卡全面更快。
- 单 A100 的 Qwen3-32B 量化矩阵同时测量资源、吞吐、SLO 容量和固定 500 题质量。self W4A16 GPTQ 将模型加载显存从 61.03 GiB 降至 18.03 GiB，GPU KV token 容量提高约 5.49 倍；在固定 SLO 阶梯中最高已验证 request-rate 从 BF16 的 0.6 req/s 提高到 1.3 req/s，但质量从 83.60% 降至 77.60%。
- Qwen3-32B 的 BF16 + FP8 KV Cache 不改变权重显存，但将 GPU KV token 容量从 39,200 提高到 78,400，固定 500 题质量与 BF16 同为 83.60%。它是 KV 容量优化，不是权重量化，也未证明所有负载显著加速。
- 旧博客中的 `gpu-memory-utilization`、TP/PP、NCCL/NUMA 和 KV Cache 均已整理为补充实验记录。这些记录用于说明排查过程和工程判断依据，结论按样本量保持限定。
- 可观测性、过载与进程恢复已有工程验证。第 6 周的短时 Smoke 不等于 60 分钟长稳；60 分钟巡检业务回放是离线文本推理回放，不是机器人端到端测试。
- TensorRT-LLM 内容定位为 PyTorch backend 的有限选型验证，不是 serialized engine 性能结论。
- GB10 上的 `llama.cpp + Qwen2.5-7B-Instruct GGUF Q4_K_M` 已完成编译、模型校验、冷启动、热请求、并发阶梯和约 20 分钟连续请求验证。它是异构硬件部署扩展，不与 A100/vLLM 主线做跨格式性能排名。
- [服务化架构材料](docs/architecture/service-governance-boundaries.md)将调用方、网关、鉴权、路由、推理引擎与监控职责分开；已验证的是直连 runner、就绪检查和实验归档，网关能力按文档中的验证状态限定。
- QLoRA 合成工业工单训练与离线评测完成三版迭代，v3 留出合成文档 all-core 为 48/48、固定硬案例为 6/8，仍有 2 个严重 OCR 硬案例未通过。它用于展示模型适配与离线评测，不构成在线 adapter serving 结论。

## 目录

| 目录 | 用途 |
| --- | --- |
| `docs/` | 架构、方法、正式报告、运行手册、概念文章和补充实验记录 |
| `configs/` | 可公开的实验模板，不含模型权重和密钥 |
| `scripts/` | Benchmark、汇总、校验、故障注入与业务回放脚本 |
| `observability/` | Prometheus、Grafana 和 GPU/System exporter 资产 |
| `results/manifests/` | 可公开的环境、配置和汇总清单 |
| `results/published/` | 适合文章引用的已审查结果摘要 |
| `assets/figures/` | 已筛选的图表和实验证据图 |
| `incident-reviews/` | 受控故障、恢复与后续防复发记录 |

## 从哪里开始

1. 阅读 [`docs/project-plan.md`](docs/project-plan.md)，了解项目主线、量化结果和已完成范围。
2. 阅读 [`docs/articles/a100-inference-experiment.md`](docs/articles/a100-inference-experiment.md)，先了解项目为什么这样做。
3. 阅读 [`docs/methodology/evidence-policy.md`](docs/methodology/evidence-policy.md)，理解“完成、验证、计划”的边界。
4. 阅读 [`docs/reports/deployment-optimization-summary.md`](docs/reports/deployment-optimization-summary.md)，了解已有实验如何支持部署选型。
5. 阅读 [`docs/reports/a100-tp1-vs-tp2-formal-baseline.md`](docs/reports/a100-tp1-vs-tp2-formal-baseline.md) 和 [`docs/reports/a100-quantization-capacity.md`](docs/reports/a100-quantization-capacity.md)。
6. 阅读 [`docs/experimental-notes/utilization-tuning.md`](docs/experimental-notes/utilization-tuning.md)、[`docs/experimental-notes/tp-vs-pp-comparison.md`](docs/experimental-notes/tp-vs-pp-comparison.md) 和 [`docs/experimental-notes/nccl-numa-observations.md`](docs/experimental-notes/nccl-numa-observations.md)，了解补充实验如何支撑工程排查。
7. 阅读 [`docs/architecture/service-governance-boundaries.md`](docs/architecture/service-governance-boundaries.md)、[`docs/reports/gb10-llama-cpp-validation.md`](docs/reports/gb10-llama-cpp-validation.md)、[`docs/reports/tensorrt-llm-validation.md`](docs/reports/tensorrt-llm-validation.md) 和 [`docs/reports/qlora-industrial-work-order-extension.md`](docs/reports/qlora-industrial-work-order-extension.md)，了解平台架构、异构部署、框架准入与模型适配的扩展记录。
8. 使用配置中的 `${MODEL_PATH}`、`${ENGINE_DIR}` 等占位符，在本地受控环境中准备模型路径；不要把权重、密钥、原始请求日志或服务器访问信息提交到仓库。

## 公开范围

本仓库是经过筛选的研究与工程材料，不是原始服务器目录的完整镜像。未迁入虚拟环境、模型权重、缓存、全量日志、原始请求数据集和内网访问配置。历史实验环境可在报告中作为事实记录出现，但公开路径、用户名、主机地址和凭据必须被替换为占位符。

## 文档阅读结构

文档按“工程问题 -> Transformer/Prefill/Decode/KV Cache 概念链 -> Serving 工程 -> 实验方法 -> 证据 -> 结论与局限”的顺序组织。正式性能基线、量化与容量实验是主线；`gpu-memory-utilization`、TP/PP、NCCL/NUMA、KV Cache 均作为已完成补充实验阅读，不替代正式基线。
