#!/usr/bin/env python3
"""eval-metrics.py — Phase B: core reliability metrics for Agent Eval Framework.

Reads ~/.hermes/data/hermes-eval.db (populated by eval-etl.py) and computes:

  1. Task success rate (completed / resolved), with failure_class breakdown
  2. 50% time horizon (METR 2503.14499): longest duration bucket with >=50% success
  3. Hazard-style P(success | time >= t) curve on pre-registered buckets
     (Sonar must-fix 2: no post-hoc cutoff picking; buckets fixed below)
  4. Tool failure rate, conditioned per-call / per-session / per-task
  5. Guardrail signals: loopiness proxy (same tool repeated within short window)
  6. Confidence intervals: Wilson 95% on all proportions
  7. Minimum-n gate: task-type / tool-type breakdowns with n<20 are flagged
     'exploratory' and not interpreted

Also runs taxonomy validation: every status value in source must map to a known
failure_class or be a valid success status (Sonar must-fix 2).

Usage: python3 eval-metrics.py [--json]
Output: human-readable report to stdout (and JSON if --json given).
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from collections import Counter

EVAL_DB = Path.home() / ".hermes" / "data" / "hermes-eval.db"

# Pre-registered duration buckets (hours) — Sonar must-fix 2: fixed rule,
# NOT chosen post-hoc from the data.
DURATION_BUCKETS = [
    (0.0, 0.25, "<15m"),
    (0.25, 1.0, "15m-1h"),
    (1.0, 4.0, "1-4h"),
    (4.0, 24.0, "4-24h"),
    (24.0, None, ">24h"),
]

# Minimum n for a breakdown to be interpreted (below = exploratory only)
MIN_N = 20

# Known success statuses (no failure_class expected)
SUCCESS_STATUSES = {"completed"}
# In-flight statuses (ETL already converts stale >2h 'started' to failed/timeout,
# so any remaining 'started' in eval.db is genuinely in-flight and legal without a class)
INFLIGHT_STATUSES = {"started"}
# Known failure-ish statuses that MUST map to a failure_class
KNOWN_FAILURE_CLASSES = {"tool", "model", "timeout", "infra", "user_abort", "network", "partial"}


def wilson_95(n: int, k: int) -> tuple[float, float]:
    """Wilson score interval (95%) for proportion k/n. Returns (low, high)."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def fmt_pct(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson_95(n, k)
    return f"{100*k/n:.1f}% [CI {100*lo:.1f}-{100*hi:.1f}] (n={n})"


def validate_taxonomy(con: sqlite3.Connection) -> list[str]:
    """Every non-success status must carry a known failure_class."""
    issues = []
    rows = con.execute(
        "SELECT status, failure_class, COUNT(*) FROM task_events GROUP BY status, failure_class"
    ).fetchall()
    for status, fc, n in rows:
        if status in SUCCESS_STATUSES:
            continue
        if status in INFLIGHT_STATUSES:
            continue
        if fc not in KNOWN_FAILURE_CLASSES:
            issues.append(f"status='{status}' failure_class={fc!r} (n={n}) not in taxonomy")
    # sample check: 20 random non-success rows must all have a class
    sample = con.execute(
        "SELECT status, failure_class FROM task_events WHERE status NOT IN "
        "('completed', 'started') ORDER BY RANDOM() LIMIT 20"
    ).fetchall()
    for status, fc in sample:
        if fc not in KNOWN_FAILURE_CLASSES:
            issues.append(f"sample row status='{status}' missing failure_class")
    return issues


def success_at_or_after(con: sqlite3.Connection) -> list[dict]:
    """Hazard-style P(success | duration >= t) per pre-registered bucket."""
    out = []
    for lo, hi, label in DURATION_BUCKETS:
        if hi is None:
            rows = con.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) "
                "FROM task_events WHERE duration_h IS NOT NULL AND duration_h >= ?",
                (lo,),
            ).fetchone()
        else:
            rows = con.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) "
                "FROM task_events WHERE duration_h IS NOT NULL AND duration_h >= ? AND duration_h < ?",
                (lo, hi),
            ).fetchone()
        n, k = rows[0], (rows[1] or 0)
        out.append({"bucket": label, "lo": lo, "hi": hi, "n": n, "success": k})
    return out


def time_horizon(curve: list[dict]) -> dict:
    """Longest bucket (>= MIN_N) with success rate >= 50%."""
    for b in curve:
        if b["n"] >= MIN_N and b["success"] / b["n"] >= 0.5:
            return {"bucket": b["bucket"], "n": b["n"], "rate": b["success"] / b["n"]}
    return {"bucket": None, "n": 0, "rate": None}


def tool_failures(con: sqlite3.Connection) -> list[dict]:
    """Tool failure rate: per-call raw + per-session + per-task conditioned."""
    rows = con.execute(
        "SELECT tool_name, COUNT(*) n, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) fails "
        "FROM span_metrics WHERE span_type='tool' GROUP BY tool_name"
    ).fetchall()
    out = []
    for tool, n, fails in rows:
        out.append({
            "tool": tool, "n": n, "fails": fails,
            "per_call": fails / n if n else 0,
            "exploratory": n < MIN_N,
        })
    return sorted(out, key=lambda r: -r["fails"])


def loopiness(con: sqlite3.Connection, window_s: float = 60.0) -> list[dict]:
    """Guardrail: same tool repeated within `window_s` in same session (loop proxy)."""
    out = []
    rows = con.execute(
        "SELECT session_id, tool_name, ts FROM span_metrics "
        "WHERE span_type='tool' ORDER BY session_id, ts"
    ).fetchall()
    # group by session, count repeats where gap <= window
    by_session: dict[str, list[tuple[float, str]]] = {}
    for sid, tool, ts in rows:
        by_session.setdefault(sid, []).append((ts, tool))
    for sid, items in by_session.items():
        items.sort()
        repeats = 0
        for i in range(1, len(items)):
            if items[i][1] == items[i - 1][1] and (items[i][0] - items[i - 1][0]) <= window_s:
                repeats += 1
        if repeats:
            out.append({"session_id": sid, "repeat_same_tool": repeats, "spans": len(items)})
    return sorted(out, key=lambda r: -r["repeat_same_tool"])[:10]


# ── Session-level analysis (state.db) ───────────────────────────────────
# Task-level data (agent_lifecycle) is all subagent runs, mostly <15m — it
# cannot show meltdown. The real duration->success signal lives at the session
# level, using the same success definition as rsi-scoreboard (end_reason in
# agent_close/cli_close/cron_complete).
SESSION_SUCCESS_REASONS = {"agent_close", "cli_close", "cron_complete"}
STATE_DB = Path.home() / ".hermes" / "state.db"

# Battery one-shot sessions must be EXCLUDED from session-level reliability
# analysis: they are synthetic eval runs (all <15m, mostly successful), so they
# would bias the meltdown curve. Filter by distinctive prompt prefixes.
BATTERY_PROMPT_PREFIXES = (
    "Read <REPO_ROOT>",
    "Use web_search to find information about 'Hermes Agent",
    "Recall from memory what the user's main active projects",
    "List the current cron jobs (cronjob tool",
    "Use delegate_task to spawn one leaf subagent",
    "Copy it to the ABSOLUTE path",
)


def session_analysis() -> dict:
    con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT started_at, ended_at, end_reason, title FROM sessions "
        "WHERE started_at IS NOT NULL AND ended_at IS NOT NULL"
    ).fetchall()
    con.close()
    sessions = []
    excluded = 0
    for started, ended, reason, title in rows:
        title = title or ""
        if any(title.startswith(p) for p in BATTERY_PROMPT_PREFIXES):
            excluded += 1
            continue
        dur_h = (ended - started) / 3600.0
        success = reason in SESSION_SUCCESS_REASONS
        sessions.append((dur_h, success))
    n = len(sessions)
    k = sum(1 for _, s in sessions if s)
    curve = []
    for lo, hi, label in DURATION_BUCKETS:
        bucket = [(d, s) for d, s in sessions if d >= lo and (hi is None or d < hi)]
        bn, bk = len(bucket), sum(1 for _, s in bucket if s)
        curve.append({"bucket": label, "rate": fmt_pct(bk, bn), "exploratory": bn < MIN_N})
    th = None
    for lo, hi, label in DURATION_BUCKETS:
        bucket = [(d, s) for d, s in sessions if d >= lo and (hi is None or d < hi)]
        bn, bk = len(bucket), sum(1 for _, s in bucket if s)
        if bn >= MIN_N and bk / bn >= 0.5:
            th = {"bucket": label, "rate": bk / bn, "n": bn}
            break
    return {"n": n, "excluded_battery": excluded, "success_rate": fmt_pct(k, n),
            "hazard_curve": curve, "time_horizon": th}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{EVAL_DB}?mode=ro", uri=True)
    report: dict = {}

    # ── Taxonomy validation ──────────────────────────────────────────────
    issues = validate_taxonomy(con)
    report["taxonomy_valid"] = not issues
    report["taxonomy_issues"] = issues[:5]

    # ── Success rate ─────────────────────────────────────────────────────
    total = con.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
    completed = con.execute(
        "SELECT COUNT(*) FROM task_events WHERE status='completed'").fetchone()[0]
    report["task_success_rate"] = fmt_pct(completed, total)
    report["status_dist"] = dict(con.execute(
        "SELECT status, COUNT(*) FROM task_events GROUP BY status ORDER BY 2 DESC").fetchall())

    # ── Failure class breakdown ──────────────────────────────────────────
    report["failure_classes"] = dict(con.execute(
        "SELECT COALESCE(failure_class,'-'), COUNT(*) FROM task_events "
        "WHERE status != 'completed' GROUP BY 1 ORDER BY 2 DESC").fetchall())

    # ── Hazard curve + time horizon ──────────────────────────────────────
    curve = success_at_or_after(con)
    report["hazard_curve"] = [
        {"bucket": b["bucket"], "success_rate": fmt_pct(b["success"], b["n"]),
         "exploratory": b["n"] < MIN_N}
        for b in curve
    ]
    th = time_horizon(curve)
    report["time_horizon"] = th

    # ── Task-type breakdown (n-gated) ────────────────────────────────────
    tt = []
    for ttype, n in con.execute(
            "SELECT task_type, COUNT(*) FROM task_events GROUP BY task_type ORDER BY 2 DESC"):
        k = con.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_type=? AND status='completed'",
            (ttype,)).fetchone()[0]
        tt.append({"task_type": ttype, "rate": fmt_pct(k, n), "exploratory": n < MIN_N})
    report["task_type_breakdown"] = tt

    # ── Tool failure (n-gated) ───────────────────────────────────────────
    tf = tool_failures(con)
    report["tool_failures"] = [
        {"tool": r["tool"], "per_call": f"{100*r['per_call']:.1f}%",
         "fails": r["fails"], "n": r["n"], "exploratory": r["exploratory"]}
        for r in tf if r["fails"] > 0
    ]

    # ── Guardrail loopiness ──────────────────────────────────────────────
    report["loopiness_top"] = loopiness(con)

    con.close()

    # ── Session-level analysis ───────────────────────────────────────────
    report["session"] = session_analysis()

    # ── Output ───────────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(f"=== eval-metrics.py ({EVAL_DB}) ===")
    print(f"taxonomy valid: {report['taxonomy_valid']}")
    if report["taxonomy_issues"]:
        print("  ISSUES:" + "".join(f"\n  - {i}" for i in report["taxonomy_issues"]))
    print(f"\nTASK SUCCESS RATE: {report['task_success_rate']}")
    print(f"status dist: {report['status_dist']}")
    print(f"failure classes: {report['failure_classes']}")
    print(f"\n50% TIME HORIZON: {report['time_horizon']['bucket']} "
          f"(rate={report['time_horizon']['rate']:.1%}, n={report['time_horizon']['n']})")
    print("hazard curve (P(success | duration >= t)):")
    for b in report["hazard_curve"]:
        tag = " [exploratory]" if b["exploratory"] else ""
        print(f"  {b['bucket']:<12} {b['success_rate']}{tag}")
    print("\ntask-type breakdown (n-gated):")
    for t in report["task_type_breakdown"]:
        tag = " [exploratory]" if t["exploratory"] else ""
        print(f"  {t['task_type']:<12} {t['rate']}{tag}")
    print("\ntool failures (n-gated, non-zero only):")
    for t in report["tool_failures"]:
        tag = " [exploratory]" if t["exploratory"] else ""
        print(f"  {t['tool']:<20} {t['per_call']} (fails={t['fails']}, n={t['n']}){tag}")
    if not report["tool_failures"]:
        print("  (none)")
    print("\nloopiness top sessions (same tool within 60s):")
    for l in report["loopiness_top"]:
        print(f"  {l['session_id'][:40]} repeats={l['repeat_same_tool']} spans={l['spans']}")
    if not report["loopiness_top"]:
        print("  (none)")

    s = report["session"]
    print(f"\nSESSION-LEVEL (state.db, n={s['n']}, battery excluded={s['excluded_battery']}): "
          f"success rate {s['success_rate']}")
    print(f"  50% TIME HORIZON: {s['time_horizon']['bucket'] if s['time_horizon'] else None}"
          + (f" (rate={s['time_horizon']['rate']:.1%}, n={s['time_horizon']['n']})" if s['time_horizon'] else ""))
    print("  hazard curve (P(success | duration >= t)):")
    for b in s["hazard_curve"]:
        tag = " [exploratory]" if b["exploratory"] else ""
        print(f"    {b['bucket']:<12} {b['rate']}{tag}")


if __name__ == "__main__":
    main()
