---
---

# 报告索引

## 主线报告

- [A100 推理部署实验主文章](../articles/a100-inference-experiment.html)：融合旧博客探索记录和后续正式实验，按“问题、实验、优化、效果、边界”组织，适合作为公开阅读入口。
- [部署优化结果总览](deployment-optimization-summary.html)：以并行、精度和 KV 容量策略为主线，汇总优化动作、量化结果、质量代价和部署边界；只引用已有实验。
- [双 A100 TP1 vs TP2 正式基线](../../a100-vllm-tp1-vs-tp2-baseline/)：主项目最强的 A 级证据。
- [Benchmark 工程化与已完成特性验证](benchmark-engineering-results.html)：runner、校验、质量门禁及有限特性对照的 B 级证据。
- [A100 量化与容量规划](a100-quantization-capacity.html)：资源收益、质量代价和容量边界。
- [第 6 周可观测性与故障验证](01_Week6阶段A_轻量可观测性故障注入长稳测试记录.html)：短时 Smoke 的边界。
- [过载与恢复](04_Week6阶段B_PrometheusGrafana过载与恢复记录.html)：指标和服务恢复链路。
- [过载与进程恢复复盘](../../incident-reviews/overload-and-process-recovery.html)：受控压力、进程退出和恢复边界。
- [巡检回放正式报告](inspection-replay-60m.html)：60 分钟离线业务回放及其严格范围。
- [巡检回放摘要](../../results/published/inspection-soak-60m-summary.html)：机器可读结果的 Markdown 摘要。

## 平台架构、异构部署与模型适配

- [服务化架构与能力边界](../architecture/service-governance-boundaries.html)：调用方、网关、推理服务、观测和实验链路的职责拆分；直连 runner 为已验证路径，网关能力按验证状态限定。
- [GB10 上 llama.cpp 独立部署验证](gb10-llama-cpp-validation.html)：GB10 ARM64、Qwen2.5-7B-Instruct GGUF Q4_K_M 的编译、冷启动、热请求、并发与连续请求验证；不参与 A100 主线性能排名。
- [TensorRT-LLM 基础验证](tensorrt-llm-validation.html)：Qwen3-32B 在单 A100、固定短负载下的 PyTorch backend 准入与三轮基础对照；不构成 TensorRT engine 性能结论。
- [QLoRA 工业工单扩展](qlora-industrial-work-order-extension.html)：训练和离线评测闭环，不含 adapter serving 结论。

## 参数、通信与运行附录

- [gpu-memory-utilization 参数补充实验](../experimental-notes/utilization-tuning.html)：整理旧博客中已完成的显存预分配参数实验，保留 0.70 到 0.95 的观测和边界。
- [TP2 与 PP2 补充对比](../experimental-notes/tp-vs-pp-comparison.html)：整理旧博客中已完成的并行方式对比，只声明本实验条件下的吞吐观察。
- [NCCL 与 NUMA 观察记录](../experimental-notes/nccl-numa-observations.html)：整理拓扑、AllReduce、NUMA 绑定和端到端波动观察，区分事实与未证实假设。
- [NCCL 通信排查笔记](../experimental-notes/nccl-troubleshooting-notes.html)：保留可公开的排查路径，不写成生产推荐。
- [KV Cache 显存分配均衡性检查](../experimental-notes/kv-cache-balance-check.html)：记录 Qwen2.5-7B TP2 条件下未观察到明显不均。
- [第 6 周操作手册](05_Week6阶段B_操作手册.html)：环境命令已替换为公开占位符；发布前仅需随最终改动复核脱敏状态。
