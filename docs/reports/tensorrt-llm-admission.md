# Qwen3-32B TensorRT-LLM 基础验证项目

本项目记录在 A100 单卡上使用 TensorRT-LLM 1.2.1 部署 Qwen3-32B BF16 的基础验证。TensorRT-LLM 1.2.1 是同一套推理框架：它既提供直接加载 Hugging Face 权重的 PyTorch backend，也提供经 checkpoint 转换和构建后运行 TensorRT engine 的路径。本次实际验证的是前者；它证明了框架服务、接口与基础性能可用，但不等同于已构建或执行 TensorRT engine。

## 项目结构

```text
TensorRT-LLM/
|-- assets/       博客截图
|-- configs/      环境和实验配置
|-- docs/         过程记录、面试摘要、完整实验记录
|-- logs/         从服务器下载的服务日志和压测日志
|-- manifests/   下载清单、文件 hash、归档说明
|-- results/
|   |-- raw/trtllm/  TensorRT-LLM 每轮原始结果
|   |-- raw/vllm/    vLLM 每轮原始结果
|   `-- summary/     汇总表和对比结果
|-- scripts/      从服务器下载的 benchmark 脚本
`-- README.md
```

## 三份主文档

- `docs/TensorRT-LLM实验_过程记录.md`：环境、结果、故障和后续验证项。
- `docs/TensorRT-LLM实验_面试表达版.md`：一句话、简历、STAR、追问、博客截图规划。
- `docs/TensorRT-LLM实验_完整实验过程记录.md`：命令、排障、测试口径、逐轮数据和结论。

## 已完成数据

正式负载：`512 input / 256 output / c16 / 96 requests / 3 repeats / warmup 8`。TensorRT-LLM 三轮输出吞吐均值 `317.85 tok/s`，vLLM 三轮均值 `313.92 tok/s`，六轮全部 `validated`、无失败。

## 归档结果结构

原始结果、日志和脚本已按公开项目的目录规范整理。当前原始产物路径如下：

```text
results/raw/trtllm/  TensorRT-LLM 三轮原始结果
results/raw/vllm/    vLLM 三轮原始结果
results/summary/     两份汇总 CSV 和汇总说明
logs/                六份压测日志
scripts/             benchmark_openai_stream.py
manifests/           下载文件 SHA256 清单
```

## 实验数据归档说明

原始结果、日志、脚本和 manifests 已按 `SOURCE_MANIFEST.md` 的范围归档到本项目。公开文档不记录服务器地址、账号、内部目录或文件传输命令；如需重新归档，请使用组织批准的安全传输流程，并只下载结果、日志、脚本和 manifests，不下载模型权重。

重新归档时应在本地生成 SHA256 清单，并将清单与结果一起保存；模型权重、凭据、原始请求内容和内部网络信息不进入公开仓库。

## 证据截图复现命令

以下命令在服务器上执行，生成终端证据文本；你在终端窗口中截取对应画面，并将 PNG 按下面的文件名保存到本地 `assets`。终端宽度建议至少 140 列，截图只保留关键输出，不要把 `.txt` 当作 PNG。

### 1. 环境与 GPU

截图文件名：`01_environment_gpu_a100.png`；证据文本：`01_environment_gpu_a100.txt`

```bash
cd <PROJECT_ROOT>
conda activate trtllm_env
NVIDIA_SITE="$CONDA_PREFIX/lib/python3.12/site-packages/nvidia"
export LD_LIBRARY_PATH="$NVIDIA_SITE/cuda_runtime/lib:$NVIDIA_SITE/cublas/lib:$NVIDIA_SITE/cu13/lib:$CONDA_PREFIX/lib"
{ date -Is; nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv; python - <<'PY'
import torch, tensorrt as trt, tensorrt_llm
print('torch', torch.__version__)
print('torch_cuda', torch.version.cuda)
print('tensorrt', trt.__version__)
print('tensorrt_llm', tensorrt_llm.__version__)
PY
} | tee /tmp/01_environment_gpu_a100.txt
```

### 2. 服务健康与模型 ID

截图文件名：`02_service_health_and_model.png`；证据文本：`02_service_health_and_model.txt`

```bash
{ curl -sS http://127.0.0.1:8001/health; echo; curl -sS http://127.0.0.1:8001/v1/models; echo; } | tee /tmp/02_service_health_and_model.txt
```

### 3. 最小生成请求

截图文件名：`03_minimal_completion_success.png`；证据文本：`03_minimal_completion_success.txt`

```bash
curl -sS http://127.0.0.1:8001/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-32B","prompt":"A100 是","max_tokens":32,"temperature":0,"stream":false}' | tee /tmp/03_minimal_completion_success.txt
```

### 4. TensorRT-LLM 三轮汇总

截图文件名：`04_trtllm_three_round_summary.png`；证据文本：`04_trtllm_three_round_summary.txt`

```bash
for file in <PROJECT_ROOT>/results/a100_trtllm_pytorch_bf16_tp1_short_r*/benchmark.json; do echo "===== $file ====="; python -m json.tool "$file"; done | tee /tmp/04_trtllm_three_round_summary.txt
```

### 5. vLLM 三轮参考汇总

截图文件名：`05_vllm_three_round_summary.png`；证据文本：`05_vllm_three_round_summary.txt`

```bash
for file in <PROJECT_ROOT>/results/a100_vllm_bf16_tp1_short_r*/benchmark.json; do echo "===== $file ====="; python -m json.tool "$file"; done | tee /tmp/05_vllm_three_round_summary.txt
```

### 6. 对比结论与归档目录

截图文件名：`06_final_comparison_and_archive.png`；证据文本：`06_final_comparison_and_archive.txt`

```bash
cat <<'EOF' | tee /tmp/06_final_comparison_and_archive.txt
Qwen3-32B BF16 | A100 GPU0 | TP=1 | 512/256 | c16 | 96 requests | 3 轮
TensorRT-LLM PyTorch 平均输出吞吐：317.85 tok/s
vLLM 平均输出吞吐：313.92 tok/s
结论：在本固定负载下，两者基础性能基本持平；TensorRT-LLM 高约 1.3%。
边界：本次使用 PyTorch backend，并非序列化后的 TensorRT engine。

原始结果目录：
  <PROJECT_ROOT>/results/a100_trtllm_pytorch_bf16_tp1_short_r*
  <PROJECT_ROOT>/results/a100_vllm_bf16_tp1_short_r*
EOF
```

公开展示时可优先使用 `01`、`02`、`03`、`04`、`06` 五张；`05` 是 vLLM 对照。`.txt` 是截图前的可复制证据，不替代 PNG。

## 当前结论

TensorRT-LLM 1.2.1 已在 A100 上成功服务 Qwen3-32B BF16，并完成基础性能数据采集。当前走的是该框架的 PyTorch backend 路径，短负载下与 vLLM 基本持平；TensorRT engine 路径尚未构建。不要将本项目当前结果称为 TensorRT engine 性能，也不要从约 1% 的差距推导全面框架优劣。
