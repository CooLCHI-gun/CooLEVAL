"""cooleval plugin — read-only, self-hosted CooLEVAL query helpers for Hermes.

What this IS:
  A Hermes plugin that gives an agent lightweight, READ-ONLY access to CooLEVAL
  eval results that exist on the SAME machine (the eval.db you built yourself by
  running scripts/eval-etl.py locally). It never calls out to the network and it
  never writes — it only opens a local SQLite file in read-only mode.

What this is NOT:
  - NOT a connection to anyone else's server. This plugin reads YOUR local file
    and nothing else. No telemetry, no phone-home.
  - NOT the MCP server (that lives in scripts/cooleval_mcp.py). This is a
    lightweight function-based alternative you can call directly.

Setup:
  1. Clone CooLEVAL and build your own eval.db on this machine:
       python3 scripts/eval-etl.py      # ingests YOUR telemetry -> YOUR eval.db
  2. Point this plugin at that file (or leave EVAL_DB set):
       export EVAL_DB=/abs/path/to/your-eval.db
  3. Enable the plugin: hermes plugins enable cooleval
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger("hermes.plugins.cooleval")

# Self-hosted: default reads a repo-relative eval.db, overridable via EVAL_DB.
_HERE = Path(__file__).resolve().parent        # .../agent-plugin/cooleval
_REPO = _HERE.parent.parent                     # .../CooLEVAL
EVAL_DB = os.environ.get("EVAL_DB", str(_REPO / "eval.db"))

_SQL_FIRST = re.compile(r"^\s*(select|with)\b", re.IGNORECASE | re.DOTALL)


def _open_readonly() -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{EVAL_DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _query(sql: str, params: tuple = ()) -> list[dict]:
    if not _SQL_FIRST.match(sql):
        raise ValueError("read-only plugin: only SELECT/WITH allowed")
    con = _open_readonly()
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def _no_db() -> str:
    """Fail-open: usable message when the local eval.db isn't present."""
    return json.dumps({
        "note": "no local eval.db found (self-hosted: build your own with "
                "scripts/eval-etl.py, then point EVAL_DB at it)",
        "eval_db": EVAL_DB,
    }, ensure_ascii=False)


def get_metrics() -> str:
    """Top-level success metrics (n statuses + completed) from the local eval.db."""
    try:
        rows = _query("SELECT status, COUNT(*) AS c FROM task_events GROUP BY status")
    except Exception as e:  # noqa: BLE001 — fail-open
        return _no_db()
    if not rows:
        return json.dumps({"note": "no task_events in local eval.db", "eval_db": EVAL_DB})
    by_status = {r["status"]: r["c"] for r in rows}
    total = sum(by_status.values())
    return json.dumps({"total": total, "by_status": by_status, "eval_db": EVAL_DB},
                      ensure_ascii=False)


def get_battery(limit: int = 20) -> str:
    """Recent battery_runs (run_id, task, status, notes) — read-only."""
    limit = max(1, min(int(limit), 500))
    try:
        rows = _query("SELECT run_id, task_key, status, notes FROM battery_runs "
                      "ORDER BY rowid DESC LIMIT ?", (limit,))
    except Exception as e:  # noqa: BLE001 — fail-open
        return _no_db()
    return json.dumps(rows, ensure_ascii=False)


# ── plugin registration (observer-style; fail-open, no blocking) ─────────────
def on_pre_tool_call(tool_name: str, args: dict, **kwargs):
    # Observer-only: never blocks, never injects. Just logs that the helper
    # module loaded so an operator can confirm it's live.
    try:
        # verify eval.db is reachable (read-only) once per first terminal/write
        if tool_name in ("terminal", "write_file", "patch"):
            _query("SELECT 1 FROM task_events LIMIT 1")
    except Exception:  # noqa: BLE001 — fail-open; report path, don't break calls
        logger.debug("cooleval: local eval.db not present (%s)", EVAL_DB)
    return None  # always allow


def register(ctx):
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    logger.info("cooleval: registered pre_tool_call observer (read-only, self-hosted); eval_db=%s", EVAL_DB)
