# 巡检场景 60 分钟离线文本推理回放

**状态：** 已完成的离线业务回放  
**证据等级：** B  
**日期：** 2026-08-03  
**模型：** Qwen2.5-7B-Instruct  
**服务路径：** 直连 OpenAI 兼容 vLLM 接口

## 1. 问题与边界

本实验验证固定的脱敏巡检场景能否在文本推理阶段持续回放约 60 分钟，并满足预先定义的质量门禁。回放从视觉识别和知识图谱检索之后开始，不连接摄像头、Neo4j、MySQL、真实机器人控制器或 UDP 控制。

因此，结果是巡检场景的离线文本推理回放，不是端到端 GraphRAG、VLA、机器人或生产可用性结论。

## 2. 负载与方法

回放客户端使用 30 条固定的脱敏场景，同时检查 HTTP 请求完成和业务输出解析；HTTP 成功不等于业务结果有效。场景分为故障诊断、危险判断、巡检总结、无效或边界四类。

测试启用流式输出，按 steady、mixed、burst、recovery 四个阶段依次回放，以观察稳定请求、混合场景、短时请求增量和恢复阶段的服务表现。阶段速率和并发由 [soak 配置](../../configs/experiments/inspection-soak-60m.yaml) 固定：steady 为 600 秒、0.25 req/s、并发 1；mixed 为 2400 秒、0.5 req/s、并发 2；burst 为 300 秒、1.0 req/s、并发 4；recovery 为 300 秒、0.25 req/s、并发 1。运行总时长为 `3601.994 s`。

每次运行归档客户端汇总、结构化 manifest、服务日志和指标快照；客户端同时记录 HTTP 完成状态与业务解析结果。质量门禁为：成功率不低于 `0.99`、业务解析率不低于 `0.99`、p99 E2E 不高于 `30000 ms`。运行期间曾由人工以 Ctrl+C 结束回放，汇总时将其作为正常结束后的状态记录核对，而不把它计为业务请求失败。

## 3. 结果

| 指标 | 结果 |
| --- | ---: |
| 请求数 | 1,725 |
| 成功请求数 | 1,725 |
| 失败或业务无效请求数 | 0 |
| 成功率 | 100.00% |
| 业务解析率 | 100.00% |
| 质量门禁 | PASS |
| E2E p50 / p95 / p99 | 412.768 / 5684.295 / 5840.389 ms |
| TTFT p50 / p95 / p99 | 81.492 / 115.697 / 169.788 ms |

四类场景分别为故障诊断 432 请求、危险判断 945 请求、巡检总结 262 请求、无效或边界 86 请求，均成功完成。

## 4. 解释与局限

该回放验证了固定场景分布下的文本服务路径和解析器可以持续运行约一小时。p99 E2E 不能与 TPOT 互换；流式 TTFT 衡量的是客户端收到第一个非空输出的时间，不只是服务端 Decode 速度。

本结果不证明新场景分布、上游视觉或检索故障、真实机器人动作、用户流量突发、请求取消或经网关调用时的表现。

## 5. 复现与溯源

- [Replay client and scenario contract](../../scripts/workloads/inspection-replay/README.md)
- [Published result summary](../../results/published/inspection-soak-60m-summary.md)
- [Structured summary manifest](../../results/manifests/inspection-soak-60m-summary.json)
- [Soak configuration](../../configs/experiments/inspection-soak-60m.yaml)

服务地址、API key 和模型标识必须通过环境变量传入。不得提交原始请求日志、端点凭据或任何可访问私有服务的信息。
