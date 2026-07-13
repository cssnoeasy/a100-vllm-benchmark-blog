# LiteLLM + PostgreSQL + Nginx：API Key权限、计量与防火墙部署教程

> 本文面向服务器管理员，记录如何在已有 VLLM 推理服务基础上部署 LiteLLM认证与计量、PostgreSQL数据库、Nginx HTTPS反向代理和网络访问控制。  
> 示例中的密码、Master Key和用户 Key均为占位符或现场随机生成，不包含真实生产凭据。

## 1. 最终架构

本文使用两台服务器：

| 角色 | 地址 | 服务 |
|---|---|---|
| 模型服务器 | `10.12.3.9` | VLLM、LiteLLM、PostgreSQL |
| 网关服务器 | `10.12.0.238` | Nginx、HTTPS证书 |

最终数据流：

```text
Codex ──────┐
Claude Code ├── HTTPS Nginx:443 ── LiteLLM:8080 ── VLLM:8000 ── Qwen 72B
OpenClaw ───┘
```

端口规划：

| 主机 | 端口 | 监听范围 | 用途 |
|---|---:|---|---|
| `10.12.3.9` | 8000 | `127.0.0.1` | VLLM OpenAI兼容接口 |
| `10.12.3.9` | 8080 | `0.0.0.0`，由防火墙限制 | LiteLLM代理 |
| `10.12.3.9` | 5432 | `127.0.0.1` | PostgreSQL |
| `10.12.0.238` | 443 | 局域网 | 统一 HTTPS入口 |

安全目标：

1. 客户端只能访问 Nginx 443；
2. 模型服务器8080只允许 Nginx服务器 `10.12.0.238`和本机访问；
3. VLLM 8000只监听本机；
4. PostgreSQL只监听本机；
5. 每个客户端或用户使用独立 LiteLLM虚拟 Key；
6. Nginx不暴露 `/key/generate` 等 LiteLLM管理接口；
7. LiteLLM将 Key、权限和使用记录写入 PostgreSQL。

## 2. 已验证版本

本文命令基于以下环境验证：

| 组件 | 版本 |
|---|---|
| Ubuntu | 24.04 |
| PostgreSQL | 16.14 |
| LiteLLM | 1.91.0 |
| Python | 3.10 |
| Prisma Python | 0.15.0 |
| Prisma CLI | 5.17.0 |
| Nginx | 1.20.1 |
| VLLM模型上下文 | 32768 Tokens |

升级版本后，数据库迁移目录、配置字段或管理 API可能变化，应先在测试端口验证。

---

# 第一阶段：检查现有服务并备份

## 3. 检查 VLLM

登录模型服务器：

```bash
ssh agent@10.12.3.9
```

检查 VLLM模型接口：

```bash
curl --connect-timeout 5 \
  http://127.0.0.1:8000/v1/models
```

预期能看到：

```text
qwen-72b
```

检查监听地址：

```bash
sudo ss -ltnp | grep ':8000'
```

建议 VLLM只监听：

```text
127.0.0.1:8000
```

不要让普通客户端直接访问 VLLM，否则会绕过 LiteLLM认证和计量。

## 4. 检查当前 LiteLLM和 PostgreSQL

```bash
curl --connect-timeout 5 \
  http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer sk-example-master-key"
```

```bash
psql --version 2>/dev/null || true
sudo pg_lsclusters 2>/dev/null || true
```

## 5. 备份现有配置

```bash
BACKUP_DIR="$HOME/llm-stack-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
```

```bash
cp -a "$HOME/litellm_config.yaml" "$BACKUP_DIR/" 2>/dev/null || true
cp -a "$HOME/fix_proxy.py" "$BACKUP_DIR/" 2>/dev/null || true
```

打包：

```bash
tar -czf "$BACKUP_DIR.tar.gz" \
  -C "$HOME" \
  "$(basename "$BACKUP_DIR")"

ls -lh "$BACKUP_DIR.tar.gz"
```

---

# 第二阶段：部署 PostgreSQL

## 6. 安装 PostgreSQL 16

如果尚未安装：

```bash
sudo apt update
sudo apt install -y postgresql postgresql-client
```

检查集群：

```bash
sudo pg_lsclusters
```

启动并设置开机自启：

```bash
echo auto |
  sudo tee /etc/postgresql/16/main/start.conf \
  > /dev/null

sudo systemctl enable --now postgresql@16-main
```

检查：

```bash
sudo systemctl status postgresql@16-main \
  --no-pager \
  -l
```

## 7. 限制 PostgreSQL只监听本机

编辑：

```text
/etc/postgresql/16/main/postgresql.conf
```

确认：

```conf
listen_addresses = 'localhost'
password_encryption = 'scram-sha-256'
```

可使用：

```bash
sudo sed -i -E \
  "s/^[#[:space:]]*listen_addresses[[:space:]]*=.*$/listen_addresses = 'localhost'/" \
  /etc/postgresql/16/main/postgresql.conf
```

```bash
sudo sed -i -E \
  "s/^[#[:space:]]*password_encryption[[:space:]]*=.*$/password_encryption = 'scram-sha-256'/" \
  /etc/postgresql/16/main/postgresql.conf
```

重启：

```bash
sudo systemctl restart postgresql@16-main
```

确认只监听本机：

```bash
sudo ss -ltnp | grep ':5432'
```

预期：

```text
127.0.0.1:5432
```

## 8. 创建 LiteLLM数据库和角色

生成随机数据库密码：

```bash
DB_PASSWORD="$(openssl rand -hex 32)"
```

创建角色：

```bash
sudo -u postgres psql \
  -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE litellm WITH LOGIN PASSWORD '$DB_PASSWORD';"
```

如果角色已经存在，使用：

```bash
sudo -u postgres psql \
  -v ON_ERROR_STOP=1 \
  -c "ALTER ROLE litellm WITH LOGIN PASSWORD '$DB_PASSWORD';"
```

创建数据库：

```bash
sudo -u postgres createdb \
  -O litellm \
  litellm \
  2>/dev/null || true
```

保存数据库密码：

```bash
umask 077

printf '%s\n' "$DB_PASSWORD" \
  > "$HOME/.litellm_db_password"

chmod 600 "$HOME/.litellm_db_password"
```

保存数据库连接环境：

```bash
printf 'DATABASE_URL=postgresql://litellm:%s@127.0.0.1:5432/litellm\n' \
  "$DB_PASSWORD" \
  > "$HOME/.litellm_db.env"

chmod 600 "$HOME/.litellm_db.env"
```

测试连接：

```bash
PGPASSWORD="$DB_PASSWORD" \
psql \
  -h 127.0.0.1 \
  -U litellm \
  -d litellm \
  -v ON_ERROR_STOP=1 \
  -c "SELECT current_user, current_database();"
```

清理当前 Shell变量：

```bash
unset DB_PASSWORD
```

说明：PostgreSQL不会保存可查询的明文密码。管理员后续需要查看明文时，只能读取自己保存的：

```text
/home/agent/.litellm_db_password
```

---

# 第三阶段：配置 LiteLLM

## 9. 检查 LiteLLM Python环境

本文实际路径：

```text
/home/agent/miniconda3/envs/vllm_env
```

检查：

```bash
PY="$HOME/miniconda3/envs/vllm_env/bin/python"
PIP="$HOME/miniconda3/envs/vllm_env/bin/pip"

"$PY" --version
"$PIP" show litellm prisma |
  grep -E '^(Name|Version|Location):'
```

如果是全新安装，可在独立环境中安装代理依赖：

```bash
conda create -n litellm_env python=3.10 -y
conda activate litellm_env
pip install "litellm[proxy]" prisma
```

如果采用独立环境，后文所有 `vllm_env`路径都需要替换成实际环境路径。

## 10. 生成 LiteLLM Master Key

Master Key只供管理员调用管理 API，不应给普通用户。

```bash
LITELLM_MASTER_KEY="sk-$(openssl rand -hex 32)"

umask 077

printf '%s\n' "$LITELLM_MASTER_KEY" \
  > "$HOME/.litellm_master_key"

chmod 600 "$HOME/.litellm_master_key"
```

创建统一环境文件：

```bash
DB_PASSWORD="$(< "$HOME/.litellm_db_password")"
LITELLM_MASTER_KEY="$(< "$HOME/.litellm_master_key")"

umask 077

{
  printf 'DATABASE_URL=postgresql://litellm:%s@127.0.0.1:5432/litellm\n' \
    "$DB_PASSWORD"

  printf 'LITELLM_MASTER_KEY=%s\n' \
    "$LITELLM_MASTER_KEY"
} > "$HOME/.litellm_proxy.env"

chmod 600 "$HOME/.litellm_proxy.env"

unset DB_PASSWORD
unset LITELLM_MASTER_KEY
```

## 11. 创建 LiteLLM配置文件

文件路径：

```text
/home/agent/litellm_config.yaml
```

内容：

```yaml
model_list:
  - model_name: qwen-72b
    litellm_params:
      model: hosted_vllm/qwen-72b
      api_base: http://127.0.0.1:8000/v1
      api_key: dummy
      max_tokens: 4096
      drop_params: true
    model_info:
      max_tokens: 4096
      max_input_tokens: 32768
      max_output_tokens: 4096
      mode: chat
      supports_tool_calls: true
      supports_vision: false

  - model_name: qwen-72b-codex
    litellm_params:
      model: openai/qwen-72b
      api_base: http://127.0.0.1:8000/v1
      api_key: dummy
      max_tokens: 4096
      drop_params: true
      use_chat_completions_api: true
    model_info:
      max_tokens: 4096
      max_input_tokens: 32768
      max_output_tokens: 4096
      mode: chat
      supports_tool_calls: true
      supports_vision: false

litellm_settings:
  drop_params: true

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

注意：

1. VLLM真实模型名为 `qwen-72b`；
2. `qwen-72b-codex` 是提供给 Codex的 LiteLLM别名；
3. Codex使用 Responses API，LiteLLM将请求转换为 VLLM Chat Completions；
4. `api_key: dummy` 是 LiteLLM访问本机 VLLM时使用的占位值，不是客户端 Key；
5. `master_key`必须从环境变量读取，不要把真实 Master Key写入 YAML；
6. `max_tokens: 4096`是默认值，不一定会强制截断客户端主动发送的更大值；Claude Code客户端还应设置 `CLAUDE_CODE_MAX_OUTPUT_TOKENS=4096`。

限制配置权限：

```bash
chmod 600 "$HOME/litellm_config.yaml"
```

## 12. 执行 Prisma数据库迁移

LiteLLM 1.91.0使用以下 schema和迁移目录：

```text
/home/agent/miniconda3/envs/vllm_env/lib/python3.10/site-packages/litellm_proxy_extras/schema.prisma
```

加载环境：

```bash
set -a
source "$HOME/.litellm_proxy.env"
set +a
```

执行迁移：

```bash
PRISMA="$HOME/miniconda3/envs/vllm_env/bin/prisma"
SCHEMA="$HOME/miniconda3/envs/vllm_env/lib/python3.10/site-packages/litellm_proxy_extras/schema.prisma"

"$PRISMA" migrate deploy \
  --schema "$SCHEMA"
```

应出现类似：

```text
All migrations have been successfully applied
```

检查数据表：

```bash
sudo -u postgres psql \
  -d litellm \
  -c "SELECT count(*) AS table_count FROM pg_tables WHERE schemaname='public';"
```

表数量应大于0。LiteLLM 1.91.0实测约为66张表，具体数量可能随版本变化。

---

# 第四阶段：使用 systemd运行 LiteLLM

## 13. 创建 LiteLLM服务

```bash
sudo tee /etc/systemd/system/litellm-proxy.service > /dev/null <<'EOF'
[Unit]
Description=LiteLLM Proxy
After=network-online.target postgresql@16-main.service
Requires=postgresql@16-main.service

[Service]
Type=simple
User=agent
Group=agent
WorkingDirectory=/home/agent
Environment=HOME=/home/agent
EnvironmentFile=/home/agent/.litellm_proxy.env
ExecStart=/home/agent/miniconda3/envs/vllm_env/bin/litellm --config /home/agent/litellm_config.yaml --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
EOF
```

如果8080上已有旧 LiteLLM进程，先停止旧进程：

```bash
sudo fuser -k 8080/tcp 2>/dev/null || true
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now litellm-proxy
```

首次启动可能因尝试读取远程模型价格映射而耗时约一分钟。不要在几秒后立即判断失败。

查看状态：

```bash
sudo systemctl status litellm-proxy \
  --no-pager \
  -l
```

查看日志：

```bash
sudo journalctl \
  -u litellm-proxy \
  -n 100 \
  --no-pager
```

## 14. 验证 Master Key和无 Key认证

```bash
LITELLM_MASTER_KEY="$(< "$HOME/.litellm_master_key")"
```

带 Master Key：

```bash
curl \
  --retry 40 \
  --retry-delay 3 \
  --retry-connrefused \
  --connect-timeout 2 \
  --max-time 150 \
  -sS \
  http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY"
```

无 Key：

```bash
curl -sS \
  -o /dev/null \
  -w 'HTTP %{http_code}\n' \
  http://127.0.0.1:8080/v1/models
```

预期无 Key返回：

```text
HTTP 401
```

清理：

```bash
unset LITELLM_MASTER_KEY
```

---

# 第五阶段：创建 API Key并配置权限

## 15. Key类型和权限原则

建议至少区分：

| Key别名 | 允许模型 | 用途 |
|---|---|---|
| `claude-client` | `qwen-72b` | Claude Code |
| `codex-client` | `qwen-72b-codex` | Codex |
| `openclaw-client` | `qwen-72b` | OpenClaw |
| `external-user-01` 等 | 根据需要 | 分发给其他用户 |

权限原则：

1. 一人一 Key；
2. 一种客户端可使用独立 Key；
3. 只开放实际需要的模型；
4. 给外部用户设置 RPM和 TPM；
5. 不把 Master Key当作普通客户端 Key；
6. 创建 Key时立即保存明文，因为数据库通常只保存哈希，之后无法还原原始 Key。

## 16. 创建 Claude、Codex和 OpenClaw Key

```bash
set -a
source "$HOME/.litellm_proxy.env"
set +a
umask 077
```

Claude：

```bash
curl -fsS \
  http://127.0.0.1:8080/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_alias": "claude-client",
    "models": ["qwen-72b"],
    "metadata": {"client": "claude"}
  }' > "$HOME/.litellm_key_claude.json"
```

Codex：

```bash
curl -fsS \
  http://127.0.0.1:8080/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_alias": "codex-client",
    "models": ["qwen-72b-codex"],
    "metadata": {"client": "codex"}
  }' > "$HOME/.litellm_key_codex.json"
```

OpenClaw：

```bash
curl -fsS \
  http://127.0.0.1:8080/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_alias": "openclaw-client",
    "models": ["qwen-72b"],
    "metadata": {"client": "openclaw"}
  }' > "$HOME/.litellm_key_openclaw.json"
```

提取并保存明文 Key：

```bash
"$HOME/miniconda3/envs/vllm_env/bin/python" - <<'PY'
import json
import os
from pathlib import Path

home = Path.home()

for client in ("claude", "codex", "openclaw"):
    response_file = home / f".litellm_key_{client}.json"
    key_file = home / f".litellm_{client}.key"

    data = json.loads(response_file.read_text())
    key = data.get("key")

    if not key:
        raise RuntimeError(f"{client}: key generation failed")

    key_file.write_text(key + "\n")
    os.chmod(key_file, 0o600)

    print(f"{client}: {key[:7]}...{key[-4:]}")
PY
```

## 17. 创建5枚外部用户 Key

以下示例允许两个模型，并设置：

```text
RPM：20
TPM：100000
```

```bash
export LITELLM_MASTER_KEY="$(< "$HOME/.litellm_master_key")"
export ISSUED_KEYS_FILE="$HOME/litellm-issued-keys-$(date +%Y%m%d-%H%M%S).csv"
```

```bash
"$HOME/miniconda3/envs/vllm_env/bin/python" - <<'PY'
import csv
import json
import os
import urllib.request
from pathlib import Path

master = os.environ["LITELLM_MASTER_KEY"]
output = Path(os.environ["ISSUED_KEYS_FILE"])
rows = []

for number in range(1, 6):
    alias = f"external-user-{number:02d}"

    body = {
        "key_alias": alias,
        "models": ["qwen-72b", "qwen-72b-codex"],
        "rpm_limit": 20,
        "tpm_limit": 100000,
        "metadata": {
            "type": "external-user",
            "number": number,
        },
    }

    request = urllib.request.Request(
        "http://127.0.0.1:8080/key/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {master}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)

    key = result["key"]

    rows.append({
        "alias": alias,
        "key": key,
        "models": "qwen-72b;qwen-72b-codex",
        "rpm_limit": 20,
        "tpm_limit": 100000,
    })

    print(f"{alias}: {key[:7]}...{key[-4:]}")

with output.open("w", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(file, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

os.chmod(output, 0o600)
print(f"Saved: {output}")
PY
```

```bash
unset LITELLM_MASTER_KEY
ls -l "$ISSUED_KEYS_FILE"
```

管理员自己查看完整 Key：

```bash
cat "$ISSUED_KEYS_FILE"
```

不要把整个 CSV发给所有用户。每个人只应收到自己的那一行 Key。

## 18. 测试模型权限

```bash
CLAUDE_KEY="$(< "$HOME/.litellm_claude.key")"
CODEX_KEY="$(< "$HOME/.litellm_codex.key")"
OPENCLAW_KEY="$(< "$HOME/.litellm_openclaw.key")"
```

```bash
for CLIENT in claude codex openclaw; do
  case "$CLIENT" in
    claude) KEY="$CLAUDE_KEY" ;;
    codex) KEY="$CODEX_KEY" ;;
    openclaw) KEY="$OPENCLAW_KEY" ;;
  esac

  echo "=== $CLIENT ==="

  curl -sS \
    http://127.0.0.1:8080/v1/models \
    -H "Authorization: Bearer $KEY"

  echo
done
```

受限 Key只会看到被授权的模型。例如 Codex Key通常只看到 `qwen-72b-codex`。

---

# 第六阶段：配置 Nginx HTTPS反向代理

## 19. 检查 Nginx现有配置和端口

登录网关服务器：

```bash
ssh root@10.12.0.238
```

```bash
nginx -v
sudo nginx -t
sudo ss -ltnp | grep nginx
```

输出完整配置用于审计：

```bash
nginx -T > /tmp/nginx-full-config.txt 2>&1

grep -nE \
  'listen |server_name|location |proxy_pass|ssl_certificate' \
  /tmp/nginx-full-config.txt
```

确认443未被其他 server占用。如果已有443服务，应使用独立域名或其他端口，不能直接覆盖。

## 20. 创建内部 CA

```bash
mkdir -p /etc/nginx/ssl/litellm
chmod 700 /etc/nginx/ssl/litellm
cd /etc/nginx/ssl/litellm
umask 077
```

CA配置：

```bash
cat > ca.cnf <<'EOF'
[req]
prompt = no
distinguished_name = ca_dn
x509_extensions = v3_ca

[ca_dn]
C = CN
ST = Guangxi
L = Guilin
O = University
OU = LLM Gateway CA
CN = LLM Gateway Internal CA

[v3_ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
EOF
```

生成 CA：

```bash
openssl genrsa \
  -out litellm-ca.key \
  4096

openssl req \
  -x509 \
  -new \
  -sha256 \
  -days 3650 \
  -key litellm-ca.key \
  -out litellm-ca.crt \
  -config ca.cnf
```

## 21. 创建带 IP SAN的服务器证书

```bash
cat > server.cnf <<'EOF'
[req]
prompt = no
distinguished_name = server_dn
req_extensions = req_ext

[server_dn]
C = CN
ST = Guangxi
L = Guilin
O = University
OU = LLM Gateway
CN = 10.12.0.238

[req_ext]
subjectAltName = @alt_names

[cert_ext]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
subjectAltName = @alt_names

[alt_names]
IP.1 = 10.12.0.238
DNS.1 = llm-gateway.local
EOF
```

生成私钥和 CSR：

```bash
openssl genrsa \
  -out litellm-server.key \
  2048

openssl req \
  -new \
  -sha256 \
  -key litellm-server.key \
  -out litellm-server.csr \
  -config server.cnf
```

签发：

```bash
openssl x509 \
  -req \
  -sha256 \
  -days 825 \
  -in litellm-server.csr \
  -CA litellm-ca.crt \
  -CAkey litellm-ca.key \
  -CAcreateserial \
  -out litellm-server.crt \
  -extfile server.cnf \
  -extensions cert_ext
```

创建完整证书链并设置权限：

```bash
cat litellm-server.crt litellm-ca.crt \
  > litellm-fullchain.crt

chmod 600 litellm-ca.key litellm-server.key
chmod 644 litellm-ca.crt litellm-server.crt litellm-fullchain.crt
```

验证：

```bash
openssl verify \
  -CAfile litellm-ca.crt \
  litellm-server.crt
```

```bash
openssl x509 \
  -in litellm-server.crt \
  -noout \
  -subject \
  -issuer \
  -dates \
  -text |
  grep -A2 -E 'Subject:|Issuer:|Subject Alternative Name'
```

必须包含：

```text
IP Address:10.12.0.238
```

## 22. 配置 Nginx统一入口

创建：

```text
/etc/nginx/conf.d/litellm-gateway.conf
```

```nginx
server {
    listen 443 ssl http2;
    server_name 10.12.0.238;

    ssl_certificate     /etc/nginx/ssl/litellm/litellm-fullchain.crt;
    ssl_certificate_key /etc/nginx/ssl/litellm/litellm-server.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_session_cache   shared:LLMGateway:10m;
    ssl_session_timeout 1d;

    client_max_body_size 50m;

    location = /healthz {
        default_type application/json;
        return 200 '{"status":"ok"}';
    }

    # Claude Messages由LiteLLM直接处理，不再需要Flask。
    location = /v1/messages {
        proxy_pass http://10.12.3.9:8080;

        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_cache off;
        gzip off;

        proxy_connect_timeout 10s;
        proxy_send_timeout 3600s;
        proxy_read_timeout 3600s;
        send_timeout 3600s;

        add_header X-Accel-Buffering no always;
    }

    # Responses、Chat Completions、Models等OpenAI兼容接口。
    location /v1/ {
        proxy_pass http://10.12.3.9:8080;

        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_cache off;
        gzip off;

        proxy_connect_timeout 10s;
        proxy_send_timeout 3600s;
        proxy_read_timeout 3600s;
        send_timeout 3600s;

        add_header X-Accel-Buffering no always;
    }

    # 阻止外部访问/key/generate、/key/list等管理接口。
    location / {
        return 404;
    }
}
```

检查并加载：

```bash
nginx -t &&
systemctl reload nginx
```

如果 firewalld正在运行：

```bash
if systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-service=https
  firewall-cmd --reload
fi
```

验证：

```bash
curl \
  --cacert /etc/nginx/ssl/litellm/litellm-ca.crt \
  https://10.12.0.238/healthz
```

预期：

```json
{"status":"ok"}
```

无 Key访问模型应返回401：

```bash
curl \
  --cacert /etc/nginx/ssl/litellm/litellm-ca.crt \
  -o /dev/null \
  -w 'HTTP %{http_code}\n' \
  https://10.12.0.238/v1/models
```

---

# 第七阶段：配置模型服务器防火墙

## 23. 目标规则

LiteLLM 8080只允许：

```text
127.0.0.1
10.12.0.238
```

其他客户端直接访问8080必须失败。

本节只限制8080，不修改 SSH和其他端口，避免锁死远程管理。

## 24. 立即应用 iptables规则

在模型服务器执行：

```bash
sudo iptables -N LITELLM_8080 2>/dev/null || true
sudo iptables -F LITELLM_8080

sudo iptables -A LITELLM_8080 \
  -i lo \
  -j ACCEPT

sudo iptables -A LITELLM_8080 \
  -p tcp \
  -s 10.12.0.238/32 \
  --dport 8080 \
  -j ACCEPT

sudo iptables -A LITELLM_8080 \
  -p tcp \
  --dport 8080 \
  -j REJECT \
  --reject-with tcp-reset

sudo iptables -C INPUT \
  -p tcp \
  --dport 8080 \
  -j LITELLM_8080 \
  2>/dev/null ||
sudo iptables -I INPUT 1 \
  -p tcp \
  --dport 8080 \
  -j LITELLM_8080
```

检查：

```bash
sudo iptables -nvL LITELLM_8080 \
  --line-numbers
```

## 25. 创建持久化脚本

```bash
sudo tee /usr/local/sbin/litellm-firewall.sh > /dev/null <<'EOF'
#!/bin/bash
set -e

IPTABLES=/usr/sbin/iptables
NGINX_IP=10.12.0.238
CHAIN=LITELLM_8080

$IPTABLES -N "$CHAIN" 2>/dev/null || true
$IPTABLES -F "$CHAIN"

$IPTABLES -A "$CHAIN" \
  -i lo \
  -j ACCEPT

$IPTABLES -A "$CHAIN" \
  -p tcp \
  -s "$NGINX_IP/32" \
  --dport 8080 \
  -j ACCEPT

$IPTABLES -A "$CHAIN" \
  -p tcp \
  --dport 8080 \
  -j REJECT \
  --reject-with tcp-reset

$IPTABLES -C INPUT \
  -p tcp \
  --dport 8080 \
  -j "$CHAIN" \
  2>/dev/null ||
$IPTABLES -I INPUT 1 \
  -p tcp \
  --dport 8080 \
  -j "$CHAIN"
EOF

sudo chmod 750 /usr/local/sbin/litellm-firewall.sh
```

## 26. 创建防火墙 systemd服务

```bash
sudo tee /etc/systemd/system/litellm-firewall.service > /dev/null <<'EOF'
[Unit]
Description=Restrict LiteLLM port 8080 to Nginx
After=network-online.target
Before=litellm-proxy.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/litellm-firewall.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now litellm-firewall
sudo systemctl status litellm-firewall --no-pager -l
```

`active (exited)`是 oneshot服务成功执行后的正常状态。

## 27. 验证防火墙

从 Nginx服务器测试：

```bash
curl -sS \
  -o /dev/null \
  -w 'HTTP %{http_code}\n' \
  http://10.12.3.9:8080/v1/models
```

预期返回401，表示允许 Nginx连接但 LiteLLM要求认证。

从普通 Windows客户端：

```powershell
Test-NetConnection 10.12.3.9 -Port 8080
```

预期：

```text
TcpTestSucceeded : False
```

同时检查统一入口仍正常：

```powershell
Test-NetConnection 10.12.0.238 -Port 443
```

---

# 第八阶段：计量、查询和日常管理

## 28. 查看已保存的管理员凭据

以下命令会显示明文，只能在管理员终端执行，不要把输出粘贴到聊天或日志：

```bash
echo "PostgreSQL password:"
cat "$HOME/.litellm_db_password"

echo
echo "LiteLLM Master Key:"
cat "$HOME/.litellm_master_key"

echo
echo "Claude Key:"
cat "$HOME/.litellm_claude.key"

echo
echo "Codex Key:"
cat "$HOME/.litellm_codex.key"

echo
echo "OpenClaw Key:"
cat "$HOME/.litellm_openclaw.key"
```

Linux登录密码不能从系统恢复明文；系统只保存密码哈希。

## 29. 查询最近使用记录

```bash
sudo -u postgres psql -d litellm <<'SQL'
SELECT jsonb_build_object(
  'time',              COALESCE(to_jsonb(s)->>'startTime',
                                to_jsonb(s)->>'start_time'),
  'model',             to_jsonb(s)->>'model',
  'call_type',         to_jsonb(s)->>'call_type',
  'prompt_tokens',     to_jsonb(s)->>'prompt_tokens',
  'completion_tokens', to_jsonb(s)->>'completion_tokens',
  'total_tokens',      to_jsonb(s)->>'total_tokens',
  'spend',             to_jsonb(s)->>'spend'
) AS usage
FROM "LiteLLM_SpendLogs" AS s
ORDER BY COALESCE(
  to_jsonb(s)->>'startTime',
  to_jsonb(s)->>'start_time'
) DESC
LIMIT 20;
SQL
```

## 30. 按 Key别名汇总 Token

```bash
sudo -u postgres psql -d litellm <<'SQL'
WITH key_data AS (
    SELECT
        COALESCE(
            to_jsonb(k)->>'key_alias',
            to_jsonb(k)->>'key_name'
        ) AS key_alias,
        COALESCE(
            to_jsonb(k)->>'token',
            to_jsonb(k)->>'hashed_token'
        ) AS token
    FROM "LiteLLM_VerificationToken" AS k
),
log_data AS (
    SELECT
        to_jsonb(s)->>'api_key' AS api_key,
        to_jsonb(s)->>'model' AS model,
        to_jsonb(s)->>'call_type' AS call_type,
        COALESCE(NULLIF(to_jsonb(s)->>'prompt_tokens', '')::bigint, 0)
            AS prompt_tokens,
        COALESCE(NULLIF(to_jsonb(s)->>'completion_tokens', '')::bigint, 0)
            AS completion_tokens,
        COALESCE(NULLIF(to_jsonb(s)->>'total_tokens', '')::bigint, 0)
            AS total_tokens,
        COALESCE(NULLIF(to_jsonb(s)->>'spend', '')::numeric, 0)
            AS spend
    FROM "LiteLLM_SpendLogs" AS s
)
SELECT
    COALESCE(k.key_alias, 'unknown') AS client,
    l.model,
    l.call_type,
    COUNT(*) AS requests,
    SUM(l.prompt_tokens) AS input_tokens,
    SUM(l.completion_tokens) AS output_tokens,
    SUM(l.total_tokens) AS total_tokens,
    SUM(l.spend) AS spend
FROM log_data AS l
LEFT JOIN key_data AS k
    ON k.token = l.api_key
GROUP BY
    COALESCE(k.key_alias, 'unknown'),
    l.model,
    l.call_type
ORDER BY
    client,
    total_tokens DESC;
SQL
```

如果 `spend=0`，但 Token有记录，说明 Token计量正常，只是尚未配置自托管模型单价。

## 31. 自定义模型单价

LiteLLM的 `spend`通常按每 Token成本计算。可在 `model_info`中添加自定义内部核算单价。

示例占位值：

```yaml
model_info:
  input_cost_per_token: 0.00000050
  output_cost_per_token: 0.00000150
```

这些数字仅为示例，不代表实际成本。管理员应根据 GPU折旧、电费、维护和资源占用制定内部价格。

修改配置后重启：

```bash
sudo systemctl restart litellm-proxy
```

先在测试 Key上验证计费，再启用 `max_budget`，否则错误单价可能导致用户被意外封禁。

## 32. RPM、TPM和预算建议

示例：

```json
{
  "rpm_limit": 20,
  "tpm_limit": 100000,
  "max_budget": 10,
  "budget_duration": "30d"
}
```

注意：

- RPM：每分钟请求数；
- TPM：每分钟 Token数；
- `max_budget`只有在模型成本不为0时才有实际预算约束意义；
- Codex工具调用会产生多轮请求，限制不宜设置得过低；
- 先观察真实用量，再逐步收紧。

---

# 第九阶段：完整验收

## 33. 检查服务状态

模型服务器：

```bash
systemctl is-active postgresql@16-main
systemctl is-active litellm-firewall
systemctl is-active litellm-proxy
```

预期均为：

```text
active
```

Nginx服务器：

```bash
systemctl is-active nginx
nginx -t
```

## 34. 检查端口

模型服务器：

```bash
sudo ss -ltnp |
  grep -E ':8000|:8080|:5432'
```

应满足：

```text
8000：仅127.0.0.1
5432：仅127.0.0.1
8080：由LiteLLM监听，iptables限制来源
```

网关服务器：

```bash
sudo ss -ltnp | grep ':443'
```

## 35. 客户端验收

Windows用户导入 `litellm-ca.crt`后测试：

```powershell
curl.exe `
  --ssl-no-revoke `
  https://10.12.0.238/healthz
```

```powershell
curl.exe `
  --ssl-no-revoke `
  https://10.12.0.238/v1/models `
  -H "Authorization: Bearer sk-user-example-key"
```

检查：

1. 无 Key返回401；
2. 有效 Key返回允许的模型；
3. 不同 Key看到不同模型；
4. Codex Responses API正常；
5. Claude `/v1/messages`流式输出正常；
6. OpenClaw Chat Completions正常；
7. PostgreSQL记录请求和 Token。

---

# 第十阶段：回滚与故障处理

## 36. LiteLLM无法启动

```bash
sudo systemctl status litellm-proxy --no-pager -l
sudo journalctl -u litellm-proxy -n 150 --no-pager
```

常见问题：

- `.litellm_proxy.env`路径错误；
- 文件权限导致 systemd无法读取；
- PostgreSQL未启动；
- Prisma迁移没有执行；
- 8080仍被旧进程占用；
- 首次启动仍在等待远程价格映射超时。

## 37. PostgreSQL表不存在

如果日志出现：

```text
LiteLLM_Config does not exist
LiteLLM_SpendLogs does not exist
```

重新加载环境并执行：

```bash
set -a
source "$HOME/.litellm_proxy.env"
set +a

"$HOME/miniconda3/envs/vllm_env/bin/prisma" migrate deploy \
  --schema "$HOME/miniconda3/envs/vllm_env/lib/python3.10/site-packages/litellm_proxy_extras/schema.prisma"
```

## 38. Nginx返回502

从 Nginx服务器检查：

```bash
curl -sS \
  -o /dev/null \
  -w 'HTTP %{http_code}\n' \
  http://10.12.3.9:8080/v1/models
```

- 返回401：上游网络正常；
- 无法连接：检查模型服务器防火墙规则和 LiteLLM状态；
- 连接超时：检查服务器路由、iptables或校园网访问控制。

## 39. 证书问题

服务器端验证：

```bash
openssl verify \
  -CAfile /etc/nginx/ssl/litellm/litellm-ca.crt \
  /etc/nginx/ssl/litellm/litellm-server.crt
```

检查 SAN：

```bash
openssl x509 \
  -in /etc/nginx/ssl/litellm/litellm-server.crt \
  -noout \
  -text |
  grep -A2 'Subject Alternative Name'
```

必须包含 `IP Address:10.12.0.238`。

## 40. 临时撤销防火墙限制

仅在确认有必要时执行：

```bash
sudo iptables -D INPUT \
  -p tcp \
  --dport 8080 \
  -j LITELLM_8080
```

恢复：

```bash
sudo systemctl restart litellm-firewall
```

## 41. 恢复旧 LiteLLM配置

```bash
cp -a \
  "$HOME/llm-stack-backup-YYYYMMDD-HHMMSS/litellm_config.yaml" \
  "$HOME/litellm_config.yaml"

sudo systemctl restart litellm-proxy
```

回滚前应记录当前配置和日志，避免丢失故障证据。

---

# 第十一阶段：安全清单

## 42. 可以分发给用户

```text
litellm-ca.crt
用户自己的LiteLLM虚拟Key
HTTPS Base URL
模型名称
客户端配置教程
```

## 43. 不得分发

```text
litellm-ca.key
litellm-server.key
LiteLLM Master Key
PostgreSQL密码
.litellm_proxy.env
其他用户的Key
完整issued-keys CSV
服务器SSH密码
```

## 44. 上线前最终检查

- [ ] VLLM只监听本机8000；
- [ ] PostgreSQL只监听本机5432；
- [ ] LiteLLM使用强 Master Key；
- [ ] LiteLLM数据库迁移完成；
- [ ] 每位用户使用独立 Key；
- [ ] Key具有明确模型权限；
- [ ] 外部 Key设置合理 RPM和 TPM；
- [ ] Nginx使用带 IP SAN的证书；
- [ ] Nginx关闭 SSE缓冲；
- [ ] Nginx不暴露管理 API；
- [ ] 8080只允许 `10.12.0.238`和本机；
- [ ] 客户端不能直连 VLLM；
- [ ] Token记录已写入 PostgreSQL；
- [ ] Master Key、数据库密码和 CA私钥权限为600；
- [ ] 已保存配置和数据库备份；
- [ ] 已制定 Key撤销与轮换流程。

