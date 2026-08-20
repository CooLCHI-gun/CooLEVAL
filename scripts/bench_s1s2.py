#!/usr/bin/env python3
"""Controlled S1 (UHMA) vs S2 (Holographic) memory-eval on the SAME seeded corpus.

Each provider gets an identical throwaway store seeded with 8 eval facts, then
recalls against the 8 task queries. Both use their real retrieval logic:
  S1 UHMA        — FTS5(unicode61) + per-keyword LIKE + CJK decomposition (local, zero API)
  S2 Holographic — FTS5 + Jaccard + HRR hybrid (plugin's own store + retriever)
Read-only w.r.t. production memory (throwaway DBs under /tmp).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

HERMES_SRC = "/root/hermes-agent-source"
sys.path.insert(0, HERMES_SRC)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_provider import UHMAProvider, HolographicProvider  # noqa: E402

# (fact_content, task_id, query, ground-truth keywords)
CORPUS = [
    ("用戶鍾意用 Python 寫 data pipeline，唔用 Node", "T1",
     "用咩語言寫 ETL data pipeline？", ["python"]),
    ("side project spec: PRD 要包括 overview、requirements、architecture、timeline",
     "T2", "side project PRD 要包括咩 sections？", ["prd", "overview", "requirements", "timeline"]),
    ("上次 debug 個 OAuth bug 用咗 3 日先搞掂", "T3",
     "上次 debug 個 OAuth bug 搞咗幾耐？", ["oauth", "3", "三日"]),
    ("上月 meeting notes: 議題 review memory architecture，action items 包括整理 benchmark",
     "T4", "上個月 meeting notes 嘅 action items 係咩？", ["meeting", "action"]),
    ("internal API rate limit 1000 req/min，auth method 係 Bearer token",
     "T5", "internal API 嘅 rate limit 同 auth method？", ["rate", "auth", "bearer"]),
    ("glossary: ProjectX 對應公司內部嘅 data pipeline 重構 project",
     "T6", "ProjectX 對應邊個 code name / 定義？", ["projectx"]),
    ("custom git workflow sync-up 包含 rebase + force push 邏輯",
     "T7", "git sync-up 包含咩步驟？", ["rebase", "force", "sync"]),
    ("regex cleaning rule: 移除空白行同 trailing spaces",
     "T8", "條 regex cleaning rule 係點？", ["regex", "trailing", "空白"]),
]


def score(kws, chunks):
    if not chunks:
        return 0.0, "empty_recall"
    return (1.0, "none") if any(k.lower() in c.text.lower() for c in chunks
                                for k in kws) else (0.0, "missed_recall")


def build_uhma(temp_db):
    spec = importlib.util.spec_from_file_location(
        "mu", os.path.expanduser("~/.hermes/scripts/memory-unified.py"))
    mu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mu)
    mu.DB_PATH = temp_db
    mu.init_db()
    db = mu.get_db()
    for content, *_ in CORPUS:
        db.execute("INSERT INTO warm_facts (content, domain, importance) VALUES (?, 'eval', 0.5)",
                   (content,))
    db.commit()
    db.close()
    return UHMAProvider(db_path=temp_db)


def build_holo(temp_db):
    p = HolographicProvider(db_path=temp_db)
    for content, *_ in CORPUS:
        p.add_fact(content, category="eval")
    return p


def main():
    td = tempfile.mkdtemp(prefix="memeval-")
    s1 = build_uhma(os.path.join(td, "uhma.db"))
    s2 = build_holo(os.path.join(td, "holo.db"))

    print(f"{'task':6} {'cat':9} {'S1 UHMA':>9} {'S2 HOLO':>9}")
    print("-" * 42)
    agg = {"s1": [0.0, 0], "s2": [0.0, 0]}
    for content, tid, q, kws in CORPUS:
        r1 = s1.pre_llm_retrieve(q)
        a1, e1 = score(kws, r1.chunks)
        r2 = s2.pre_llm_retrieve(q)
        a2, e2 = score(kws, r2.chunks)
        for key, a, r in (("s1", a1, r1), ("s2", a2, r2)):
            agg[key][0] += a
            agg[key][1] += 1
        print(f"{tid:6} {e1:>19} / {e2:>9}")
        print(f"{'':6}   S1 acc={a1} lat={r1.latency_ms}ms | S2 acc={a2} lat={r2.latency_ms}ms")
        print(f"{'':6}   S1 trace: {r1.trace[:70]}")
        print(f"{'':6}   S2 trace: {r2.trace[:40]}")
    print("-" * 42)
    n = agg["s1"][1]
    print(f"RECALL  S1 UHMA: {agg['s1'][0]:.0f}/{n} ({agg['s1'][0]/n*100:.0f}%) "
          f"| S2 HOLO: {agg['s2'][0]:.0f}/{n} ({agg['s2'][0]/n*100:.0f}%)")
    print("temp dbs:", td)


if __name__ == "__main__":
    main()
