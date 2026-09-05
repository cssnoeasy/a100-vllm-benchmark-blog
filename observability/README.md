# 可观测性资产

本目录保存 vLLM 服务的可复现监控资产。它是经过脱敏的配置副本，用于审查、学习和在受控环境中复现；修改本目录中的文件不会自动修改系统正在使用的配置。

## 目录结构

- `prometheus/prometheus.yml`：已验证的 Prometheus 配置副本。
- `grafana/`：Grafana Dashboard JSON。
- `exporters/week6_gpu_system_exporter.py`：轻量 GPU 与主机指标 exporter。
- `exporters/week6-gpu-system-exporter.service`：用于运行 exporter 的 systemd unit。

## 运行边界

参考部署中，vLLM、Prometheus、Grafana 和 GPU/System exporter 均只绑定回环地址：

| 服务 | 本地地址 |
| --- | --- |
| vLLM | `127.0.0.1:8000` |
| Prometheus | `127.0.0.1:9090` |
| Grafana | `127.0.0.1:3000` |
| GPU/System exporter | `127.0.0.1:9400` |

Prometheus 与 Grafana 的运行数据位于系统运行目录，不进入本仓库。需要远程查看时，应使用组织批准的私有访问渠道；不要在公开仓库记录主机名、账号、SSH 命令、隧道地址或端口映射。

## 静态检查

在受控服务器的项目根目录执行：

```bash
promtool check config observability/prometheus/prometheus.yml
python -m py_compile observability/exporters/week6_gpu_system_exporter.py
systemctl is-active prometheus grafana-server week6-gpu-system-exporter
curl -fsS http://127.0.0.1:9400/metrics
curl -fsS http://127.0.0.1:9090/-/ready
```

这些命令只能证明配置语法、exporter 和本地服务状态。关于受控过载、进程退出与恢复的实际实验结论，请阅读 [第 6 周过载与恢复记录](../docs/reports/04_Week6阶段B_PrometheusGrafana过载与恢复记录.md) 和 [故障复盘](../incident-reviews/overload-and-process-recovery.md)。
