#!/usr/bin/env python3
"""eval-api.py — read-only REST API for the Hermes Eval Framework (OMP metaharness concept E, Phase 4a).

Sonar must-fix 3: read-only FIRST, no dashboard write path. Metrics schema defined here
(task success, tool-call count, retry count, guard trigger rate) so a future dashboard
has something meaningful to render.

Run (on demand — no daemon by default, 4GB host constraint):
    cd <repo>
    python3 scripts/eval-api.py [--port 8790]

Endpoints (all GET, localhost only):
    /health              — ok + db info
    /metrics             — computed metrics (Sonar must-fix 3 schema)
    /runs                — battery runs (recent first)
    /tasks               — task events with filters
    /spans               — span metrics with filters
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import statistics
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from pathlib import Path

DB_PATH = os.environ.get("EVAL_DB", str(Path(__file__).resolve().parent.parent / "data" / "eval.db"))


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _iso(ts):
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, OSError):
        return ts


# ---------------------------------------------------------------- handlers

async def health(request):
    try:
        with _db() as db:
            tables = [r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        return JSONResponse({"ok": True, "db": DB_PATH, "tables": counts})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


async def metrics(request):
    with _db() as db:
        # --- task success
        total = db.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
        completed = db.execute(
            "SELECT COUNT(*) FROM task_events WHERE status='completed'").fetchone()[0]
        failed = db.execute(
            "SELECT COUNT(*) FROM task_events WHERE status='failed'").fetchone()[0]
        by_type = {r["task_type"]: r["n"] for r in db.execute(
            "SELECT task_type, COUNT(*) n FROM task_events GROUP BY task_type ORDER BY n DESC")}
        success_by_type = {r["task_type"]: {"completed": r["c"], "total": r["t"]}
                           for r in db.execute(
            "SELECT task_type, SUM(status='completed') c, COUNT(*) t "
            "FROM task_events GROUP BY task_type")}
        by_difficulty = {r["difficulty"]: r["n"] for r in db.execute(
            "SELECT difficulty, COUNT(*) n FROM task_events GROUP BY difficulty ORDER BY n DESC")}

        # --- tool calls (spans)
        span_total = db.execute("SELECT COUNT(*) FROM span_metrics").fetchone()[0]
        tool_calls = {r["tool_name"]: r["n"] for r in db.execute(
            "SELECT tool_name, COUNT(*) n FROM span_metrics GROUP BY tool_name "
            "ORDER BY n DESC LIMIT 15")}
        span_by_type = {r["span_type"]: r["n"] for r in db.execute(
            "SELECT span_type, COUNT(*) n FROM span_metrics GROUP BY span_type")}

        # --- retries / failures
        spans_failed = db.execute(
            "SELECT COUNT(*) FROM span_metrics WHERE ok=0").fetchone()[0]
        multi_run_tasks = db.execute(
            "SELECT COUNT(DISTINCT task_key) FROM battery_runs "
            "WHERE run_n > 1").fetchone()[0]

        # --- tokens + latency
        tok = db.execute(
            "SELECT SUM(input_tok) i, SUM(output_tok) o FROM span_metrics").fetchone()
        lat = [r["latency_ms"] for r in db.execute(
            "SELECT latency_ms FROM span_metrics WHERE latency_ms IS NOT NULL")]
        lat_avg = round(statistics.mean(lat), 1) if lat else None
        lat_med = round(statistics.median(lat), 1) if lat else None

        # --- guard trigger rate: NOT tracked in current eval schema
        guard_mentions = db.execute(
            "SELECT COUNT(*) FROM task_events WHERE result_summary LIKE '%guard%' "
            "OR result_summary LIKE '%blocked%' OR result_summary LIKE '%policy%'"
        ).fetchone()[0]

    return JSONResponse({
        "as_of": datetime.now(timezone.utc).isoformat(),
        "task_success": {
            "total": total, "completed": completed, "failed": failed,
            "success_rate": round(completed / total, 4) if total else None,
            "by_task_type": success_by_type, "by_difficulty": by_difficulty,
        },
        "tool_call_count": {
            "total_spans": span_total, "by_span_type": span_by_type,
            "top_tools": tool_calls,
        },
        "retry_count": {
            "failed_spans_ok0": spans_failed,
            "tasks_with_multiple_runs": multi_run_tasks,
        },
        "guard_trigger_rate": {
            "value": None,
            "note": "guard events not tracked in eval DB yet — pending ETL schema addition",
            "proxy_mentions_in_summary": guard_mentions,
        },
        "usage": {"input_tok": tok["i"], "output_tok": tok["o"]},
        "latency_ms": {"mean": lat_avg, "median": lat_med},
    })


async def runs(request):
    limit = int(request.query_params.get("limit", 50))
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM battery_runs ORDER BY ts DESC LIMIT ?", (min(limit, 500),)
        ).fetchall()
    return JSONResponse({"count": len(rows), "runs": [
        {k: (_iso(v) if k == "ts" else v) for k, v in dict(r).items()} for r in rows]})


async def tasks(request):
    limit = int(request.query_params.get("limit", 50))
    status = request.query_params.get("status")
    task_type = request.query_params.get("task_type")
    q = "SELECT * FROM task_events"
    conds, args = [], []
    if status:
        conds.append("status=?"); args.append(status)
    if task_type:
        conds.append("task_type=?"); args.append(task_type)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY started_at DESC LIMIT ?"
    args.append(min(limit, 500))
    with _db() as db:
        rows = db.execute(q, args).fetchall()
    return JSONResponse({"count": len(rows), "tasks": [
        {k: (_iso(v) if k in ("started_at", "finished_at") else v) for k, v in dict(r).items()}
        for r in rows]})


async def spans(request):
    limit = int(request.query_params.get("limit", 50))
    tool_name = request.query_params.get("tool_name")
    q = "SELECT * FROM span_metrics"
    conds, args = [], []
    if tool_name:
        conds.append("tool_name=?"); args.append(tool_name)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(min(limit, 500))
    with _db() as db:
        rows = db.execute(q, args).fetchall()
    return JSONResponse({"count": len(rows), "spans": [
        {k: (_iso(v) if k == "ts" else v) for k, v in dict(r).items()} for r in rows]})


app = Starlette(routes=[
    Route("/health", health),
    Route("/metrics", metrics),
    Route("/runs", runs),
    Route("/tasks", tasks),
    Route("/spans", spans),
])


def main():
    ap = argparse.ArgumentParser(description="Hermes eval read-only API")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    import uvicorn
    print(f"eval-api on http://{args.host}:{args.port} (read-only, localhost)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
