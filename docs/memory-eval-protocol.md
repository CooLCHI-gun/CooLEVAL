# Memory-Eval Protocol (Phase 1) — CooLEVAL

Status: APPROVED (drafted by Hy3 via opencode-go, 2026-08-20; reviewed + integrated by agent; Sonar verdict APPROVE WITH CHANGES).

This module adds a memory-system comparison battery to CooLEVAL: empirically compare agent memory backends on the same seeded corpus instead of trusting vendor-reported benchmarks.

## Providers (scripts/memory_provider.py)

- `UHMAProvider` (S1) — Hermes UHMA warm tier: SQLite `warm_facts` + FTS5(unicode61), with per-keyword LIKE + CJK 2/3-char decomposition fallback. Local, zero API, ~ms recall.
- `HolographicProvider` (S2) — Hermes holographic plugin: FTS5 + Jaccard + HRR hybrid on a throwaway store (seeded via `add_fact`).
- `OpenVikingProvider` (S3) — HTTP client to a local openviking-server; gated (server-down trace if not running).

## Run

```bash
# S1 UHMA baseline against a real/throwaway Hermes DB (RAM-guarded)
python3 scripts/eval-memory.py --provider uhma --label s1 --min-ram 700

# Controlled same-corpus S1 vs S2 comparison (throwaway DBs, no prod writes)
python3 scripts/bench_s1s2.py
```

## Design notes

- Control: freeze LLM/prompt; the only variable is the memory provider.
- RAM guard: `eval-memory.py` checks `/proc/meminfo MemAvailable` before each task and aborts gracefully below `--min-ram`.
- Observable retrieval: every recall records the path that produced it (FTS / LIKE / HRR), not just the result.
- Task taxonomy: long-term multi-step / episodic recall / factual knowledge / skill workflow (T1–T8; T9–T10 = real-content probes).
- Metrics: success_rate, latency p95/p99, input + write-side tokens (S1/S2 = 0), error modes (missed_recall / hallucination / empty_recall).

## Findings (2026-08-20 smoke)

- Controlled 8-fact corpus: S1 UHMA 8/8 (100%), S2 Holographic 8/8 (100%), latency 2–6ms. Not a differentiator at small scale — consistent with CooLEVAL's existing "long-context recall saturates at small size" finding. Full differentiation needs a larger corpus + ambiguous queries + cross-session decay — shipped as `eval-memory-full.py` (see RESEARCH.md §4).

Raw per-run JSONL lives under `reports/memory-eval/` and `reports/memory-eval-full/` (gitignored).
