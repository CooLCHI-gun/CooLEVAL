#!/usr/bin/env python3
"""eval-tokeneff.py — Token-efficiency layer for CooLEVAL battery runs.

Cost-aware differentiator: measure how many tokens a model spends PER SUCCESS,
cache-adjusted, instead of raw totals. Reads the `usage` telemetry that
eval-runner.py captures via `hermes -z --usage-file` and stored in
battery_runs.notes.usage.

Design (Sonar must-fix 1 — define metric semantics BEFORE reporting):
  - Separates input / output / reasoning / cache-read tokens explicitly —
    they have very different costs and information content.
  - PRIMARY metric: tokens-per-success = (paid_input + output + reasoning)
    tokens / #successful-runs. "Paid input" = billed input tokens, NOT raw:
    cache hits are billed at a discount on most providers, so we report BOTH
    raw-input and paid-input (apply a configurable cache discount factor).
  - cache_state is reported per model (cache-read / raw-input ratio) so a
    warm-cache run isn't silently compared against a cold-cache run.
  - Explicitly NOT a quality proxy: token efficiency is only meaningful
    against a success floor (default ≥[80%]) and must be read together with
    success rate / duration / rubric. Fewer tokens with lower success is not
    "better".
  - Cost-per-success computed with configurable per-M token prices
    (defaults: input $3/M, output $15/M, cache-read $0.3/M — the classic
    10x/0.1x pricing shape; override with --price flags).

Data path: battery_runs table in EVAL_DB (see eval-runner.py). Runs without
usage telemetry are excluded and reported separately, so the metric never
silently degrades to guessing.

Usage:
  python3 eval-tokeneff.py                         # all models, all tasks
  python3 eval-tokeneff.py --task t1_file_summary  # one task
  python3 eval-tokeneff.py --min-success 0.8       # require >=80% success floor
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

EVAL_DB = str(Path(os.environ.get("EVAL_DB", str(Path.home() / ".hermes" /
                                                 "data" / "hermes-eval.db"))))

# Default per-1M-token prices (USD). Shape mirrors the common
# output-3-5x-input, cache-0.1x-input discount. Override with --price.
D_PRICE_IN = 3.0
D_PRICE_OUT = 15.0
D_PRICE_CACHE = 0.3


def wilson(k, n, z=1.96):
    """Wilson 95% CI for a proportion. Returns (lo, hi) or (None, None) if n<1."""
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z / denom * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5
    return (max(0.0, centre - half), min(1.0, centre + half))


def fmt_ci(lo, hi):
    if lo is None:
        return "n/a"
    return f"[{lo * 100:.1f}%–{hi * 100:.1f}%]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=None, help="filter to one task_key")
    ap.add_argument("--min-success", type=float, default=0.0,
                    help="skip models below this success floor (default 0 = show all)")
    ap.add_argument("--price-in", type=float, default=D_PRICE_IN)
    ap.add_argument("--price-out", type=float, default=D_PRICE_OUT)
    ap.add_argument("--price-cache", type=float, default=D_PRICE_CACHE)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of table")
    a = ap.parse_args()

    con = sqlite3.connect(EVAL_DB)
    con.row_factory = sqlite3.Row
    q = "SELECT run_id, task_key, status, notes FROM battery_runs"
    args = []
    if a.task:
        q += " WHERE task_key=?"
        args.append(a.task)
    rows = con.execute(q, args).fetchall()
    con.close()

    # model/task -> agg
    agg = {}
    no_usage = 0
    for r in rows:
        try:
            n = json.loads(r["notes"])
        except Exception:
            n = {}
        m = n.get("model") or "baseline"
        task = r["task_key"]
        u = n.get("usage")
        key = (m, task)
        d = agg.setdefault(key, {
            "n": 0, "ok": 0, "n_usage": 0, "in": 0, "out": 0, "cache": 0,
            "reason": 0, "calls": 0, "no_usage": 0})
        d["n"] += 1
        d["calls"] += u.get("api_calls", 0) if u else 0
        if r["status"] == "completed":
            d["ok"] += 1
        if u:
            d["n_usage"] += 1
            d["in"] += u.get("input_tokens", 0)
            d["out"] += u.get("output_tokens", 0)
            d["cache"] += u.get("cache_read_tokens", 0)
            d["reason"] += u.get("reasoning_tokens", 0)
        else:
            d["no_usage"] += 1
            no_usage += 1

    out = []
    for (m, task), d in sorted(agg.items()):
        # Token efficiency is only computable where usage telemetry exists.
        # Groups with zero usage-bearing runs are excluded from the table —
        # NOT reported as "0 tokens" (that would be a silent fabrication).
        if d["n_usage"] == 0:
            continue
        sr = d["ok"] / d["n"] if d["n"] else 0.0
        if sr < a.min_success:
            continue
        # paid input = raw input minus the discounted share of cache-read.
        # Most providers bill cache-read tokens at a discount (0.1x here).
        paid_in = d["in"] - d["cache"] * (1 - 0.1)  # cache billed at 0.1x
        paid_in = max(0, paid_in)
        billable = paid_in + d["out"] + d["reason"]
        per_success = billable / d["ok"] if d["ok"] else float("inf")
        raw_per_success = (d["in"] + d["out"] + d["reason"]) / d["ok"] if d["ok"] else float("inf")
        cache_ratio = d["cache"] / d["in"] if d["in"] else 0.0
        cost_in = d["in"] / 1e6 * a.price_in
        cost_out = d["out"] / 1e6 * a.price_out
        cost_cache = d["cache"] / 1e6 * a.price_cache
        cost = cost_in + cost_out + cost_cache
        cost_per_success = cost / d["ok"] if d["ok"] else float("inf")
        lo, hi = wilson(d["ok"], d["n"])
        out.append({
            "model": m, "task": task, "n": d["n"], "ok": d["ok"],
            "n_usage": d["n_usage"], "usage_coverage": round(d["n_usage"] / d["n"], 3)
            if d["n"] else 0,
            "success_rate": round(sr, 4), "success_ci": fmt_ci(lo, hi),
            "raw_in": d["in"], "out": d["out"], "reason": d["reason"],
            "cache": d["cache"], "paid_in": int(paid_in), "calls": d["calls"],
            "cache_ratio": round(cache_ratio, 3),
            "billable_per_success": int(billable / d["ok"]) if d["ok"] else None,
            "raw_per_success": int(raw_per_success),
            "cost_per_success_usd": round(cost_per_success, 4),
            "cache_state": "warm" if cache_ratio > 0.5 else ("partial" if cache_ratio > 0.1 else "cold"),
            "no_usage_runs": d["no_usage"],
        })

    if a.json:
        print(json.dumps({"rows": out, "excluded_no_usage": no_usage},
                         indent=2, ensure_ascii=False))
        return

    print("=== Token-efficiency (cache-adjusted) ===")
    print(f"EVAL_DB={EVAL_DB} | prices in ${a.price_in}/M out ${a.price_out}/M "
          f"cache ${a.price_cache}/M")
    hdr = (f"{'model':<14}{'task':<20}{'n':>3} {'ok':>2} {'cov%':>5} {'SR':>6} "
           f"{'CI':>16}  {'bill/ok':>12}{'raw/ok':>10}{'cache%':>7}{'state':>8}  {'$/ok':>9}")
    print(hdr)
    print("-" * len(hdr))
    for o in out:
        sr = f"{o['success_rate']*100:.0f}%"
        cov = f"{o['usage_coverage']*100:.0f}"
        print(f"{o['model']:<14}{o['task']:<20}{o['n']:>3} {o['ok']:>2} "
              f"{cov:>5} {sr:>6} {o['success_ci']:>16}  "
              f"{o['billable_per_success']:>12,}{o['raw_per_success']:>10,}"
              f"{o['cache_ratio']*100:>6.0f}%{o['cache_state']:>8}  "
              f"{o['cost_per_success_usd']:>9.4f}")
    if no_usage:
        print(f"\n(excluded {no_usage} runs without usage telemetry — "
              f"see eval-runner --usage-file note)")
    print("\nNote: token efficiency is NOT a quality proxy — read against the "
          "success floor + duration. billable/ok = (paid_input+out+reason)/success\n"
          "paid_input = raw_in − cache_read×(1−0.1) [cache billed at 0.1x]. "
          "Cache state is per-model so warm vs cold runs aren't compared directly.")


if __name__ == "__main__":
    main()
