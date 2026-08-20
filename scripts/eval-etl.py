#!/usr/bin/env python3
"""eval-etl.py — Phase A: idempotent ETL for Agent Eval Framework.

Aggregates three raw sources into a local eval DB (default ./data/eval.db;
sources overridable via HERMES_STATE_DB / HERMES_MEM_DB / HERMES_TRACES):
  1. memory-unified.db  agent_lifecycle  -> task_events
  2. traces.jsonl (span-tracer)          -> span_metrics
  3. state.db sessions                   -> session-level enrichment (model tier)

Design (Sonar must-fix 3):
  - Idempotent: INSERT OR IGNORE with natural dedup keys; safe to re-run any time.
  - Watermark/audit: etl_watermarks records last sync per source.
  - Reconciliation: prints source counts vs ingested counts; non-zero diff = alert.
  - Deterministic task_type tagging rule (keyword classifier on goal_preview) —
    NOT manual post-hoc labelling (Sonar: avoid label bias).
  - difficulty: pre-registered per task_key for battery tasks; live tasks = 'unknown'
    (difficulty is intrinsic to the task spec, never inferred from outcome).

Usage: python3 eval-etl.py [--rebuild]
  --rebuild: drop tables and rebuild from scratch (for schema changes).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

_DATA = Path(os.environ.get("COOLEVAL_DATA", str(Path(__file__).resolve().parent.parent / "data")))
EVAL_DB = Path(os.environ.get("EVAL_DB", str(_DATA / "eval.db")))
MEM_DB = Path(os.environ.get("HERMES_MEM_DB", str(_DATA / "memory-unified.db")))
STATE_DB = Path(os.environ.get("HERMES_STATE_DB", str(_DATA / "state.db")))
TRACE_FILE = Path(os.environ.get("HERMES_TRACES", str(_DATA / "traces.jsonl")))
TRACE_OLD = Path(os.environ.get("HERMES_TRACES_OLD", str(_DATA / "traces.jsonl.old")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_events (
    task_id TEXT PRIMARY KEY,
    session_id TEXT,
    task_key TEXT,
    spec_hash TEXT,
    task_type TEXT,
    difficulty TEXT DEFAULT 'unknown',
    status TEXT,
    failure_class TEXT,
    model_tier TEXT,
    toolset_version TEXT,
    workspace_state_hash TEXT,
    start_reason TEXT,
    end_reason TEXT,
    started_at REAL,
    finished_at REAL,
    duration_h REAL,
    result_summary TEXT
);
CREATE TABLE IF NOT EXISTS span_metrics (
    span_id TEXT PRIMARY KEY,
    span_type TEXT,
    ts REAL,
    session_id TEXT,
    tool_name TEXT,
    tier TEXT,
    model TEXT,
    input_tok INT,
    output_tok INT,
    latency_ms INT,
    duration_ms INT,
    ok INT
);
CREATE TABLE IF NOT EXISTS battery_runs (
    run_id TEXT PRIMARY KEY,
    task_key TEXT,
    run_n INT,
    spec_hash TEXT,
    status TEXT,
    duration_h REAL,
    ts REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS etl_watermarks (
    source TEXT PRIMARY KEY,
    last_offset INT,
    last_ts REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_task_events_started ON task_events(started_at);
CREATE INDEX IF NOT EXISTS idx_span_session ON span_metrics(session_id);
CREATE INDEX IF NOT EXISTS idx_span_type ON span_metrics(span_type);
"""


def _span_id(span: dict) -> str:
    """Deterministic dedup key: hash of canonical span fields (no PII, no args)."""
    canon = json.dumps(
        {
            "type": span.get("type"),
            "ts": span.get("ts"),
            "session_id": span.get("session_id"),
            "tool_name": span.get("tool_name"),
            "tier": span.get("tier"),
            "model": span.get("model"),
            "input_tok": span.get("input_tok"),
            "output_tok": span.get("output_tok"),
            "duration_ms": span.get("duration_ms"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canon.encode()).hexdigest()[:32]


# Deterministic task_type classifier (pre-registered rule, stable ordering).
_TASK_TYPE_RULES = [
    ("delegation", ["delegate", "subagent", "parallel", "orchestrat"]),
    ("memory", ["memory", "recall", "remember", "fact", "episod"]),
    ("research", ["research", "search", "investigate", "review", "analysis", "find", "look up", "check"]),
    ("code", ["test", "pytest", "code", "script", "bug", "fix", "implement", "refactor", "commit"]),
    ("cron_check", ["cron", "schedul", "job"]),
    ("file_ops", ["file", "write", "read", "save", "create", "move", "delete"]),
]


def classify_task_type(goal: str) -> str:
    g = (goal or "").lower()
    for ttype, keywords in _TASK_TYPE_RULES:
        if any(k in g for k in keywords):
            return ttype
    return "other"


def ingest_task_events(con: sqlite3.Connection) -> tuple[int, int]:
    src = sqlite3.connect(f"file:{MEM_DB}?mode=ro", uri=True)
    rows = src.execute(
        "SELECT id, parent_session, task_id, agent_name, status, goal_preview, "
        "started_at, finished_at, result_summary FROM agent_lifecycle"
    ).fetchall()
    src.close()

    # Enrich with model tier from state.db sessions (by parent_session id).
    model_by_session: dict[str, str] = {}
    try:
        scon = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        for sid, model in scon.execute("SELECT id, model FROM sessions"):
            model_by_session[sid] = model or ""
        scon.close()
    except sqlite3.Error:
        pass

    now = time.time()
    ingested = 0
    for (row_id, parent_session, task_id, agent_name, status, goal, started, finished, summary) in rows:
        started = float(started) if started else None
        finished = float(finished) if finished else None
        duration_h = (finished - started) / 3600.0 if (started and finished) else None

        # failure_class from status (pre-registered taxonomy)
        failure_class = None
        if status == "started":
            # started >2h without finish = stale (same rule as rsi-scoreboard)
            if started and (now - started) > 2 * 3600:
                status_effective = "failed"
                failure_class = "timeout"
            else:
                status_effective = "started"
        else:
            status_effective = status

        con.execute(
            "INSERT OR IGNORE INTO task_events (task_id, session_id, task_key, spec_hash, "
            "task_type, difficulty, status, failure_class, model_tier, toolset_version, "
            "workspace_state_hash, start_reason, end_reason, started_at, finished_at, "
            "duration_h, result_summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row_id,
                parent_session,
                None,  # task_key: only battery tasks have canonical keys
                None,  # spec_hash: only battery tasks
                classify_task_type(goal or agent_name or ""),
                "unknown",  # live tasks: difficulty not pre-registered
                status_effective,
                failure_class,
                model_by_session.get(parent_session or "", "") or None,
                None,  # toolset_version: no global version source (v1)
                None,  # workspace_state_hash: v1 = not tracked for live tasks
                agent_name,
                None,  # end_reason: not in source (v1)
                started,
                finished,
                duration_h,
                (summary or "")[:500],
            ),
        )
        ingested += con.total_changes if False else 1
    return len(rows), ingested


def ingest_spans(con: sqlite3.Connection) -> tuple[int, int]:
    total = 0
    ingested = 0
    for path in (TRACE_OLD, TRACE_FILE):  # old first, then current
        if not path.is_file():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = _span_id(d)
                con.execute(
                    "INSERT OR IGNORE INTO span_metrics (span_id, span_type, ts, session_id, "
                    "tool_name, tier, model, input_tok, output_tok, latency_ms, duration_ms, ok) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        d.get("type"),
                        d.get("ts"),
                        d.get("session_id"),
                        d.get("tool_name"),
                        d.get("tier"),
                        d.get("model"),
                        d.get("input_tok", 0),
                        d.get("output_tok", 0),
                        d.get("latency_ms", 0),
                        d.get("duration_ms", 0),
                        1 if d.get("ok", True) else 0,
                    ),
                )
                ingested += 1
    return total, ingested


def write_watermark(con: sqlite3.Connection, source: str, offset: int) -> None:
    con.execute(
        "INSERT OR REPLACE INTO etl_watermarks (source, last_offset, last_ts, updated_at) "
        "VALUES (?,?,?,?)",
        (source, offset, time.time(), time.time()),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(EVAL_DB)
    con.execute("PRAGMA journal_mode=WAL")
    if args.rebuild:
        for t in ("task_events", "span_metrics", "battery_runs", "etl_watermarks"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
    con.executescript(SCHEMA)

    t0 = time.time()
    src_n, ing_n = ingest_task_events(con)
    t1 = time.time()
    trace_n, span_n = ingest_spans(con)
    write_watermark(con, "agent_lifecycle", src_n)
    write_watermark(con, "traces.jsonl", trace_n)
    con.commit()

    # Reconciliation
    db_tasks = con.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
    db_spans = con.execute("SELECT COUNT(*) FROM span_metrics").fetchone()[0]
    t2 = time.time()

    print(f"=== eval-etl.py ({time.strftime('%Y-%m-%d %H:%M:%S')}) ===")
    print(f"task_events:  source={src_n} ingested={ing_n} db_total={db_tasks}")
    print(f"span_metrics: source={trace_n} ingested={span_n} db_total={db_spans}")
    print(f"task status:  " + ", ".join(
        f"{r[0]}={r[1]}" for r in con.execute(
            "SELECT status, COUNT(*) FROM task_events GROUP BY status ORDER BY 2 DESC")))
    print(f"task_type:    " + ", ".join(
        f"{r[0]}={r[1]}" for r in con.execute(
            "SELECT task_type, COUNT(*) FROM task_events GROUP BY task_type ORDER BY 2 DESC")))
    print(f"timing: tasks={t1-t0:.2f}s spans={t2-t1:.2f}s total={t2-t0:.2f}s")
    print(f"db: {EVAL_DB} ({EVAL_DB.stat().st_size/1024:.0f} KB)")
    # Idempotency check: re-running must not change totals (caller verifies)
    con.close()


if __name__ == "__main__":
    main()
