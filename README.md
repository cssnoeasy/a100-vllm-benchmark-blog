# A100 vLLM 技术博客

[![Jekyll](https://img.shields.io/badge/Jekyll-4.x-CC0000?logo=jekyll)](https://jekyllrb.com/)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-已部署-222222?logo=github)](https://cssnoeasy.github.io/a100-vllm-benchmark-blog/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

> **大模型推理部署 · 性能调优 · 平台架构实战**
>
> 在线访问：[cssnoeasy.github.io/a100-vllm-benchmark-blog](https://cssnoeasy.github.io/a100-vllm-benchmark-blog/)

---

## 📖 关于本博客

本博客记录了我在双 A100 80GB PCIe 服务器上，从裸机环境搭建到生产级大模型平台落地的完整实践过程。所有内容均基于**真实硬件、可复现的实验设计、未经修饰的原始数据**撰写而成。

截至目前，博客涵盖以下几个核心方向：

### 1. 推理服务部署

从 NVIDIA 驱动选型、CUDA 版本对齐、Miniconda 环境隔离，到 vLLM 多模型服务的完整部署流程。每一步都附带了踩坑记录与验证方法，目标是让读者可以按图索骥、一次跑通。

- [从零搭建大模型推理服务：A100 服务器环境配置与 vLLM 多模型部署全指南](./a100-llm-inference-setup-guide.md)

### 2. 推理性能压测与调优

在单卡/双卡 × FP16/FP8 KV Cache 四种配置下，对短文本低并发和长文本高并发两个典型场景进行系统性压测，采集吞吐、TTFT、Token 吞吐和显存峰值四项核心指标。核心发现包括：

- **无 NVLink 的双卡 TP2 在短文本场景下吞吐反而不如单卡**——通信开销在轻负载下可能完全吞噬并行收益
- **`--gpu-memory-utilization` 的社区经验值 0.85 并非最优**，在 Qwen2.5-7B + 双 A100 环境下实际最优值为 0.75，性能饱和点远早于经验值
- **TP vs PP 在无 NVLink 拓扑下有决定性差距**——短文本场景 TP 吞吐是 PP 的 2 倍，长文本场景更是接近 3 倍，流水线气泡是根本原因
- **FP8 KV Cache 并非万能优化**——在长文本双卡场景下吞吐反而下降 38.5%，量化/反量化开销与 TP 通信开销叠加产生了负优化

相关文章：

- [vLLM 四种推理配置性能对比（主实验）](./index.md)
- [--gpu-memory-utilization 多值对比实验](./补充实验1.md)
- [张量并行 vs 流水线并行实测对比](./补充实验2.md)

### 3. 平台架构设计

在 vLLM 推理后端之上，搭建了一套分层安全、面向团队的大模型服务平台：

- **接入层**：Nginx HTTPS 终端 + 反向代理 + IP 白名单
- **网关层**：One API（统一鉴权、模型路由、用量统计）/ LiteLLM（支持 Coding Agent 的多种 API 格式）
- **推理层**：vLLM OpenAI 兼容 API Server
- **应用层**：Open WebUI 对话界面 / Codex / Claude Code / OpenClaw

设计原则是"每一层只与相邻层通信，不跨层调用"，确保安全纵深。

相关文章：

- [Nginx + One API + Open WebUI 完整方案](./llm-platform-nginx-oneapi-webui.md)
- [内网大模型接入 Coding Agent 架构](./内网大模型接入coding-agent架构.md)

### 4. 踩坑与排查

多卡部署中的隐形问题往往没有报错、没有 traceback——服务卡住不动，GPU 利用率 100% 却没有 token 输出。本系列记录了三个真实踩坑点及标准化排查流程：

- **NCCL 初始化 hang**：无 NVLink 的 PCIe 拓扑下 P2P 通信默认开启导致死锁
- **Worker 崩溃后的 NCCL 死锁**：PyTorch 弹性训练机制与 vLLM 的默认行为冲突
- **NCCL 版本不兼容**：conda 环境中的 NCCL 与系统库冲突

同时也梳理了 KV Cache 显存分配不均问题的社区已知根因与诊断方法。

相关文章：

- [NCCL 通信踩坑实录](./补充实验3.md)
- [KV Cache 显存分配不均排查](./补充实验4.md)

---

## 🛠 技术栈

| 层级 | 组件 |
|:---|:---|
| 硬件 | NVIDIA A100 80GB PCIe ×2（无 NVLink） |
| 推理引擎 | vLLM 0.6.10 / 0.19.0 |
| 模型 | Qwen2.5-7B-Instruct · Qwen2.5-72B-Instruct-AWQ · DeepSeek-R1-Distill-Qwen-32B · GLM-4-9B |
| 网关 | One API · LiteLLM |
| Web 代理 | Nginx（HTTPS 终端、反向代理、SSL 卸载） |
| 数据库 | PostgreSQL（LiteLLM 持久化） |
| 应用界面 | Open WebUI · Codex · Claude Code · OpenClaw |
| 静态站点 | Jekyll 4.x + GitHub Pages |

---

## 🚀 本地运行

```bash
# 1. 克隆仓库
git clone https://github.com/cssnoeasy/a100-vllm-benchmark-blog.git
cd a100-vllm-benchmark-blog

# 2. 安装 Jekyll 依赖
bundle install

# 3. 启动本地预览（默认 http://localhost:4000）
bundle exec jekyll serve

# 4. 如需草稿预览
bundle exec jekyll serve --draft
```

> 确保本地已安装 Ruby 3.x 和 Bundler。Jekyll 版本锁定在 `Gemfile` 中。

---

## 📂 目录结构

```
blog-source/
├── index.md                              # 主实验：四种 vLLM 配置性能对比
├── a100-llm-inference-setup-guide.md     # A100 从零搭建推理环境
├── llm-platform-nginx-oneapi-webui.md    # Nginx + One API + Open WebUI 平台架构
├── 内网大模型接入coding-agent架构.md       # LiteLLM + PostgreSQL 接入 Coding Agent
├── 补充实验1.md                          # --gpu-memory-utilization 调优
├── 补充实验2.md                          # TP vs PP 并行策略对比
├── 补充实验3.md                          # NCCL 通信踩坑排查
├── 补充实验4.md                          # KV Cache 显存分配不均排查
├── _config.yml                           # Jekyll 站点配置
├── _layouts/                             # 页面布局模板
├── _sass/                                # SCSS 样式模块
├── assets/css/                           # 编译入口样式
├── _data/navigation.yml                  # 侧边栏导航
└── 初次实验/ 补充实验/                    # 实验原始截图与数据
```

---

## 📝 写作约定

- **实验可复现**：所有压测数据标注了硬件拓扑、软件版本、启动参数和测试条件，读者可在同等环境下复现
- **数据优先于直觉**：遇到反直觉的结果（如双卡吞吐不如单卡、FP8 KV Cache 负优化），不凭经验下结论，而是重跑确认、分析根因
- **踩坑不粉饰**：失败的尝试和排查过程比成功的经验更有价值，每个踩坑点按"现象 → 根因 → 解决 → 验证"的结构完整记录

---

## 🎯 后续学习方向

坦率地说，写完这些内容后，我越意识到自己目前触及的只是 LLM 推理工程化的冰山一角。以下几件事是我接下来迫切想深入的方向：

- **CUDA 内存层级与算子优化**：当前对 vLLM 的性能调优停留在参数层面（`--gpu-memory-utilization`、`--tensor-parallel-size`），但对 PagedAttention 内部的内存管理机制、KV Cache 的块分配与回收策略、以及 GPU 寄存器/共享内存/L2 Cache/全局内存的访存路径理解还远远不够。下一步计划系统学习 CUDA 内存模型，并结合 Nsight Systems 对推理过程做微观层面的 profiling
- **推理框架对比**：vLLM 之外，SGLang 的 RadixAttention、TensorRT-LLM 的 Graph Optimization 各自有不同的设计取舍。希望在同等硬件条件下做一套横向对比，用数据说话
- **大模型量化方案**：AWQ、GPTQ、FP8 权重量化与 FP8 KV Cache 量化在不同 batch size 和序列长度下的 trade-off 表现差异很大，目前只是浅尝辄止，需要有更系统性的认知
- **分布式推理的通信优化**：NCCL 调优、NVLink/NVSwitch 拓扑对 TP/PP 效率的影响、跨节点推理中的网络瓶颈分析，这些都还是知识盲区

这是一个快速迭代的领域，今天认为最优的配置、明天可能就有新的方案取而代之。保持实验驱动、保持数据诚实，是我对自己持续学习的基本要求。

---

## 📬 反馈与交流

如果你在实际部署中遇到了类似问题，或者对文中的实验设计有改进建议，欢迎提 [Issue](https://github.com/cssnoeasy/a100-vllm-benchmark-blog/issues) 交流讨论。

---

*搭建环境：双 A100 80GB PCIe（无 NVLink）· vLLM 0.6.10 / 0.19.0 · Ubuntu 22.04 LTS · 2026 年 7 月*
