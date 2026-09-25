#!/usr/bin/env python3
"""LCZ class ranges (Stewart & Oke, 2012) against the measured form at the 80 sites — one panel per index.

(a) building height, (b) height-to-width ratio, (c) sky view factor. Grey band = the range that defines the
LCZ class the neighbourhood was assigned; dots = sites; bar = neighbourhood median.
Sources: scs_master_80.csv — H = width_m × hw_ratio (building footprints + road centreline; 7 sites without a
centreline are absent from (a)(b)), SVF = 360° panorama, 2026-09-21 consistent set (one site without pose, n 79).
Replaces the deck figure whose SVF used the discarded 2026-09-14 set and whose height / surface-fraction
columns had no reproducible source in the repository.
"""
import sys, numpy as np, pandas as pd, matplotlib as mpl, matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"; OUT = sys.argv[2] if len(sys.argv) > 2 else "figout"
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8.5, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "axes.spines.top": True, "axes.spines.right": True, "xtick.direction": "in", "ytick.direction": "in",
    "figure.dpi": 200, "savefig.dpi": 1200, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED, FAINT, SURF = "#1A1A1A", "#5E5E5E", "#B8B8B8", "#E4E4E2"
MM = 1 / 25.4

# Stewart & Oke (2012), Table 3: building height (m), aspect ratio, sky view factor
LCZ = [("부암제1동", "LCZ 1\nBuam 1",   (25, None), (2.0, None), (0.2, 0.4)),
       ("보수동",   "LCZ 2\nBosu",     (10, 25),   (0.75, 2.0), (0.3, 0.6)),
       ("서제2동",  "LCZ 3\nSeo 2",     (3, 10),    (0.75, 1.5), (0.2, 0.6)),
       ("용호제1동", "LCZ 4\nYongho",   (25, None), (0.75, 1.25), (0.5, 0.7)),
       ("명장동",   "LCZ 5\nMyeong-\njang", (10, 25),   (0.3, 0.75), (0.5, 0.8))]

d = pd.read_csv(f"{DATA}/scs_master_80.csv", encoding="utf-8-sig")
d["H"] = d.width_m * d.hw_ratio
panels = [("(a)  Building height (m)", "H", 0, (0, 36), [0, 10, 20, 30]),
          ("(b)  Height-to-width ratio", "hw_ratio", 1, (0, 3.2), [0, 1, 2, 3]),
          ("(c)  Sky view factor", "SVF", 2, (0, 1.0), [0, 0.2, 0.4, 0.6, 0.8, 1.0])]

fig, axes = plt.subplots(1, 3, figsize=(190 * MM, 78 * MM))
fig.subplots_adjust(left=0.05, right=0.99, top=0.88, bottom=0.26, wspace=0.22)
rng = np.random.default_rng(3)
for ax, (title, col, k, ylim, yt) in zip(axes, panels):
    for i, (dong, lab, *rngs) in enumerate(LCZ):
        lo, hi = rngs[k]; hi_draw = ylim[1] if hi is None else hi
        ax.add_patch(Rectangle((i - 0.38, lo), 0.76, hi_draw - lo, facecolor=SURF, edgecolor="none", zorder=0))
        if hi is None:
            ax.text(i, ylim[1] - 0.03 * (ylim[1] - ylim[0]), f"> {lo:g}", ha="center", va="top", fontsize=6.4, color=MUTED)
        q = d[d["권역"] == dong][col].dropna()
        x = i + rng.uniform(-0.2, 0.2, len(q))
        ax.scatter(x, q, s=9, color=INK, edgecolor="white", lw=0.3, zorder=3, alpha=0.85)
        med = float(q.median())
        ax.plot([i - 0.3, i + 0.3], [med, med], color=INK, lw=1.6, zorder=4)
        inside = (med >= lo) and (hi is None or med <= hi)
        ax.text(i + 0.34, med, f"{med:.1f}" if col == "H" else f"{med:.2f}", ha="left", va="center", fontsize=6.6, color=INK, zorder=5)
        print(f"{title[:4]} {dong:6s} n {len(q):2d} median {med:.3f} max {q.max():.2f}  class {lo}-{hi}  {'in' if inside else 'OUTSIDE'}")
    ax.set_xlim(-0.6, 4.6); ax.set_ylim(*ylim); ax.set_yticks(yt)
    ax.set_xticks(range(5)); ax.set_xticklabels([l for _, l, *_ in LCZ], fontsize=7, linespacing=1.15)
    ax.text(0.0, 1.03, title, transform=ax.transAxes, ha="left", va="bottom", fontsize=8.2, fontweight="bold", color=INK)
fig.legend(handles=[Rectangle((0, 0), 1, 1, facecolor=SURF, edgecolor="none", label="range defining the assigned LCZ class (Stewart and Oke, 2012)"),
                    Line2D([], [], marker="o", color="none", markerfacecolor=INK, markersize=3.6, markeredgecolor="none", label="measurement site"),
                    Line2D([], [], color=INK, lw=1.6, label="neighbourhood median")],
           loc="lower center", bbox_to_anchor=(0.5, 0.02), ncol=3, frameon=False, fontsize=7, handletextpad=0.5, columnspacing=1.8)
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_lcz_form.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_lcz_form.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
