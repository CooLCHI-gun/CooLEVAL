#!/usr/bin/env python3
"""eval-report.py — Phase E: generate a consolidated eval report (manual-first).

Runs the full pipeline (ETL → metrics → battery summary) and writes a
human-readable markdown report to reports/eval-report-YYYYMMDD.md.

Usage: python3 eval-report.py
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
EVAL_DB = Path.home() / ".hermes" / "data" / "hermes-eval.db"


def run_script(name: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / name), "--json"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        return f"ERROR running {name}: {proc.stderr[-800:]}"
    try:
        return json.dumps(json.loads(proc.stdout), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return proc.stdout


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    out = REPORTS / f"eval-report-{today}.md"

    # 1. Run ETL (idempotent)
    etl = subprocess.run(
        [sys.executable, str(SCRIPTS / "eval-etl.py")],
        capture_output=True, text=True, timeout=300,
    ).stdout.strip()

    # 2. Metrics JSON
    metrics = json.loads(run_script("eval-metrics.py"))

    # 3. Battery summary from DB
    con = sqlite3.connect(f"file:{EVAL_DB}?mode=ro", uri=True)
    batt_rows = con.execute(
        "SELECT task_key, status, duration_h, spec_hash FROM battery_runs ORDER BY task_key, run_n"
    ).fetchall()
    con.close()

    battery_lines = []
    by_task: dict[str, list[tuple[str, float]]] = {}
    for task_key, status, dur_h, spec_hash in batt_rows:
        by_task.setdefault(task_key, []).append((status, dur_h))
        battery_lines.append(f"  - {task_key} [{spec_hash[:8]}]: {status} ({dur_h*3600:.0f}s)")
    batt_summary_lines = []
    for task_key in sorted(by_task):
        runs = by_task[task_key]
        n = len(runs)
        k = sum(1 for s, _ in runs if s == "completed")
        durs = [d for _, d in runs]
        mean_s = statistics.mean(durs) * 3600 if durs else 0
        std_s = statistics.stdev(durs) * 3600 if len(durs) > 1 else 0
        batt_summary_lines.append(
            f"  - **{task_key}**: {k}/{n} pass | duration {mean_s:.0f}s ± {std_s:.0f}s")

    s = metrics["session"]
    lines = [
        f"# Agent Eval Report — {today}",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. ETL",
        "",
        "```",
        etl,
        "```",
        "",
        "## 2. Task-level success",
        "",
        f"- **Success rate**: {metrics['task_success_rate']}",
        f"- Status: {metrics['status_dist']}",
        f"- Failure classes: {metrics['failure_classes']}",
        "",
        "## 3. Session-level reliability (battery excluded)",
        "",
        f"- Success rate: {s['success_rate']} (n={s['n']}, battery excluded={s['excluded_battery']})",
        f"- **50% time horizon**: {s['time_horizon']['bucket'] if s['time_horizon'] else 'none'}"
        + (f" (rate={s['time_horizon']['rate']:.1%}, n={s['time_horizon']['n']})"
           if s['time_horizon'] else ""),
        "",
        "Hazard curve (P(success | duration >= t)):",
        "",
        "| bucket | success rate | note |",
        "|--------|-------------|------|",
    ]
    for b in s["hazard_curve"]:
        lines.append(f"| {b['bucket']} | {b['rate']} | {'exploratory' if b['exploratory'] else ''} |")

    lines += [
        "",
        "## 4. Task-type breakdown (n-gated)",
        "",
        "| task_type | rate | note |",
        "|-----------|------|------|",
    ]
    for t in metrics["task_type_breakdown"]:
        lines.append(f"| {t['task_type']} | {t['rate']} | {'exploratory' if t['exploratory'] else ''} |")

    lines += [
        "",
        "## 5. Battery runs (active benchmark)",
        "",
        *batt_summary_lines,
        "",
        "Details:",
        "",
        *battery_lines,
        "",
        "## 6. Tool failures (n-gated)",
        "",
    ]
    if metrics["tool_failures"]:
        lines.append("| tool | per-call | fails | n | note |")
        lines.append("|------|----------|-------|---|------|")
        for t in metrics["tool_failures"]:
            lines.append(f"| {t['tool']} | {t['per_call']} | {t['fails']} | {t['n']} "
                         f"| {'exploratory' if t['exploratory'] else ''} |")
    else:
        lines.append("(none)")

    lines += ["", "## 7. Loopiness (guardrail)", ""]
    if metrics["loopiness_top"]:
        for l in metrics["loopiness_top"]:
            lines.append(f"- {l['session_id'][:40]}: {l['repeat_same_tool']} repeats / {l['spans']} spans")
    else:
        lines.append("(none)")

    lines += ["", "## 8. Taxonomy", "",
              f"- valid: {metrics['taxonomy_valid']}",
              f"- issues: {metrics.get('taxonomy_issues') or '(none)'}",
              ""]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written: {out}")
    print(f"  battery runs: {len(batt_rows)}, task success: {metrics['task_success_rate']}, "
          f"session horizon: {s['time_horizon']['bucket'] if s['time_horizon'] else 'none'}")


if __name__ == "__main__":
    main()
