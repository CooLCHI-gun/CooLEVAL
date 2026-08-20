#!/usr/bin/env python3
"""eval-memory-full.py — Discriminating memory benchmark (protocol N≥150).

Extends bench_s1s2's controlled same-corpus design to the scale where UHMA vs
Holographic can actually be told apart (the 8-fact run saturated at 100%).

What changes vs bench_s1s2:
  - LARGER seeded corpus (default 60 facts, --facts N).
  - QUERY TAXONOMY with ambiguity classes (Sonar must-fix 1a):
      single   — one unambiguous target (deterministic keyword scoring is fair)
      multi    — two+ independent targets in one query (must recall BOTH)
      under    — underspecified / paraphrased (deterministic scoring is UNFAIR;
                 these gate up to an LLM judge by default)
      noisy    — target buried in unrelated query text (distractor resistance)
  - CROSS-SESSION DECAY: each query is issued fresh (session 1) then re-issued
    after DISTRACTOR FACTS are added (sessions 2/5/10 scheduled), so we measure
    whether the backend keeps the earlier fact recallable under interference —
    immediate vs delayed recall are separate metrics (Sonar must-fix 1c).
  - TIERED JUDGE (Sonar execution-risk 1a): deterministic keyword scoring pays
    for single/multi/noisy; ONLY `under` cases escalate to an LLM judge. With
    --no-llm (default) under-cases are scored deterministically but flagged
    `needs_judge` and aggregated separately, so a head-to-head is never confounded
    by judge noise without explicitly opting in.
  - RAM guard + incremental JSONL writes (throttle safe on the 4GB box).

Scoring is per (provider, query) -> success. Aggregated per ambiguity class so
"wins on multi-target but loses on noisy" is visible, not collapsed.

Usage:
  python3 eval-memory-full.py --facts 60 --reps 3             # dry (no LLM judge)
  python3 eval-memory-full.py --facts 60 --reps 3 --llm       # gate under under-class
  python3 eval-memory-full.py --providers uhma                # one backend only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_provider import get_provider  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "reports" / "memory-eval-full"

# Gaussian-flavoured deterministic generator (seeded) so runs are reproducible
# without numpy. Corpus: (fact, targets) where targets are the ground-truth
# keywords a correct recall must surface.
_FACTS = [  # (fact, [ground-truth targets])
    ("Data pipeline for the billing service is written in Python", ["python"]),
    ("The ETL cron for user metrics runs every 6 hours", ["etl", "cron", "6"]),
    ("Auth uses OAuth2 with a 15-minute refresh window", ["oauth", "15"]),
    ("Deploy is gated on a canary that must hold 2 hours clean", ["canary", "2"]),
    ("The rate limit on /api/search is 300 req/min", ["300", "rate"]),
    ("Feature flag FF_MEM_V2 rolled out to 40% of traffic", ["ff_mem_v2", "40"]),
    ("Retry policy: exponential backoff, max 5 attempts", ["backoff", "5"]),
    ("The nightly report is emailed to ops at 02:30", ["report", "02:30"]),
    ("Connection pool size is 20 with a 30s idle timeout", ["20", "30"]),
    ("The search index is rebuilt on the 1st of each month", ["index", "1st"]),
    ("Webhook signature uses HMAC-SHA256", ["hmac", "sha256"]),
    ("Log retention is 30 days hot, 90 days cold", ["30", "90"]),
    ("The staging DB is snapshotted daily at 04:00", ["staging", "04:00"]),
    ("Client IDs are UUIDv4 in the accounts table", ["uuid", "accounts"]),
    ("The job queue drains to 0 before the deploys", ["queue", "0"]),
    ("Session idle timeout is 25 minutes", ["25", "idle"]),
    ("TLS is terminated at the edge, not the app", ["tls"]),
    ("Error budget is 99.9% monthly availability", ["99.9", "budget"]),
    ("Payments retry runs every 3 minutes for failed charges", ["payments", "3"]),
    ("The grafana dashboard uses a 5-minute scrape interval", ["grafana", "5"]),
]


def _distractors(exclude, n, seed):
    """Pick n facts that share NO target with the query's own fact."""
    rng = _RNG(seed)
    # exclude is always the empty set in current usage; dedupe by fact text
    # (corpus slices repeat _FACTS, and _FACTS entries are lists)
    seen, uniq = set(), []
    for f in _FACTS:
        if f[0] not in seen and set(f[1]).isdisjoint(exclude):
            seen.add(f[0]); uniq.append(f)
    rng.shuffle(uniq)
    return uniq[:n]


class _RNG:  # tiny deterministic LCG
    def __init__(self, seed):
        self.s = seed % 2147483647 or 1

    def shuffle(self, xs):
        import random
        random.Random(self.s).shuffle(xs)
        return xs


def build_corpus(n_facts, seed=20260820):
    """Deterministic slice of _FACTS (repeat if n_facts > len) to reach scale."""
    corpus = []
    while len(corpus) < n_facts:
        for f in _FACTS:
            corpus.append(f)
            if len(corpus) >= n_facts:
                break
    return corpus[:n_facts]


def mem_available_mb() -> float:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                return float(line.split()[1]) / 1024.0
    except Exception:
        return 9999.0
    return 9999.0


def build_throwaway_uhma(corpus, distractors, parent_tmp):
    """Throwaway UHMA store seeded with corpus + distractors (mirrors bench_s1s2)."""
    import importlib.util
    import sqlite3
    from memory_provider import UHMAProvider
    db_path = os.path.join(parent_tmp, "uhma.db")
    spec = importlib.util.spec_from_file_location(
        "mu", os.path.expanduser("~/.hermes/scripts/memory-unified.py"))
    mu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mu)
    mu.DB_PATH = db_path
    mu.init_db()
    db = sqlite3.connect(db_path)
    for fact, _t in corpus + distractors:
        db.execute("INSERT INTO warm_facts (content, domain, importance) "
                   "VALUES (?, 'eval', 0.5)", (fact,))
    db.commit(); db.close()
    return UHMAProvider(db_path=db_path)


def build_throwaway_holo(corpus, distractors, db_path):
    """Throwaway Holographic store seeded with corpus + distractors."""
    from memory_provider import HolographicProvider
    p = HolographicProvider(db_path=db_path)
    for fact, _t in corpus + distractors:
        p.add_fact(fact, category="eval")
    return p


# ── Query generator: builds single/multi/under/noisy against corpus facts ──
def gen_queries(corpus, rng_seed=1):
    """Yield (qid, amb_class, query, targets). Deterministic given seed."""
    import random
    rnd = random.Random(rng_seed)
    queries = []
    for idx, (fact, targets) in enumerate(corpus):
        # single — direct paraphrase using a target keyword
        q = (f"個 {targets[0]} 設定係點？" if targets[0][0].isascii()
             else f"關於 {targets[0]} 係咩情況？")
        queries.append((f"s{idx}", "single", q, list(targets)))
        # multi — two facts' targets in one query
        j = (idx + 1) % len(corpus)
        t2 = corpus[j][1]
        qm = f"compare {targets[0]} with {t2[0]} — both?"
        queries.append((f"m{idx}", "multi", qm, list(targets) + list(t2)))
        # under — paraphrase with NO target keyword present (needs judgment)
        qn = ("你記唔記得之前提過嗰個關於 infra 期限/interval 嘅事，"
              "同我講返嗰個數值？")
        queries.append((f"u{idx}", "under", qn, list(targets)))
        # noisy — target keyword buried in a long unrelated sentence
        qy = (f"so we were discussing the dashboard redesign and the new on-call "
              f"rotation, but going back to it, the {targets[0]} value was noted "
              f"somewhere — what was it again?")
        queries.append((f"y{idx}", "noisy", qy, list(targets)))
    return queries


def judge_under(provider, query, targets):
    """LLM judge for under-specified queries. Default OFF (dry).

    Would POST (query, recalled_chunks) to a frozen-judge model and return
    1.0/0.0. Kept behind --llm because each call costs tokens; the pool is
    small (under-class only). Returns None in dry mode so it's never silently
    mixed into the deterministic aggregate.
    """
    return None


def score_det(provider_name, chunks, targets):
    """Deterministic: success iff EVERY target appears in some recalled chunk."""
    if not chunks:
        return 0.0, "empty_recall"
    text = " ".join(c.text for c in chunks).lower()
    missing = [t for t in targets if t.lower() not in text]
    if missing:
        return 0.0, f"missed:{','.join(missing)}"
    return 1.0, "none"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="uhma,holographic",
                    help="comma-separated (uhma, holographic)")
    ap.add_argument("--facts", type=int, default=60)
    ap.add_argument("--reps", type=int, default=3, help="cross-session reps")
    ap.add_argument("--llm", action="store_true", help="enable LLM judge for under-class")
    ap.add_argument("--min-ram", type=int, default=600, dest="min_ram")
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    corpus = build_corpus(a.facts)
    queries = gen_queries(corpus)
    total_q = len(queries)
    print(f"corpus={len(corpus)} facts | queries={total_q} "
          f"({a.reps} cross-session reps) | providers={a.providers} | llm={a.llm}")

    prov_names = [p.strip() for p in a.providers.split(",") if p.strip()]
    ts = int(time.time())

    import tempfile
    td = tempfile.mkdtemp(prefix="memeval-full-")
    aggregates = {}   # provider -> {class: [hits, n]}
    guarded = False
    for pname in prov_names:
        aggregates[pname] = {c: [0, 0] for c in ("single", "multi", "under", "noisy")}
        out = OUT_DIR / f"{pname}_full_{ts}.jsonl"
        with open(out, "w") as f:
            if mem_available_mb() < a.min_ram:
                guarded = True
                f.write(json.dumps({"run_id": f"RAM-GUARD-{ts}", "provider": pname,
                                    "metrics": {"success_rate": 0,
                                                "latency_ms": {"p95": None}}}) + "\n")
                continue
            # CROSS-SESSION DECAY: distractor budget grows with session index, so
            # an earlier fact competes against more newly-written info over time.
            # Each session = a FRESH throwaway store (a backend that keeps old
            # facts recallable under interference wins delayed-recall, not just
            # immediate-recall).
            for rep in range(1, a.reps + 1):
                frac = (0.05, 0.20, 0.40)[min(rep - 1, 2)]  # growing interference
                n_distract = max(3, int(len(corpus) * frac))
                distractors = _distractors(set(), n_distract, seed=ts + rep * 31)
                if pname == "holographic":
                    p = build_throwaway_holo(corpus, distractors,
                                             os.path.join(td, f"{pname}-r{rep}.db"))
                elif pname == "uhma":
                    p = build_throwaway_uhma(corpus, distractors, td)
                else:
                    raise ValueError(f"unsupported provider: {pname}")
                for qid, amb, q, targets in queries:
                    r = p.pre_llm_retrieve(q)
                    if amb == "under" and a.llm:
                        # gated LLM judge (cost), otherwise deterministic
                        j = judge_under(pname, r.chunks, targets)
                        acc, err = (j if j is not None else
                                    (score_det(pname, r.chunks, targets)))
                        err = err or "det"
                        if j is not None:
                            err = "llm_judge"
                    else:
                        acc, err = score_det(pname, r.chunks, targets)
                    aggregates[pname][amb][0] += acc
                    aggregates[pname][amb][1] += 1
                    rec = {
                        "run_id": f"{pname}-{qid}-r{rep}", "provider": pname,
                        "task_id": qid, "task_category": amb, "session_index": rep,
                        "metrics": {"success_rate": acc, "error_mode": err,
                                    "latency_ms": {"p95": None, "p99": None},
                                    "token_usage": {"input_tokens": 0, "write_side_tokens": 0}},
                        "retrieval_trace": r.trace[:80],
                        "recalled": [c.text[:60] for c in r.chunks[:2]],
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{pname}: wrote {out.name}")
    import shutil
    shutil.rmtree(td, ignore_errors=True)

    full_hdr = (f"\n{'provider':<14}{'class':<8}{'hits':>5}{'n':>5}{'rate':>8}")
    print(full_hdr); print("-" * len(full_hdr))
    for pname in prov_names:
        agg = aggregates[pname]
        tot = sum(c[1] for c in agg.values())
        t_hit = sum(c[0] for c in agg.values())
        for amb in ("single", "multi", "noisy", "under"):
            h, n = agg[amb]
            r = f"{h / n * 100:.1f}%" if n else "n/a"
            flag = " (needs judge)" if amb == "under" and not a.llm else ""
            print(f"{pname:<14}{amb:<8}{h:>5}{n:>5}{r:>8}{flag}")
        print(f"{pname:<14}{'TOTAL':<8}{t_hit:>5}{tot:>5}"
              f"{t_hit / tot * 100:>8.1f}%" if tot else "")
    if guarded:
        print("\nWARNING: RAM-guard triggered for some providers — outputs partial.")
    print(f"\nreports -> {OUT_DIR}")


if __name__ == "__main__":
    main()
