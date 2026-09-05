---
---

# NCCL 与 NUMA 观察记录

## 这组记录回答的问题

这组记录来自旧博客和补充实验材料，主要用于说明我排查过双 A100 PCIe 机器上的 GPU 互联、NCCL 通信和 NUMA 亲和性问题。

它不是正式性能基线，也不把某个系统参数写成最终根因。它保留的是排查过程中的事实：硬件拓扑、NCCL AllReduce 观测、NUMA 绑定尝试，以及端到端吞吐出现过不稳定状态。

## 已确认事实

| 项目 | 观察 |
| --- | --- |
| GPU 拓扑 | 双 A100 PCIe，无 NVLink，GPU 间链路显示为 `SYS` |
| AllReduce 默认 P2P | 平均 bus bandwidth 约 11.4508 GB/s |
| 关闭 P2P | 平均 bus bandwidth 约 11.4480 GB/s |
| 显式 NUMA 绑定 | 平均 bus bandwidth 约 11.4242 GB/s |
| interleave | 平均 bus bandwidth 约 11.3313 GB/s |
| TP2 端到端状态 | 曾观察到约 2600 tok/s 与约 1500 tok/s 两种状态，原因未证实 |

NCCL micro benchmark 中，默认 P2P、关闭 P2P、显式 NUMA、interleave 的平均 bus bandwidth 差距很小。这个结果说明，在当时测试条件下，仅靠这些 NCCL/NUMA 开关没有带来明显通信带宽改善。

## 我如何解释这件事

PCIe A100 没有 NVLink 时，多卡张量并行需要面对跨 GPU 通信开销。`SYS` 拓扑意味着两张 GPU 的通信链路不是高速直连，这会影响 TP2 在轻负载或同步频繁场景下的收益。

不过，NCCL micro benchmark 与 vLLM 端到端推理不是同一个层面的测试。前者更接近通信原语测量，后者还包含模型计算、prefill/decode 调度、batching、KV Cache 管理、CPU 侧调度等因素。因此，当端到端吞吐出现 2600 tok/s 和 1500 tok/s 两种状态时，不能只凭 NCCL/NUMA 记录把原因归到某一个系统设置上。

## 不能从这里推出什么

- 不能把 IOMMU、VT-d、P2P 或 NUMA 写成已证实根因。
- 不能说某个 NCCL 环境变量已经形成生产级自愈能力。
- 不能把单次或少量 AllReduce 结果当成完整通信性能结论。
- 不能把 NCCL micro benchmark 的带宽差异直接换算成 vLLM 请求吞吐提升。

## 工程实践要点

这部分可以说明我在做多卡推理时会看硬件拓扑和通信路径，不会只看 GPU 型号和显存。面对 TP2 性能波动，我会把问题拆成硬件互联、通信原语、运行时调度、负载形态和服务指标几个层面分别排查，并把“已确认事实”和“待验证假设”分开写。
