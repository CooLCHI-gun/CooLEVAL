#!/usr/bin/env python3
"""CooLEVAL memory-eval runner (Phase 1 protocol).

Usage:
  python3 eval-memory.py --provider uhma            # one provider battery
  python3 eval-memory.py --provider uhma --tasks T1 T3 --reps 2

Emits JSONL to reports/memory-eval/ per the Phase 1 protocol record schema.
Read-only against UHMA; rules-based recall scoring (no LLM judge needed for
the smoke baseline — swap in a frozen LLM-judge for the full benchmark).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_provider import get_provider  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "reports", "memory-eval")


def mem_available_mb() -> float:
    """MemAvailable from /proc/meminfo (MB)."""
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                return float(line.split()[1]) / 1024.0
    except Exception:
        return 9999.0
    return 9999.0

# task_id -> (category, query, ground-truth keywords[], setup note)
TASKS = {
    "T1": ("long_term", "用咩語言寫 ETL data pipeline？",
           ["python"], "寫入：用戶偏好 Python 寫 data pipeline（session 1）"),
    "T2": ("long_term", "side project PRD 要包括咩 sections？",
           ["prd", "spec", "specification"], "跨 5 session 堆 spec"),
    "T3": ("episodic", "上次 debug 個 OAuth bug 搞咗幾耐？",
           ["oauth", "3", "三日"], "寫入：OAuth bug 3 日"),
    "T4": ("episodic", "上個月 meeting notes 嘅 action items 係咩？",
           ["meeting", "action"], "寫入：meeting notes + action items"),
    "T5": ("factual", "internal API 嘅 rate limit 同 auth method？",
           ["rate", "auth"], "knowledge：API rate limit/auth"),
    "T6": ("factual", "ProjectX 對應邊個 code name / 定義？",
           ["projectx"], "glossary code name 對應表"),
    "T7": ("skill", "git sync-up 包含咩步驟？",
           ["rebase", "sync"], "寫入：custom git workflow sync-up"),
    "T8": ("skill", "條 regex cleaning rule 係點？",
           ["regex", "clean"], "寫入：regex cleaning rule"),
    # real-content probes (facts already in warm tier) — non-zero baseline
    "T9": ("factual", "Hermes 嘅 UHMA / memory architecture 係點部署？",
           ["memory", "uhma", "warm_facts"], "查真實 warm tier：UHMA 部署"),
    "T10": ("factual", "用戶用邊啲 LLM provider / 模型？",
           ["opencode", "deepseek", "nvidia"], "查真實 memory：provider 偏好"),
}


def score(tax, chunks):
    """Rule-based: 1.0 if any ground-truth keyword in a recalled chunk."""
    if not chunks:
        return 0.0, "empty_recall"
    hits = [kw.lower() for kw in tax if any(kw.lower() in c.text.lower()
                                            for c in chunks)]
    if not hits:
        return 0.0, "missed_recall"
    return 1.0, "none"


def run_battery(provider_name, task_ids, reps, label, min_ram_mb=700):
    os.makedirs(OUT_DIR, exist_ok=True)
    prov = get_provider(provider_name)
    ts = int(time.time())
    out = os.path.join(OUT_DIR, f"{label}_{provider_name}_{ts}.jsonl")
    lat = []
    n = 0
    guarded = False
    with open(out, "w") as f:
        for tid in task_ids:
            cat, q, tax, _ = TASKS[tid]
            for rep in range(reps):
                if mem_available_mb() < min_ram_mb:
                    f.write(json.dumps({"run_id": f"RAM-GUARD-{ts}",
                                        "scenario_id": provider_name,
                                        "task_id": tid, "task_category": cat,
                                        "session_index": rep,
                                        "metrics": {"success_rate": 0,
                                                    "latency_ms": {"p95": None, "p99": None},
                                                    "token_usage": {"input_tokens": 0, "write_side_tokens": 0},
                                                    "error_mode": "ram_guard_abort"},
                                        "retrieval_trace": "MEMORY GUARD: available="
                                                           f"{mem_available_mb():.0f}MB < {min_ram_mb}MB"},
                                      ensure_ascii=False) + "\n")
                    guarded = True
                    continue
                r = prov.pre_llm_retrieve(q)
                acc, err = score(tax, r.chunks)
                lat.append(r.latency_ms)
                rec = {
                    "run_id": f"{label}-{provider_name}-{tid}-{rep}",
                    "scenario_id": provider_name.upper()[0] + "x",
                    "task_id": tid,
                    "task_category": cat,
                    "session_index": rep + 1,
                    "metrics": {
                        "success_rate": acc,
                        "latency_ms": {"p95": None, "p99": None},
                        "token_usage": {"input_tokens": 0, "write_side_tokens": 0},
                        "error_mode": err,
                    },
                    "retrieval_trace": r.trace,
                    "recalled": [c.text[:80] for c in r.chunks[:3]],
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    if lat:
        lat_sorted = sorted(lat)
        p95 = lat_sorted[min(len(lat_sorted)-1, int(0.95*len(lat_sorted)))]
        p99 = lat_sorted[min(len(lat_sorted)-1, int(0.99*len(lat_sorted)))]
    else:
        p95 = p99 = 0
    print(json.dumps({"out": out, "runs": n, "provider": provider_name,
                      "latency_p95_ms": p95, "latency_p99_ms": p99,
                      "ram_guard_aborts": guarded, "mem_available_mb": round(mem_available_mb(), 1)},
                     ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True)
    ap.add_argument("--tasks", default=",".join(TASKS))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--label", default="smoke")
    ap.add_argument("--min-ram", type=int, default=700, dest="min_ram")
    a = ap.parse_args()
    tids = [t.strip() for t in a.tasks.split(",") if t.strip()]
    run_battery(a.provider, tids, a.reps, a.label, min_ram_mb=a.min_ram)
