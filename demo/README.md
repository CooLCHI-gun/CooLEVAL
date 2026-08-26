# Demo fixture — pipeline smoke data

**This is NOT a benchmark and NOT real traffic.**

`make-demo-data.py` generates a small, *deterministic, synthetic* telemetry set
whose only job is to let a fresh clone reproduce the CooLEVAL pipeline
end-to-end (ETL → metrics) with **no network, no real agent, and no original
telemetry**. It exists so the advertised quickstart actually runs on a clean
checkout, and so CI can prove the pipeline doesn't crash on empty/missing
sources.

The numbers it produces are **meaningless as results** — they are shape-checkers
(does the meltdown curve render? does the failure/risk ratio compute? does the
taxonomy validate?). CooLEVAL remains a *self-hosted, statistically-honest,
real-traffic* tool. **Never report these numbers as a finding.** The shape IS
intentionally like real data (short sessions succeed, long sessions fail) only
so the curve has the same *shape* as the real meltdown.

## Reproduce the pipeline

```bash
git clone https://github.com/CooLCHI-gun/CooLEVAL.git && cd CooLEVAL

# 1. Generate the (synthetic, seeded) fixture
python3 scripts/make-demo-data.py

# 2. Ingest via the same idempotent ETL used for real data
HERMES_MEM_DB=demo/memory-unified.db HERMES_TRACES=demo/traces.jsonl \
HERMES_STATE_DB=demo/state.db EVAL_DB=demo/eval.db \
    python3 scripts/eval-etl.py --rebuild

# 3. Compute metrics (same code, same stats rules)
EVAL_DB=demo/eval.db HERMES_STATE_DB=demo/state.db \
    python3 scripts/eval-metrics.py
```

The output is a full report: success rate with Wilson CI, task-type breakdown,
tool-failure rates, loopiness, and the session-level meltdown curve. The demo
set is sized so the short bucket clears the n-gate (n ≥ 20) while the long
buckets stay marked `[exploratory]` — exactly how real, low-n data is treated.

Generated artifacts (`demo/*.db`, `demo/*.jsonl`, `demo/eval.db`) are
gitignored — regenerate locally.

## Why this matters

The quickstart previously promised "regenerate every table from your own data,"
but a fresh clone had no way to do that — the telemetry sources aren't committed
and a missing `state.db` made `eval-metrics.py` traceback. This fixture makes the
promise true and gives CI a regression test for it.
