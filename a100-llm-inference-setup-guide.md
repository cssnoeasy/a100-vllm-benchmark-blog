# 从零搭建大模型推理服务：A100 服务器环境配置与 vLLM 多模型部署全指南

> 硬件：NVIDIA A100 80GB PCIe ×2 · 系统：Ubuntu 22.04 LTS · 推理框架：vLLM 0.19.0

## 一、前言

团队近期在一台双 A100 服务器上从头搭建了一套大模型推理环境，期间踩了不少坑——从 NVIDIA 驱动选型、CUDA 版本对齐，到 HuggingFace 模型下载加速、vLLM 多模型服务的配置。本文把这些步骤整理成一份可复现的操作手册，希望帮助同行少走弯路。

本文假设你有一台**刚装好 Ubuntu 22.04 LTS 的裸机**，手头有 sudo 权限，最终目标是让 vLLM 稳定地跑起多款开源大模型。

## 二、环境概览

### 2.1 硬件配置

| 组件 | 规格 |
|:---|:---|
| GPU | NVIDIA A100 80GB PCIe ×2 |
| CPU | Intel Xeon |
| 系统内存 | ≥ 256 GB |
| 操作系统 | Ubuntu 22.04 LTS (x86_64, HWE kernel) |

### 2.2 软件版本矩阵

| 组件 | 版本 | 说明 |
|:---|:---|:---|
| NVIDIA 驱动 | 580.159.03 (Server) | A100 专用，兼容 CUDA 12.x |
| CUDA Toolkit | 12.0 | 实际安装版本，与驱动解耦 |
| Miniconda | 26.1.1 | Python 虚拟环境管理 |
| Python | 3.10.20 | LLM 生态最佳兼容版本 |
| vLLM | 0.19.0 | PagedAttention 推理引擎 |

> **关于 CUDA 版本的说明**：`nvidia-smi` 右上角显示的 "CUDA Version" 是驱动支持的最高版本，而 `nvcc --version` 显示的是已安装的 CUDA Toolkit 版本——两者是不同概念，不要求完全一致，只要驱动版本 ≥ Toolkit 版本即可。

## 三、系统初始化

以下操作均在服务器终端中完成（物理机或 SSH 远程登录均可）。

### 3.1 配置国内镜像源

全新安装的 Ubuntu 默认使用海外源，下载速度往往很慢。建议先切换到清华镜像源：

```bash
# 备份原始源
sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak

# 写入清华源（Ubuntu 22.04 jammy）
sudo tee /etc/apt/sources.list <<EOF
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-security main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-backports main restricted universe multiverse
EOF

# 更新系统
sudo apt update && sudo apt upgrade -y
```

### 3.2 安装基础工具

```bash
sudo apt install -y wget curl git build-essential vim net-tools unzip
```

### 3.3 防火墙放行端口

vLLM 服务默认占用 8000 端口（若部署多个模型可使用 8001 等端口），需要提前放行：

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 8001/tcp
sudo ufw reload
```

> 如果服务器在云平台上，还需要在控制台的「安全组」中同步放行对应端口。

## 四、NVIDIA 显卡驱动安装

### 4.1 确认 GPU 型号

```bash
lspci | grep -i nvidia
# 预期输出: 2 x NVIDIA GA100 [Tesla A100]
```

### 4.2 禁用 Nouveau 开源驱动

Nouveau 是 Linux 自带的 NVIDIA 开源驱动，与官方驱动冲突，必须先禁用：

```bash
sudo tee /etc/modprobe.d/blacklist-nouveau.conf <<EOF
blacklist nouveau
options nouveau modeset=0
EOF

sudo update-initramfs -u
sudo reboot
```

重启后重新登录，继续以下步骤。

### 4.3 安装 A100 专用驱动

推荐使用 Server 版本的 580 驱动（A100 的最佳适配版本）：

```bash
sudo apt install -y nvidia-driver-580
sudo reboot
```

重启后验证：

```bash
nvidia-smi
```

预期输出：显示 2 张 NVIDIA A100 80GB PCIe，驱动版本 580.159.03。

## 五、CUDA Toolkit 安装

### 5.1 安装 CUDA 12.0

```bash
# 从交大镜像下载 CUDA 12.0 runfile
wget https://mirror.sjtu.edu.cn/nvidia/cuda/12.0.0/local_installers/cuda_12.0.0_525.60.13_linux.run
sudo sh cuda_12.0.0_525.60.13_linux.run
```

**安装注意事项**：
- 进入安装界面后，用方向键和回车操作
- 先输入 `accept` 同意协议
- **关键步骤**：用方向键移动到 `Driver` 选项，按空格取消勾选（驱动已通过 apt 安装，无需重复安装）
- 其余保持默认，选择 `Install` 等待完成

### 5.2 配置环境变量

```bash
# 编辑 ~/.bashrc
vim ~/.bashrc
```

在文件末尾添加：

```bash
# CUDA 12.0
export PATH=/usr/local/cuda-12.0/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda-12.0/lib64:$LD_LIBRARY_PATH
```

保存退出后执行 `source ~/.bashrc` 使其生效。

### 5.3 验证 CUDA

```bash
nvcc --version
# 预期: release 12.0, V12.0.140

nvidia-smi
# 右上角 CUDA Version 显示驱动支持的最高版本
```

## 六、Miniconda 与 Python 环境

### 6.1 安装 Miniconda

```bash
# 从清华镜像下载
wget https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh

# 安装
bash Miniconda3-latest-Linux-x86_64.sh
```

安装过程中的交互：
1. 输入 `yes` 同意协议
2. 安装路径直接回车（默认 `~/miniconda3`）
3. 询问是否初始化 conda → 输入 `yes`

```bash
source ~/.bashrc
conda --version
# 预期: conda 26.1.1
```

### 6.2 配置 Conda 清华源

```bash
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free/
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/
conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/pytorch/
conda config --set show_channel_urls yes
conda config --set auto_activate_base false
```

### 6.3 创建 vLLM 专用虚拟环境

```bash
conda create -n vllm_env python=3.10.20 -y
conda activate vllm_env
python --version
# 预期: Python 3.10.20
```

> **提示**：后续所有 vLLM 相关操作都需要先执行 `conda activate vllm_env`，请确认终端提示符前显示 `(vllm_env)`。

## 七、vLLM 安装

### 7.1 配置 pip 清华源

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip config set install.trusted-host pypi.tuna.tsinghua.edu.cn
```

### 7.2 安装 vLLM 及依赖

```bash
conda activate vllm_env

pip install --upgrade pip
pip install vllm==0.19.0

# 安装 FlashAttention（可选，大幅提升长序列推理速度）
pip install flash-attn --no-build-isolation

# 安装辅助工具
pip install huggingface_hub openai
```

### 7.3 验证安装

```bash
python -c "import vllm; print(vllm.__version__)"
# 预期输出: 0.19.0
```

## 八、模型下载

### 8.1 配置 HuggingFace 国内镜像

模型文件动辄几十 GB，务必配置国内镜像避免下载失败：

```bash
# 临时生效（当前会话）
export HF_ENDPOINT=https://hf-mirror.com

# 永久生效
echo 'export HF_ENDPOINT=https://hf-mirror.com' >> ~/.bashrc
source ~/.bashrc
```

### 8.2 准备模型存储目录

```bash
sudo mkdir -p /data/models
sudo chown -R $USER:$USER /data/models
```

### 8.3 下载常用开源模型

以下列出几款常用模型的下载命令。如果 `huggingface-cli` 因版本问题无法使用，可将命令中的 `huggingface-cli` 替换为 `hf`（HuggingFace Hub 的新 CLI 工具）。

**Qwen3.6-35B-A3B**（MoE 架构，总参数量 35B，每次激活约 3B）：

```bash
huggingface-cli download Qwen/Qwen3.6-35B-A3B \
    --local-dir /data/models/Qwen3.6-35B-A3B \
    --local-dir-use-symlinks False
```

**DeepSeek-R1-Distill-Qwen-32B**（DeepSeek R1 蒸馏版，32B 参数）：

```bash
huggingface-cli download deepseek-ai/DeepSeek-R1-Distill-Qwen-32B \
    --local-dir /data/models/DeepSeek-R1-Distill-Qwen-32B \
    --local-dir-use-symlinks False
```

**Qwen2.5-72B-Instruct-AWQ**（72B 模型，AWQ 4-bit 量化，显存约 40GB）：

```bash
huggingface-cli download Qwen/Qwen2.5-72B-Instruct-AWQ \
    --local-dir /data/models/Qwen2.5-72B-Instruct-AWQ \
    --local-dir-use-symlinks False
```

**GLM-4-9B-Chat-1M**（9B 参数，支持 1M token 超长上下文）：

```bash
huggingface-cli download THUDM/glm-4-9b-chat-1m \
    --local-dir /data/models/glm-4-9b-chat-1m \
    --local-dir-use-symlinks False
```

> **备选方案**：如果 HuggingFace 镜像不稳定，可以使用 ModelScope（魔搭社区）下载：
>
> ```bash
> pip install modelscope
> modelscope download --model Qwen/Qwen3.6-35B-A3B --local_dir /data/models/Qwen3.6-35B-A3B
> ```

## 九、vLLM 服务启动

vLLM 会在启动时**一次性预分配**大部分 GPU 显存（由 `--gpu-memory-utilization` 控制），后续推理在预分配池内管理 KV Cache 块。因此 `nvidia-smi` 看到的显存占用基本不变，实际使用率需要关注 vLLM 的 `/metrics` 端点。

下面列出几个常用模型的启动命令。由于服务器有两张 A100，对 35B 以上的模型统一使用 `--tensor-parallel-size 2` 做张量并行。同一时刻通常只启动一个模型服务。

### 启动 Qwen3.6-35B-A3B

```bash
vllm serve /data/models/Qwen3.6-35B-A3B \
    --served-model-name qwen3.6 \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization 0.9 \
    --tensor-parallel-size 2 \
    --trust-remote-code
```

### 启动 DeepSeek-R1-Distill-Qwen-32B

```bash
vllm serve /data/models/DeepSeek-R1-Distill-Qwen-32B \
    --served-model-name ds-r1 \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization 0.9 \
    --tensor-parallel-size 2 \
    --trust-remote-code
```

### 启动 Qwen2.5-72B-Instruct-AWQ

AWQ 量化后体积约 40GB，单卡放不下，必须使用双卡 TP2：

```bash
vllm serve /data/models/Qwen2.5-72B-Instruct-AWQ \
    --served-model-name qwen2.5-awq \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization 0.9 \
    --tensor-parallel-size 2 \
    --trust-remote-code
```

### 启动 GLM-4-9B-Chat-1M

9B 模型单卡即可，但仍用 TP2 可获得更大的可用 KV Cache 池（显存分布在两张卡上）：

```bash
vllm serve /data/models/glm-4-9b-chat-1m \
    --served-model-name glm-4 \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization 0.9 \
    --tensor-parallel-size 2 \
    --trust-remote-code
```

### 关键参数说明

| 参数 | 作用 | 调优建议 |
|:---|:---|:---|
| `--gpu-memory-utilization` | 预分配显存比例（默认 0.9） | 如遇进程意外退出且无 OOM 日志，尝试降至 0.85 |
| `--tensor-parallel-size` | 张量并行卡数 | 模型权重 + KV Cache 超过单卡容量时使用；无 NVLink 需评估通信开销 |
| `--max-model-len` | 最大上下文长度 | 未指定时从模型 config 自动读取；设太大会浪费 KV Cache 池 |
| `--trust-remote-code` | 允许执行模型仓库中的自定义代码 | HF 上部分模型必需（如 Qwen、GLM 系列） |

## 十、后台持久化运行

### 10.1 使用 tmux（临时方案）

适合调试阶段快速启停：

```bash
tmux new -s vllm          # 创建名为 vllm 的会话
conda activate vllm_env
vllm serve ...            # 启动服务

# 按 Ctrl+B 然后按 D 脱离会话，服务继续运行

tmux ls                   # 查看所有会话
tmux a -t vllm            # 重新连接
tmux kill-session -t vllm # 关闭会话
```

### 10.2 使用 systemd（生产推荐）

编写 systemd 服务文件 `/etc/systemd/system/vllm.service`：

```ini
[Unit]
Description=vLLM Inference Service
After=network.target

[Service]
Type=simple
User=<your-user>
ExecStart=/home/<your-user>/miniconda3/envs/vllm_env/bin/vllm serve \
    /data/models/your-model \
    --served-model-name your-model-name \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization 0.9 \
    --tensor-parallel-size 2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable vllm
sudo systemctl start vllm
sudo systemctl status vllm   # 查看运行状态
```

## 十一、总结

至此，一台从零开始的 Ubuntu 服务器已经具备了运行多种开源大模型的能力。回顾整个流程，有几个关键点值得注意：

1. **驱动版本要与 GPU 型号和 CUDA 版本对齐**。A100 建议使用 580 系列 Server 驱动，CUDA Toolkit 版本不要超过驱动支持的版本
2. **国内网络环境下务必配置镜像源**——apt、pip、conda、HuggingFace 四个环节都要配，否则下载模型时容易中断
3. **vLLM 的显存预分配机制容易造成"显存充裕"的假象**，真正反映压力的是 `/metrics` 端点中的 `gpu_cache_usage_perc` 指标
4. **多模型可以共用一个 vLLM 环境**，只需要切换启动命令中的模型路径和 `--served-model-name` 即可；如果需要同时运行多个模型，可以用不同端口区分

下一篇博客将介绍如何在这套推理后端之上，搭建**Nginx 反向代理 + One API 统一网关 + Open WebUI 对话界面**的企业级大模型平台，敬请期待。

---

*环境：双 A100 80GB PCIe · Ubuntu 22.04 LTS · vLLM 0.19.0 · 2026 年 7 月*
