#!/usr/bin/env python3
import json
import sys
import time
import urllib.error
import urllib.request

def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: scripts/wait_vllm_ready.py <port> [timeout_seconds]")

    port = int(sys.argv[1])
    timeout = int(sys.argv[2]) if len(sys.argv) >= 3 else 300
    url = f"http://127.0.0.1:{port}/v1/models"
    deadline = time.time() + timeout
    last_error = ""

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body)
                model_ids = [item.get("id", "") for item in data.get("data", [])]
                print("vLLM ready")
                print("models:", ", ".join(model_ids))
                return
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(2)

    raise SystemExit(f"vLLM readiness timeout after {timeout}s: {last_error}")

if __name__ == "__main__":
    main()
