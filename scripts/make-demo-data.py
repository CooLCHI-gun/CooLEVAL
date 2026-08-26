#!/usr/bin/env python3
"""make-demo-data.py — generate a small, DETERMINISTIC demo telemetry set.

PURPOSE (read this first)
-------------------------
This is **NOT a benchmark and NOT real traffic**. It is a *pipeline smoke
fixture*: a tiny, clearly-synthetic, seeded telemetry set whose only job is to
let a fresh clone reproduce the CooLEVAL pipeline end-to-end
(ETL -> metrics) without network, without a real agent, and without owning the
original telemetry. The numbers it yields are meaningless as results — they are
shape-checkers (does the meltdown curve render? does the failure risk ratio
compute? does the taxonomy validate?). CooLEVAL remains a *self-hosted,
statistically-honest, real-traffic* tool; this fixture exists so the advertised
quickstart actually runs on a clean checkout.

The set is intentionally shaped like REAL data (mostly short sessions that
succeed, long sessions that fail) so the curve has the same *shape* the real
meltdown has — but every row is fabricated. Never report these numbers as a
result.

OUTPUT (in demo/, override with COOLEVAL_DEMO_DIR)
  1. memory-unified.db   -> agent_lifecycle table   (task_events source)
  2. traces.jsonl        -> span records             (span_metrics source)
  3. state.db            -> sessions table           (session-level source)

Usage:
  python3 scripts/make-demo-data.py
  HERMES_MEM_DB=demo/memory-unified.db HERMES_TRACES=demo/traces.jsonl \
  HERMES_STATE_DB=demo/state.db EVAL_DB=demo/eval.db \
      python3 scripts/eval-etl.py --rebuild
  EVAL_DB=demo/eval.db HERMES_STATE_DB=demo/state.db python3 scripts/eval-metrics.py
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
from pathlib import Path

_DEMO = Path(os.environ.get("COOLEVAL_DEMO_DIR", str(Path(__file__).resolve().parent.parent / "demo")))

# Fixed base so the whole set is reproducible byte-for-byte (CI-safe).
_BASE = 1_787_664_000.0          # fixed epoch (seconds)
_RNG = random.Random(2026_08_27)  # fixed seed

_MODELS = ["deepseek-v4-flash", "claude-sonnet-4-6", "optimus-4b"]
_TOOLS = ["read_file", "terminal", "web_search", "execute_code", "write_file"]
_TITLES = [
    "Fix the CI workflow", "Search API docs for pagination", "Debug the import error",
    "Write a weekly blog draft", "Investigate the memory leak", "Refactor the router proxy",
    "Add a new endpoint", "Update the README", "Triage the open issues",
]


def _mk_sessions() -> list[dict]:
    """30 short (<15m, mostly success) + 20 long (>=1h, mostly fail)."""
    sessions: list[dict] = []
    i = 0
    short_fail_at = {12, 25}  # two short sessions "error" out

    def add(dur_s: float, success: bool, title: str) -> None:
        nonlocal i
        sid = f"sess-{i:03d}"
        started = _BASE + i * 7200.0
        ended = started + dur_s
        reason = "agent_close" if success else ("timeout" if dur_s >= 3600 else "error")
        sessions.append({
            "id": sid, "model": _MODELS[i % len(_MODELS)],
            "started_at": started, "ended_at": ended, "end_reason": reason, "title": title,
        })
        i += 1

    for n in range(30):
        dur = _RNG.uniform(120.0, 780.0)  # 2-13 min
        add(dur, n not in short_fail_at, _TITLES[n % len(_TITLES)])
    for n in range(6):
        add(_RNG.uniform(3600.0, 4 * 3600.0), n == 0, "Long build & release pipeline")
    for n in range(8):
        add(_RNG.uniform(4 * 3600.0, 24 * 3600.0), False, "Overnight migration run")
    for n in range(6):
        add(_RNG.uniform(24 * 3600.0, 3 * 24 * 3600.0), False, "Multi-day data backfill")
    return sessions


def _mk_lifecycle(sessions: list[dict], now: float) -> list[dict]:
    """agent_lifecycle rows: mostly completed, a couple stale 'started' -> timeout."""
    rows = []
    # reference the same session ids so model enrichment is non-empty
    for k in range(14):
        sess = sessions[k % len(sessions)]
        started = _BASE + k * 3600.0
        if k in (11, 12):  # stale in-flight -> ETL converts to failed/timeout
            status, finished, summary = "started", None, ""
        else:
            status, finished = "completed", started + _RNG.uniform(60.0, 900.0)
            summary = f"result for task {k}"
        goal = [
            "Research the API rate limits", "Fix the failing pytest test",
            "Recall the project fact from memory", "List the current cron jobs",
            "Write a file to the output dir", "Use delegate_task to spawn a subagent",
        ][k % 6]
        rows.append({
            "id": f"life-{k:03d}", "parent_session": sess["id"], "task_id": f"task-{k:03d}",
            "agent_name": "hermes", "status": status, "goal_preview": goal,
            "started_at": started, "finished_at": finished, "result_summary": summary,
        })
    return rows


def _mk_spans(sessions: list[dict]) -> list[dict]:
    """Tool spans: some failures, one loop (same tool back-to-back <60s)."""
    spans = []
    ts = _BASE
    sid0 = sessions[0]["id"]
    # a loop: execute_code repeated 5x within 12s in session 0
    for j in range(5):
        spans.append({
            "type": "tool", "ts": ts + j * 3.0, "session_id": sid0,
            "tool_name": "execute_code", "tier": "tier1", "model": "deepseek-v4-flash",
            "input_tok": 400 + j, "output_tok": 50, "latency_ms": 800, "duration_ms": 750,
            "ok": True, "turn_id": f"t0-{j}", "tool_call_id": f"c0-{j}",
            "schema_version": 1, "args_shape": {"code": "str", "timeout": "int"},
        })
    # scattered spans across sessions, a few failing
    base = _BASE + 1_000_000.0
    for k, sess in enumerate(sessions[1:26]):
        tool = _TOOLS[k % len(_TOOLS)]
        # one terminal failure (exit 1) every 9th span
        ok = not (k % 9 == 4)
        spans.append({
            "type": "tool", "ts": base + k * 120.0, "session_id": sess["id"],
            "tool_name": tool, "tier": "tier1", "model": _MODELS[k % len(_MODELS)],
            "input_tok": 120 + k * 5, "output_tok": 30, "latency_ms": 300 + k, "duration_ms": 260 + k,
            "ok": ok, "is_error": 0 if ok else 1, "error_type": None if ok else "exit_1",
            "error_message": None if ok else "command failed",
            "turn_id": f"tk-{k}", "tool_call_id": f"ck-{k}",
            "schema_version": 1, "args_shape": {"path": "str"},
        })
    return spans


def main() -> None:
    _DEMO.mkdir(parents=True, exist_ok=True)
    now = 1_787_664_000.0 + 30 * 7200.0  # ~ just past the last short session

    sessions = _mk_sessions()
    lifecycle = _mk_lifecycle(sessions, now)
    spans = _mk_spans(sessions)

    # ── state.db (sessions) ──────────────────────────────────────────────
    scon = sqlite3.connect(_DEMO / "state.db")
    scon.execute("DROP TABLE IF EXISTS sessions")
    scon.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, started_at REAL, "
        "ended_at REAL, end_reason TEXT, title TEXT)")
    scon.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?)", [
        (s["id"], s["model"], s["started_at"], s["ended_at"], s["end_reason"], s["title"])
        for s in sessions])
    scon.commit()
    scon.close()

    # ── memory-unified.db (agent_lifecycle) ──────────────────────────────
    mcon = sqlite3.connect(_DEMO / "memory-unified.db")
    mcon.execute("DROP TABLE IF EXISTS agent_lifecycle")
    mcon.execute(
        "CREATE TABLE agent_lifecycle (id TEXT PRIMARY KEY, parent_session TEXT, "
        "task_id TEXT, agent_name TEXT, status TEXT, goal_preview TEXT, started_at REAL, "
        "finished_at REAL, result_summary TEXT)")
    mcon.executemany("INSERT INTO agent_lifecycle VALUES (?,?,?,?,?,?,?,?,?)", [
        (r["id"], r["parent_session"], r["task_id"], r["agent_name"], r["status"],
         r["goal_preview"], r["started_at"], r["finished_at"], r["result_summary"])
        for r in lifecycle])
    mcon.commit()
    mcon.close()

    # ── traces.jsonl (spans) ─────────────────────────────────────────────
    with open(_DEMO / "traces.jsonl", "w", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"[make-demo-data] wrote NON-BENCHMARK pipeline smoke fixture to {_DEMO}")
    print(f"[make-demo-data]   sessions={len(sessions)} lifecycle={len(lifecycle)} spans={len(spans)}")
    print("[make-demo-data] NOT a benchmark / NOT real traffic — shape-checker only.")
    print("\nRun it through the pipeline:\n"
          f"  HERMES_MEM_DB={_DEMO}/memory-unified.db HERMES_TRACES={_DEMO}/traces.jsonl \\\n"
          f"  HERMES_STATE_DB={_DEMO}/state.db EVAL_DB={_DEMO}/eval.db \\\n"
          f"      python3 scripts/eval-etl.py --rebuild\n"
          f"  EVAL_DB={_DEMO}/eval.db HERMES_STATE_DB={_DEMO}/state.db \\\n"
          f"      python3 scripts/eval-metrics.py")


if __name__ == "__main__":
    main()
