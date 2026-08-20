#!/usr/bin/env python3
"""make_assets.py — CooLEVAL 原創視覺資產（matplotlib，Sonar 設計 brief 色板）。

色板（Sonar brief）：
  BG       #050711  深黑藍背景
  PANEL    #0B1020  面板
  GRID     #2C3550  網格線
  TEXT     #C3C7D6  主文字
  GREEN    #3DD68C  成功/安全
  BLUE     #4DA3FF  資訊
  ORANGE   #FFB84B  警告
  RED      #FF4B4B  危險/meltdown
  PURPLE   #C792EA  輔助
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

BG = "#050711"
PANEL = "#0B1020"
GRID = "#2C3550"
TEXT = "#C3C7D6"
GREEN = "#3DD68C"
BLUE = "#4DA3FF"
ORANGE = "#FFB84B"
RED = "#FF4B4B"
PURPLE = "#C792EA"

THEMES = {
    "dark": {"BG": "#050711", "PANEL": "#0B1020", "GRID": "#2C3550",
             "TEXT": "#C3C7D6", "GREEN": "#3DD68C", "BLUE": "#4DA3FF",
             "ORANGE": "#FFB84B", "RED": "#FF4B4B", "PURPLE": "#C792EA"},
    "light": {"BG": "#FFFFFF", "PANEL": "#F2F4F8", "GRID": "#D5DAE5",
              "TEXT": "#12151F", "GREEN": "#17A05F", "BLUE": "#1F6FEB",
              "ORANGE": "#D97706", "RED": "#DC2626", "PURPLE": "#7C3AED"},
}


def render_all(theme: str):
    global BG, PANEL, GRID, TEXT, GREEN, BLUE, ORANGE, RED, PURPLE
    colors = THEMES[theme]
    BG = colors["BG"]; PANEL = colors["PANEL"]; GRID = colors["GRID"]
    TEXT = colors["TEXT"]; GREEN = colors["GREEN"]; BLUE = colors["BLUE"]
    ORANGE = colors["ORANGE"]; RED = colors["RED"]; PURPLE = colors["PURPLE"]
    plt.rcParams.update({
        "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
        "text.color": TEXT, "axes.edgecolor": GRID, "axes.labelcolor": TEXT,
        "xtick.color": TEXT, "ytick.color": TEXT, "grid.color": GRID,
    })
    suffix = f"_{theme}"
    meltdown_curve(suffix)
    survival_hazard(suffix)
    architecture(suffix)
    if theme == "dark":
        logo()

OUT = Path(__file__).resolve().parent.parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "figure.facecolor": BG,
    "axes.facecolor": BG,
    "savefig.facecolor": BG,
    "text.color": TEXT,
    "axes.edgecolor": GRID,
    "axes.labelcolor": TEXT,
    "xtick.color": TEXT,
    "ytick.color": TEXT,
    "font.size": 11,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "grid.alpha": 0.5,
})

# ── 1. Meltdown curve (flagship) ────────────────────────────────────────
def meltdown_curve(suffix: str = ""):
    buckets = ["<15 min", "15–60 min", "1–4 h", "4–24 h", ">24 h"]
    rate = [0.984, 0.944, 0.250, 0.0, 0.0]
    lo = [0.969, 0.849, 0.071, 0.0, 0.0]
    hi = [0.992, 0.981, 0.591, 0.354, 0.243]
    n = [505, 54, 8, 7, 12]
    x = range(len(buckets))

    fig, ax = plt.subplots(figsize=(11, 5.2), dpi=150)
    ax.plot(x, rate, color=RED, lw=2.6, marker="o", ms=7,
            markerfacecolor=BG, markeredgecolor=RED, markeredgewidth=2,
            label="success rate")
    ax.fill_between(x, lo, hi, color=RED, alpha=0.15, label="Wilson 95% CI")
    for xi, r, n_i in zip(x, rate, n):
        ax.annotate(f"{r*100:.1f}%\nn={n_i}", (xi, r),
                    textcoords="offset points", xytext=(0, 12),
                    ha="center", fontsize=9.5, color=TEXT)
    ax.set_xticks(list(x)); ax.set_xticklabels(buckets)
    ax.set_ylim(-0.05, 1.08)
    ax.set_ylabel("session success rate")
    ax.set_title("Session meltdown — reliability decays with duration",
                 fontsize=14, fontweight="bold", color=TEXT, pad=14)
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 0.02), fontsize=10,
              frameon=True, facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.subplots_adjust(left=0.09, right=0.97, top=0.90, bottom=0.17)
    fig.text(0.5, 0.045,
             "586 real sessions (battery one-shots excluded by pre-registered rule) · "
             "artifact-verified outcomes, not self-report · Wilson 95% CIs shown — "
             "long-duration buckets are low-n, interpret carefully",
             ha="center", fontsize=8.5, color=TEXT, alpha=0.8)
    fig.savefig(OUT / f"meltdown_curve{suffix}.png", bbox_inches="tight")
    plt.close(fig)
    print(f"meltdown_curve{suffix}.png")

# ── 2. Survival + hazard ────────────────────────────────────────────────
def survival_hazard(suffix: str = ""):
    buckets = ["<15 min", "15–60 min", "1–4 h", "4–24 h", ">24 h"]
    rate = [0.984, 0.944, 0.250, 0.0, 0.0]
    n = [505, 54, 8, 7, 12]
    # discrete survival: cumulative product of bucket success rates
    surv = [1.0]
    for r in rate:
        surv.append(surv[-1] * r)
    # hazard: per-bucket failure rate (discrete estimate)
    hazard = [1 - r for r in rate]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10.5, 6.4), dpi=150,
                                   sharex=True, gridspec_kw={"hspace": 0.18})
    x = range(len(buckets))
    xs = list(range(len(surv)))

    ax1.plot(xs, surv, color=BLUE, lw=2.4, marker="o", ms=6)
    ax1.fill_between(xs, surv, 0, color=BLUE, alpha=0.08)
    ax1.set_ylabel("survival\nP(still successful)")
    ax1.set_ylim(0, 1.05)
    ax1.set_title("Survival & hazard — where agents melt down", fontsize=13,
                  fontweight="bold", color=TEXT, pad=10)
    ax1.spines["top"].set_visible(False); ax1.spines["right"].set_visible(False)

    ax2.bar(x, hazard, color=RED, alpha=0.85, width=0.6,
            label="per-bucket failure rate")
    for xi, h, n_i in zip(x, hazard, n):
        ax2.annotate(f"{h*100:.1f}%\n(n={n_i})", (xi, h), textcoords="offset points",
                     xytext=(0, 6), ha="center", fontsize=8.5, color=TEXT)
    ax2.set_xticks(list(x)); ax2.set_xticklabels(buckets)
    ax2.set_ylabel("hazard")
    ax2.set_ylim(0, 1.1)
    ax2.legend(loc="upper left", frameon=True, facecolor=PANEL,
               edgecolor=GRID, labelcolor=TEXT)
    ax2.spines["top"].set_visible(False); ax2.spines["right"].set_visible(False)
    fig.subplots_adjust(left=0.09, right=0.97, top=0.93, bottom=0.14)
    fig.text(0.5, 0.03,
             "Discrete estimates from observed session outcomes · long buckets are low-n",
             ha="center", fontsize=8.5, alpha=0.8)
    fig.savefig(OUT / f"survival_hazard{suffix}.png", bbox_inches="tight")
    plt.close(fig)
    print(f"survival_hazard{suffix}.png")

# ── 3. Architecture diagram ─────────────────────────────────────────────
def architecture(suffix: str = ""):
    fig, ax = plt.subplots(figsize=(11.5, 4.6), dpi=150)
    ax.set_xlim(0, 12); ax.set_ylim(0, 5); ax.axis("off")

    def box(x, y, w, h, text, fc=PANEL, ec=GRID, tc=TEXT, fs=10.5):
        ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08",
                     fc=fc, ec=ec, lw=1.4))
        ax.text(x + w/2, y + h/2, text, ha="center", va="center", fontsize=fs, color=tc)

    def arrow(x1, y1, x2, y2, color=GRID):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=1.6))

    # L0 sources
    box(0.3, 4.1, 2.6, 0.7, "traces.jsonl\n(span tracer)", fs=9)
    box(0.3, 3.2, 2.6, 0.7, "agent lifecycle\n(SQLite)", fs=9)
    box(0.3, 2.3, 2.6, 0.7, "sessions\n(state.db)", fs=9)
    # L0 ETL
    box(3.6, 3.0, 2.6, 1.4, "eval-etl.py\nidempotent ETL\nwatermarks · dedup", fs=9.5)
    arrow(2.95, 4.2, 3.55, 3.7); arrow(2.95, 3.3, 3.55, 3.5); arrow(2.95, 2.7, 3.55, 3.3)
    # eval.db
    box(6.9, 3.0, 2.0, 1.4, "eval.db\nSQLite", fs=10)
    arrow(6.25, 3.7, 6.85, 3.7)
    # L1 metrics
    box(9.5, 3.0, 2.2, 1.4, "eval-metrics.py\nWilson CI · hazard\nfailure taxonomy", fs=9)
    arrow(8.95, 3.7, 9.45, 3.7)
    # L2 runner
    box(3.6, 0.9, 2.6, 1.1, "eval-runner.py\ndogfood battery\nartifact-verified", fs=9.5)
    arrow(4.9, 2.0, 4.9, 2.95, color=BLUE)
    # L3 report
    box(6.9, 0.9, 2.0, 1.1, "eval-report.py\nmarkdown/API", fs=9.5)
    arrow(6.25, 1.45, 6.85, 1.45)
    # model eval
    box(9.5, 0.9, 2.2, 1.1, "extreme-test-\nrunner.py\n11 models × 5 tests", fs=8.5)
    arrow(8.95, 1.45, 9.45, 1.45, color=PURPLE)

    ax.text(0.3, 4.85, "L0 · DATA", fontsize=8.5, color=GREEN, fontweight="bold")
    ax.text(3.6, 4.85, "L1 · METRICS / EXECUTION", fontsize=8.5, color=BLUE, fontweight="bold")
    ax.text(9.5, 4.85, "L3 · REPORTING", fontsize=8.5, color=ORANGE, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / f"architecture{suffix}.png", bbox_inches="tight")
    plt.close(fig)
    print(f"architecture{suffix}.png")

# ── 4. Logo ─────────────────────────────────────────────────────────────
def logo():
    fig, ax = plt.subplots(figsize=(3.2, 3.2), dpi=200)
    ax.set_xlim(-1.2, 1.2); ax.set_ylim(-1.2, 1.2); ax.axis("off")

    # dial
    dial = mpatches.Circle((0, 0), 1.0, fc=BG, ec="#E8EAF0", lw=2.2)
    ax.add_patch(dial)
    # tick marks (12)
    import numpy as np
    for k in range(12):
        ang = np.deg2rad(k * 30 - 90)
        r0, r1 = 0.88, 0.95
        ax.plot([r0*np.cos(ang), r1*np.cos(ang)], [r0*np.sin(ang), r1*np.sin(ang)],
                color="#E8EAF0", lw=1.0, alpha=0.6)
    # green start tick at 12 o'clock
    ax.plot([0, 0], [0.88, 0.95], color=GREEN, lw=2.4)
    # red melting arc descending from top clockwise
    th = np.linspace(np.deg2rad(-90), np.deg2rad(200), 120)
    ax.plot(np.cos(th), np.sin(th), color=RED, lw=3.2,
            solid_capstyle="round", alpha=0.95)
    # fading tail (particles)
    for t in np.linspace(np.deg2rad(200), np.deg2rad(235), 10):
        r = np.random.uniform(0.55, 0.9)
        ax.plot(r*np.cos(t), r*np.sin(t), "o", ms=np.random.uniform(2, 5),
                color=RED, alpha=0.35)
    fig.tight_layout()
    fig.savefig(OUT / "cooleval_logo.png", bbox_inches="tight", transparent=True)
    plt.close(fig)
    print("cooleval_logo.png")


if __name__ == "__main__":
    for theme in ("dark", "light"):
        render_all(theme)
    print("done ->", OUT)
