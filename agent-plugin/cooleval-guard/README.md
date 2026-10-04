# cooleval-guard (Hermes plugin)

An **observe-only** meltdown-precursor monitor. CooLEVAL measures *that* long sessions collapse;
this plugin watches the *precursors* CooLEVAL already tracks — repeated tool calls, loops, slow
steps — and reports them early. It is the "alert on precursors" step of CooLEVAL's
[remediation playbook](../../README.md#what-to-do-about-it).

## What it does

- Hooks `pre_tool_call` and keeps a small in-memory window of recent calls.
- Reports when a **loop** (same tool back-to-back ≥ `repeat` times) or a **slow step**
  (≥ `slow_ms` since the previous call) appears, with the triggering evidence.
- Exposes `guard_status()` — a JSON snapshot of the current precursor state.

## What it deliberately does NOT do

- **Never blocks.** It returns `None` unconditionally — a call is never refused, rewritten, or
  terminated. Acting on an alert is a human/agent decision, not the plugin's.
- **Never writes.** No files, no agent state, no database. State is in-process only and dies
  with the process.
- **No network.** No HTTP, no telemetry, no phone-home. Nothing leaves the process.
- **Does not predict.** A precursor is an *association* with later failure, not proof a run will
  fail. It reports hits; you should also look at its false alarms. Thresholds are yours to tune.

## Install

```bash
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/plugins"
cp -r agent-plugin/cooleval-guard "${HERMES_HOME:-$HOME/.hermes}/plugins/"
hermes plugins enable cooleval-guard      # then restart / next session
```

No API key, no `eval.db`, no configuration is required — it starts with sensible defaults.

## Thresholds (optional)

| Env var | Default | Meaning |
|---|---|---|
| `COOLEVAL_GUARD_WINDOW` | `12` | how many recent calls to remember |
| `COOLEVAL_GUARD_REPEAT` | `4` | same-tool back-to-back run that counts as a loop |
| `COOLEVAL_GUARD_SLOW_MS` | `60000` | a single step longer than this is "slow" |

## Verify it works

```bash
python3 agent-plugin/cooleval-guard/_selftest.py   # no keys, no network, no side effects
```

## Honest limits

- End-of-session *failure* is the outcome CooLEVAL scores; the guard only watches the precursor
  side. A quiet guard does not mean the session will succeed.
- Thresholds are heuristics; tune them to your own workload rather than trusting the defaults.
- `args_shape` records key→type only — never argument values — so the log stays PII-safe.
