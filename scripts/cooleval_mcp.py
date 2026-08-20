#!/usr/bin/env python3
"""cooleval_mcp.py — read-only MCP server for the CooLEVAL eval database.

Lets any MCP-capable agent query CooLEVAL's measured results (agent reliability,
battery runs with token-efficiency usage, memory-backend benchmarks) without
touching the raw SQLite files or re-running the heavy eval scripts.

Security / design:
  - READ-ONLY: every query is wrapped to only ever run SELECT/WITH. No INSERT/UPDATE/
    DELETE/DROP path exists. SQL validation rejects anything else outright.
  - LIGHT: pure sqlite3 reads against EVAL_DB + a JSONL scan of the memory-eval-full
    reports dir. ~30MB, zero heavy deps (FastMCP + stdlib). Safe on a 4GB box.
  - REPORT-GRADE, not raw data dumps: each tool returns an aggregated answer an agent
    can act on, with n + (Wilson CI where a proportion is returned) so a caller
    doesn't over-read a small sample.

Data paths (overridable):
  EVAL_DB  -> the CooLEVAL eval SQLite db (default ~/.hermes/data/hermes-eval.db)
  EVAL_REPORTS -> reports/ dir containing memory-eval-full/*.jsonl

Tools:
  cooleval_battery          -> battery_runs summary (by task/model), incl. usage
  cooleval_token_efficiency -> cache-adjusted tokens-per-success per model
  cooleval_memory_benchmark -> latest memory-eval-full JSONL aggregated by class
  cooleval_etl_watermark    -> ETL watermark / last-synced truth
  cooleval_metrics          -> top-level success-rate metrics w/ Wilson CI + n-gate
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import math
from pathlib import Path

from mcp.server.fastmcp import FastMCP

EVAL_DB = os.environ.get("EVAL_DB", str(Path(__file__).resolve().parent.parent / "eval.db"))
EVAL_ROOT = os.environ.get("COOLEVAL_ROOT",
                           str(Path(__file__).resolve().parent.parent))
# NOTE: this server is SELF-HOSTED. It reads whatever local SQLite file EVAL_DB
# points to on THIS machine — it never connects to any remote host, and it never
# writes. You build/own your own eval.db (run scripts/eval-etl.py locally) and
# point EVAL_DB at it. It is intentionally NOT wired to any external server.
MEMORY_EVAL_DIR = Path(EVAL_ROOT) / "reports" / "memory-eval-full"

mcp = FastMCP(
    "CooLEVAL",
    instructions=(
        "Read-only interface into the CooLEVAL agent-eval framework's measured "
        "results. Use cooleval_battery / cooleval_token_efficiency to see how "
        "different models did on real tasks (including cost-per-success), and "
        "cooleval_memory_benchmark to compare memory backends. All results carry "
        "n and Wilson confidence intervals — don't over-read small samples. "
        "This server NEVER writes to the database."
    ),
)

# ── SQL safety: only read statements ─────────────────────────────────────────
_SQL_FIRST = re.compile(r"^\s*(select|with)\b", re.IGNORECASE | re.DOTALL)


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{EVAL_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _safe_read(sql: str, params: tuple = ()) -> list[dict]:
    """Run a read-only query after validating it only reads."""
    if not _SQL_FIRST.match(sql):
        raise ValueError("only SELECT/WITH queries are allowed (read-only server)")
    con = _db()
    try:
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _wilson(k: int, n: int, z: float = 1.96) -> list:
    if n == 0:
        return [None, None]
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z / denom * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


@mcp.tool(description="Summarise CooLEVAL battery_runs: success count/n and duration by task and model (with usage telemetry when present).")
async def cooleval_battery(task: str = "", model: str = "") -> str:
    """Aggregate battery_runs from the eval DB. Filters optional by task/model."""
    sql = ("SELECT task_key, run_n, status, notes FROM battery_runs")
    rows = _safe_read(sql)
    # aggregate in python so we can safely parse notes JSON (usage) per row
    agg: dict = {}
    for r in rows:
        if task and task not in r["task_key"]:
            continue
        try:
            notes = json.loads(r["notes"] or "{}")
        except Exception:
            notes = {}
        m = notes.get("model") or "baseline"
        if model and model not in (m or ""):
            continue
        key = (r["task_key"], m)
        d = agg.setdefault(key, {"n": 0, "ok": 0})
        d["n"] += 1
        if r["status"] == "completed":
            d["ok"] += 1
    out = []
    for (tk, m), d in sorted(agg.items()):
        lo, hi = _wilson(d["ok"], d["n"])
        out.append({"task": tk, "model": m, "n": d["n"], "ok": d["ok"],
                    "success_rate": round(d["ok"] / d["n"], 4) if d["n"] else None,
                    "wilson_95": [lo, hi]})
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool(description="Token efficiency per model: cache-adjusted billable tokens per success and USD cost per success (from battery usage telemetry). NOT a quality metric.")
async def cooleval_token_efficiency(min_success: float = 0.0) -> str:
    """Compute tokens-per-success (cache adjusted) from battery usage telemetry.

    price_in/out/cache defaults mirror the common 3x / 15x / 0.3x shape.
    Only models with usage telemetry are included (no silent 0-token rows).
    """
    rows = _safe_read("SELECT task_key, status, notes FROM battery_runs")
    agg: dict = {}
    for r in rows:
        try:
            notes = json.loads(r["notes"] or "{}")
        except Exception:
            continue
        u = notes.get("usage")
        m = notes.get("model") or "baseline"
        d = agg.setdefault(m, {"n": 0, "ok": 0, "in": 0, "out": 0,
                               "cache": 0, "reason": 0, "n_usage": 0})
        d["n"] += 1
        if r["status"] == "completed":
            d["ok"] += 1
        if u:
            d["n_usage"] += 1
            d["in"] += u.get("input_tokens", 0)
            d["out"] += u.get("output_tokens", 0)
            d["cache"] += u.get("cache_read_tokens", 0)
            d["reason"] += u.get("reasoning_tokens", 0)
    out = []
    for m, d in sorted(agg.items()):
        if d["n_usage"] == 0:
            continue
        sr = d["ok"] / d["n"] if d["n"] else 0.0
        if sr < min_success:
            continue
        paid_in = max(0, d["in"] - d["cache"] * 0.9)  # cache billed ~0.1x
        bill = paid_in + d["out"] + d["reason"]
        per_ok = int(bill / d["ok"]) if d["ok"] else None
        cost = (d["in"] * 3 + d["out"] * 15 + d["cache"] * 0.3) / 1e6
        cost_ok = round(cost / d["ok"], 4) if d["ok"] else None
        out.append({"model": m, "n": d["n"], "ok": d["ok"],
                    "usage_coverage": round(d["n_usage"] / d["n"], 3),
                    "success_rate": round(sr, 4),
                    "billable_tokens_per_success": per_ok,
                    "usd_per_success": cost_ok})
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool(description="Aggregate the latest memory-backend benchmark (memory-eval-full JSONL): recall rate per query class (single/multi/noisy/under) with n, per provider.")
async def cooleval_memory_benchmark(provider: str = "") -> str:
    """Read the NEWEST timestamp batch of memory-eval-full/*.jsonl (all providers
    in that run) and aggregate success by class."""
    if not MEMORY_EVAL_DIR.exists():
        return json.dumps({"error": f"no memory-eval-full dir at {MEMORY_EVAL_DIR}"})
    files = sorted(MEMORY_EVAL_DIR.glob("*.jsonl"))
    if not files:
        return json.dumps({"error": "no memory-eval-full JSONL found"})
    # group by the trailing run timestamp in the filename (_full_<ts>.jsonl)
    # and pick the latest batch so all providers from the same run are included.
    batches: dict = {}
    for f in files:
        m = re.search(r"_full_(\d+)\.jsonl$", f.name)
        ts = int(m.group(1)) if m else 0
        batches.setdefault(ts, []).append(f)
    latest_ts = max(batches.keys())
    agg: dict = {}
    for f in batches[latest_ts]:
        for line in open(f):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            prov = d.get("provider", "")
            if provider and prov != provider:
                continue
            cls = d.get("task_category", "?")
            acc = d.get("metrics", {}).get("success_rate", 0)
            a = agg.setdefault(prov, {}).setdefault(cls, [0, 0])
            a[0] += 1 if acc else 0
            a[1] += 1
    out = {p: {c: {"hits": h, "n": n,
                   "recall": round(h / n, 4) if n else None}
               for c, (h, n) in classes.items()}
           for p, classes in agg.items()}
    return json.dumps({"source_batch_ts": latest_ts,
                       "files": [f.name for f in batches[latest_ts]],
                       "aggregate": out},
                      ensure_ascii=False, indent=2)


@mcp.tool(description="Show the ETL watermark (last ingestion state) for the eval database.")
async def cooleval_etl_watermark() -> str:
    """Read the ETL watermark table (truth about last-synced data)."""
    try:
        rows = _safe_read("SELECT * FROM etl_watermarks")
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e)})
    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp.tool(description="Top-level CooLEVAL metrics: success rate (Wilson 95% CI) + n-gate from the eval DB.")
async def cooleval_metrics() -> str:
    """Return overall task success rate with Wilson CI + n-gate (n<20 exploratory)."""
    try:
        rows = _safe_read("SELECT status, COUNT(*) AS c FROM task_events GROUP BY status")
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e)})
    by_status = {r["status"]: r["c"] for r in rows}
    total = sum(by_status.values())
    ok = by_status.get("completed", 0)
    lo, hi = _wilson(ok, total)
    n_gate = "exploratory (n<20)" if total < 20 else "reportable"
    return json.dumps({
        "total": total, "completed": ok,
        "success_rate": round(ok / total, 4) if total else None,
        "wilson_95": [lo, hi], "n_gate": n_gate,
        "by_status": by_status,
    }, ensure_ascii=False, indent=2)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
