#!/usr/bin/env python3
import csv
import html
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(".")
MATRIX_PATH = ROOT / "results/week5/week5_matrix.csv"
QUALITY_GATE_PATH = ROOT / "results/week5/week5_quality_gate.md"
RUNS_DIR = ROOT / "results/runs"
OUT_DIR = ROOT / "results/dashboard"
OUT_HTML = OUT_DIR / "index.html"


def read_csv_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_quality_gate(path: Path) -> dict:
    if not path.exists():
        return {}

    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("| "):
            continue
        if "----" in line or "Run ID" in line:
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 7:
            continue

        result, run_id, compared_with, output_tps, baseline_tps, threshold_tps, note = cells[:7]
        rows[run_id] = {
            "result": result,
            "compared_with": compared_with,
            "output_throughput": to_float(output_tps),
            "baseline_throughput": to_float(baseline_tps),
            "threshold_throughput": to_float(threshold_tps),
            "note": note,
        }
    return rows


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def parse_diagnosis_category(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"## Classification\s+\*\*([^*]+)\*\*", text, re.S)
    if match:
        return match.group(1).replace("\\_", "_").strip()
    return ""


def summarize_gpu(path: Path) -> dict:
    if not path.exists():
        return {}

    samples = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append(row)

    if not samples:
        return {}

    util = [to_float(r.get("utilization_gpu"), 0) for r in samples]
    mem = [to_float(r.get("memory_used_mb"), 0) for r in samples]
    power = [to_float(r.get("power_draw_w"), 0) for r in samples]
    temp = [to_float(r.get("temperature_gpu"), 0) for r in samples]

    return {
        "samples": len(samples),
        "avg_utilization_gpu": round(sum(util) / len(util), 2),
        "max_utilization_gpu": round(max(util), 2),
        "max_memory_used_mb": round(max(mem), 2),
        "avg_power_draw_w": round(sum(power) / len(power), 2),
        "max_temperature_gpu": round(max(temp), 2),
    }


def build_data() -> dict:
    matrix_rows = read_csv_rows(MATRIX_PATH)
    quality = parse_quality_gate(QUALITY_GATE_PATH)

    runs = []
    for row in matrix_rows:
        run_id = row["run_id"]
        run_dir = RUNS_DIR / run_id
        manifest = load_json(run_dir / "manifest.json")
        diagnosis_category = parse_diagnosis_category(run_dir / "diagnosis.md")
        gpu_summary = summarize_gpu(run_dir / "gpu_metrics.csv")

        qg = quality.get(run_id, {})
        run = dict(row)
        run.update({
            "quality_gate": qg.get("result", ""),
            "quality_compared_with": qg.get("compared_with", ""),
            "quality_baseline_throughput": qg.get("baseline_throughput"),
            "quality_threshold_throughput": qg.get("threshold_throughput"),
            "quality_note": qg.get("note", ""),
            "failed_stage": manifest.get("failed_stage", ""),
            "diagnosis_category": diagnosis_category,
            "has_environment": (run_dir / "environment.txt").exists(),
            "has_diagnosis": (run_dir / "diagnosis.md").exists(),
            "gpu_summary": gpu_summary,
        })
        runs.append(run)

    status_counts = Counter(r["status"] for r in runs)
    qg_counts = Counter(r["quality_gate"] or "NONE" for r in runs)

    return {
        "generated_from": {
            "matrix": str(MATRIX_PATH),
            "quality_gate": str(QUALITY_GATE_PATH),
            "runs_dir": str(RUNS_DIR),
        },
        "counts": {
            "total": len(runs),
            "status": dict(sorted(status_counts.items())),
            "quality_gate": dict(sorted(qg_counts.items())),
        },
        "runs": runs,
    }


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Week5 vLLM Dashboard</title>
<style>
:root {{
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #17202a;
  --muted: #667085;
  --line: #d8dde6;
  --green: #15803d;
  --red: #b42318;
  --amber: #b54708;
  --blue: #175cd3;
  --gray: #475467;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: Arial, Helvetica, sans-serif;
  color: var(--text);
  background: var(--bg);
}}
header {{
  padding: 24px 28px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}}
h1 {{
  margin: 0 0 8px;
  font-size: 24px;
  line-height: 1.25;
  letter-spacing: 0;
}}
h2 {{
  margin: 0 0 12px;
  font-size: 18px;
}}
main {{
  padding: 18px 28px 32px;
}}
.meta {{
  color: var(--muted);
  font-size: 13px;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
  gap: 12px;
  margin: 14px 0 18px;
}}
.stat {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}}
.stat .label {{
  color: var(--muted);
  font-size: 12px;
}}
.stat .value {{
  margin-top: 6px;
  font-size: 24px;
  font-weight: 700;
}}
.panel {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}}
.filters {{
  display: grid;
  grid-template-columns: repeat(6, minmax(120px, 1fr));
  gap: 10px;
}}
label {{
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 4px;
}}
select, input {{
  width: 100%;
  height: 34px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 0 8px;
  background: #fff;
  color: var(--text);
}}
.table-wrap {{
  overflow-x: auto;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  min-width: 980px;
  font-size: 13px;
}}
th, td {{
  border-bottom: 1px solid var(--line);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}}
th {{
  color: var(--muted);
  font-weight: 700;
  background: #fafbfc;
}}
.badge {{
  display: inline-block;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid currentColor;
}}
.validated, .pass {{ color: var(--green); }}
.failed, .validation_failed, .warn-bad {{ color: var(--red); }}
.warn {{ color: var(--amber); }}
.skip, .none {{ color: var(--gray); }}
.bar-row {{
  display: grid;
  grid-template-columns: minmax(260px, 1.4fr) minmax(120px, 3fr) 90px;
  gap: 10px;
  align-items: center;
  margin: 8px 0;
  font-size: 13px;
}}
.bar-track {{
  height: 16px;
  background: #eef1f5;
  border-radius: 4px;
  overflow: hidden;
}}
.bar {{
  height: 100%;
  background: var(--blue);
}}
.small {{
  color: var(--muted);
  font-size: 12px;
}}
@media (max-width: 900px) {{
  main, header {{ padding-left: 14px; padding-right: 14px; }}
  .grid {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
  .filters {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
  .bar-row {{ grid-template-columns: 1fr; gap: 4px; }}
}}
</style>
</head>
<body>
<header>
  <h1>Week5 vLLM Dashboard</h1>
  <div class="meta">单文件静态看板。性能图只使用 validated run；失败样本进入故障中心。</div>
</header>
<main>
  <section class="grid" id="stats"></section>

  <section class="panel">
    <h2>筛选</h2>
    <div class="filters">
      <div><label>Status</label><select id="statusFilter"></select></div>
      <div><label>Profile</label><select id="profileFilter"></select></div>
      <div><label>Mode</label><select id="modeFilter"></select></div>
      <div><label>Feature</label><select id="featureFilter"></select></div>
      <div><label>Workload</label><select id="workloadFilter"></select></div>
      <div><label>Search</label><input id="searchBox" placeholder="run id / error / diagnosis"></div>
    </div>
  </section>

  <section class="panel">
    <h2>Output Throughput</h2>
    <div class="small">仅展示筛选后的 validated run，不同 workload/concurrency 请分组理解。</div>
    <div id="throughputBars"></div>
  </section>

  <section class="panel">
    <h2>故障中心</h2>
    <div class="table-wrap"><table id="failureTable"></table></div>
  </section>

  <section class="panel">
    <h2>完整结果表</h2>
    <div class="table-wrap"><table id="runTable"></table></div>
  </section>
</main>

<script type="application/json" id="dashboard-data">{payload}</script>
<script>
const data = JSON.parse(document.getElementById('dashboard-data').textContent);
const runs = data.runs;

const filters = {{
  status: document.getElementById('statusFilter'),
  profile: document.getElementById('profileFilter'),
  mode: document.getElementById('modeFilter'),
  feature: document.getElementById('featureFilter'),
  workload: document.getElementById('workloadFilter'),
  search: document.getElementById('searchBox'),
}};

function unique(field) {{
  return [...new Set(runs.map(r => r[field] || '').filter(Boolean))].sort();
}}

function fillSelect(el, values) {{
  el.innerHTML = '<option value="">all</option>' + values.map(v => `<option value="${{escapeHtml(v)}}">${{escapeHtml(v)}}</option>`).join('');
}}

function escapeHtml(s) {{
  return String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

function badge(value, kind) {{
  const cls = String(kind || value || 'none').toLowerCase().replace(/[^a-z0-9_ -]/g, '').replace(/ /g, '-');
  return `<span class="badge ${{cls}}">${{escapeHtml(value || '')}}</span>`;
}}

function filteredRuns() {{
  const query = filters.search.value.trim().toLowerCase();
  return runs.filter(r => {{
    if (filters.status.value && r.status !== filters.status.value) return false;
    if (filters.profile.value && r.profile !== filters.profile.value) return false;
    if (filters.mode.value && r.service_mode !== filters.mode.value) return false;
    if (filters.feature.value && r.feature !== filters.feature.value) return false;
    if (filters.workload.value && r.workload !== filters.workload.value) return false;
    if (query) {{
      const hay = [r.run_id, r.error, r.diagnosis_category, r.failed_stage, r.quality_gate].join(' ').toLowerCase();
      if (!hay.includes(query)) return false;
    }}
    return true;
  }});
}}

function renderStats(rows) {{
  const statusCounts = rows.reduce((acc, r) => (acc[r.status] = (acc[r.status] || 0) + 1, acc), {{}});
  const qgCounts = rows.reduce((acc, r) => (acc[r.quality_gate || 'NONE'] = (acc[r.quality_gate || 'NONE'] || 0) + 1, acc), {{}});
  const items = [
    ['Runs', rows.length],
    ['Validated', statusCounts.validated || 0],
    ['Failed', (statusCounts.failed || 0) + (statusCounts.validation_failed || 0)],
    ['Quality WARN', qgCounts.WARN || 0],
  ];
  document.getElementById('stats').innerHTML = items.map(([label, value]) => `
    <div class="stat"><div class="label">${{label}}</div><div class="value">${{value}}</div></div>
  `).join('');
}}

function renderBars(rows) {{
  const valid = rows.filter(r => r.status === 'validated' && Number(r.output_throughput) > 0);
  const max = Math.max(1, ...valid.map(r => Number(r.output_throughput)));
  document.getElementById('throughputBars').innerHTML = valid
    .sort((a, b) => Number(b.output_throughput) - Number(a.output_throughput))
    .map(r => {{
      const pct = Math.max(2, Number(r.output_throughput) / max * 100);
      const label = `${{r.run_id}} · ${{r.workload}} c${{r.max_concurrency}}`;
      return `<div class="bar-row">
        <div>${{escapeHtml(label)}}<div class="small">${{escapeHtml(r.feature)}} / ${{escapeHtml(r.feature_variant)}} / ${{escapeHtml(r.profile || 'unspecified')}}</div></div>
        <div class="bar-track"><div class="bar" style="width:${{pct}}%"></div></div>
        <div>${{Number(r.output_throughput).toFixed(2)}} tok/s</div>
      </div>`;
    }}).join('') || '<div class="small">No validated runs in current filter.</div>';
}}

function renderFailureTable(rows) {{
  const failures = rows.filter(r => r.status !== 'validated');
  const html = ['<tr><th>Status</th><th>Run ID</th><th>Stage</th><th>Diagnosis</th><th>Error</th></tr>']
    .concat(failures.map(r => `<tr>
      <td>${{badge(r.status, r.status)}}</td>
      <td>${{escapeHtml(r.run_id)}}</td>
      <td>${{escapeHtml(r.failed_stage || '')}}</td>
      <td>${{escapeHtml(r.diagnosis_category || '')}}</td>
      <td>${{escapeHtml(r.error || '')}}</td>
    </tr>`));
  document.getElementById('failureTable').innerHTML = html.join('');
}}

function renderRunTable(rows) {{
  const html = ['<tr><th>Status</th><th>Quality</th><th>Run ID</th><th>Profile</th><th>Mode</th><th>Feature</th><th>Workload</th><th>c</th><th>Output tok/s</th><th>p99 TTFT</th><th>p99 TPOT</th><th>GPU</th></tr>']
    .concat(rows.map(r => `<tr>
      <td>${{badge(r.status, r.status)}}</td>
      <td>${{r.quality_gate ? badge(r.quality_gate, r.quality_gate.toLowerCase()) : ''}}</td>
      <td>${{escapeHtml(r.run_id)}}</td>
      <td>${{escapeHtml(r.profile || '')}}</td>
      <td>${{escapeHtml(r.service_mode)}}</td>
      <td>${{escapeHtml(r.feature)}} / ${{escapeHtml(r.feature_variant)}}</td>
      <td>${{escapeHtml(r.workload)}}</td>
      <td>${{escapeHtml(r.max_concurrency)}}</td>
      <td>${{r.output_throughput ? Number(r.output_throughput).toFixed(2) : ''}}</td>
      <td>${{r.p99_ttft_ms ? Number(r.p99_ttft_ms).toFixed(2) : ''}}</td>
      <td>${{r.p99_tpot_ms ? Number(r.p99_tpot_ms).toFixed(2) : ''}}</td>
      <td>${{r.gpu_summary && r.gpu_summary.samples ? `${{r.gpu_summary.samples}} samples, max util ${{r.gpu_summary.max_utilization_gpu}}%` : ''}}</td>
    </tr>`));
  document.getElementById('runTable').innerHTML = html.join('');
}}

function render() {{
  const rows = filteredRuns();
  renderStats(rows);
  renderBars(rows);
  renderFailureTable(rows);
  renderRunTable(rows);
}}

fillSelect(filters.status, unique('status'));
fillSelect(filters.profile, unique('profile'));
fillSelect(filters.mode, unique('service_mode'));
fillSelect(filters.feature, unique('feature'));
fillSelect(filters.workload, unique('workload'));
Object.values(filters).forEach(el => el.addEventListener('input', render));
render();
</script>
</body>
</html>"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = build_data()
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(f"Wrote {OUT_HTML}")
    print(f"Runs: {data['counts']['total']}")
    print("Status counts:", data["counts"]["status"])
    print("Quality counts:", data["counts"]["quality_gate"])


if __name__ == "__main__":
    main()
