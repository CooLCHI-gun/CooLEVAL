#!/usr/bin/env python3
"""Memory-recall health report (CooLEVAL #1 — Sonar MF1/MF2).

Aggregates the UHMA observable-retrieval trace log (~/.hermes/cognitive/
uhma-retrieval-trace.log, one JSON line per pre_llm_call turn) into an
OPERATIONAL DIAGNOSTIC report. PRIVACY: outputs AGGREGATE COUNTS ONLY — never
raw keywords, fact ids, or content. PRIVACY guardrail (Sonar MF2): if you need
the report committed to a public repo, ensure only this aggregate output is
committed, never the raw trace.

Metrics (Sonar MF1 metric semantics):
  opportunity_count     — turns with any retrieval attempt
  recall_success_count  — turns where merged retrieval produced >=1 fact
  success_rate + Wilson 95% CI
  fts_hit_rate          — turns where FTS produced at least one id
  graph_hit_rate        — turns where graph produced at least one id
  cold_ref_coverage     — turns where cold refs were present
  rolling window by time with n shown per window (no point estimate without n)

Small-n gate: n < 20 flagged EXPLORATORY (report is not a reliability claim).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import deque
from datetime import datetime

TRACE_LOG = os.path.expanduser("~/.hermes/cognitive/uhma-retrieval-trace.log")
N_GATE = 20


def _wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% CI for a binomial proportion k/n (returns low, high)."""
    if n == 0:
        return (0.0, 1.0)
    z = 1.96
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def load_records(path: str):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", default=TRACE_LOG)
    ap.add_argument("--window-hours", type=int, default=0, help="0 = no rolling window")
    a = ap.parse_args()

    rows = load_records(a.trace)
    n = len(rows)
    if n == 0:
        print("memory-recall-health: no records yet (trace log has no entries)")
        return

    hits = sum(1 for r in rows if r.get("merged_hit"))
    fts = sum(1 for r in rows if r.get("fts_ids"))
    graph = sum(1 for r in rows if r.get("graph_ids"))
    cold = sum(1 for r in rows if r.get("cold_refs"))
    lo, hi = _wilson(hits, n)

    print("=== Memory Recall Health Report (OPERATIONAL DIAGNOSTIC) ===")
    print(f"source: {a.trace}")
    print(f"records: {n}  [{ 'EXPLORATORY (n<20)' if n < N_GATE else 'n>=20' }]")
    print(f"recall_success: {hits}/{n} = {hits/n*100:.1f}%  Wilson95 [{lo*100:.1f}%, {hi*100:.1f}%]")
    print(f"fts_hit_rate:   {fts}/{n} = {fts/n*100:.1f}%")
    print(f"graph_hit_rate: {graph}/{n} = {graph/n*100:.1f}%")
    print(f"cold_ref_cov:   {cold}/{n} = {cold/n*100:.1f}%")

    if a.window_hours > 0 and "ts" in rows[0]:
        print(f"\n=== Rolling windows ({a.window_hours}h) — n shown per window ===")
        base = None
        try:
            base = datetime.strptime(rows[0]["ts"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        if base:
            windows = {}  # window_idx -> [hits, n]
            for r in rows:
                try:
                    d = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                idx = int((d - base).total_seconds() // (a.window_hours * 3600))
                w = windows.setdefault(idx, [0, 0])
                w[0] += 1 if r.get("merged_hit") else 0
                w[1] += 1
            for idx in sorted(windows):
                wk, wn = windows[idx]
                print(f"  window~{idx * a.window_hours}h: n={wn} hits={wk} "
                      f"rate={wk / wn * 100:.1f}%" if wn else f"  window~{idx * a.window_hours}h: n=0")

    print("\nnote: aggregate counts only (privacy). n<20 is exploratory, not a reliability claim.")


if __name__ == "__main__":
    main()
