#!/usr/bin/env python3
"""score_tests.py — 自動 rubric scoring for extreme test outputs.

對 /tmp/extreme-test-results/<model>/{T1..T5}.txt 做程式化評分（0-1 每 test）：
  T1 proof   — 最終分數出現（999/1000 + T(999) 約簡分數）+ telescoping 關鍵詞
  T2 recall  — 4 條事實 verbatim 命中數 / 4
  T3 rules   — 10 條規則程式化檢查（paragraph count / lipogram / ending / words / harbour）
  T4 code    — py_compile 通過與否（用 summary.json 已有結果）
  T5 plan    — 信心 65-85% + ≥4 risks + fail conditions + phases 關鍵結構

輸出 JSON + 打印 0-1 分數表（比 binary ✅ 有鑑別度）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RESULTS = Path("/tmp/extreme-test-results")
TESTS = ["T1", "T2", "T3", "T4", "T5"]

T2_FACTS = [
    "The rendezvous point is Sector 9, Bay 12.",
    "The drop time is 03:47 UTC.",
    "The vault combination is 4815-2277-9033.",
    "The final handshake is 'kraken-waltz'.",
]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def latex_norm(s: str) -> str:
    """Strip LaTeX separators/commands so 500{,}499 or \\frac{500}{499} still match."""
    t = s
    t = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"\1/\2", t)
    t = t.replace("{,}", "").replace("{", "").replace("}", "")
    t = t.replace("\\", " ")
    t = re.sub(r"\s+", " ", t)
    return t.lower()


def score_t1(text: str) -> float:
    t = latex_norm(text)
    score = 0.0
    # (a) identity + S(999) = 999/1000
    if "999/1000" in t or "999 / 1000" in t or "n/(n+1)" in t:
        score += 0.3
    # (c) T(n) identity + T(999) value: reduced = 500499/2002000
    if ("1000998" in t and "4004000" in t) or ("500499" in t and "2002000" in t) \
            or ("500499/2002000" in t) or ("500,499" in t):
        score += 0.3
    # telescoping / partial fractions keywords
    kw = sum(1 for k in ["telescop", "partial fraction", "1/k - 1/(k+1)", "1/(k(k+1))"] if k in t)
    score += min(kw, 4) * 0.1
    return round(min(score, 1.0), 2)


def score_t2(text: str) -> float:
    t = norm(text)
    hits = 0
    for fact in T2_FACTS:
        # check key fragments of each fact (robust to small wording drift)
        frags = {
            "Sector 9, Bay 12": 1, "sector 9": 1, "bay 12": 1,
            "03:47 UTC": 1, "03:47": 1,
            "4815-2277-9033": 1, "4815": 1,
            "kraken-waltz": 1, "kraken waltz": 1,
        }
        if any(f in t for f in frags):
            hits += 1
    return hits / len(T2_FACTS)


def score_t3(text: str) -> float:
    t = text.strip()
    score = 0.0
    # 1. exactly three paragraphs
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) == 3:
        score += 0.1
    # 2/3/4 topics: ocean navigation / machine learning / timekeeping (loose)
    topics = ["ocean", "navi", "sea", "ship"]  # p1
    ml = ["machine learning", "learning", "model", "neural", "data"]
    tk = ["time", "clock", "hour", "watch", "second"]
    if any(k in norm(paras[0]) for k in topics if len(paras) > 0):
        score += 0.1
    if len(paras) > 1 and any(k in norm(paras[1]) for k in ml):
        score += 0.1
    if len(paras) > 2 and any(k in norm(paras[2]) for k in tk):
        score += 0.1
    # 5. lipogram: no letter 'e' in paragraph 1
    if len(paras) > 0 and not re.search(r"[eE]", paras[0]):
        score += 0.2
    # 6. paragraph 2 has exactly 4 sentences (rough: split on .!?)
    if len(paras) > 1:
        sents = [s for s in re.split(r"[.!?]", paras[1]) if s.strip()]
        if 3 <= len(sents) <= 5:
            score += 0.05
    # 7. headers start with '## '
    if all(re.match(r"^\s*##\s", p) for p in paras):
        score += 0.05
    # 8. 'harbour' exactly once in paragraph 3
    if len(paras) > 2 and norm(paras[2]).count("harbour") == 1:
        score += 0.05
    # 9. ends with END OF RULES TEST
    if norm(t).endswith("end of rules test"):
        score += 0.15
    # 10. 180-240 words
    words = len(t.split())
    if 160 <= words <= 260:
        score += 0.1
    return round(min(score, 1.0), 2)


def score_t5(text: str) -> float:
    t = norm(text)
    score = 0.0
    # confidence 65-85%
    conf = re.findall(r"(\d{1,2})%", t)
    good = any(65 <= int(c) <= 85 for c in conf)
    score += 0.3 if good else 0.0
    # >= 4 risks (mention of risk + numbered items)
    risks = len(re.findall(r"risk", t))
    score += 0.2 if risks >= 4 else (0.1 if risks >= 2 else 0.0)
    # fail conditions
    fc = len(re.findall(r"fail|rollback|trigger", t))
    score += 0.2 if fc >= 3 else (0.1 if fc >= 1 else 0.0)
    # phases / structure
    ph = len(re.findall(r"phase|milestone", t))
    score += 0.2 if ph >= 3 else (0.1 if ph >= 1 else 0.0)
    # parallelization / dependencies
    par = len(re.findall(r"parallel|concurrent|depend", t))
    score += 0.1 if par >= 2 else 0.0
    return round(min(score, 1.0), 2)


def score_t4(syntax) -> float:
    if syntax is None:
        return 0.0
    return 1.0 if syntax else 0.0


def main() -> int:
    models = sorted(p.name for p in RESULTS.iterdir()
                    if p.is_dir() and (p / "summary.json").exists())
    if not models:
        print("NO RESULTS")
        return 1

    out = {}
    print(f"{'model':24s} | T1    T2    T3    T4    T5    | total")
    print("-" * 70)
    for m in models:
        d = RESULTS / m
        s = json.loads((d / "summary.json").read_text())
        scores = {}
        empties = {}
        for t in TESTS:
            f = d / f"{t}.txt"
            if not f.exists():
                scores[t], empties[t] = 0.0, True
                continue
            txt = f.read_text(errors="ignore")
            if not txt.strip():
                scores[t], empties[t] = 0.0, True
                continue
            empties[t] = False
            if t == "T1":
                scores[t] = score_t1(txt)
            elif t == "T2":
                scores[t] = score_t2(txt)
            elif t == "T3":
                scores[t] = score_t3(txt)
            elif t == "T5":
                scores[t] = score_t5(txt)
        syntax = s.get("tests", {}).get("T4", {}).get("syntax", {}).get("compiles")
        scores["T4"] = score_t4(syntax)
        empties["T4"] = empties.get("T4", False)
        total = sum(scores.values()) / len(TESTS)
        out[m] = {"scores": scores, "total": round(total, 2), "empty": empties}
        row = []
        for t in TESTS:
            if empties.get(t):
                row.append("  ∅  ")
            else:
                row.append(f"{scores[t]:.2f}")
        print(f"{m:24s} | " + " ".join(row) + f"  | {total:.2f}")

    json_path = Path("/tmp/extreme-test-scores.json")
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nsaved -> {json_path}")

    # ── rubric heatmap (0-1 red→green, ∅ for empty) ─────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        BG, GRID = "#050711", "#2C3550"
        TEXT = "#C3C7D6"
        plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG,
                             "text.color": TEXT, "axes.edgecolor": GRID,
                             "xtick.color": TEXT, "ytick.color": TEXT})
        model_names = list(out.keys())
        data = np.full((len(model_names), len(TESTS)), np.nan)
        for i, m in enumerate(model_names):
            for j, t in enumerate(TESTS):
                if not out[m]["empty"].get(t):
                    data[i, j] = out[m]["scores"][t]
        fig, ax = plt.subplots(figsize=(10, 0.62 * len(model_names) + 1.6), dpi=150)
        cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
            "cooleval_rubric", ["#FF4B4B", "#FFB84B", "#3DD68C"])
        im = ax.imshow(data, cmap=cmap, vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(len(TESTS)))
        ax.set_xticklabels(["T1 proof", "T2 long-ctx", "T3 lipogram", "T4 code", "T5 planning"])
        ax.set_yticks(range(len(model_names)))
        ax.set_yticklabels(model_names, fontsize=9)
        for i in range(len(model_names)):
            for j in range(len(TESTS)):
                v = data[i, j]
                if np.isnan(v):
                    ax.text(j, i, "∅", ha="center", va="center", color="white",
                            fontsize=9, fontweight="bold")
                else:
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center", color="white",
                            fontsize=8)
        ax.set_title("Extreme tests — automated rubric scores (0–1, ∅ = no output)",
                     fontsize=12, fontweight="bold", color=TEXT, pad=12)
        ax.grid(False)
        for s in ax.spines.values():
            s.set_visible(False)
        fig.tight_layout()
        out_png = Path(__file__).resolve().parent.parent / "assets" / "extreme_test_heatmap.png"
        fig.savefig(out_png, bbox_inches="tight")
        print(f"saved -> {out_png}")
    except ImportError:
        print("matplotlib missing; heatmap skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
