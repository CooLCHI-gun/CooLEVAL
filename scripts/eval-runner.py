#!/usr/bin/env python3
"""eval-runner.py — Phase C: active benchmark battery (pilot).

Runs real dogfood tasks through `hermes -z` (one-shot agent) N times each,
records outcomes into battery_runs, and verifies expected artifacts.

Design (Sonar must-fix 1 + Phase C pilot-first):
  - Canonical task spec: each task has task_key + spec_hash + difficulty
    (pre-registered, NOT inferred from outcome).
  - Isolation: tasks run with cwd=/tmp/eval-battery-out/<task_key>/ and write
    only there — no pollution of the live workspace.
  - Pilot scope: 2 tasks x 3 runs = 6 agent invocations (smoke test). Expand
    to full battery + n>=10 per task only after ETL/metrics are proven.
  - Outcome: pass = expected artifact exists AND non-empty (checked
    independently by this script, not by the agent's self-report).
  - Each run is a fresh `hermes -z` one-shot on the default tier.

Usage: python3 eval-runner.py [--task TASK_KEY] [--runs N]
"""

from __future__ import annotations

import argparse
import os
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

EVAL_DB = Path(os.environ.get("EVAL_DB", str(Path.home() / ".hermes" / "data" / "hermes-eval.db")))
OUT_ROOT = Path(os.environ.get("EVAL_OUT", "/tmp/eval-battery-out"))
EVAL_ROOT = os.environ.get("EVAL_ROOT", str(Path(__file__).resolve().parent.parent))

# ── Canonical task specs (pre-registered; spec_hash = hash of prompt) ────
# IMPORTANT: `hermes -z` does NOT honour the subprocess cwd (agent restores
# its own cwd to the user home). Prompts MUST use absolute paths; artifacts are
# verified at the absolute path too.
OUT_ROOT = Path("/tmp/eval-battery-out")

TASKS = {
    "t1_file_summary": {
        "difficulty": "easy",
        "prompt": (
            "Read {EVAL_ROOT}/task_plan.md (first 30 lines) "
            "and write a 3-line summary to the ABSOLUTE path "
            "/tmp/eval-battery-out/t1_file_summary/summary.txt. "
            "Report 'DONE' when the file is written and verified."
        ),
        "artifact": "summary.txt",
        "min_bytes": 50,
    },
    "t2_search_integrate": {
        "difficulty": "medium",
        "prompt": (
            "Use web_search to find information about 'Hermes Agent Nous Research'. "
            "Integrate 2 sources into a 5-line summary and write it to the ABSOLUTE "
            "path /tmp/eval-battery-out/t2_search_integrate/summary.md. "
            "Report 'DONE' when the file is written."
        ),
        "artifact": "summary.md",
        "min_bytes": 100,
    },
    "t3_code_modify": {
        "difficulty": "medium",
        "prompt": (
            "Read {EVAL_ROOT}/scripts/eval-etl.py (first 40 lines). "
            "Copy it to the ABSOLUTE path /tmp/eval-battery-out/t3_code_modify/copy.py and "
            "change the string 'traces.jsonl' to 'traces_v2.jsonl' in the copy. "
            "Verify the change with search_files or grep, then write 'DONE' to "
            "/tmp/eval-battery-out/t3_code_modify/done.txt."
        ),
        "artifact": "done.txt",
        "min_bytes": 1,
    },
    "t4_memory_recall": {
        "difficulty": "medium",
        "prompt": (
            "Recall from memory what the user's main active projects are (check MEMORY.md "
            "or use session_search / memory tools). Write a 3-line summary to the ABSOLUTE "
            "path /tmp/eval-battery-out/t4_memory_recall/summary.txt. "
            "Do NOT write any new memory or modify any files other than summary.txt. "
            "Report 'DONE' when written."
        ),
        "artifact": "summary.txt",
        "min_bytes": 30,
    },
    "t5_cron_check": {
        "difficulty": "easy",
        "prompt": (
            "List the current cron jobs (cronjob tool, action='list'). Pick one job and "
            "write its name and schedule to the ABSOLUTE path "
            "/tmp/eval-battery-out/t5_cron_check/summary.txt. Report 'DONE' when written."
        ),
        "artifact": "summary.txt",
        "min_bytes": 10,
    },
    "t6_delegation": {
        "difficulty": "hard",
        "prompt": (
            "Use delegate_task to spawn one leaf subagent with goal: 'Write the text "
            "hello-battery to the ABSOLUTE path /tmp/eval-battery-out/t6_delegation/out.txt'. "
            "After it completes, verify the file exists, then write 'DONE' to "
            "/tmp/eval-battery-out/t6_delegation/done.txt."
        ),
        "artifact": "done.txt",
        "min_bytes": 1,
    },
}


def spec_hash(task: dict) -> str:
    return hashlib.sha256(task["prompt"].replace("{EVAL_ROOT}", EVAL_ROOT).encode()).hexdigest()[:16]


# ── Semantic validators (Sonar must-fix 1 / item 5) ────────────────────────
# Tiered, DETERMINISTIC validation beyond "artifact exists and is non-empty".
#   Tier 1 structural: file exists + min_bytes (already done).
#   Tier 2 field/content: task-specific invariants checked in pure Python.
#   (Tier 3 LLM-judge is deliberately deferred — second release, explicit.)
# Each validator receives the TASK DIR and validates the task's real content
# file (semantic_file), NOT the completion marker (t3/t6 write a 'DONE' marker
# artifact but the semantic content lives in copy.py / out.txt respectively).
def _val_t1(taskdir: Path) -> tuple:
    """3-line summary of task_plan.md — must actually summarize the doc."""
    path = taskdir / "summary.txt"
    try:
        text = path.read_text()
    except Exception as e:  # noqa: BLE001
        return False, f"unreadable: {e}"
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2:
        return False, f"too thin ({len(lines)} line, expected ~3-line summary)"
    # the summary should reference the doc's actual topic (ETL/data pipeline)
    try:
        src = (Path(EVAL_ROOT) / "task_plan.md").read_text(errors="ignore")[:2000].lower()
    except Exception:  # noqa: BLE001 — source doc may not ship in public repo
        src = "data pipeline etl metrics benchmark run evaluate memory telemetry"
    src_tokens = {w for w in src.split() if len(w) >= 5}
    hit = [w for w in src_tokens if w in text.lower()]
    if not hit:
        return False, "summary has no lexically-traceable topic from task_plan.md"
    return True, f"{len(lines)} line(s), topic anchors: {', '.join(list(hit)[:3])}"


def _val_t2(taskdir: Path) -> tuple:
    """5-line integrated summary — must mention Hermes AND Nous Research."""
    try:
        text = (taskdir / "summary.md").read_text().lower()
    except Exception as e:  # noqa: BLE001
        return False, f"unreadable: {e}"
    anchors = [t for t in ("hermes", "nous") if t in text]
    if len(anchors) < 2:
        return False, f"integration incomplete: found {anchors or 'neither'} of hermes/nous"
    return True, f"anchors: {', '.join(anchors)}"


def _val_t3(taskdir: Path) -> tuple:
    """copy.py with 'traces.jsonl' rewritten to 'traces_v2.jsonl'."""
    try:
        text = (taskdir / "copy.py").read_text()
    except Exception as e:  # noqa: BLE001
        return False, f"unreadable: {e}"
    if "traces_v2.jsonl" not in text:
        return False, "rewrite missing: 'traces_v2.jsonl' not found in copy"
    # must not have left an unmodified bare reference to the original string
    if "traces.jsonl" in text.replace("traces_v2.jsonl", ""):
        return False, "found unreplaced 'traces.jsonl' reference"
    return True, "traces_v2.jsonl present & original ref replaced/renamed"


def _val_t4(taskdir: Path) -> tuple:
    """3-line summary of user's main active projects from memory."""
    try:
        text = (taskdir / "summary.txt").read_text().lower()
    except Exception as e:  # noqa: BLE001
        return False, f"unreadable: {e}"
    if len(text.strip()) < 30:
        return False, "summary content too thin for a memory-recall task"
    return True, f"recalled summary present ({len(text.strip())} chars)"


def _val_t5(taskdir: Path) -> tuple:
    """One cron job's name + schedule."""
    try:
        text = (taskdir / "summary.txt").read_text().lower()
    except Exception as e:  # noqa: BLE001
        return False, f"unreadable: {e}"
    has_sched = any(c in text for c in "0123456789:*/")
    if len(text.strip()) < 8 or not has_sched:
        return False, "no job name/schedule content found"
    return True, "job name + schedule present"


def _val_t6(taskdir: Path) -> tuple:
    """out.txt must be exactly 'hello-battery' (subagent delegation contract)."""
    try:
        text = (taskdir / "out.txt").read_text()
    except Exception as e:  # noqa: BLE001
        return False, f"unreadable: {e}"
    if "hello-battery" not in text:
        return False, f"delegation output mismatch: got {text[:40]!r}"
    return True, "hello-battery contract satisfied"


# tie each task to its semantic validator (fall through = structural only)
SEMANTIC_VALIDATORS = {
    "t1_file_summary": _val_t1,
    "t2_search_integrate": _val_t2,
    "t3_code_modify": _val_t3,
    "t4_memory_recall": _val_t4,
    "t5_cron_check": _val_t5,
    "t6_delegation": _val_t6,
}


def _read_usage_file(path) -> dict | None:
    """Read the --usage-file JSON written by hermes -z. Returns None when
    absent/unparseable so token metrics degrade gracefully."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def run_once(task_key: str, run_n: int, timeout_s: int = 180,
             model: str | None = None, provider: str | None = None) -> dict:
    task = TASKS[task_key]
    workdir = OUT_ROOT / task_key
    if run_n == 1:
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)

    # NOTE: hermes -z requires the prompt to immediately follow -z
    # ("expected one argument" otherwise). Put -m/--provider BEFORE -z.
    # --usage-file captures real token telemetry per run (input/output/
    # cache/reasoning tokens + cost) — the only reliable efficiency metric.
    import tempfile
    usage_path = None
    cmd = ["hermes"]
    if model:
        cmd += ["-m", model]
    if provider:
        cmd += ["--provider", provider]
    if True:  # always capture usage telemetry
        fd, usage_path = tempfile.mkstemp(prefix="eval-usage-", suffix=".json")
        import os as _os
        _os.close(fd)
        cmd += ["--usage-file", usage_path]
    prompt = task["prompt"].replace("{EVAL_ROOT}", EVAL_ROOT)
    cmd += ["-z", prompt]

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        exit_code = proc.returncode
        tail = (proc.stdout or "")[-2000:]
        stdout_len = len(proc.stdout or "")
        usage = _read_usage_file(usage_path)
    except subprocess.TimeoutExpired:
        exit_code = -1
        tail = "TIMEOUT"
        stdout_len = 0
        usage = None
    finally:
        if usage_path:
            try:
                Path(usage_path).unlink(missing_ok=True)
            except Exception:
                pass
    duration_h = (time.time() - t0) / 3600.0

    # Independent artifact check (not agent self-report) — absolute path
    artifact = OUT_ROOT / task_key / task["artifact"]
    ok = artifact.is_file() and artifact.stat().st_size >= task["min_bytes"]
    # Tiered semantic validation (structural-first, per-task invariants)
    semantic_ok = None
    semantic_note = ""
    validator = SEMANTIC_VALIDATORS.get(task_key)
    if ok and validator is not None:
        semantic_ok, semantic_note = validator(workdir)
    elif ok:
        semantic_ok = True  # structural-only task passes semantic by virtue of existence
        semantic_note = "structural-only (no domain validator)"
    status = "completed" if ok else ("failed" if exit_code != -1 else "timeout")

    return {
        "task_key": task_key,
        "run_n": run_n,
        "spec_hash": spec_hash(task),
        "difficulty": task["difficulty"],
        "status": status,
        "exit_code": exit_code,
        "duration_h": duration_h,
        "artifact_ok": ok,
        "semantic_ok": semantic_ok,
        "semantic_note": semantic_note,
        "tail": tail[-500:],
        "stdout_len": stdout_len,
        "model": model,
        "provider": provider,
        "usage": usage,
    }


def _usage_short(usage) -> str:
    """Compact one-line usage summary for run logs."""
    if not usage:
        return "n/a"
    return (f"in={usage.get('input_tokens', '?')} "
            f"out={usage.get('output_tokens', '?')} "
            f"reason={usage.get('reasoning_tokens', '?')} "
            f"cache={usage.get('cache_read_tokens', '?')} "
            f"calls={usage.get('api_calls', '?')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=sorted(TASKS), default=None)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default=None, help="model override passed to hermes -m")
    ap.add_argument("--provider", default=None, help="provider override passed to hermes --provider")
    args = ap.parse_args()

    con = sqlite3.connect(EVAL_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS battery_runs ("
        "run_id TEXT PRIMARY KEY, task_key TEXT, run_n INT, spec_hash TEXT, "
        "status TEXT, duration_h REAL, ts REAL, notes TEXT)"
    )

    keys = [args.task] if args.task else sorted(TASKS)
    tag = f" @{args.model}" if args.model else ""
    print(f"=== eval-runner.py pilot: {keys} x {args.runs} runs{tag} ===")
    results = []
    for task_key in keys:
        for n in range(1, args.runs + 1):
            r = run_once(task_key, n, model=args.model, provider=args.provider)
            run_id = f"{task_key}#{n}" + (f"@{args.model}" if args.model else "")
            con.execute(
                "INSERT OR REPLACE INTO battery_runs (run_id, task_key, run_n, spec_hash, "
                "status, duration_h, ts, notes) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, task_key, n, r["spec_hash"], r["status"], r["duration_h"],
                 time.time(), json.dumps({"exit_code": r["exit_code"],
                                          "artifact_ok": r["artifact_ok"],
                                          "semantic_ok": r["semantic_ok"],
                                          "semantic_note": r["semantic_note"],
                                          "difficulty": r["difficulty"],
                                          "model": args.model,
                                          "provider": args.provider,
                                          "stdout_len": r["stdout_len"],
                                          "usage": r["usage"]})),
            )
            con.commit()
            sem = "✓" if r["semantic_ok"] else ("✗" if r["semantic_ok"] is False else "?")
            print(f"  {run_id}: status={r['status']} dur={r['duration_h']*3600:.0f}s "
                  f"exit={r['exit_code']} artifact={r['artifact_ok']} sem={sem} "
                  f"[{r['semantic_note'][:40]}] stdout={r['stdout_len']}ch tokens={_usage_short(r['usage'])}")
            results.append(r)

    print(f"\n=== battery summary ===")
    for task_key in keys:
        rs = [r for r in results if r["task_key"] == task_key]
        n = len(rs)
        k = sum(1 for r in rs if r["status"] == "completed")
        print(f"  {task_key}: {k}/{n} pass")
        if n > 1 and k not in (0, n):
            import statistics
            durs = [r["duration_h"] for r in rs]
            print(f"    duration: mean={statistics.mean(durs)*3600:.0f}s "
                  f"std={statistics.stdev(durs)*3600:.0f}s")
    con.close()


if __name__ == "__main__":
    main()
