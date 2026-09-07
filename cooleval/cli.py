#!/usr/bin/env python3
"""cooleval CLI — a single entry point for the CooLEVAL pipeline.

One command, no clone-and-hunt. Every subcommand dispatches to the matching
script under ``scripts/`` (kept at the repo root / bundled into the wheel),
so the packaged CLI and the from-source quickstart are the same code path.

Subcommands:
  demo         generate NON-BENCHMARK demo telemetry and run ETL -> metrics
               (the meltdown curve renders in ~seconds, zero setup).
  etl          ingest telemetry (idempotent, safe to rerun).
  metrics      success rate, hazard curve, failure taxonomy (Wilson CI).
  report       emit the report.
  runner       dogfood battery: N runs of real tasks through your agent loop.
  trace        which tool call broke a run, loop signal, slowest step.
  tokeneff     token-efficiency / cost-per-success.
  extreme      frontier-model ceiling battery (direct API).

Unknown args after a subcommand are passed straight through to the script,
so every script's native flags keep working (e.g. ``cooleval runner
--task t1_file_summary --runs 10``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

__all__ = ["main"]

_SCRIPTS_BY_CMD = {
    "etl": "eval-etl.py",
    "metrics": "eval-metrics.py",
    "report": "eval-report.py",
    "runner": "eval-runner.py",
    "trace": "trace-steps.py",
    "tokeneff": "eval-tokeneff.py",
    "extreme": "extreme-test-runner.py",
    "bench": "bench_s1s2.py",
}


def _scripts_dir() -> Path:
    """Locate the scripts/ directory across all install shapes.

    1. Source checkout / editable install: scripts/ sits at the repo root,
       i.e. the parent of the installed package dir.
    2. Wheel install: scripts/ is bundled via data-files either beside the
       package or under sys.prefix — check both.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / "scripts",   # repo root when run from source
        here.parent / "scripts",          # scripts co-located in the package
        Path(sys.prefix) / "cooleval" / "scripts",  # data-files install
    ]
    for cand in candidates:
        if (cand / "eval-etl.py").exists():
            return cand
    raise SystemExit(
        "CooLEVAL: could not locate the scripts/ tree (looked at %s). "
        "Run from a CooLEVAL checkout or reinstall the wheel."
        % "; ".join(str(c) for c in candidates)
    )


def _run(script: str, args: list[str], env_extra: dict | None = None) -> int:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, str(_scripts_dir() / script), *args]
    return subprocess.call(cmd, env=env)


def _demo_env(demo_dir: Path) -> dict:
    """Env vars the ETL/metrics scripts read, pointed at the demo fixture."""
    return {
        "HERMES_MEM_DB": str(demo_dir / "memory-unified.db"),
        "HERMES_TRACES": str(demo_dir / "traces.jsonl"),
        "HERMES_STATE_DB": str(demo_dir / "state.db"),
        "EVAL_DB": str(demo_dir / "eval.db"),
    }


def _cmd_demo(args: list[str]) -> int:
    demo_dir = Path("./demo").resolve()
    if args and args[0] == "--dir" and len(args) >= 2:
        demo_dir = Path(args[1]).resolve()
    demo_dir.mkdir(parents=True, exist_ok=True)

    print(f"# [cooleval demo] synthetic NON-BENCHMARK fixture in {demo_dir}")
    rc = _run("make-demo-data.py", [], env_extra={"COOLEVAL_DEMO_DIR": str(demo_dir)})
    if rc:
        return rc
    rc = _run("eval-etl.py", ["--rebuild"], env_extra=_demo_env(demo_dir))
    if rc:
        return rc
    print("\n# [cooleval demo] metrics (shape-checker only, NOT real results):")
    return _run("eval-metrics.py", [], env_extra=_demo_env(demo_dir))


_USAGE = """\
CooLEVAL — how long can your AI agent work before it breaks?

Usage:
  cooleval demo                       regenerate demo fixture -> meltdown curve
  cooleval etl [--rebuild]            ingest telemetry (idempotent)
  cooleval metrics [--json]           success rate, hazard curve, taxonomy
  cooleval report                     emit report
  cooleval runner [--task T] [--runs N] [--provider P]
  cooleval trace [--session ID | --run ID] [--durations] [--json]
  cooleval tokeneff [--task T] [--json]
  cooleval extreme [--models a,b,c]

Pass extra flags straight through to the underlying script.
  e.g. cooleval runner --task t1_file_summary --runs 10
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    if args[0] in ("--version", "-V"):
        try:
            from cooleval import __version__
        except ImportError:  # running the .py from a source checkout
            __version__ = "0.1.0"
        print(f"cooleval {__version__}")
        return 0

    cmd, rest = args[0], args[1:]
    if cmd == "demo":
        return _cmd_demo(rest)
    if cmd in _SCRIPTS_BY_CMD:
        return _run(_SCRIPTS_BY_CMD[cmd], rest)
    print(f"cooleval: unknown command '{cmd}'\n\n{_USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
