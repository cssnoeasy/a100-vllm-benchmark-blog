# 报告索引

## 主线报告

- [部署优化结果总览](deployment-optimization-summary.md)：以并行、精度和 KV 容量策略为主线，汇总优化动作、量化结果、质量代价和部署边界；只引用已有实验。
- [双 A100 TP1 vs TP2 正式基线](a100-tp1-vs-tp2-formal-baseline.md)：主项目最强的 A 级证据。
- [Benchmark 工程化与已完成特性验证](benchmark-engineering-results.md)：runner、校验、质量门禁及有限特性对照的 B 级证据。
- [A100 量化与容量规划](a100-quantization-capacity.md)：资源收益、质量代价和容量边界。
- [第 6 周可观测性与故障验证](01_Week6阶段A_轻量可观测性故障注入长稳测试记录.md)：短时 Smoke 的边界。
- [过载与恢复](04_Week6阶段B_PrometheusGrafana过载与恢复记录.md)：指标和服务恢复链路。
- [过载与进程恢复复盘](../../incident-reviews/overload-and-process-recovery.md)：受控压力、进程退出和恢复边界。
- [巡检回放正式报告](inspection-replay-60m.md)：60 分钟离线业务回放及其严格范围。
- [巡检回放摘要](../../results/published/inspection-soak-60m-summary.md)：机器可读结果的 Markdown 摘要。

## 附录与扩展

- [GB10 上 llama.cpp 独立部署验证](gb10-llama-cpp-validation.md)：GB10 ARM64、Qwen2.5-7B-Instruct GGUF Q4_K_M 的编译、冷启动、热请求、并发与连续请求验证；不参与 A100 主线性能排名。
- [TensorRT-LLM 准入记录](tensorrt-llm-admission.md)：过程与证据归档；实际为 PyTorch backend 的有限选型验证，不构成 TensorRT engine 性能结论。
- [TensorRT-LLM 基础验证](tensorrt-llm-validation.md)：固定短负载下的单卡基础对照，作为扩展材料阅读。
- [QLoRA 工业工单扩展](qlora-industrial-work-order-extension.md)：训练和离线评测闭环，不含 adapter serving 结论。
- [第 6 周操作手册](05_Week6阶段B_操作手册.md)：环境命令已替换为公开占位符；发布前仅需随最终改动复核脱敏状态。
