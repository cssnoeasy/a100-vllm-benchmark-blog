# Source Manifest

本文件记录主项目副本的来源范围和整理原则。原始资料仍保留在暑期任务目录的周任务文件夹与服务器工程快照中，主项目只复制经过筛选的代码、报告、配置模板、图表和公开结果。

## 迁入内容

| 来源类别 | 迁入内容 | 证据定位 |
| --- | --- | --- |
| 第 3 周 A100 受控实验 | 正式 TP1/TP2 报告、核心图表 | A：正式受控实验 |
| 第 6 周可观测性 | Prometheus/Grafana、exporter、过载与恢复报告 | B：工程验证 |
| 第 7 周巡检回放 | 脱敏场景、Replay 客户端、测试、公开摘要 | B：离线业务回放 |
| 第 4/5 周 Benchmark 工程化记录 | 配置驱动 runner、服务就绪检查、失败启动归档、单卡/TP2 运行、Prefix Caching 与 Chunked Prefill 已完成记录、质量门禁和汇总报告 | B：工程验证；特性对照按报告的样本量和边界引用 |
| 第 5 周服务化记录 | 脱敏后的服务治理架构、能力矩阵和 runner 工程证据，不迁入私有部署手册 | B/C：runner 与就绪检查已验证；网关能力保持有限验证或待验证 |
| 补充任务量化 | A100 量化容量与质量报告、图表 | A/B：实验报告，需保留限定条件 |
| 已完成实验的跨文档汇总 | 部署优化结果总览，将 TP1/TP2、量化、SLO 容量和质量结果按部署决策整理 | A/B：仅汇总现有报告，不新增实验或派生数据 |
| 补充任务 QLoRA | 脱敏后的训练和离线评测扩展报告，不迁入数据、权重或原始结果 | C：记录完整但公开复现证据未闭环 |
| TensorRT-LLM | 选型 admission/validation 报告和配置模板 | C：有限验证 |
| 补充任务 GB10/llama.cpp | GB10 ARM64 上 Qwen2.5-7B-Instruct GGUF Q4_K_M 的中文验证报告和四张截图 | B/C：已完成部署、冷启动、热请求、并发阶梯与短时连续请求；不与 A100 主线作跨格式排名 |
| 服务器工程快照 | runner、校验器、监控资产和数据 schema | B/C：工程结构，未在此副本重跑 |

第 4/5 周的结果来源包括 `prefix_caching_repeated_c16.md`、`chunked_prefill_on_off_c16.md`、`week5_validated_summary.md`、`week5_quality_gate.md` 和 `week5_runner_smoke_summary.md`。这些原始记录仍保留在服务器工程快照，公开副本只整理脱敏后的结论、脚本和可公开配置。

GB10/llama.cpp 的原始已完成记录位于 `补充任务/DGX spark实验/llama.cpp/`，原始截图位于 `补充任务/DGX spark实验/assets/`。主项目只复制 `06` 至 `09` 四张公开证据图，并以脱敏后的中文报告整理结果。

`docs/reports/deployment-optimization-summary.md` 的所有数字均来自 `a100-tp1-vs-tp2-formal-baseline.md` 与 `a100-quantization-capacity.md` 等已迁入报告；该文件用于降低跨报告阅读成本，不构成新的测试或新的性能声明。

## 明确排除

虚拟环境、模型权重、adapter 权重、原始请求日志、全量数据集、缓存、机器凭据、SSH 配置、内网主机信息和大体积压缩包不进入公开项目。

## 使用约束

迁入脚本和配置保留历史实验结构，但脚本未必能在任意机器直接运行。运行前必须填写模型路径、服务地址、依赖版本和 GPU 拓扑，并生成新的 manifest。历史结果只能引用原报告中声明的环境，不能被误写成当前机器的新结果。
