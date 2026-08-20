#!/usr/bin/env python3
"""price-probe.py — zen 價格探針：每個候選 model 打 1 個最細 call（max_tokens=5）。

流程：
1. GET /v1/models 攞 live catalog（config 入面啲 list 會 stale）
2. 候選 model 存在先 probe（1 call，max_tokens=5, temp=0.1）
3. 記錄 success / latency / usage / error
4. 輸出 JSON + 摘要

注意（model-evaluation-protocol skill）：
- opencode.ai edge 會 403 code 1010 ban 純 urllib — 一定要 browser UA + Origin/Referer
- claude 系要 anthropic-dangerous-direct-browser-access header，否則 CORS auth error
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

CFG = Path(os.environ.get("HERMES_CONFIG", str(Path(__file__).resolve().parent.parent / "data" / "hermes-config.yaml")))
OUT = Path(os.environ.get("COOLEVAL_OUT",
                         str(Path(__file__).resolve().parent.parent / "reports"
                             / "price-probe.json")))

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

CANDIDATES = {
    "closed": [
        "claude-opus-5",
        "claude-fable-5",
        "gpt-5.6-sol",
        "gemini-3.1-pro",
        "grok-4.6",
    ],
    "open": [
        "deepseek-v4-pro",
        "qwen3.6-plus",
        "glm-5.2",
        "kimi-k3",
        "nemotron-3-ultra-free",
        "nemotron-3.5-lightning-free",
    ],
    "baseline": ["deepseek-v4-flash"],
    "known_working": ["claude-opus-4-8", "claude-sonnet-4-6"],
}


def headers(extra: dict | None = None) -> dict:
    h = {
        "User-Agent": UA,
        "Origin": "https://opencode.ai",
        "Referer": "https://opencode.ai/",
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _req(url: str, body: dict | None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    extra = {"anthropic-dangerous-direct-browser-access": "true"} if (
        body and body.get("model", "").startswith("claude")) else None
    req = urllib.request.Request(url, data=data, headers=headers(extra), method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def list_models() -> list[str]:
    try:
        cat = _req(f"{BASE}/models", None, timeout=30)
        data = cat.get("data", cat if isinstance(cat, list) else [])
        return [m.get("id", "") for m in data if isinstance(m, dict)]
    except Exception as e:
        print(f"MODELS LIST FAILED: {e!r}", file=sys.stderr)
        return []


def probe(model: str) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 5,
        "temperature": 0.1,
    }
    t0 = time.time()
    try:
        r = _req(f"{BASE}/chat/completions", body)
        text = r["choices"][0]["message"]["content"]
        usage = r.get("usage", {})
        return {
            "status": "OK",
            "latency_ms": round((time.time() - t0) * 1000),
            "content": text,
            "finish_reason": r["choices"][0].get("finish_reason"),
            "usage": usage,
        }
    except urllib.error.HTTPError as e:
        return {"status": f"HTTP {e.code}", "latency_ms": round((time.time() - t0) * 1000),
                "error": e.read().decode()[:300]}
    except Exception as e:
        return {"status": "ERR", "latency_ms": round((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"[:300]}


def main() -> int:
    live = list_models()
    live_set = set(live)
    print(f"live catalog: {len(live)} models")
    print("sample:", ", ".join(sorted(live)[:12]))
    print()

    results: dict[str, dict] = {}
    for group, models in CANDIDATES.items():
        for m in models:
            if m not in live_set:
                results[m] = {"group": group, "status": "NOT_LISTED"}
                print(f"[{group:14s}] {m:30s} -> NOT_LISTED (不在 live catalog)")
                continue
            r = probe(m)
            r["group"] = group
            results[m] = r
            if r["status"] == "OK":
                print(f"[{group:14s}] {m:30s} -> OK {r['latency_ms']}ms "
                      f"in={r['usage'].get('prompt_tokens', '?')} out={r['usage'].get('completion_tokens', '?')}")
            else:
                print(f"[{group:14s}] {m:30s} -> {r['status']} {r.get('error', '')[:120]}")

    out = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": BASE,
        "live_count": len(live),
        "live_sample": sorted(live)[:30],
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nsaved -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
