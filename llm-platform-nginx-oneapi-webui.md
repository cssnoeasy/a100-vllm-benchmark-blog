---
title: "大模型平台架构实战：Nginx + One API + Open WebUI 完整方案"
date: 2026-07-06
description: "在 vLLM 推理后端之上，用 Nginx 反向代理、One API 网关和 Open WebUI 搭建一套安全、可管理、面向团队的大模型服务平台"
categories: ["架构", "教程"]
---

# 大模型平台架构实战：Nginx 反向代理 + One API 网关 + Open WebUI 完整方案

> 在 vLLM 推理后端之上，用 Nginx、One API 和 Open WebUI 搭建一套安全、可管理、面向团队的大模型服务平台

## 一、背景

[上一篇文章](/a100-llm-inference-setup-guide/) 介绍了如何在 A100 服务器上从零部署 vLLM 推理服务。但裸奔的 vLLM 服务离"生产可用"还有一段距离——没有认证鉴权、没有流量管理、没有用户界面，普通业务方和产品同学根本用不起来。

本文记录了我们团队在 vLLM 推理后端之上，搭建一整套**安全可控的大模型平台**的完整方案，核心架构包含四层：

```
用户浏览器 ──→ Nginx (HTTPS) ──→ Open WebUI ──→ One API ──→ vLLM ──→ 模型
业务客户端 ──→ Nginx (HTTPS) ──→ One API ──→ vLLM ──→ 模型
```

本文适合需要在**内网环境**中建设大模型服务平台的工程团队阅读。由于原始环境涉及内网敏感信息，文中所有 IP 地址、密码、证书信息均已脱敏替换为占位符。

## 二、架构设计

### 2.1 四层架构解耦

| 层级 | 组件 | 职责 |
|:---|:---|:---|
| **接入层** | Nginx | HTTPS 终端、反向代理、IP 白名单、SSL 卸载 |
| **网关层** | One API | 统一 API Key 管理、模型路由、用量统计、速率限制 |
| **推理层** | vLLM | OpenAI 兼容 API Server、多模型加载、GPU 推理 |
| **应用层** | Open WebUI | Web 对话界面、会话管理、RAG 嵌入 |

### 2.2 分层安全策略

- **推理层（vLLM）**绑定 `127.0.0.1`，仅允许本机 One API 调用，不直接对外暴露
- **网关层（One API）**仅允许 Nginx 所在服务器访问，通过 API Key 做用户级鉴权
- **接入层（Nginx）**作为唯一公网入口，负责 HTTPS 加密和 IP 白名单
- **应用层（Open WebUI）**同样通过 Nginx 代理，用户不直接访问后端端口

> **设计原则**：每一层只与相邻层通信，不跨层调用。如果某一层被攻破，攻击者无法直接触及模型层。

## 三、服务器规划

在本次部署中，使用了两台服务器做职责分离：

| 角色 | 操作系统 | 部署组件 |
|:---|:---|:---|
| **反向代理服务器** | CentOS 7 | Nginx、SSL 证书、防火墙 |
| **GPU 推理服务器** | Ubuntu 22.04 | vLLM、One API、Open WebUI |

> **为什么用两台机器？** GPU 服务器计算资源宝贵，把 SSL 卸载和反向代理的 CPU 开销放到一台轻量级机器上，避免抢占 GPU 服务器的 CPU 资源；同时将安全边界收敛在代理服务器上，方便统一管理访问控制。

### 端口规划

| 端口 | 绑定地址 | 用途 | 对外暴露 |
|:---|:---|:---|:---|
| 80/443 | 代理服务器 | HTTP→HTTPS 重定向 / HTTPS 入口 | ✅ 是 |
| 3000 | GPU 服务器 (内网) | One API 管理后台 + API 端点 | ❌ 仅代理服务器可访问 |
| 8000 | GPU 服务器 (127.0.0.1) | vLLM OpenAI API | ❌ 仅本机 One API 可访问 |
| 8088 | GPU 服务器 (内网) | Open WebUI | ❌ 仅代理服务器可访问 |

## 四、Nginx 反向代理配置

在反向代理服务器上完成以下操作。

### 4.1 安装 Nginx

```bash
# CentOS 7 环境
sudo yum install nginx -y
```

### 4.2 生成自签名 SSL 证书

内网环境通常无法使用 Let's Encrypt 等公网 CA，使用自签名证书即可（生产环境建议部署内部 CA 签发的正式证书）：

```bash
sudo mkdir -p /etc/nginx/ssl
cd /etc/nginx/ssl

sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout server.key \
  -out server.crt \
  -subj "/C=CN/ST=<Province>/L=<City>/O=<Organization>/OU=<Unit>/CN=<nginx-server-ip>"
```

> 证书中的组织信息已脱敏。`-days 3650` 表示证书有效期 10 年，可根据安全策略调整。

### 4.3 编写反向代理配置

将以下内容保存为 `/etc/nginx/conf.d/llm-platform.conf`：

```nginx
# ─── 上游服务定义 ───
upstream oneapi_backend {
    server <gpu-server-ip>:3000;
}

upstream webui_backend {
    server <gpu-server-ip>:8088;
}

# ─── HTTP → HTTPS 重定向 ───
server {
    listen 80;
    server_name <nginx-server-ip>;
    return 301 https://$server_name$request_uri;
}

# ─── One API HTTPS 代理 ───
server {
    listen 443 ssl;
    server_name <nginx-server-ip>;

    ssl_certificate     /etc/nginx/ssl/server.crt;
    ssl_certificate_key /etc/nginx/ssl/server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # 管理后台仅允许内网访问
    location /admin {
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;

        proxy_pass http://oneapi_backend/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    # API 端点对外开放（由 One API 的 Key 机制做鉴权）
    location / {
        proxy_pass http://oneapi_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}

# ─── Open WebUI HTTPS 代理 ───
server {
    listen 8443 ssl;
    server_name <nginx-server-ip>;

    ssl_certificate     /etc/nginx/ssl/server.crt;
    ssl_certificate_key /etc/nginx/ssl/server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://webui_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 600s;   # WebUI 可能涉及长时生成，timeout 设大一些
        proxy_send_timeout 600s;
    }
}
```

### 4.4 配置说明

| 配置项 | 说明 |
|:---|:---|
| 443 端口 | One API 的 HTTPS 入口，业务方调用 `/v1/chat/completions` 等接口 |
| 8443 端口 | Open WebUI 的 HTTPS 入口，面向内部用户做 AI 对话 |
| `/admin` 路径 | One API 管理后台，仅允许内网网段访问 |
| `proxy_read_timeout` | WebUI 设为 600s，避免长文本生成时 Nginx 超时断开 |

### 4.5 防火墙放行并启动

```bash
# 放行端口
sudo firewall-cmd --add-port=80/tcp --permanent
sudo firewall-cmd --add-port=443/tcp --permanent
sudo firewall-cmd --add-port=8443/tcp --permanent
sudo firewall-cmd --reload

# 如有 SELinux，需要为自定义端口添加上下文
sudo setenforce 0   # 临时关闭排查
# sudo semanage port -a -t http_port_t -p tcp 8443  # 如需开启 SELinux 则执行
# sudo setenforce 1

# 验证配置并启动
sudo nginx -t
sudo systemctl start nginx
sudo systemctl enable nginx

# 验证
curl -k -I https://127.0.0.1:443
curl -k -I https://127.0.0.1:8443
```

## 五、vLLM 推理服务配置

在 GPU 推理服务器上操作。确保已完成 [上一篇文章](/a100-llm-inference-setup-guide/) 中的环境安装。

### 5.1 启动 vLLM

```bash
conda activate vllm_env

python -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 \
  --port 8000 \
  --model /data/models/Qwen3.6-35B-A3B \
  --served-model-name qwen3.6 \
  --trust-remote-code \
  --max-model-len 32768
```

> **安全提醒**：此处 `--host 0.0.0.0` 仅在后续 iptables 规则限制下使用。生产环境可改为 `--host 127.0.0.1`，仅允许本机 One API 调用。

### 5.2 验证 vLLM 正常运行

```bash
curl http://127.0.0.1:8000/v1/models

curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6",
    "messages": [{"role": "user", "content": "你好"}],
    "max_tokens": 64
  }'
```

### 5.3 后台运行

```bash
# 调试阶段使用 tmux
tmux new -s vllm
conda activate vllm_env
# 启动 vLLM...
# Ctrl+B D 脱离

# 生产环境建议配置 systemd（参考上一篇文章第十节）
```

## 六、One API 统一网关配置

One API 是开源的多模型管理网关（[GitHub](https://github.com/songquanpeng/one-api)），提供统一的 OpenAI 兼容接口、API Key 管理、用量统计等功能。

### 6.1 部署 One API

在 GPU 推理服务器上操作：

```bash
cd /home/<user>
wget https://github.com/songquanpeng/one-api/releases/download/v0.6.10/one-api-linux-amd64 -O one-api
chmod +x one-api
mkdir -p logs

# 后台启动
nohup ./one-api --port 3000 --log-dir ./logs > one-api.log 2>&1 &

# 确认端口已监听
ss -tlnp | grep 3000
```

### 6.2 初始化与管理后台

1. 通过 Nginx 代理访问管理后台：`https://<nginx-server-ip>/admin`
2. 初始账号：`root`，初始密码：`123456`
3. **⚠️ 首次登录后务必立即修改密码**

### 6.3 添加 vLLM 渠道

在 One API 后台「渠道」页面添加：

| 配置项 | 值 |
|:---|:---|
| 类型 | OpenAI |
| 渠道名称 | `vLLM-Qwen` |
| Base URL | `http://127.0.0.1:8000` |
| 密钥 | 留空即可（vLLM 默认无需认证） |
| 模型列表 | `qwen3.6`（需与 vLLM 的 `--served-model-name` 一致） |

如果有多个模型通过不同端口或有多个 GPU 服务器，可以添加多个渠道：

```json
{
  "qwen3.6": "/data/models/Qwen3.6-35B-A3B",
  "qwen-72b": "/data/models/Qwen2.5-72B-Instruct-AWQ",
  "deepseek-r1-32b": "/data/models/DeepSeek-R1-Distill-Qwen-32B",
  "glm-4-9b": "/data/models/glm-4-9b-chat-1m"
}
```

### 6.4 生成 API Key

1. 进入 One API「令牌」页面，点击「添加令牌」
2. 名称建议按用途命名，例如 `openwebui-system`（供 WebUI 调用）或 `dev-team`（供开发团队调用）
3. 选择该令牌可访问的模型范围
4. 设置过期时间（按安全策略，建议不超过 180 天）
5. 提交后复制生成的 `sk-` 开头的 Key，后续配置 WebUI 和业务客户端时需要

### 6.5 配置防火墙（关键安全步骤）

vLLM（8000）和 One API（3000）端口不应该对全网开放：

```bash
# vLLM 仅允许本机 One API 访问
sudo iptables -I INPUT 1 -p tcp -s 127.0.0.1 --dport 8000 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8000 -j DROP

# One API 仅允许本机和 Nginx 代理服务器访问
sudo iptables -I INPUT 1 -p tcp -s 127.0.0.1 --dport 3000 -j ACCEPT
sudo iptables -A INPUT -p tcp -s <nginx-server-ip> --dport 3000 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 3000 -j DROP

# 持久化
sudo netfilter-persistent save
```

> 上述规则确保即使 vLLM 绑定了 `0.0.0.0:8000`，外部也无法直接绕过 One API 调用模型。

### 6.6 配置 systemd（可选，推荐）

```bash
sudo tee /etc/systemd/system/one-api.service <<EOF
[Unit]
Description=One API Gateway
After=network.target

[Service]
Type=simple
User=<user>
WorkingDirectory=/home/<user>
ExecStart=/home/<user>/one-api --port 3000 --log-dir ./logs
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable one-api
sudo systemctl start one-api
```

## 七、Open WebUI 部署

Open WebUI 是一款类 ChatGPT 的开源对话界面，支持通过 OpenAI 兼容 API 对接后端模型。

### 7.1 安装

在 GPU 推理服务器上操作：

```bash
conda create -n openwebui python=3.11 -y
conda activate openwebui
pip install open-webui
```

### 7.2 配置环境变量

Open WebUI 的 RAG 嵌入模型默认从 HuggingFace 拉取，内网环境可能无法访问。推荐将嵌入引擎也指向 One API（使用 OpenAI 兼容的嵌入接口）：

```bash
export OPENAI_API_BASE_URL="http://127.0.0.1:3000/v1"
export OPENAI_API_KEY="sk-<your-one-api-key>"
export WEBUI_SECRET_KEY="<随机生成一串高强度字符串>"
export RAG_EMBEDDING_ENGINE="openai"
export RAG_EMBEDDING_MODEL="text-embedding-ada-002"
export RAG_OPENAI_API_BASE_URL="$OPENAI_API_BASE_URL"
export RAG_OPENAI_API_KEY="$OPENAI_API_KEY"

open-webui serve --port 8088
```

### 7.3 后台运行

```bash
tmux new -s webui
conda activate openwebui
# 重新设置环境变量（如上）
open-webui serve --port 8088
# Ctrl+B D 脱离
```

### 7.4 防火墙配置

```bash
# Open WebUI 仅允许 Nginx 代理服务器访问
sudo iptables -A INPUT -p tcp -s <nginx-server-ip> --dport 8088 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8088 -j DROP
sudo netfilter-persistent save
```

### 7.5 首次使用

1. 通过浏览器访问 `https://<nginx-server-ip>:8443`
2. 由于使用自签名证书，浏览器会提示证书风险——点击「高级」→「继续访问」
3. 首次注册的用户自动成为管理员，之后建议在设置中关闭开放注册
4. 在设置中确认模型列表已通过 One API 同步（应显示 `qwen3.6` 等已配置的模型）

## 八、Python 客户端调用指南

业务系统可以通过 OpenAI Python SDK 直接调用平台的 API 接口。

### 8.1 安装依赖

```bash
pip install openai
```

### 8.2 调用方式一：通过 Nginx HTTPS（推荐）

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://<nginx-server-ip>/v1",
    api_key="sk-<your-one-api-key>"
)

response = client.chat.completions.create(
    model="qwen3.6",
    messages=[
        {"role": "system", "content": "你是一个有帮助的AI助手。"},
        {"role": "user", "content": "你好，请介绍一下你自己。"}
    ],
    max_tokens=1024,
    temperature=0.7,
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### 8.3 调用方式二：绕过 Nginx（仅限 GPU 服务器本机调试）

```python
client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="EMPTY"  # 直连 vLLM 无需认证
)
```

### 8.4 关键参数说明

| 参数 | 建议值 | 说明 |
|:---|:---|:---|
| `base_url` | `https://<nginx-server-ip>/v1` | 统一通过网关访问，享受鉴权和统计 |
| `api_key` | One API 生成的 `sk-` Key | 不要硬编码在代码中，建议用环境变量 |
| `model` | 与 One API 渠道配置一致 | 如 `qwen3.6`、`deepseek-r1-32b` |
| `max_tokens` | 按业务需求 | 单次生成的最大 token 数 |
| `temperature` | 0.2~0.7 | 低值（0.2）适合确定性任务，高值（0.7）适合创意生成 |
| `stream` | `True` | 流式输出，用户体验更好 |

### 8.5 自签名证书的处理

使用 `https://<nginx-server-ip>/v1` 时，Python SDK 会校验 SSL 证书。自签名证书会导致请求失败。

**方案 A（测试环境）：关闭 SSL 验证**

```python
import httpx
client = OpenAI(
    base_url="https://<nginx-server-ip>/v1",
    api_key="sk-<your-one-api-key>",
    http_client=httpx.Client(verify=False)
)
```

**方案 B（生产环境，推荐）：将证书导入系统信任链**

```bash
# 将自签名证书复制到客户端机器
sudo cp server.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

## 九、端到端验证流程

从底层到上层，按顺序逐层验证，可以快速定位问题所在：

| 步骤 | 验证命令 | 预期结果 |
|:---|:---|:---|
| 1. vLLM | `curl http://127.0.0.1:8000/v1/models` | 返回包含 `qwen3.6` 的模型列表 |
| 2. One API | `curl http://127.0.0.1:3000/v1/models -H "Authorization: Bearer sk-<key>"` | 返回 One API 暴露的模型 |
| 3. Open WebUI | 浏览器访问 `http://<gpu-server-ip>:8088` | 能打开 WebUI 页面 |
| 4. Nginx → One API | `curl -k https://<nginx-server-ip>/v1/models -H "Authorization: Bearer sk-<key>"` | HTTPS 正常转发 |
| 5. Nginx → WebUI | 浏览器访问 `https://<nginx-server-ip>:8443` | 能打开 WebUI 登录页 |
| 6. Python 客户端 | 运行 `chat_llm.py` | 正常收到流式回复 |

## 十、常见问题排查

| 现象 | 可能原因 | 排查方向 |
|:---|:---|:---|
| 模型不响应 | 模型名称不匹配 | 确认 vLLM 的 `--served-model-name`、One API 渠道路由、客户端 `model` 参数三者完全一致 |
| 401 Unauthorized | API Key 无效 | 检查 One API 令牌是否过期、是否选择了正确的模型范围 |
| Nginx 502 Bad Gateway | 后端服务不可达 | 检查 GPU 服务器上的 vLLM/One API/WebUI 是否在运行，防火墙是否放行 |
| HTTPS 证书警告 | 自签名证书不受信任 | 测试环境可忽略，生产环境需部署正式证书或导入系统信任链 |
| Open WebUI 无法显示模型 | 环境变量配置错误 | 确认 `OPENAI_API_BASE_URL` 指向 `http://127.0.0.1:3000/v1`，且 `OPENAI_API_KEY` 为有效的 One API 令牌 |
| 生成中断 / 超时 | 超时配置不够 | 检查 Nginx 的 `proxy_read_timeout`，长文本生成建议 ≥ 300s |

## 十一、总结

本文介绍的架构已经在团队内部稳定运行了一段时间，总结几个核心设计决策：

1. **职责分离**：Nginx 负责加密和接入、One API 负责鉴权和路由、vLLM 专心做推理。各层各司其职，出了问题也容易定位
2. **安全纵深**：vLLM → One API → Nginx 三层访问控制，每一层都有独立的防护策略。即使某一层配置失误，也不会让模型直接暴露
3. **统一入口**：所有业务方通过同一个 `base_url` 调用模型，切换后端模型时客户端无需改代码——One API 的模型重映射功能让运维和业务解耦
4. **自签名证书的取舍**：内网环境没有公网域名，自签名证书是务实的折衷方案。如果安全策略允许，内部 CA 签发的正式证书体验更好

后续优化方向：

- **高可用**：One API 支持 MySQL/PostgreSQL 作为后端存储，可避免 SQLite 单点故障
- **监控告警**：对接 Prometheus + Grafana，监控各层的 QPS、延迟、错误率
- **模型灰度**：利用 One API 的模型映射功能，实现模型版本的平滑切换

---

*部署环境：双 A100 80GB PCIe · vLLM 0.19.0 · One API v0.6.10 · Open WebUI · 2026 年 7 月*

> **安全声明**：本文中所有 IP 地址、密码、API Key、证书信息均已替换为占位符。读者在实际部署时请替换为自己的环境配置。
