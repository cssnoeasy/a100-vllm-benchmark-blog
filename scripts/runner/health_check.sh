#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8000}"
URL="http://127.0.0.1:${PORT}/v1/models"

echo "Checking vLLM server: ${URL}"

for i in $(seq 1 60); do
  if curl -fsS "${URL}" > /tmp/vllm_models.json; then
    echo "vLLM server is ready."
    python -m json.tool /tmp/vllm_models.json
    exit 0
  fi

  echo "Waiting for server... ${i}/60"
  sleep 5
done

echo "vLLM server is not ready after 300 seconds."
exit 1
