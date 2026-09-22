#!/usr/bin/env python3
"""도시형태 결과 — SVF 구간 × 볕/그늘 상자그림 (2026-09-22).

한 장에서 세 가지를 읽는다.
  1. 구간이 트일수록 그늘 비율이 준다        (상자 위 「shaded k of n」)
  2. 같은 구간 안에서 볕과 그늘의 간격        (구간마다 Δ 중앙값)
  3. 같은 색끼리 구간을 건너갈 때의 변화      (상자 높이)

구간  닫힘 SVF < 0.45 / 중간 0.45–0.65 / 트임 ≥ 0.65
자료  tier3_engine_output_80_v7_photo.csv (PET·볕), aug80_viewfactors.csv (SVF, n 79)
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 7.5, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
MM = 1 / 25.4
DATA = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/climax_mvp/data"
VF = sys.argv[2] if len(sys.argv) > 2 else f"{DATA}/aug80_viewfactors.csv"
OUT = sys.argv[3] if len(sys.argv) > 3 else "/tmp/claude-0/fig_ab"
INK, MUTED = "#1A1A1A", "#5E5E5E"
SUN, SHADE, THR = "#C0504D", "#3F6496", "#B3261E"
BINS = [0.0, 0.45, 0.65, 1.01]
NAMES = [("Enclosed", "SVF < 0.45"), ("Intermediate", "0.45–0.65"), ("Open", "SVF ≥ 0.65")]


def load():
    m = pd.read_csv(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")
    v = pd.read_csv(VF, encoding="utf-8-sig")
    v.loc[v["비고"].notna(), "SVF"] = np.nan
    d = m[["측정ID", "PET", "볕"]].merge(v[["측정ID", "SVF"]], on="측정ID").dropna(subset=["SVF"])
    d["sun"] = d["볕"].astype(int)
    d["bin"] = pd.cut(d.SVF, BINS, right=False, labels=False)
    return d


def box(ax, x, vals, col):
    q1, med, q3 = np.percentile(vals, [25, 50, 75])
    iqr = q3 - q1
    lo = vals[vals >= q1 - 1.5 * iqr].min(); hi = vals[vals <= q3 + 1.5 * iqr].max()
    w = 0.30
    ax.add_patch(plt.Rectangle((x - w / 2, q1), w, q3 - q1, facecolor=col, alpha=0.18,
                               edgecolor=col, lw=0.9, zorder=2))
    ax.plot([x - w / 2, x + w / 2], [med, med], color=col, lw=1.8, zorder=4, solid_capstyle="butt")
    ax.plot([x, x], [q3, hi], color=col, lw=0.8, zorder=2)
    ax.plot([x, x], [lo, q1], color=col, lw=0.8, zorder=2)
    for yv in (lo, hi):
        ax.plot([x - 0.06, x + 0.06], [yv, yv], color=col, lw=0.8, zorder=2)
    rng = np.random.default_rng(int(abs(x) * 100) + 7)
    ax.scatter(x + rng.uniform(-0.09, 0.09, len(vals)), vals, s=9, color=col,
               edgecolor="white", lw=0.3, zorder=5, alpha=0.9)
    return med


def main():
    d = load()
    fig, ax = plt.subplots(figsize=(140 * MM, 88 * MM))
    fig.subplots_adjust(left=0.10, right=0.98, top=0.86, bottom=0.20)
    ax.axhline(41, color=THR, lw=0.7, ls=(0, (5, 3)), zorder=1)
    ax.text(-0.52, 41.25, "PET 41 °C — extreme heat stress", fontsize=6.4, color=THR,
            ha="left", va="bottom")
    OFF = 0.19
    for b in range(3):
        q = d[d.bin == b]
        s, h = q[q.sun == 1].PET.values, q[q.sun == 0].PET.values
        ms = box(ax, b + OFF, s, SUN)
        if len(h):
            mh = box(ax, b - OFF, h, SHADE)
            # 같은 구간 안 볕−그늘 간격
            xg = b + OFF + 0.24
            ax.annotate("", xy=(xg, mh), xytext=(xg, ms),
                        arrowprops=dict(arrowstyle="<->", lw=0.7, color=INK, shrinkA=0, shrinkB=0))
            ax.text(xg + 0.035, (ms + mh) / 2, f"{ms - mh:.1f} °C", fontsize=7.0, color=INK,
                    va="center", ha="left", fontweight="bold")
        else:
            ax.text(b - OFF, 38.0, "no shaded\nsite", fontsize=6.6, color=SHADE, ha="center",
                    va="center", style="italic", linespacing=1.2)
        # 상자 위 한 줄 — 그 구간의 그늘 비율
        pct = 100 * len(h) / len(q)
        ax.text(b, 56.2, f"shaded {len(h)} of {len(q)}  ({pct:.0f} %)", ha="center", va="bottom",
                fontsize=7.2, color=SHADE if len(h) else MUTED,
                fontweight="bold" if len(h) else "normal")
        ax.text(b + OFF, 33.6, f"n = {len(s)}", ha="center", fontsize=6.3, color=SUN)
        if len(h):
            ax.text(b - OFF, 33.6, f"n = {len(h)}", ha="center", fontsize=6.3, color=SHADE)
    ax.set_xlim(-0.55, 2.6); ax.set_ylim(33.0, 56.0)
    ax.set_xticks(range(3))
    ax.set_xticklabels([f"{a}\n{b}" for a, b in NAMES], linespacing=1.35)
    ax.set_ylabel("Measured PET (°C)")
    ax.set_xlabel("Sky view factor class at the site (fisheye)", labelpad=6)
    ax.spines["bottom"].set_visible(False); ax.tick_params(axis="x", length=0)
    ax.legend(handles=[Patch(facecolor=SUN, alpha=0.35, edgecolor=SUN, label="sunlit — direct beam at the sensor"),
                       Patch(facecolor=SHADE, alpha=0.35, edgecolor=SHADE, label="shaded — no direct beam")],
              loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=2, frameon=False,
              fontsize=6.8, handlelength=1.4, columnspacing=2.0)
    for ext in ("pdf", "png", "eps"):
        fig.savefig(f"{OUT}/SCS_Fig_form_box.{ext}", bbox_inches="tight", pad_inches=0.03)
    print(d.groupby(["bin", "sun"]).PET.agg(["count", "median"]).round(1))


if __name__ == "__main__":
    main()
