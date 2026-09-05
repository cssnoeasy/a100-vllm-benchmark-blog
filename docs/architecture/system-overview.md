# 系统总览

## 主线

项目研究一条从模型到服务的工程链路：请求进入 OpenAI 兼容接口后，服务经历排队、Prefill、Decode、KV Cache 管理和响应输出；外围由 Benchmark、Prometheus/Grafana、GPU exporter、故障注入和业务回放工具提供测量与验证。

```text
业务回放 / Benchmark
        |
        v
OpenAI-compatible API -> 调度器 -> Prefill / Decode -> KV Cache -> Response
        |                    |             |
        +--------------------+-------------+--> vLLM metrics
                                             --> GPU/System exporter
                                             --> Prometheus -> Grafana
```

## 证据闭环

一次可引用的实验至少应有冻结配置、环境信息、运行 ID、原始结果、指标采集、汇总脚本输出和报告解释。图表是结果的可读投影，不是原始证据的替代品。

## 主项目章节映射

1. 问题与目标：解释为什么要比较部署方案。
2. 环境与方法：冻结模型、软件、硬件和负载。
3. A100 单卡基线与 TP2：回答拓扑和并行策略问题。
4. Benchmark 工程化与容量规划：已完成 runner、校验、质量门禁、共享前缀和 Chunked Prefill 的有限记录；这些结果不构成全面调优结论。量化用于说明资源与质量边界。
5. 服务治理：区分 runner 已验证能力、过载/恢复工程验证与仍待测试的网关行为。
6. 业务负载：证明离线业务回放如何进入服务链路。
7. 扩展与局限：GB10/llama.cpp 已完成独立部署、冷启动、热请求、并发阶梯和短时连续请求验证；TensorRT-LLM、QLoRA 与其他扩展均按各自证据边界说明，不做跨模型、跨硬件、跨量化格式排名。

第 4/5 周工程证据的统一入口为 [Benchmark 工程化与已完成特性验证](../reports/benchmark-engineering-results.md)。

GB10 异构硬件部署证据的统一入口为 [GB10 上 llama.cpp 独立部署验证](../reports/gb10-llama-cpp-validation.md)。

## 优化决策主线

主项目的性能工作按部署决策顺序组织，而不是把每项实验并列为功能清单：

```text
冻结基线
    -> 并行策略（TP1 / TP2）
    -> 精度与 KV 策略（BF16、FP8 KV Cache、W4A16）
    -> SLO 下容量验证
    -> 指标、故障与回放验证
```

每一步都同时记录收益和代价。TP2 的绝对吞吐收益不能省略双卡资源与轻负载尾延迟边界；W4A16 的显存和容量收益不能省略固定质量集的下降；FP8 KV Cache 的容量翻倍不能改写为权重量化收益。完整的跨文档汇总见：[部署优化结果总览](../reports/deployment-optimization-summary.md)。
