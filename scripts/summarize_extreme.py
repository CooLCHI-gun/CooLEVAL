#!/usr/bin/env python3
"""summarize_extreme.py — 讀 /tmp/extreme-test-results/*/summary.json，
輸出：per-model × per-test 狀態表 + 統計 + extreme heatmap PNG。

用法：python3 summarize_extreme.py [results_dir] [--out /root/workspace/CooLEVAL/assets]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESULTS = Path("/tmp/extreme-test-results")
TESTS = ["T1", "T2", "T3", "T4", "T5"]


def wilson_ok(entry) -> bool:
    return entry.get("status") == "OK"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="?", default=str(RESULTS))
    ap.add_argument("--out", default="/root/workspace/CooLEVAL/assets")
    args = ap.parse_args()

    root = Path(args.results)
    models = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "summary.json").exists())
    if not models:
        print("NO MODELS DONE YET")
        return 1

    rows = []
    for m in models:
        s = json.loads((root / m / "summary.json").read_text())
        row = {"model": m}
        for t in TESTS:
            e = s.get("tests", {}).get(t, {})
            row[t] = {
                "status": e.get("status", "-"),
                "ok": wilson_ok(e),
                "chars": e.get("chars", 0),
                "content_chars": e.get("content_chars"),
                "reasoning_chars": e.get("reasoning_chars"),
                "latency_ms": e.get("latency_ms", 0),
                "syntax": (e.get("syntax") or {}).get("compiles"),
                "usage": e.get("usage", {}),
                "error": e.get("error", ""),
            }
        rows.append(row)

    print(f"=== Extreme Test Summary: {len(models)} models ===\n")
    header = f"{'model':26s} | " + " | ".join(f"{t:6s}" for t in TESTS)
    print(header)
    print("-" * len(header))
    for r in rows:
        cells = []
        for t in TESTS:
            e = r[t]
            if e["status"] != "OK":
                cells.append(f"{'FAIL':6s}")
            else:
                tag = ""
                if t == "T4" and e["syntax"] is not None:
                    tag = "✓" if e["syntax"] else "✗"
                cells.append(f"{'OK' + tag:6s}")
        print(f"{r['model']:26s} | " + " | ".join(cells))

    # per-model aggregates
    print("\n--- per-model ---")
    for r in rows:
        ok = sum(1 for t in TESTS if r[t]["ok"])
        tot_chars = sum(r[t]["chars"] for t in TESTS)
        tot_ms = sum(r[t]["latency_ms"] for t in TESTS)
        toks = sum((r[t]["usage"] or {}).get("completion_tokens", 0) for t in TESTS)
        print(f"  {r['model']:24s} {ok}/{len(TESTS)} ok | chars={tot_chars:>7,} | "
              f"latency={tot_ms/1000:>6.1f}s | out_tokens={toks:>7,}")

    # save JSON for README build
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    json_path = Path("/tmp/extreme-test-summary.json")
    json_path.write_text(json.dumps(
        {"models": [{"model": r["model"],
                     "tests": {t: r[t] for t in TESTS}} for r in rows]},
        indent=2, ensure_ascii=False))
    print(f"\nsaved -> {json_path}")

    # ── heatmap ─────────────────────────────────────────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        BG, PANEL, GRID = "#050711", "#0B1020", "#2C3550"
        TEXT, GREEN, RED = "#C3C7D6", "#3DD68C", "#FF4B4B"
        plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG,
                             "text.color": TEXT, "axes.edgecolor": GRID,
                             "xtick.color": TEXT, "ytick.color": TEXT})
        data = [[1.0 if r[t]["ok"] else 0.0 for t in TESTS] for r in rows]
        fig, ax = plt.subplots(figsize=(9.5, 0.55 * len(rows) + 1.6), dpi=150)
        cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
            "cooleval", ["#FF4B4B", "#B0647A", "#3DD68C"])  # red -> green
        im = ax.imshow(data, cmap=cmap, vmin=0, vmax=1, aspect="auto")
        ax.set_xticks(range(len(TESTS)))
        ax.set_xticklabels(["T1 proof", "T2 long-ctx", "T3 lipogram",
                            "T4 code", "T5 planning"])
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([r["model"] for r in rows], fontsize=9)
        for i in range(len(rows)):
            for j in range(len(TESTS)):
                e = rows[i]["tests"][TESTS[j]]
                if e["status"] != "OK":
                    ax.text(j, i, "FAIL", ha="center", va="center",
                            color="white", fontsize=8, fontweight="bold")
                else:
                    txt = "✓"
                    if TESTS[j] == "T4" and e["syntax"] is False:
                        txt = "✓✗"
                    ax.text(j, i, txt, ha="center", va="center", color="white",
                            fontsize=10, fontweight="bold")
        ax.set_title("Extreme tests — 11 frontier models under stress",
                     fontsize=13, fontweight="bold", color=TEXT, pad=12)
        ax.grid(False)
        for s in ax.spines.values():
            s.set_visible(False)
        fig.tight_layout()
        out_png = Path(args.out) / "extreme_test_heatmap.png"
        fig.savefig(out_png, bbox_inches="tight")
        print(f"saved -> {out_png}")
    except ImportError:
        print("matplotlib missing; heatmap skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
