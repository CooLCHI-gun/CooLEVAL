# Contributing to CooLEVAL

Thanks for helping make CooLEVAL better — whether you're a human or an AI agent.

CooLEVAL is a **dogfood evaluation framework**: it measures how reliable an AI
agent actually is on real workload telemetry, with statistics-honesty as the
reputation moat. This file is the contract for contributing in a way that
keeps the numbers trustworthy.

## The trust rule (read first)

Every number in this repo comes from an actual measured run, reported with its
sample size and uncertainty — never from a guess, a model's self-report, or a
"looks reasonable" estimate.

**Rules that never bend:**

- **No fabricated data.** Never invent a result to make a run look good. If a
  metric can't be measured, say so.
- **Report n and confidence.** Any success rate / recall / percentage must
  carry its sample size `n` and a Wilson 95% interval (or an explicit
  `n < 20 → exploratory` flag). No naked percentages.
- **Never claim before you verify.** Run the script, read the output, confirm
  the artifact exists before saying "done".
- **Anti-ASP — anti-attractive-sounding-prose.** No hype adjectives on numbers.

If you are an **AI agent** contributing: say so. Do not present agent-written
changes as human-authored, and do not alter commit identity to impersonate a
human owner. When in doubt, leave a note in the PR/commit like
"generated with AI assistance".

## How to run

```bash
python3 scripts/eval-etl.py         # ingest telemetry (idempotent — safe to rerun)
python3 scripts/eval-metrics.py     # metrics w/ Wilson CI + n-gate
python3 scripts/eval-runner.py --task t1_file_summary --runs 10   # dogfood battery
python3 scripts/eval-report.py      # report
```

Requires Python 3.10+ (the code uses `X | None` type unions) and SQLite3.
Only dependency outside stdlib is PyYAML (for a couple of scripts). No venv,
no framework — one script per layer.

## Repository layout

```
scripts/
  eval-etl.py              L0 ETL (idempotent)
  eval-metrics.py          L1 metrics (success rate, hazard, n-gate)
  eval-runner.py           L2 dogfood battery (artifact + semantic verified)
  eval-report.py / eval-api.py  L3 reporting + read-only REST
  eval-tokeneff.py         cost-aware tokens-per-success (cache-adjusted)
  eval-memory.py / bench_s1s2.py / eval-memory-full.py   memory-backend evals
  memory_health_report.py  memory recall diagnostics
  cooleval_mcp.py          read-only MCP server over the eval DB
  price-probe.py / extreme-test-runner.py / score_tests.py   model ceilings
  make_assets.py / make_animate.py  regenerate visuals
assets/  reports/  docs/   original visuals + generated reports + protocol docs
RESEARCH.md                probe findings + methodology
```

## Adding a dogfood task (`eval-runner.py`)

1. Add an entry to the `TASKS` dict: a `task_key`, `difficulty`
   (`easy`/`medium`/`hard`), a prompt that uses **absolute paths**, the
   expected `artifact` file, and a `min_bytes`.
2. Add a semantic validator (a `_val_tN()` function) wired into
   `SEMANTIC_VALIDATORS`. A task that only checks "file exists" is a floor,
   not a real check — prefer validating the actual content.
3. `spec_hash` is computed from the prompt automatically; that's how a task
   stays reproducible. Don't reuse a key already in the DB.
4. Run `--task <key> --runs 3` and confirm it passes before submitting.

## Adding a metric

Follow the existing pattern: a pure function over the DB/JSONL that returns
counts + `n` + a Wilson CI (see `metrics.py` / `eval-tokeneff.py` for the
shape). Metrics must be:

- **Defined before measured.** Say what it means and its failure mode before
  reporting numbers (mirrors the "metric semantics first" rule).
- **Not a silent quality proxy.** A cost/efficiency metric like
  tokens-per-success must be labeled "not a quality score" and read against a
  success floor.
- **Reproducible.** Deterministic, seeded, or re-runnable; commit the generator
  if you add a visual.

## Commit / PR checklist

- [ ] Numbers carry `n` + CI or an `exploratory` flag
- [ ] Full scripts committed, not just results
- [ ] Raw telemetry / session data stays OUT of the repo (gitignored)
- [ ] No private/third-party secrets or identifiers
- [ ] Commit attribution is the author's own identity (never impersonate)
- [ ] AI assistance noted on the PR/commit
- [ ] `git diff` reviewed before submit

## License

MIT — see [LICENSE](LICENSE). By contributing you agree to license your
contribution under the same terms.
