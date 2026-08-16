#!/usr/bin/env python3
"""make_animate.py — CooLEVAL 動態圖（GIF，包埋 CooLEVAL logo）。

1. meltdown_curve_animated.gif — intro (logo) → 曲線逐步繪製 → MELTDOWN 揭示
2. extreme_race_animated.gif   — intro (logo) → model 分數條 race（closed/open/baseline 分色）

用法：python3 make_animate.py [results_dir]
結果：/root/workspace/CooLEVAL/assets/*.gif
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

BG, PANEL, GRID = "#050711", "#0B1020", "#2C3550"
TEXT, GREEN, RED, BLUE, ORANGE, PURPLE = "#C3C7D6", "#3DD68C", "#FF4B4B", "#4DA3FF", "#FFB84B", "#C792EA"

ASSETS = Path(__file__).resolve().parent.parent / "assets"
RESULTS = Path("/tmp/extreme-test-results")
LOGO = Image.open(ASSETS / "cooleval_logo.png").convert("RGBA")
TESTS = ["T1", "T2", "T3", "T4", "T5"]

plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG,
                     "text.color": TEXT, "axes.edgecolor": GRID, "axes.labelcolor": TEXT,
                     "xtick.color": TEXT, "ytick.color": TEXT, "font.size": 11,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
                     "grid.alpha": 0.5})

DPI = 100


def logo_img(width_px: int, alpha: float = 1.0) -> Image.Image:
    scale = width_px / LOGO.width
    im = LOGO.resize((width_px, max(1, int(LOGO.height * scale))), Image.LANCZOS)
    if alpha < 1.0:
        im = im.copy()
        a = im.getchannel("A").point(lambda v: int(v * alpha))
        im.putalpha(a)
    return im


def fig_to_pil(fig) -> Image.Image:
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())
    plt.close(fig)
    return Image.fromarray(buf).convert("RGB")


def watermark(fig, width_px: int = 84, alpha: float = 0.9):
    im = logo_img(width_px, alpha)
    arr = np.asarray(im)
    fig_w = fig.get_size_inches()[0] * fig.dpi
    fig_h = fig.get_size_inches()[1] * fig.dpi
    xo = int(fig_w - width_px - 14)
    yo = int(fig_h - im.height - 14)
    fig.figimage(arr, xo=xo, yo=yo, origin="upper")


def intro_frame(w_in: float, h_in: float, subtitle: str) -> Image.Image:
    fig = plt.figure(figsize=(w_in, h_in), dpi=DPI, facecolor=BG)
    fig.patch.set_facecolor(BG)
    im = logo_img(150)
    fig_w = w_in * DPI; fig_h = h_in * DPI
    fig.figimage(np.asarray(im), xo=int((fig_w - im.width) / 2),
                 yo=int((fig_h - im.height) / 2 - 90), origin="upper")
    fig.text(0.5, 0.30, "CooLEVAL", ha="center", fontsize=34, fontweight="bold", color=TEXT)
    fig.text(0.5, 0.205, subtitle, ha="center", fontsize=13, color=TEXT, alpha=0.85)
    return fig_to_pil(fig)


def meltdown_gif():
    buckets = ["<15 min", "15–60 min", "1–4 h", "4–24 h", ">24 h"]
    rate = [0.984, 0.944, 0.250, 0.0, 0.0]
    lo = [0.969, 0.849, 0.071, 0.0, 0.0]
    hi = [0.992, 0.981, 0.591, 0.354, 0.243]
    n = [505, 54, 8, 7, 12]

    fig = plt.figure(figsize=(10.2, 4.8), dpi=DPI, facecolor=BG)
    ax = fig.add_subplot(111)
    ax.set_xlim(-0.4, len(buckets) - 0.6)
    ax.set_ylim(-0.08, 1.12)
    ax.set_xticks(range(len(buckets)))
    ax.set_xticklabels(buckets)
    ax.set_ylabel("session success rate")
    ax.set_title("Session meltdown — reliability decays with duration",
                 fontsize=14, fontweight="bold", color=TEXT, pad=12)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)

    line, = ax.plot([], [], color=RED, lw=2.6, marker="o", ms=7,
                    markerfacecolor=BG, markeredgecolor=RED, markeredgewidth=2)
    band = None
    anns = []
    frames = []

    def snapshot():
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())
        return Image.fromarray(buf).convert("RGB")

    # reveal per bucket: 3 subframes (point -> CI -> label)
    for i in range(len(buckets)):
        xs = list(range(i + 1)); ys = rate[:i + 1]
        for step in range(3):
            line.set_data(xs if step > 0 else [], ys if step > 0 else [])
            if step >= 1 and i > 0:
                if band is not None:
                    band.remove()
                xb = list(range(i)); yl = lo[:i]; yh = hi[:i]
                band = ax.fill_between(xb, yl, yh, color=RED, alpha=0.15)
            if step == 2:
                ann = ax.annotate(f"{rate[i]*100:.1f}%  n={n[i]}", (i, rate[i]),
                                  textcoords="offset points", xytext=(0, 10),
                                  ha="center", fontsize=9.5, color=TEXT)
                anns.append(ann)
            watermark(fig)
            frames.append(snapshot())

    # MELTDOWN reveal
    ax.annotate("MELTDOWN", xy=(2, 0.25), xytext=(2.05, 0.62),
                fontsize=15, fontweight="bold", color=RED,
                arrowprops=dict(arrowstyle="-|>", color=RED, lw=2))
    for _ in range(4):
        watermark(fig)
        frames.append(snapshot())

    out = ASSETS / "meltdown_curve_animated.gif"
    intro = intro_frame(10.2, 4.8, "Session meltdown — measured, not benchmarked")
    all_frames = [intro] + frames
    all_frames[0].save(out, save_all=True, append_images=all_frames[1:],
                       duration=190, loop=0, optimize=True)
    print(f"saved -> {out} ({len(all_frames)} frames)")


def load_scores() -> list[dict]:
    models = sorted(p.name for p in RESULTS.iterdir()
                    if p.is_dir() and (p / "summary.json").exists())
    scores = []
    families = {"claude": "closed", "gpt": "closed", "grok": "closed", "gemini": "closed",
                "deepseek": "open", "qwen": "open", "glm": "open", "kimi": "open",
                "nemotron": "open", "minimax": "open", "mimo": "open"}
    for m in models:
        s = json.loads((RESULTS / m / "summary.json").read_text())
        ok = sum(1 for t in TESTS if s.get("tests", {}).get(t, {}).get("status") == "OK")
        scores.append({"model": m, "score": ok,
                       "family": next((v for k, v in families.items() if k in m), "other")})
    return scores


def race_gif():
    scores = load_scores()
    if not scores:
        print("no results yet; race skipped")
        return
    scores.sort(key=lambda r: -r["score"])
    colors = {"closed": RED, "open": BLUE, "baseline": GREEN, "other": PURPLE}

    fig = plt.figure(figsize=(10.2, max(4.6, 0.5 * len(scores) + 2.2)), dpi=DPI, facecolor=BG)
    ax = fig.add_subplot(111)
    ax.set_xlim(0, 5.2)
    ax.set_ylim(-0.6, len(scores) - 0.4)
    ax.set_xlabel("extreme tests completed (of 5)")
    ax.set_title("Extreme tests — who survives all five?",
                 fontsize=14, fontweight="bold", color=TEXT, pad=12)
    ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
    ax.set_yticks(range(len(scores)))
    ax.set_yticklabels([f"{r['model']}  ({r['family']})" for r in scores], fontsize=9.5)
    ax.grid(axis="x")
    bars = [ax.barh(i, 0, color=colors[r["family"]], alpha=0.9, height=0.62)
            for i, r in enumerate(scores)]
    val_txt = [ax.text(0, i, "", ha="left", va="center", fontsize=10,
                       fontweight="bold", color="white") for i in range(len(scores))]

    frames = []
    def snapshot():
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())
        return Image.fromarray(buf).convert("RGB")

    # animate each bar growing 0 -> score (4 steps each)
    for i, r in enumerate(scores):
        steps = max(1, int(r["score"] * 4))
        for s in range(1, steps + 1):
            w = r["score"] * s / steps
            bars[i][0].set_width(w)
            val_txt[i].set_text(f"{r['score']}/5" if s == steps else "")
            val_txt[i].set_x(w + 0.08)
            watermark(fig)
            frames.append(snapshot())

    out = ASSETS / "extreme_race_animated.gif"
    intro = intro_frame(10.2, max(4.6, 0.5 * len(scores) + 2.2),
                        "Who survives all five extreme tests?")
    all_frames = [intro] + frames
    all_frames[0].save(out, save_all=True, append_images=all_frames[1:],
                       duration=170, loop=0, optimize=True)
    print(f"saved -> {out} ({len(all_frames)} frames)")


def main() -> int:
    global RESULTS
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="?", default=str(RESULTS))
    args = ap.parse_args()
    RESULTS = Path(args.results)

    frames = [intro_frame(10.2, 4.8, "Agent success collapses past the one-hour mark")]
    # meltdown gif with its own intro
    im = intro_frame(10.2, 4.8, "Session meltdown — measured, not benchmarked")
    # simple: prepend intro to meltdown by regenerating inside; keep race standalone
    meltdown_gif()
    race_gif()
    return 0


if __name__ == "__main__":
    sys.exit(main())
