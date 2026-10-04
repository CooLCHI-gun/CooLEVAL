#!/usr/bin/env python3
"""Zero-dependency self-test for cooleval-guard. No keys, no network, no side effects.

Proves: (1) the observer never blocks a call, (2) a same-tool loop is detected and counted,
(3) guard_status() is valid JSON and PII-safe (keys only, no values).
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cooleval_guard", os.path.join(HERE, "__init__.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

fails = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# 1. never blocks
r = g.on_pre_tool_call("terminal", {"command": "ls -la", "workdir": "/tmp"})
check("observer never blocks (returns None)", r is None, f"returned={r!r}")

# 2. loop detection — same tool 5x back-to-back (repeat threshold default 4)
for _ in range(4):
    g.on_pre_tool_call("terminal", {"command": "tail -f x"})
st = json.loads(g.guard_status())
check("loop precursor detected", st["alerts"]["loops"] >= 1, f"loops={st['alerts']['loops']}")
check("longest run counted", st["longest_same_tool_run"] >= 4, f"run={st['longest_same_tool_run']}")

# 3. PII-safe + valid JSON status
check("guard_status() is valid JSON", isinstance(st, dict))
check("no argument values leaked", "ls -la" not in json.dumps(st) and "tail -f x" not in json.dumps(st))

print(f"\nTOTAL {len(fails) == 0 and 'PASS' or 'FAIL'}")
sys.exit(1 if fails else 0)
