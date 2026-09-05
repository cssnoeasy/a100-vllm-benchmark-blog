---
---

# Week6 阶段 B 操作手册：Prometheus、Grafana、过载与进程恢复

本文面向受控 Linux GPU 实验环境中的实际操作，不适用于 Windows 本地目录直接执行。公开仓库不记录远程主机、账户或远程访问命令；先通过组织批准的安全访问方式进入目标环境，再在项目根目录执行以下检查。涉及进程退出的步骤有破坏性，只在确认没有业务流量时执行。

## 1. 日常健康检查

目标环境：

```bash
cd <PROJECT_ROOT>
conda activate vllm_env

systemctl is-active prometheus grafana-server week6-gpu-system-exporter
ss -ltnp | grep -E ':8000|:9090|:3000|:9400' || true
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:8000/v1/models | grep -o 'Qwen2.5-7B-Instruct' | head -n 1
curl -fsS http://127.0.0.1:9400/metrics | grep -m 1 'week6_gpu_exporter_up 1'
```

预期三个 systemd 服务均为 `active`，Prometheus ready，模型名存在，exporter up 为 1。

## 2. 查看 Grafana

通过组织批准的安全访问方式打开 Grafana；不要把管理端口暴露到公网，也不要把访问命令、地址或账户信息写入本仓库。

在已获授权的浏览器会话中打开本地映射地址：

```text
http://127.0.0.1:3000
http://127.0.0.1:9090
```

本方案不需要将 3000/9090 暴露到外网。浏览器里的本地地址通过组织批准的安全访问通道映射到实验机。

## 3. 检查 Prometheus Targets

```bash
curl -s http://127.0.0.1:9090/api/v1/targets |
python -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(sorted({x["labels"].get("job","")+" "+x["health"] for x in d["data"]["activeTargets"]})))'
```

预期：

```text
node up
prometheus up
vllm up
week6_gpu_system up
```

若 `vllm down`，先检查 8000 和 vLLM 日志；若 `week6_gpu_system down`，检查 9400 与 exporter 服务。

## 4. 配置变更流程

仓库配置：`observability/prometheus/prometheus.yml`。活动配置：受控 Linux 环境的 `/etc/prometheus/prometheus.yml`。`/etc`、`/var/lib`、systemd 与 `promtool` 均属于运行环境，不是仓库目录；修改仓库文件不会自动生效。

安全流程：

```bash
cd <PROJECT_ROOT>
promtool check config observability/prometheus/prometheus.yml

sudo cp /etc/prometheus/prometheus.yml \
  /etc/prometheus/prometheus.yml.bak.$(date +%Y%m%d-%H%M%S)
sudo cp observability/prometheus/prometheus.yml /etc/prometheus/prometheus.yml
sudo promtool check config /etc/prometheus/prometheus.yml
sudo systemctl restart prometheus
systemctl is-active prometheus
curl -fsS http://127.0.0.1:9090/-/ready
```

不要删除 `/var/lib/prometheus` 或 `/var/lib/grafana`，它们是运行数据，不是项目垃圾。

## 5. Exporter 管理

正式源码：

```text
observability/exporters/week6_gpu_system_exporter.py
```

检查和重启：

```bash
python -m py_compile observability/exporters/week6_gpu_system_exporter.py
sudo systemctl restart week6-gpu-system-exporter
systemctl status week6-gpu-system-exporter --no-pager
curl -fsS http://127.0.0.1:9400/metrics | head -n 40
```

安装/更新 unit：

```bash
sudo cp observability/exporters/week6-gpu-system-exporter.service \
  /etc/systemd/system/week6-gpu-system-exporter.service
sudo systemctl daemon-reload
sudo systemctl enable --now week6-gpu-system-exporter
```

排障：

```bash
journalctl -u week6-gpu-system-exporter -n 100 --no-pager
ss -ltnp | grep ':9400' || true
nvidia-smi
```

## 6. 导入最终 Grafana Dashboard

公开仓库文件：

```text
<PROJECT_ROOT>/observability/grafana/vllm_engineered_dashboard.json
```

Windows 下载：

```powershell
$dest = "$env:USERPROFILE\Downloads\Week6-Grafana"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
# 通过组织批准的文件传输工具获取 Dashboard JSON，不在公开文档中记录主机或账号。
```

Grafana 中选择 `Dashboards -> New -> Import`，上传 JSON，`DS_PROMETHEUS` 选择现有 Prometheus。UID 冲突时覆盖现有 Dashboard。

最终应看到 `P99 TTFT`、`P99 TPOT`、同时包含 running/waiting 的面板，以及单条聚合后的 success。

## 7. 常用 PromQL

```promql
up{job="vllm"}
sum(rate(vllm:prompt_tokens_total{job="vllm"}[5m]))
sum(rate(vllm:generation_tokens_total{job="vllm"}[5m]))
sum(vllm:num_requests_running{job="vllm"})
sum(vllm:num_requests_waiting{job="vllm"})
vllm:kv_cache_usage_perc{job="vllm"} * 100
rate(vllm:num_preemptions_total{job="vllm"}[5m])
histogram_quantile(0.99, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket{job="vllm"}[5m]))) * 1000
histogram_quantile(0.99, sum by (le) (rate(vllm:request_time_per_output_token_seconds_bucket{job="vllm"}[5m]))) * 1000
week6_gpu_utilization_percent{job="week6_gpu_system"}
week6_gpu_memory_used_mib{job="week6_gpu_system"}
week6_gpu_power_watts{job="week6_gpu_system"}
```

无请求时 P99 可能为 `NaN/No data`。先产生流量并等待至少一个 15 秒 scrape 周期。

## 8. 运行受控过载探针

确认服务健康且没有其他压测：

```bash
cd <PROJECT_ROOT>
conda activate vllm_env
ps -ef | grep 'vllm serve' | grep -v grep
nvidia-smi
python scripts/runner/run_overload_probe.py
```

脚本依次执行 C=8 基线、C=48 高并发、C=8 恢复。原工程快照中的结果曾写入以下路径；当前公开副本未迁入该目录，实际重新运行时应以脚本生成的位置为准。查看：

```bash
cat results/week6/overload_probe/overload_summary.md
```

成功标准：三轮 `failed=0`；高并发时 running、GPU、KV Cache 或延迟明显上升；最终 `up=1` 且 running/waiting 回落；恢复轮指标接近基线。

这是压力探针，不是 OOM 注入。不要在存在真实业务请求时执行。

## 9. 运行进程退出/恢复探针

警告：脚本会向当前 `vllm serve` 发送 SIGTERM，并自行重启服务。先确认启动参数与脚本一致，且没有业务流量。

```bash
cd <PROJECT_ROOT>
conda activate vllm_env
python scripts/runner/run_process_exit_recovery_probe.py
```

原工程快照中的结果曾写入以下路径；当前公开副本未迁入该目录，实际重新运行时应以脚本生成的位置为准。查看证据：

```bash
cat results/week6/process_exit_recovery/process_exit_recovery_summary.md
cat results/week6/process_exit_recovery/post_recovery_smoke/result.json
ps -ef | grep 'vllm serve' | grep -v grep
curl -fsS http://127.0.0.1:8000/v1/models
```

成功标准：`down_detected=True`、`up_detected=True`、`model_endpoint_ok=True`、smoke return code 0、16 completed、0 failed。

## 10. 常见故障处理

### Grafana 显示 `No data`

依次检查：时间范围是否覆盖压测；数据源是否选 Prometheus；Prometheus query 是否有结果；target 是否 up；指标名是否真实存在。不要先改可视化样式。

### P99 为 NaN

最近 5 分钟 histogram 没有增长。发一轮请求，等待 15 至 30 秒。若仍为 NaN，直接查询 `_bucket` 指标确认是否采集。

### Prometheus 配置重启失败

先 `promtool check config`，查看 `journalctl -u prometheus -n 100 --no-pager`，必要时恢复最近备份后重启。

### Exporter up 但 GPU 指标缺失

直接访问 9400，检查 `nvidia-smi` 是否成功、systemd 用户是否有权限，再看 exporter journal。

### 进程重启后端口有了但请求失败

端口监听不等于模型 ready。检查 `/v1/models`，再做真实 smoke request；同时查看模型加载日志和 GPU 显存是否恢复到常驻水平。

## 11. 截图流程

时间范围设为 `Last 15 minutes`，刷新设为 `5s`。在探针运行时观察，结束后等待一个 scrape 周期。正式截图建议使用：

```text
Week6_Grafana_vLLM工程化可观测性总览_最终版.png
Week6_Grafana_vLLM持续过载压力观测.png
Week6_Grafana_vLLM进程退出与恢复观测.png
```

## 12. 最终静态验收

```bash
cd <PROJECT_ROOT>
python -m compileall -q scripts observability/exporters
for file in scripts/*.sh; do bash -n "$file" || exit 1; done
python -m json.tool observability/grafana/vllm_engineered_dashboard.json >/dev/null
promtool check config observability/prometheus/prometheus.yml
find . -type d -name '__pycache__' -print -exec rm -rf -- {} +
```

本手册记录的是第六周阶段 B 的操作范围；截至该阶段结束时，至少 60 分钟真实业务长稳尚未执行。后续第七周已另行完成 [60 分钟离线巡检文本推理回放](inspection-replay-60m.html)，它不包含真实业务链路，也不改变本手册中 Smoke、overload 和 recovery 的证据范围。后续若执行真实业务长稳，应另建结果目录和报告，不覆盖本周证据。
