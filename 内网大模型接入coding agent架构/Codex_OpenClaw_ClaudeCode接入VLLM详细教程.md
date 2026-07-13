# Codex、OpenClaw（小龙虾）与 Claude Code 接入校内 VLLM 模型详细教程

> 适用对象：需要在 Windows 10/11 电脑上使用学校自建模型的用户。  


## 1. 当前服务信息

| 项目 | 地址或值 |
|---|---|
| HTTPS 网关 | `https://10.12.0.238` |
| OpenAI 兼容 Base URL | `https://10.12.0.238/v1` |
| Claude Code Base URL | `https://10.12.0.238` |
| 普通对话模型 | `qwen-72b` |
| Codex 模型 | `qwen-72b-codex` |
| 最大上下文 | 32768 Tokens |
| 建议最大输出 | 4096 Tokens |
| 身份认证 | 每位用户独立 LiteLLM API Key |
| CA 证书 | `litellm-ca.crt`，由管理员分发 |

生产链路如下：

```text
Codex ──────┐
Claude Code ├── HTTPS Nginx:443 ── LiteLLM:8080 ── VLLM:8000 ── Qwen 72B
OpenClaw ───┘
```

用户不应直接访问以下后端端口：

```text
10.12.3.9:8000
10.12.3.9:8080
```

其中 `10.12.3.9:8080` 已限制为仅允许 Nginx 服务器访问。

## 2. 使用前需要向管理员领取的文件和信息

每位用户需要领取：

1. 一枚个人 LiteLLM API Key，格式通常以 `sk-` 开头。
2. 内部 CA 证书 `litellm-ca.crt`。请明确向管理员索取这个文件，不要索取或接收任何 `.key` 私钥文件。
3. `litellm-ca.crt` 的 SHA-256 指纹，由管理员通过独立消息提供，用于核对证书在传输过程中没有被替换。
4. 允许使用的模型名称。

不得向普通用户分发：

- LiteLLM Master Key；
- PostgreSQL 密码；
- Nginx 服务器私钥；
- 内部 CA 私钥 `litellm-ca.key`；
- 其他用户的 API Key。

建议不同用户、不同用途使用不同 Key。这样可以分别统计 Token、设置模型权限、RPM 和 TPM 限制，并可单独撤销。

## 3. Windows 公共准备工作

下面三种客户端都建议先完成本节。

### 3.1 安装 Windows Terminal

可从 Microsoft Store 安装 Windows Terminal。安装完成后打开 PowerShell。

### 3.2 安装 Node.js LTS

Codex、Claude Code 和 OpenClaw 均可通过 npm 安装，建议安装 Node.js LTS。

下载地址：

```text
https://nodejs.org/
```

安装后关闭并重新打开 PowerShell，检查：

```powershell
node --version
npm --version
```

两条命令都应返回版本号。

### 3.3 安装 Git

Codex 处理代码仓库时建议安装 Git for Windows。

下载地址：

```text
https://git-scm.com/download/win
```

检查：

```powershell
git --version
```

### 3.4 向管理员领取、核验并导入内部 CA

内部 CA 是访问校内 HTTPS 网关的信任基础。第一次配置 Codex、Claude Code 或 OpenClaw 时，每台客户端电脑都需要完成一次本节操作。

#### 3.4.1 向管理员索取正确的文件

请向管理员索取：

```text
文件名：litellm-ca.crt
文件类型：X.509 CA 公钥证书
用途：信任 https://10.12.0.238
```

同时向管理员索取该文件的 SHA-256 指纹。建议管理员通过与证书文件不同的渠道发送指纹，例如证书发群文件、指纹单独私发。

管理员可以使用以下命令生成待公布的指纹：

```powershell
Get-FileHash `
  "litellm-ca.crt" `
  -Algorithm SHA256
```

普通用户只应收到：

```text
litellm-ca.crt
```

如果收到以下任何文件，请不要导入或传播，并立即联系管理员：

```text
litellm-ca.key
litellm-server.key
litellm-fullchain.crt（普通客户端通常不需要）
```

其中 `.key` 文件是私钥，绝不能分发给普通用户。

#### 3.4.2 将收到的证书保存到稳定目录

假设收到的 `litellm-ca.crt` 当前位于 Windows“下载”目录。不要长期直接引用下载目录，因为用户清理下载文件后，Claude Code 和 OpenClaw 会失去证书。

创建稳定目录并复制证书：

```powershell
$CertDir = "$HOME\.certs"
$CaFile = "$CertDir\litellm-ca.crt"

New-Item `
  -ItemType Directory `
  -Path $CertDir `
  -Force | Out-Null

Copy-Item `
  "$HOME\Downloads\litellm-ca.crt" `
  $CaFile `
  -Force
```

确认文件存在：

```powershell
Get-Item $CaFile |
  Select-Object FullName, Length, LastWriteTime
```

#### 3.4.3 核对 SHA-256 指纹

计算本机收到文件的指纹：

```powershell
$LocalFingerprint = (
  Get-FileHash `
    $CaFile `
    -Algorithm SHA256
).Hash

$LocalFingerprint
```

将输出与管理员提供的 SHA-256 指纹逐字核对：

- 完全一致：可以继续导入；
- 任意字符不一致：停止操作，删除该文件并联系管理员重新获取；
- 管理员没有提供指纹：不要仅凭文件名判断证书可信。

也可以查看证书主题和有效期：

```powershell
$Certificate = New-Object `
  System.Security.Cryptography.X509Certificates.X509Certificate2 `
  $CaFile

$Certificate |
  Select-Object Subject, Issuer, NotBefore, NotAfter, Thumbprint
```

正常情况下 Subject 和 Issuer应指向校内 LLM Gateway Internal CA。

#### 3.4.4 导入 Windows 当前用户信任库

将证书导入当前用户的“受信任的根证书颁发机构”：

```powershell
Import-Certificate `
  -FilePath $CaFile `
  -CertStoreLocation Cert:\CurrentUser\Root
```

该命令通常不需要管理员权限，只影响当前 Windows用户。导入成功后会显示证书 Thumbprint和 Subject。

确认信任库中已经存在该证书：

```powershell
Get-ChildItem Cert:\CurrentUser\Root |
  Where-Object {
    $_.Subject -like "*LLM Gateway Internal CA*"
  } |
  Select-Object Subject, Thumbprint, NotAfter
```

如果命令没有返回任何证书，说明导入未成功。

#### 3.4.5 为 Claude Code和 OpenClaw配置 Node.js CA

Claude Code和 OpenClaw运行在 Node.js环境中。除了导入 Windows信任库，还要设置 `NODE_EXTRA_CA_CERTS`：

```powershell
$env:NODE_EXTRA_CA_CERTS = $CaFile

[Environment]::SetEnvironmentVariable(
  "NODE_EXTRA_CA_CERTS",
  $CaFile,
  "User"
)
```

这个变量只会被新启动的进程读取。设置后必须：

1. 关闭所有 Claude Code、OpenClaw及其 Gateway进程；
2. 关闭旧 PowerShell窗口；
3. 重新打开 PowerShell。

然后检查：

```powershell
$env:NODE_EXTRA_CA_CERTS
```

应输出类似：

```text
C:\Users\你的用户名\.certs\litellm-ca.crt
```

同时确认文件仍存在：

```powershell
Test-Path $env:NODE_EXTRA_CA_CERTS
```

预期返回：

```text
True
```

#### 3.4.6 测试证书和 HTTPS网关

先检查443端口：

```powershell
Test-NetConnection 10.12.0.238 -Port 443
```

预期：

```text
TcpTestSucceeded : True
```

再测试 HTTPS：

```powershell
curl.exe `
  --ssl-no-revoke `
  https://10.12.0.238/healthz
```

预期返回：

```json
{"status":"ok"}
```

说明：内部 CA 没有公网 CRL/OCSP 吊销查询服务，因此 Windows 自带 curl 可能提示：

```text
CRYPT_E_NO_REVOCATION_CHECK
```

测试时使用 `--ssl-no-revoke` 只关闭吊销检查，仍会校验证书链和服务器 IP地址。不要使用 `-k` 或 `--insecure` 作为长期方案，因为它们会跳过关键的证书身份验证。

#### 3.4.7 测试个人 Key

将管理员分配的个人 Key临时放入当前 PowerShell变量：

```powershell
$PersonalKey = "请替换为管理员分配的个人Key"
```

测试模型权限：

```powershell
curl.exe `
  --ssl-no-revoke `
  https://10.12.0.238/v1/models `
  -H "Authorization: Bearer $PersonalKey"
```

正常情况下会返回该 Key被授权使用的模型。不同 Key看到的模型可能不同，这是 LiteLLM模型权限隔离的正常表现。

测试完成后清理临时变量：

```powershell
$PersonalKey = $null
```

如果返回 `401`，说明证书和网络可能已经正常，但 Key缺失、填写错误、已被撤销或没有权限，需要联系管理员检查 Key。

#### 3.4.8 CA更新和删除

管理员更换内部 CA后，用户必须重新领取、核对指纹并导入新证书。不要仅覆盖文件而不核对新指纹。

如需删除旧证书，先列出目标证书：

```powershell
Get-ChildItem Cert:\CurrentUser\Root |
  Where-Object {
    $_.Subject -like "*LLM Gateway Internal CA*"
  } |
  Select-Object Subject, Thumbprint
```

确认 Thumbprint无误后再删除：

```powershell
Remove-Item `
  "Cert:\CurrentUser\Root\请替换为旧证书Thumbprint"
```

不要在没有确认 Thumbprint的情况下批量删除根证书。

---

# 第一部分：Codex 教程

## 4. Codex 简介

Codex 是面向软件开发任务的编码代理，可以读取项目、搜索代码、执行命令、编辑文件并调用工具。

本文配置使用自建模型：

```text
qwen-72b-codex
```

本文已验证的客户端版本为：

```text
OpenAI Codex 0.144.1
```

不同版本的命令行参数可能略有变化，可随时执行：

```powershell
codex.cmd --help
codex.cmd exec --help
```

## 5. 安装 Codex

在 PowerShell 中执行：

```powershell
npm install -g @openai/codex
```

检查安装：

```powershell
codex.cmd --version
```

如果提示找不到 `codex.cmd`，关闭并重新打开 PowerShell，再检查：

```powershell
npm config get prefix
```

确认 npm 全局目录已加入系统 `PATH`。

## 6. 配置 Codex 个人 Key

把管理员分配的 Key 填入当前 PowerShell：

```powershell
$CodexKey = "请替换为管理员分配的个人Key"

$env:LITELLM_API_KEY = $CodexKey

[Environment]::SetEnvironmentVariable(
  "LITELLM_API_KEY",
  $CodexKey,
  "User"
)

$CodexKey = $null
```

关闭并重新打开 PowerShell，确认变量存在但不要打印完整 Key：

```powershell
if ($env:LITELLM_API_KEY) {
  "LITELLM_API_KEY is configured"
} else {
  "LITELLM_API_KEY is missing"
}
```

测试模型权限：

```powershell
curl.exe `
  --ssl-no-revoke `
  https://10.12.0.238/v1/models `
  -H "Authorization: Bearer $env:LITELLM_API_KEY"
```

Codex 专用 Key 正常情况下应至少看到：

```text
qwen-72b-codex
```

## 7. 创建 Codex 配置文件

配置路径：

```text
%USERPROFILE%\.codex\config.toml
```

创建目录：

```powershell
New-Item `
  -ItemType Directory `
  -Path "$HOME\.codex" `
  -Force | Out-Null
```

如果已有配置，先备份：

```powershell
$ConfigFile = "$HOME\.codex\config.toml"

if (Test-Path $ConfigFile) {
  Copy-Item `
    $ConfigFile `
    "$ConfigFile.backup" `
    -Force
}
```

将下面内容保存为 `config.toml`：

```toml
model = "qwen-72b-codex"
model_provider = "litellm"

model_context_window = 32768
model_auto_compact_token_limit = 24000

[model_providers.litellm]
name = "LiteLLM"
base_url = "https://10.12.0.238/v1"
env_key = "LITELLM_API_KEY"
wire_api = "responses"
requires_openai_auth = false

[features]
multi_agent = false
apps = false
```

可直接使用 PowerShell 写入：

```powershell
$ConfigFile = "$HOME\.codex\config.toml"

@'
model = "qwen-72b-codex"
model_provider = "litellm"

model_context_window = 32768
model_auto_compact_token_limit = 24000

[model_providers.litellm]
name = "LiteLLM"
base_url = "https://10.12.0.238/v1"
env_key = "LITELLM_API_KEY"
wire_api = "responses"
requires_openai_auth = false

[features]
multi_agent = false
apps = false
'@ | Set-Content `
  -Path $ConfigFile `
  -Encoding utf8
```

必须关闭 `multi_agent` 和 `apps`，因为当前 VLLM 不支持 Codex Apps 可能发送的 `namespace` 类型工具。

如果以前登录过 OpenAI 官方账号，可退出旧登录，避免后台刷新官方 OAuth Token：

```powershell
codex.cmd logout
```

自建 LiteLLM 通过 `LITELLM_API_KEY` 认证，不需要执行 OpenAI 官方登录。

## 8. 测试 Codex 对话

```powershell
codex.cmd exec `
  --skip-git-repo-check `
  "Reply only CODEX_CONNECTED"
```

预期返回：

```text
CODEX_CONNECTED
```

可能看到：

```text
Model metadata for qwen-72b-codex not found. Defaulting to fallback metadata
```

这是自定义模型未收录在 Codex 内置模型目录中的提示。由于配置中已经显式指定 32768 上下文和 24000 自动压缩阈值，通常不影响使用。

## 9. 使用 Codex 编辑项目

进入项目目录：

```powershell
Set-Location "D:\你的项目目录"
```

启动交互模式，并允许写入当前工作区：

```powershell
codex.cmd `
  -s workspace-write `
  -a on-request
```

示例任务：

```text
检查这个项目的结构，找出启动方式，并说明如何运行测试。
```

```text
创建 hello.txt，内容为 Codex connected，然后读取并确认。
```

当 Codex 请求执行写文件或命令时，确认命令和目录无误后再批准。

## 10. Codex 常见问题

### 10.1 提示无法连接 127.0.0.1:4000

说明仍在使用旧 SSH 隧道配置。检查：

```powershell
Select-String `
  -Path "$HOME\.codex\config.toml" `
  -Pattern "base_url"
```

正确值应为：

```text
https://10.12.0.238/v1
```

### 10.2 返回 401

检查环境变量：

```powershell
if ($env:LITELLM_API_KEY) { "Key exists" } else { "Key missing" }
```

如果 Key 已撤销或使用了其他客户端的受限 Key，请向管理员重新申请。

### 10.3 工具调用报 namespace 不支持

确认配置包含：

```toml
[features]
multi_agent = false
apps = false
```

### 10.4 Codex 只能读文件，不能写文件

检查启动信息中的 sandbox。如果显示 `read-only`，使用：

```powershell
codex.cmd `
  -s workspace-write `
  -a on-request
```

---

# 第二部分：Claude Code 教程

## 11. Claude Code 简介

Claude Code 是终端中的编码代理。本文不连接 Anthropic 官方模型，而是通过 Anthropic Messages 兼容接口使用自建：

```text
qwen-72b
```

## 12. 安装 Claude Code

在 PowerShell 执行：

```powershell
npm install -g @anthropic-ai/claude-code
```

检查：

```powershell
claude.cmd --version
```

如果命令不存在，关闭并重新打开 PowerShell，确认 npm 全局目录已加入 `PATH`。

## 13. 创建 Claude Code 配置

配置文件路径：

```text
%USERPROFILE%\.claude\settings.json
```

创建目录：

```powershell
New-Item `
  -ItemType Directory `
  -Path "$HOME\.claude" `
  -Force | Out-Null
```

备份旧配置：

```powershell
$SettingsFile = "$HOME\.claude\settings.json"

if (Test-Path $SettingsFile) {
  Copy-Item `
    $SettingsFile `
    "$SettingsFile.backup" `
    -Force
}
```

将管理员分发的个人 Key 填入下面配置：

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "请替换为管理员分配的个人Key",
    "ANTHROPIC_BASE_URL": "https://10.12.0.238",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "qwen-72b",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "qwen-72b",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "qwen-72b",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"
  },
  "includeCoAuthoredBy": false,
  "model": "qwen-72b",
  "maxTokens": 4096
}
```

特别注意：

- Claude Base URL 不要在结尾添加 `/v1`；
- 必须设置 `CLAUDE_CODE_MAX_OUTPUT_TOKENS=4096`；
- 否则 Claude Code 可能请求 32000 输出 Tokens，与 32768 上下文叠加后导致 400 错误。

## 14. 测试 Claude Code

关闭所有旧 Claude Code 进程：

```powershell
Get-Process claude -ErrorAction SilentlyContinue |
  Stop-Process -Force
```

重新打开 PowerShell，检查 CA：

```powershell
$env:NODE_EXTRA_CA_CERTS
```

测试：

```powershell
claude.cmd -p "Output exactly CLAUDE_CONNECTED and nothing else."
```

预期：

```text
CLAUDE_CONNECTED
```

进入项目并启动交互模式：

```powershell
Set-Location "D:\你的项目目录"
claude.cmd
```

## 15. Claude Code 常见问题

### 15.1 ContextWindowExceededError，requested 32000 output tokens

确认当前终端：

```powershell
$env:CLAUDE_CODE_MAX_OUTPUT_TOKENS
```

如未设置：

```powershell
$env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "4096"

[Environment]::SetEnvironmentVariable(
  "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
  "4096",
  "User"
)
```

同时确认 `settings.json` 中存在：

```json
"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"
```

### 15.2 证书或 unknown issuer 错误

检查：

```powershell
$env:NODE_EXTRA_CA_CERTS
Test-Path $env:NODE_EXTRA_CA_CERTS
```

修改 `NODE_EXTRA_CA_CERTS` 后必须关闭旧终端并重新打开。

### 15.3 返回 401

检查 `ANTHROPIC_AUTH_TOKEN` 是否为管理员分发的个人 Key。不要填写 `dummy`，也不要填写 LiteLLM Master Key。

### 15.4 输出没有严格遵循指定文本

这通常属于 Qwen 模型的指令遵循差异。只要能够返回模型内容，连接、证书和认证通常已经正常。

---

# 第三部分：OpenClaw（小龙虾）教程

## 16. OpenClaw 简介

OpenClaw 可配置 OpenAI 兼容 Provider，并通过网关使用：

```text
qwen-72b
```

本文已验证版本：

```text
OpenClaw 2026.6.11
```

## 17. 安装 OpenClaw

在 PowerShell 执行：

```powershell
npm install -g openclaw@latest
```

检查安装：

```powershell
openclaw.cmd --version
```

查看可用命令：

```powershell
openclaw.cmd --help
```

首次运行可根据当前版本的引导创建基础配置。配置文件通常位于：

```text
%USERPROFILE%\.openclaw\openclaw.json
```

## 18. 配置内部 CA

OpenClaw 运行于 Node.js，必须完成公共准备工作中的：

```powershell
[Environment]::SetEnvironmentVariable(
  "NODE_EXTRA_CA_CERTS",
  "$HOME\.certs\litellm-ca.crt",
  "User"
)
```

关闭旧终端，重新打开后检查：

```powershell
$env:NODE_EXTRA_CA_CERTS
```

## 19. 配置 OpenClaw Provider

先备份：

```powershell
$ConfigFile = "$HOME\.openclaw\openclaw.json"

Copy-Item `
  $ConfigFile `
  "$ConfigFile.backup" `
  -Force
```

如果 OpenClaw 已通过初始化向导创建了 `models.providers`，可使用以下脚本添加校内 Provider。

先填写个人 Key：

```powershell
$OpenClawKey = "请替换为管理员分配的个人Key"
```

加载配置：

```powershell
$Config = Get-Content $ConfigFile -Raw |
  ConvertFrom-Json
```

创建 Provider：

```powershell
$CampusProvider = [PSCustomObject]@{
  baseUrl = "https://10.12.0.238/v1"
  apiKey  = $OpenClawKey
  api     = "openai-completions"
  models  = @(
    [PSCustomObject]@{
      id            = "qwen-72b"
      name          = "Qwen 72B Campus VLLM"
      reasoning     = $false
      input         = @("text")
      contextWindow = 32768
      maxTokens     = 4096
      cost          = [PSCustomObject]@{
        input      = 0
        output     = 0
        cacheRead  = 0
        cacheWrite = 0
      }
    }
  )
}

$Config.models.providers |
  Add-Member `
    -MemberType NoteProperty `
    -Name "campus-vllm" `
    -Value $CampusProvider `
    -Force
```

设置默认模型：

```powershell
$Config.agents.defaults.model.primary = "campus-vllm/qwen-72b"

$Config.agents.defaults.models |
  Add-Member `
    -MemberType NoteProperty `
    -Name "campus-vllm/qwen-72b" `
    -Value ([PSCustomObject]@{
      alias = "campus-qwen"
    }) `
    -Force
```

保存：

```powershell
$Config |
  ConvertTo-Json -Depth 100 |
  Set-Content `
    -Path $ConfigFile `
    -Encoding utf8

$OpenClawKey = $null
```

如果配置中没有 `models.providers` 或 `agents.defaults`，应先运行 OpenClaw 首次配置向导，不要直接覆盖整个 JSON 文件。

## 20. 重启并验证 OpenClaw

```powershell
openclaw.cmd gateway restart
```

检查模型状态：

```powershell
openclaw.cmd models status
```

应看到类似：

```text
Default: campus-vllm/qwen-72b
BaseUrl: https://10.12.0.238/v1
```

发送真实请求：

```powershell
openclaw.cmd agent `
  --agent main `
  --message "Output exactly OPENCLAW_CONNECTED and nothing else."
```

预期：

```text
OPENCLAW_CONNECTED
```

如果提示没有目标 Session：

```text
No target session selected
```

需要添加：

```powershell
--agent main
```

## 21. 修改已有的直连 VLLM 配置

如果旧 Provider 使用：

```text
http://10.12.3.9:8000/v1
```

可动态查找并修改：

```powershell
$ConfigFile = "$HOME\.openclaw\openclaw.json"
$Config = Get-Content $ConfigFile -Raw | ConvertFrom-Json

$TargetProvider = $Config.models.providers.PSObject.Properties |
  Where-Object {
    $_.Value.baseUrl -eq "http://10.12.3.9:8000/v1"
  } |
  Select-Object -First 1

if (-not $TargetProvider) {
  throw "Could not find the old VLLM provider"
}

$TargetProvider.Value.baseUrl = "https://10.12.0.238/v1"

$TargetProvider.Value |
  Add-Member `
    -MemberType NoteProperty `
    -Name apiKey `
    -Value "请替换为管理员分配的个人Key" `
    -Force

$Config |
  ConvertTo-Json -Depth 100 |
  Set-Content -Path $ConfigFile -Encoding utf8

openclaw.cmd gateway restart
```

## 22. OpenClaw 常见问题

### 22.1 models status 仍显示旧地址

检查运行时模型缓存：

```powershell
$ModelsFile = "$HOME\.openclaw\agents\main\agent\models.json"
$Models = Get-Content $ModelsFile -Raw | ConvertFrom-Json

$Models.providers.PSObject.Properties |
  ForEach-Object {
    [PSCustomObject]@{
      Provider = $_.Name
      BaseUrl  = $_.Value.baseUrl
      Models   = ($_.Value.models.id -join ", ")
      HasKey   = -not [string]::IsNullOrWhiteSpace($_.Value.apiKey)
    }
  } |
  Format-Table -AutoSize
```

修改 `openclaw.json` 后执行：

```powershell
openclaw.cmd gateway restart
```

### 22.2 No target session selected

使用：

```powershell
openclaw.cmd agent `
  --agent main `
  --message "你好"
```

### 22.3 插件自动加载警告

如果出现：

```text
plugins.allow is empty
```

表示发现了第三方插件但未显式设置信任列表。检查：

```powershell
openclaw.cmd plugins list --enabled --verbose
```

只允许确实信任的插件，不要盲目允许未知插件。

### 22.4 401 或模型不可用

确认个人 Key是否允许 `qwen-72b`。管理员可为不同 Key设置不同模型权限，因此并非所有 Key都能访问所有模型。

---

# 第四部分：统一验收与安全说明

## 23. 三个客户端的快速验收命令

### Codex

```powershell
codex.cmd exec `
  --skip-git-repo-check `
  "Reply only CODEX_OK"
```

### Claude Code

```powershell
claude.cmd -p "Output exactly CLAUDE_OK and nothing else."
```

### OpenClaw

```powershell
openclaw.cmd agent `
  --agent main `
  --message "Output exactly OPENCLAW_OK and nothing else."
```

只要能够返回模型内容，就表示以下环节正常：

1. 客户端安装；
2. 内部 CA 信任；
3. HTTPS Nginx访问；
4. LiteLLM Key认证；
5. 模型权限；
6. LiteLLM协议转换；
7. VLLM推理。

## 24. Key使用规范

1. 每人使用独立 Key，不要多人共享。
2. 不要把 Key写入公开代码仓库、聊天群或截图。
3. 不要把 Key提交到 Git。
4. Key泄漏后立即联系管理员撤销并重新生成。
5. 管理员可按 Key查询 Token用量、设置 RPM/TPM和模型权限。
6. 用户不得使用 LiteLLM Master Key。

## 25. 证书安全规范

普通用户只需要：

```text
litellm-ca.crt
```

不得向客户端分发：

```text
litellm-ca.key
litellm-server.key
```

CA证书本身不是秘密，但应通过可信渠道分发，并核对文件来源。

## 26. 网络说明

正常情况下只访问：

```text
https://10.12.0.238
```

无需：

- 打开 SSH隧道；
- 连接 Flask 8081；
- 直连 LiteLLM 8080；
- 直连 VLLM 8000。

如果 HTTPS 网关无法访问，先测试：

```powershell
Test-NetConnection 10.12.0.238 -Port 443
```

然后测试：

```powershell
curl.exe `
  --ssl-no-revoke `
  https://10.12.0.238/healthz
```

## 27. 管理员需要提供给用户的标准信息模板

```text
服务名称：校内 Qwen 72B 推理服务
HTTPS 网关：https://10.12.0.238
OpenAI Base URL：https://10.12.0.238/v1
Claude Base URL：https://10.12.0.238
普通模型：qwen-72b
Codex模型：qwen-72b-codex
个人API Key：单独发送
CA证书：litellm-ca.crt
最大上下文：32768
建议最大输出：4096
```

## 28. 已验证的软件与服务版本

本文部署环境已验证：

| 组件 | 已验证版本 |
|---|---|
| Codex | 0.144.1 |
| OpenClaw | 2026.6.11 |
| LiteLLM | 1.91.0 |
| PostgreSQL | 16.14 |
| Nginx | 1.20.1 |
| Claude Code | 2.1.205 |
| VLLM | 0.24.0 OpenAI兼容服务，模型上下文32768 |

升级客户端后，如果命令行参数或配置格式发生变化，应先执行对应客户端的 `--help`，并在一台测试电脑完成验收后再批量更新。
