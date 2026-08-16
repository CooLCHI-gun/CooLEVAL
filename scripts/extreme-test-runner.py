#!/usr/bin/env python3
"""extreme-test-runner.py — 最貴 LLM 值唔值？5 extreme tests × 11 models（zen）。

Tests（model-evaluation-protocol Phase 1b）：
  T1 multi-step reasoning  — telescoping proof + 精確計算
  T2 long context         — ~50K tokens 嵌入 6 位置事實 recall
  T3 adversarial instr    — 10 條規則含 lipogram（禁字母 e）
  T4 complex code         — 7+ requirements 並發系統（py_compile 驗證語法）
  T5 agentic planning     — 全棧任務 + fail conditions + 風險 + 信心校準

執行：順序 per model（避免 race）；chat/completions 或 /v1/responses（gpt-*）；
claude 新系 deprecate temperature → 全部 call 唔送 temperature；
opencode.ai edge → browser UA + Origin/Referer；claude → anthropic header。
Resume：model 已出 summary.json 就 skip。

用法：python3 extreme-test-runner.py [--models m1,m2] [--tests T1,T2,...] [--max-tokens N]
輸出：/tmp/extreme-test-results/<model>/<T>.txt + summary.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

CFG = Path.home() / ".hermes" / "config.yaml"
OUT_ROOT = Path("/tmp/extreme-test-results")

# API credentials: env vars take priority, fall back to a Hermes-style
# config.yaml (providers.opencode-zen). Keep keys OUT of the repo.
def _load_zen_creds():
    api_key = os.environ.get("COOLEVAL_ZEN_API_KEY")
    base = os.environ.get("COOLEVAL_ZEN_BASE_URL", "https://opencode.ai/zen/v1")
    if not api_key and CFG.exists():
        cfg = yaml.safe_load(CFG.read_text())
        zen = cfg.get("providers", {}).get("opencode-zen", {})
        api_key = zen.get("api_key")
        base = zen.get("base_url", base)
    if not api_key:
        raise SystemExit("COOLEVAL_ZEN_API_KEY not set and no config.yaml found")
    return api_key, base

API_KEY: str
BASE: str
API_KEY, BASE = _load_zen_creds()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MODELS = [
    # (id, api_type)
    ("claude-opus-5", "chat"),
    ("claude-fable-5", "chat"),
    ("claude-sonnet-4-6", "chat"),
    ("grok-4.6", "chat"),
    ("gpt-5.6-sol", "responses"),
    ("deepseek-v4-pro", "chat"),
    ("qwen3.6-plus", "chat"),
    ("glm-5.2", "chat"),
    ("kimi-k3", "chat"),
    ("nemotron-3-ultra-free", "chat"),
    ("deepseek-v4-flash", "chat"),  # baseline
]

# ── T2 long-context document（build once）───────────────────────────────
_FACTS = {
    10: "The vault combination is 4815-2277-9033.",
    50: "The launch code is ALPHA-OMEGA-7.",
    100: "The rendezvous point is Sector 9, Bay 12.",
    200: "The safe password is 'sunflower-cobalt'.",
    500: "The drop time is 03:47 UTC.",
    1000: "The final handshake is 'kraken-waltz'.",
}


def build_long_doc() -> str:
    """~50K tokens synthetic doc with 6 embedded facts at sentence positions."""
    filler = ("The Pacific swallows sunlight and returns it as salt. "
              "Corrosion writes its signature on every hull. "
              "The navigator's chart folds at the meridian. "
              "A bell below deck marks the change of watch. "
              "The cargo manifest lists cargo and nothing else. "
              "Waves translate the wind into motion. ")
    filler2 = ("Qubits settle into their eigenstates only when observed. "
               "A lattice of lasers cools atoms toward absolute zero. "
               "Entanglement links two particles across any distance. "
               "The measurement problem remains unresolved at every scale. "
               "Decoherence leaks information into the environment. "
               "Interference fringes reappear when paths are labeled. ")
    parts = []
    for i in range(1, 1101):
        if i in _FACTS:
            parts.append(f"[FACT-{i}] {_FACTS[i]}")
        elif i % 2 == 0:
            parts.append(filler2)
        else:
            parts.append(filler)
    return "\n".join(parts)


# ── Test prompts ────────────────────────────────────────────────────────
def t1_prompt() -> str:
    return (
        "Solve the following contest-style mathematics problem with a complete, rigorous proof. "
        "Show every step of the telescoping argument.\n\n"
        "(a) Prove by induction or partial fractions that for every positive integer n,\n"
        "    S(n) = sum_{k=1}^{n} 1/(k(k+1)) = n/(n+1).\n\n"
        "(b) Using the identity, compute the EXACT value of S(999) as a reduced fraction.\n\n"
        "(c) Now prove the harder telescoping identity:\n"
        "    T(n) = sum_{k=1}^{n} 1/(k(k+1)(k+2)) = n(n+3) / (4(n+1)(n+2)).\n"
        "    Then compute T(999) exactly as a reduced fraction.\n\n"
        "Do not approximate. Give exact arithmetic. Verify the final fractions by cross-checking "
        "the telescoping collapse (write out the partial-fraction decomposition explicitly)."
    )


def t2_prompt(doc: str) -> str:
    return (
        "Below is a long technical document consisting of numbered sentences. "
        "Six sentences contain bracketed labels [FACT-10], [FACT-50], [FACT-100], [FACT-200], "
        "[FACT-500], [FACT-1000], each followed by a piece of information.\n\n"
        "Read the entire document carefully, then answer these questions EXACTLY:\n"
        "1. What is written after [FACT-100]?\n"
        "2. What is written after [FACT-500]?\n"
        "3. What is written after [FACT-10]?\n"
        "4. What is written after [FACT-1000]?\n\n"
        "Quote each fact verbatim, labeled by its FACT number. If you cannot find one, say "
        "'CANNOT FIND' for that number — do not guess.\n\n"
        "=== DOCUMENT START ===\n" + doc + "\n=== DOCUMENT END ==="
    )


def t3_prompt() -> str:
    return (
        "Write a response that satisfies ALL of the following 10 rules simultaneously:\n"
        "1. Exactly three paragraphs.\n"
        "2. Paragraph 1 must be about ocean navigation.\n"
        "3. Paragraph 2 must be about machine learning.\n"
        "4. Paragraph 3 must be about timekeeping.\n"
        "5. Paragraph 1 must contain NO letter 'e' anywhere (lipogram: no 'e' at all, "
        "   upper or lower case, in any word of paragraph 1).\n"
        "6. Paragraph 2 must contain exactly 4 sentences.\n"
        "7. Every paragraph must start with a header line beginning with '## '.\n"
        "8. The word 'harbour' must appear exactly once, in paragraph 3.\n"
        "9. The whole response must end with the exact phrase: 'END OF RULES TEST'.\n"
        "10. The total response must be between 180 and 240 words.\n\n"
        "Count your words carefully. Rule 5 is the hardest — do not write any word that "
        "contains the letter e in paragraph 1 (words like 'the', 'are', 'use', 'been', "
        "'every' are forbidden there)."
    )


def t4_prompt() -> str:
    return (
        "Write a single-file Python program (stdlib only, no external dependencies, "
        "strictly under 220 lines) implementing a concurrent retry task scheduler that satisfies "
        "ALL of these requirements:\n"
        "1. Thread-safe task queue processed by a bounded thread pool (max_workers configurable).\n"
        "2. Automatic retry with exponential backoff (base delay, jitter) on task failure.\n"
        "3. Dead-letter queue: tasks failing 3 attempts are moved to a DLQ and listed, not dropped.\n"
        "4. A custom @retryable decorator usable on arbitrary functions.\n"
        "5. Full type hints on all public functions and classes.\n"
        "6. Graceful exit: on KeyboardInterrupt, drain pending tasks before exiting (with timeout).\n"
        "7. Thread-safe statistics: counters for submitted/succeeded/failed/dead-lettered.\n"
        "8. A __main__ CLI demo: submit 20 tasks (mix of success and controlled failures), run, "
        "   and print the stats + DLQ contents in under 5 seconds.\n\n"
        "Output ONLY the Python code inside a single ```python code block. Do not include "
        "explanations outside the block. The code must be syntactically valid and runnable as-is."
    )


def t5_prompt() -> str:
    return (
        "You are the technical lead for a small team. Produce a detailed implementation plan for "
        "the following full-stack mission:\n\n"
        "Mission: Build 'FleetWatch' — a service that ingests GPS pings from 5,000 delivery "
        "vehicles (JSON over HTTPS), stores them in Postgres, detects vehicles that deviate from "
        "their route by >2km, alerts dispatchers in real time, and exposes a REST dashboard API. "
        "You have one backend engineer and one frontend engineer for 6 weeks. Budget: a single "
        "16-core / 64GB VM plus one managed Postgres instance.\n\n"
        "Tools available: Python, FastAPI, PostgreSQL, Redis, Docker, React, nginx, Grafana.\n\n"
        "Your plan MUST include:\n"
        "1. Phases with measurable goals and exact deliverables (file/module names).\n"
        "2. Which tools are used in each phase, and why.\n"
        "3. Explicit fail conditions — measurable, not generic (e.g. 'ingest >5k pings/min', "
        "   'deviation alert latency <30s') — and what triggers rollback.\n"
        "4. A parallelization map: what can run concurrently and what must wait (dependencies).\n"
        "5. Risk assessment: at least 4 specific risks, each with likelihood (low/med/high) "
        "   and a concrete mitigation.\n"
        "6. A confidence level between 65% and 85% for delivering on time, with explicit rationale.\n\n"
        "Be concrete. Prefer specific numbers, file names, and interfaces over general advice."
    )


TESTS = {
    "T1": t1_prompt,
    "T2": lambda: t2_prompt(_LONG_DOC),
    "T3": t3_prompt,
    "T4": t4_prompt,
    "T5": t5_prompt,
}

_LONG_DOC = build_long_doc()


def headers(model: str) -> dict:
    h = {
        "User-Agent": UA,
        "Origin": "https://opencode.ai",
        "Referer": "https://opencode.ai/",
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    if model.startswith("claude"):
        h["anthropic-dangerous-direct-browser-access"] = "true"
    return h


def call_model(model: str, api_type: str, prompt: str, max_tokens: int) -> dict:
    """Returns {ok, status, text, usage, latency_ms, error}."""
    if api_type == "responses":
        body = {"model": model, "input": [{"role": "user", "content": prompt}],
                "max_output_tokens": max_tokens}
        url = f"{BASE}/responses"
    else:
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens}
        url = f"{BASE}/chat/completions"

    t0 = time.time()
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers=headers(model), method="POST")
            with urllib.request.urlopen(req, timeout=300) as resp:
                r = json.loads(resp.read())
            if api_type == "responses":
                text = ""
                for item in r.get("output", []):
                    if item.get("type") == "message":
                        for c in item.get("content", []):
                            if c.get("type") == "output_text":
                                text += c.get("text", "")
                usage = r.get("usage", {})
            else:
                msg = r["choices"][0]["message"]
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""
                # DeepSeek-family puts ALL output in reasoning_content with empty content
                text = content if content else reasoning
                usage = r.get("usage", {})
            return {"ok": True, "status": "OK", "text": text,
                    "content_chars": len(content) if api_type == "chat" else len(text),
                    "reasoning_chars": len(reasoning) if api_type == "chat" else 0,
                    "usage": usage,
                    "latency_ms": round((time.time() - t0) * 1000)}
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read().decode()[:200]}"
            time.sleep(2 ** attempt)
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"
            time.sleep(2 ** attempt)
    return {"ok": False, "status": "FAIL", "text": "", "usage": {},
            "latency_ms": round((time.time() - t0) * 1000), "error": last_err}


def verify_t4_syntax(model_dir: Path, text: str) -> dict:
    """Extract python code block and run py_compile (syntax only, no execution)."""
    m = re.search(r"```python\n(.*?)```", text, re.S)
    code = m.group(1) if m else text
    if "def " not in code and "import " not in code:
        return {"extracted": False, "compiles": None, "lines": 0}
    tmp = model_dir / "T4_code.py"
    tmp.write_text(code)
    r = subprocess.run([sys.executable, "-m", "py_compile", str(tmp)],
                       capture_output=True, text=True, timeout=30)
    return {"extracted": True, "compiles": r.returncode == 0,
            "lines": len(code.splitlines()), "error": r.stderr[:200] if r.returncode else None}


def run_model(model: str, api_type: str, tests: list[str], max_tokens: int) -> dict:
    model_dir = OUT_ROOT / model
    model_dir.mkdir(parents=True, exist_ok=True)
    summary_path = model_dir / "summary.json"
    if summary_path.exists():
        prev = json.loads(summary_path.read_text())
        print(f"[skip] {model} already has summary.json")
        return prev

    summary = {"model": model, "api_type": api_type, "started": time.strftime("%H:%M:%S"),
               "tests": {}}
    for t in tests:
        t0 = time.time()
        print(f"[{model}] {t} -> running ...", flush=True)
        try:
            prompt = TESTS[t]()
            res = call_model(model, api_type, prompt, max_tokens)
        except Exception as e:
            res = {"ok": False, "status": "ERR", "text": "", "usage": {},
                   "latency_ms": 0, "error": f"{type(e).__name__}: {e}"[:300]}
        (model_dir / f"{t}.txt").write_text(res.get("text", "") or "")
        entry = {k: v for k, v in res.items() if k != "text"}
        entry["chars"] = len(res.get("text", ""))
        if t == "T4":
            entry["syntax"] = verify_t4_syntax(model_dir, res.get("text", ""))
        summary["tests"][t] = entry
        summary["tests"][t]["elapsed_s"] = round(time.time() - t0)
        print(f"[{model}] {t} -> {res['status']} {entry['latency_ms']}ms "
              f"{entry['chars']}ch {res.get('error', '')[:80]}", flush=True)
    summary["finished"] = time.strftime("%H:%M:%S")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=None, help="comma list; default all 11")
    ap.add_argument("--tests", default="T1,T2,T3,T4,T5")
    ap.add_argument("--max-tokens", type=int, default=8192)
    args = ap.parse_args()

    models = MODELS
    if args.models:
        wanted = set(args.models.split(","))
        models = [(m, t) for m, t in MODELS if m in wanted]

    tests = args.tests.split(",")
    print(f"=== Extreme Test Battery: {len(models)} models x {len(tests)} tests ===", flush=True)
    for m, t in models:
        run_model(m, t, tests, args.max_tokens)
    print("=== DONE ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
