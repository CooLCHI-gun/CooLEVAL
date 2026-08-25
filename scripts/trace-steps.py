#!/usr/bin/env python3
"""trace-steps.py — per-session / per-run step timeline from span-tracer traces.

Reads traces.jsonl (span-tracer output; enriched tool spans from the
capture_step_detail opt-in), groups tool spans by session (or by a battery
run, joined via the eval.db time window), and reports:

  - total tool steps
  - first-failing step (tool + error_type/error_message)
  - top tools (usage distribution)
  - loop detection (longest run of the SAME tool_name back-to-back)
  - slowest step (max duration_ms)

Usage:
  python3 trace-steps.py --session <sid>            # one session
  python3 trace-steps.py --all                      # every session (compact)
  python3 trace-steps.py --run t1_file_summary#1    # one battery run (join via ts window)
  python3 trace-steps.py --session <sid> --json     # machine-readable
  python3 trace-steps.py --durations                # show per-step duration
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

TRACE_FILE = Path(os.environ.get(
    "HERMES_TRACES", str(Path.home() / ".hermes" / "data" / "traces.jsonl")))
EVAL_DB = Path(os.environ.get(
    "EVAL_DB", str(Path(__file__).resolve().parent.parent / "data" / "eval.db")))


def load_spans() -> list[dict]:
    spans = []
    for path in (TRACE_FILE.with_suffix(".jsonl.old"), TRACE_FILE):
        if not path.is_file():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    spans.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return spans


def tool_spans(spans: list[dict]) -> list[dict]:
    return [s for s in spans if s.get("type") == "tool"]


def norm(s: dict) -> dict:
    """Normalise a tool span into a step dict (works for enriched or plain)."""
    is_err = bool(s.get("is_error")) or (s.get("ok") in (0, False))
    return {
        "ts": s.get("ts"),
        "turn_id": s.get("turn_id") or "",
        "tool_call_id": s.get("tool_call_id") or "",
        "tool_name": s.get("tool_name") or "",
        "ok": bool(s.get("ok", True)),
        "is_error": is_err,
        "error_type": s.get("error_type") or "",
        "error_message": s.get("error_message") or "",
        "duration_ms": s.get("duration_ms") or 0,
        "status": s.get("status") or "",
        "args_shape": s.get("args_shape") or {},
        "result_summary": s.get("result_summary") or "",
    }


def analyze(steps: list[dict]) -> dict:
    if not steps:
        return {"total_steps": 0}
    top = Counter(s["tool_name"] for s in steps)
    # longest consecutive run of the same tool -> loop signal
    longest_run, cur_run, cur_tool = 0, 0, None
    for s in steps:
        if s["tool_name"] == cur_tool:
            cur_run += 1
        else:
            cur_run, cur_tool = 1, s["tool_name"]
        longest_run = max(longest_run, cur_run)
    first_fail = next((s for s in steps if s["is_error"]), None)
    slowest = max(steps, key=lambda s: s["duration_ms"])
    return {
        "total_steps": len(steps),
        "first_failing_step": first_fail,
        "top_tools": top.most_common(8),
        "longest_same_tool_run": longest_run,
        "loop_likely": longest_run >= 5,
        "slowest_step": slowest,
        "steps": steps,
    }


def print_report(session_id: str, steps: list[dict], durations=False) -> dict:
    a = analyze(steps)
    print(f"\n[{session_id}]  ({a['total_steps']} tool steps)")
    if a["total_steps"] == 0:
        print("  no tool spans")
        return a
    print(f"  first failing: ", end="")
    ff = a["first_failing_step"]
    if ff:
        print(f"{ff['tool_name']} | {ff['error_type'] or 'n/a'} | "
              f"{(ff['error_message'] or '')[:80]} | turn {ff['turn_id'][:20]}")
    else:
        print("none (all ok)")
    print(f"  top tools: " + ", ".join(f"{t}×{n}" for t, n in a["top_tools"]))
    print(f"  longest same-tool run: {a['longest_same_tool_run']} "
          f"{'(loop?)' if a['loop_likely'] else ''}")
    slow = a["slowest_step"]
    print(f"  slowest: {slow['tool_name']} {slow['duration_ms']}ms")
    if durations:
        for s in steps:
            shape = (json.dumps(s["args_shape"], ensure_ascii=False, sort_keys=True)
                     if s["args_shape"] else "-")
            print(f"    {s['ts']:.3f}  {s['tool_name']:<18} "
                  f"{'ERR' if s['is_error'] else 'ok '} {s['duration_ms']:>6}ms "
                  f"res={s['result_summary']:<10} shape={shape[:110]}")
    return a


def run_session(session_id: str, traces, durations: bool) -> dict:
    steps = sorted((norm(s) for s in tool_spans(traces)
                    if s.get("session_id") == session_id),
                   key=lambda s: s["ts"])
    return print_report(session_id, steps, durations)


def run_all(traces, durations: bool) -> dict:
    by_session: dict[str, list[dict]] = {}
    for s in tool_spans(traces):
        by_session.setdefault(s.get("session_id") or "?", []).append(norm(s))
    out = {}
    for sid, steps in sorted(by_session.items(), key=lambda kv: min(x["ts"] for x in kv[1])):
        out[sid] = print_report(sid, sorted(steps, key=lambda x: x["ts"]), durations)
    return out


def run_battery_run(run_id: str, traces, durations: bool) -> dict:
    con = sqlite3.connect(f"file:{EVAL_DB}?mode=ro", uri=True)
    row = con.execute(
        "SELECT ts, duration_h FROM battery_runs WHERE run_id=?", (run_id,)).fetchone()
    con.close()
    if not row:
        print(f"run {run_id} not found in battery_runs")
        return {}
    start = float(row[0])
    end = start + (float(row[1] or 0) * 3600.0)
    # session_id(s) whose spans fall in [start, end]
    session_ids = Counter(
        s.get("session_id") for s in tool_spans(traces)
        if start <= float(s.get("ts", 0)) <= end
    )
    if not session_ids:
        print(f"run {run_id}: no spans in time window ({start:.0f}-{end:.0f})")
        return {}
    sid = session_ids.most_common(1)[0][0]
    print(f"run {run_id}: inferred session={sid} "
          f"({len(session_ids)} candidate(s))")
    return run_session(sid, traces, durations) if sid else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", help="analyze one session_id")
    g.add_argument("--all", action="store_true", help="summarise every session")
    g.add_argument("--run", help="battery run id (task_key#n), joined by time window")
    ap.add_argument("--durations", action="store_true", help="list every step")
    ap.add_argument("--json", action="store_true", help="output JSON only")
    args = ap.parse_args()

    traces = load_spans()
    print(f"traces: {TRACE_FILE}  ({len(traces)} spans loaded)", file=sys.stderr)

    if args.run:
        out = run_battery_run(args.run, traces, args.durations)
    elif args.session:
        out = run_session(args.session, traces, args.durations)
    else:
        out = run_all(traces, args.durations)

    if args.json:
        json.dump({k: v for k, v in out.items() if k != "steps"},
                  sys.stdout, ensure_ascii=False, indent=2, default=str)
    return 0


if __name__ == "__main__":
    main()
