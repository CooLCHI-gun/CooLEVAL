"""cooleval-guard — an OBSERVE-ONLY meltdown-precursor monitor for Hermes.

Why this exists
---------------
CooLEVAL measures *that* long sessions collapse (the meltdown). The guard watches the
*precursors* CooLEVAL already tracks — repeated tool calls, loops, slow steps — and reports
them early, before the session fails. It is the "alert on precursors" step of CooLEVAL's
remediation playbook, and it is deliberately small.

What this IS
  A read-only observer on `pre_tool_call`. It keeps a small in-memory window of recent tool
  calls and reports when a loop / retry / slow-step signal appears, with the triggering evidence.

What this is NOT
  - NOT an actor. It NEVER blocks a call, rewrites agent state, or terminates a run. It returns
    None (allow) unconditionally. Acting on an alert is a human/agent decision.
  - NOT networked. No HTTP, no telemetry, no phone-home. Nothing leaves the process.
  - NOT a predictor. A precursor is an *association* with later failure, not proof that a run
    will fail. Report false alarms as well as hits — that is the point.

Setup
  Copy this directory into <HERMES_HOME>/plugins/ and enable it:
      mkdir -p "${HERMES_HOME:-$HOME/.hermes}/plugins"
      cp -r agent-plugin/cooleval-guard "${HERMES_HOME:-$HOME/.hermes}/plugins/"
      hermes plugins enable cooleval-guard

Thresholds (optional env):
      COOLEVAL_GUARD_WINDOW   = 12    # how many recent calls to remember
      COOLEVAL_GUARD_REPEAT   = 4     # same-tool back-to-back run that counts as a loop
      COOLEVAL_GUARD_SLOW_MS  = 60000 # a single step longer than this is "slow"
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque

logger = logging.getLogger("hermes.plugins.cooleval_guard")

_WINDOW = max(4, int(os.environ.get("COOLEVAL_GUARD_WINDOW", "12") or 12))
_REPEAT = max(2, int(os.environ.get("COOLEVAL_GUARD_REPEAT", "4") or 4))
_SLOW_MS = max(1000, int(os.environ.get("COOLEVAL_GUARD_SLOW_MS", "60000") or 60000))

# ── in-memory state (no disk, no network, no mutation of anything else) ──────
_recent: deque[dict] = deque(maxlen=_WINDOW)   # {tool, t, shape}
_last_t: list[float] = [0.0]
_stats: dict = {"calls": 0, "loops": 0, "slow": 0, "last_alert": None}


def _shape(args) -> str:
    """Structure-only signature of a call — key -> type, never the values (PII-safe)."""
    if not isinstance(args, dict):
        return type(args).__name__
    return ",".join(f"{k}:{type(v).__name__}" for k, v in sorted(args.items()))


def _longest_same_run() -> int:
    """Longest run of the same tool back-to-back in the recent window (a stall signature)."""
    best = run = 0
    prev = None
    for c in _recent:
        if c["tool"] == prev:
            run += 1
        else:
            run = 1
            prev = c["tool"]
        best = max(best, run)
    return best


def guard_status() -> str:
    """Current precursor state — call this to read the monitor (read-only)."""
    return json.dumps({
        "calls_seen": _stats["calls"],
        "longest_same_tool_run": _longest_same_run(),
        "windows": {
            "window": _WINDOW,
            "repeat_threshold": _REPEAT,
            "slow_ms_threshold": _SLOW_MS,
        },
        "alerts": {"loops": _stats["loops"], "slow_steps": _stats["slow"],
                   "last": _stats["last_alert"]},
        "note": "observe-only: precursors are an association with later failure, not a prediction",
    }, ensure_ascii=False)


def on_pre_tool_call(tool_name: str, args: dict | None = None, **kwargs):
    """Record the call and report precursors. ALWAYS returns None (never blocks)."""
    try:
        now = time.time()
        gap_ms = int((now - _last_t[0]) * 1000) if _last_t[0] else 0
        _last_t[0] = now
        _recent.append({"tool": tool_name, "t": now, "shape": _shape(args)})
        _stats["calls"] += 1

        run = _longest_same_run()
        if run >= _REPEAT:
            _stats["loops"] += 1
            _stats["last_alert"] = f"loop:{tool_name}x{run}"
            logger.warning(
                "cooleval-guard: [PRECURSOR loop] %s called %d× back-to-back "
                "(window=%d) — a stall signature. Consider a checkpoint/handoff. "
                "Evidence: last shapes=%s",
                tool_name, run, _WINDOW,
                [c["tool"] for c in list(_recent)[-run:]],
            )
        if gap_ms >= _SLOW_MS:
            _stats["slow"] += 1
            _stats["last_alert"] = f"slow:{tool_name}:{gap_ms}ms"
            logger.warning(
                "cooleval-guard: [PRECURSOR slow step] %d ms since the previous call "
                "(threshold %d ms) before %s — check for a long tool or a load stall.",
                gap_ms, _SLOW_MS, tool_name,
            )
    except Exception:  # noqa: BLE001 — fail-open: a monitor must never break a run
        logger.debug("cooleval-guard: observer error (ignored)", exc_info=True)
    return None  # never block


def register(ctx):
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    logger.info(
        "cooleval-guard: registered observe-only precursor monitor "
        "(window=%d repeat=%d slow_ms=%d) — non-blocking, no network",
        _WINDOW, _REPEAT, _SLOW_MS,
    )
