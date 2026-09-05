#!/usr/bin/env python3
import argparse
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def run_cmd(command):
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def parse_float(value):
    value = value.strip()
    if value in {"", "[Not Supported]", "N/A"}:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def read_gpu_metrics():
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu,clocks.sm",
        "--format=csv,noheader,nounits",
    ]
    result = run_cmd(command)
    lines = []
    if result.returncode != 0:
        lines.append("# HELP week6_gpu_exporter_up Whether nvidia-smi query succeeded.")
        lines.append("# TYPE week6_gpu_exporter_up gauge")
        lines.append("week6_gpu_exporter_up 0")
        return lines

    lines.append("# HELP week6_gpu_exporter_up Whether nvidia-smi query succeeded.")
    lines.append("# TYPE week6_gpu_exporter_up gauge")
    lines.append("week6_gpu_exporter_up 1")

    specs = [
        ("week6_gpu_utilization_percent", "GPU utilization percent."),
        ("week6_gpu_memory_used_mib", "GPU memory used in MiB."),
        ("week6_gpu_memory_total_mib", "GPU memory total in MiB."),
        ("week6_gpu_power_watts", "GPU power draw in watts."),
        ("week6_gpu_temperature_celsius", "GPU temperature in Celsius."),
        ("week6_gpu_sm_clock_mhz", "GPU SM clock in MHz."),
    ]
    for name, help_text in specs:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")

    for raw in result.stdout.splitlines():
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) < 8:
            continue

        index, gpu_name = parts[0], parts[1]
        labels = f'gpu="{index}",name="{gpu_name}"'
        util = parse_float(parts[2])
        mem_used = parse_float(parts[3])
        mem_total = parse_float(parts[4])
        power = parse_float(parts[5])
        temp = parse_float(parts[6])
        clock = parse_float(parts[7])

        lines.append(f"week6_gpu_utilization_percent{{{labels}}} {util}")
        lines.append(f"week6_gpu_memory_used_mib{{{labels}}} {mem_used}")
        lines.append(f"week6_gpu_memory_total_mib{{{labels}}} {mem_total}")
        lines.append(f"week6_gpu_power_watts{{{labels}}} {power}")
        lines.append(f"week6_gpu_temperature_celsius{{{labels}}} {temp}")
        lines.append(f"week6_gpu_sm_clock_mhz{{{labels}}} {clock}")

    return lines


def read_system_metrics():
    lines = [
        "# HELP week6_system_exporter_up Whether system metrics were collected.",
        "# TYPE week6_system_exporter_up gauge",
        "week6_system_exporter_up 1",
    ]

    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as file:
            load1, load5, load15 = file.read().split()[:3]
        lines.extend([
            "# HELP week6_system_load1 System 1 minute load average.",
            "# TYPE week6_system_load1 gauge",
            f"week6_system_load1 {parse_float(load1)}",
            "# HELP week6_system_load5 System 5 minute load average.",
            "# TYPE week6_system_load5 gauge",
            f"week6_system_load5 {parse_float(load5)}",
            "# HELP week6_system_load15 System 15 minute load average.",
            "# TYPE week6_system_load15 gauge",
            f"week6_system_load15 {parse_float(load15)}",
        ])
    except OSError:
        pass

    mem = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as file:
            for line in file:
                key, value = line.split(":", 1)
                mem[key] = parse_float(value.replace("kB", "")) * 1024
        total = mem.get("MemTotal", 0.0)
        available = mem.get("MemAvailable", 0.0)
        used = max(total - available, 0.0)
        lines.extend([
            "# HELP week6_system_memory_total_bytes System memory total bytes.",
            "# TYPE week6_system_memory_total_bytes gauge",
            f"week6_system_memory_total_bytes {total}",
            "# HELP week6_system_memory_available_bytes System memory available bytes.",
            "# TYPE week6_system_memory_available_bytes gauge",
            f"week6_system_memory_available_bytes {available}",
            "# HELP week6_system_memory_used_bytes System memory used bytes.",
            "# TYPE week6_system_memory_used_bytes gauge",
            f"week6_system_memory_used_bytes {used}",
        ])
    except OSError:
        pass

    return lines


def render_metrics():
    lines = []
    lines.extend(read_gpu_metrics())
    lines.extend(read_system_metrics())
    lines.append(f"week6_exporter_scrape_timestamp_seconds {time.time()}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/metrics", "/"}:
            self.send_response(404)
            self.end_headers()
            return

        body = render_metrics().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9400)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"week6 gpu/system exporter listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
