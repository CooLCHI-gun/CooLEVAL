# CooLEVAL

**Dogfood evaluation framework for production AI agents.**

CooLEVAL measures how reliable your agent actually is on *its own real workload* —
not on synthetic leaderboard tasks. It combines an artifact-verified task battery,
survival/hazard-curve analysis of session reliability, idempotent telemetry ETL,
and model A/B comparison with real token economics.

## Why this exists

LLM agents look impressive on short tasks and silently fall apart on long ones.
CooLEVAL was built after we measured the following on **622 real agent sessions**
(no synthetic benchmarks):

| Session duration | Success rate (95% CI) | n |
|---|---:|---:|
| < 15 min | 97.9% [96.0–99.0] | 388 |
| 15 min – 1 h | 94.2% [84.4–98.0] | 52 |
| 1 – 4 h | 25.0% [7.1–59.1] | 8 |
| 4 – 24 h | 0.0% [0.0–35.4] | 7 |
| > 24 h | 0.0% [0.0–24.3] | 12 |

That is a **meltdown curve**: reliability decays non-linearly with session length.
Most evaluation tools never see this because they test prompts, not real sessions.
CooLEVAL's hazard-curve analysis surfaces it on your own data, continuously.

## Architecture

```
L0  Data layer   eval-etl.py      ← idempotent ETL: JSONL traces + SQLite lifecycle +
                                    sessions → eval.db (watermarks, dedup, reconciliation)
L1  Metrics      eval-metrics.py  ← success rate (Wilson CI), time horizon, hazard curve,
                                    failure taxonomy, tool-failure rates, loopiness guardrails
L2  Runner       eval-runner.py   ← dogfood task battery: N runs per task through the real
                                    agent loop, artifacts verified independently (not self-report)
L3  Reporting    eval-report.py   ← human-readable report; cron-friendly
```

Plus `extreme-test-runner.py` — a direct-API ceiling battery for comparing LLMs on 5
hard dimensions (multi-step reasoning, long-context recall, adversarial instruction
following incl. lipogram, complex code generation, agentic planning) across providers.

## Key design principles

- **Canonical task spec** — every task carries `task_key` + `spec_hash` + pre-registered
  difficulty; reproducibility without outcome-inferred labels.
- **Failure taxonomy** — outcomes split into completed / failed / partial / user_abort /
  infra_fail with a failure class; execution failure ≠ prompt failure ≠ tooling failure.
- **Hazard / survival analysis** — meltdown onset measured as P(success | duration ≥ t)
  with pre-registered buckets + confidence intervals; no post-hoc cutoff cherry-picking.
- **Minimum n gate + CI** — n < 20 is flagged exploratory; all proportions use Wilson
  95% intervals.
- **Idempotent ETL + reconciliation** — rerunnable, dedup-able, auditable; cron only after
  the pipeline is proven.
- **Artifact-verified outcomes** — pass = expected artifact exists *and* is non-empty,
  checked independently of the agent's self-report.

## Quick start

```bash
# 1. ETL: ingest telemetry into the eval DB (idempotent — safe to rerun)
python3 scripts/eval-etl.py

# 2. Metrics: reliability + hazard curve + taxonomy validation
python3 scripts/eval-metrics.py [--json]

# 3. Dogfood battery: N runs of each real task through the agent loop
python3 scripts/eval-runner.py --task t1_file_summary --runs 10

# 4. Model comparison through the same battery (any provider Hermes supports)
python3 scripts/eval-runner.py --model claude-sonnet-4-6 --provider opencode-zen --runs 3

# 5. Extreme ceiling battery: 5 hard tests × N models via direct API
python3 scripts/extreme-test-runner.py --models claude-opus-5,deepseek-v4-pro --tests T1,T2,T3,T4,T5
```

Requirements: Python 3.10+, SQLite3, an agent loop that records per-task lifecycle
events and per-span traces (see `scripts/eval-etl.py` for the expected schemas).

## What the data says (sample findings)

- Task-level success 93.5–94.2% across 600+ real sessions, stable across weeks.
- Battery (fixed-spec tasks, n=10 per task): 60/60 artifact-verified passes, mean
  duration 23–43 s per task — controlled variance for regression checks.
- Model A/B (same 6 real tasks, n=3-10 per model): a reasoning-heavy flagship scored
  *worse* on delegation tasks than the cheap baseline (0/3 vs 10/10) — stronger model
  ≠ better agent loop. Token telemetry (input/output/cache/reasoning) is captured per
  run via `--usage-file`.
- Extreme ceiling test (2026-08): among 11 candidate models on one provider, all
  Claude-family endpoints rejected `temperature` (deprecated) and GPT-family required
  the Responses API — a pre-flight check that saves a full wasted battery.

## Repository layout

```
scripts/
  eval-etl.py              L0 ETL (idempotent, reconcilable)
  eval-metrics.py          L1 metrics (Wilson CI, hazard curve, n-gate)
  eval-runner.py           L2 dogfood battery (artifact-verified)
  eval-report.py           L3 report generation
  eval-api.py              read-only REST API over the eval DB
  price-probe.py           provider pre-flight: which listed models actually respond
  extreme-test-runner.py   5-test ceiling battery (direct API, multi-provider)
reports/                   generated reports (samples)
```

## License

MIT — see [LICENSE](LICENSE).
