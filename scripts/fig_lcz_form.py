#!/usr/bin/env python3
"""Administrative LCZ labels against the measured form at the sites — rows = neighbourhood, one panel per index.

Bar = the range that defines the assigned LCZ class (Stewart & Oke, 2012, Table 3); dot = median over the
neighbourhood's measurement sites, whisker = interquartile range; red = median outside the class range.
(a) building height, (b) height-to-width ratio, (c) sky view factor.
Sources: scs_master_80.csv — H = width_m × hw_ratio (building footprints + road centreline; 7 sites without a
centreline are absent from (a)(b)), SVF = 360° panorama, 2026-09-21 consistent set (one site without pose, n 79).
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
INK, MUTED, SURF, RED = "#1A1A1A", "#5E5E5E", "#E4E4E2", "#C0504D"
MM = 1 / 25.4

# Stewart & Oke (2012), Table 3: building height (m), aspect ratio, sky view factor
LCZ = [("부암제1동", "LCZ 1  compact high-rise", "Buam 1",     (25, None), (2.0, None), (0.2, 0.4)),
       ("보수동",   "LCZ 2  compact mid-rise",  "Bosu",       (10, 25),   (0.75, 2.0), (0.3, 0.6)),
       ("서제2동",  "LCZ 3  compact low-rise",  "Seo 2",      (3, 10),    (0.75, 1.5), (0.2, 0.6)),
       ("용호제1동", "LCZ 4  open high-rise",    "Yongho 1",   (25, None), (0.75, 1.25), (0.5, 0.7)),
       ("명장동",   "LCZ 5  open mid-rise",     "Myeongjang", (10, 25),   (0.3, 0.75), (0.5, 0.8))]

d = pd.read_csv(f"{DATA}/scs_master_80.csv", encoding="utf-8-sig")
d["H"] = d.width_m * d.hw_ratio
panels = [("(a)  Building height (m)", "H", 0, (0, 36), [0, 10, 20, 30], "{:.1f}"),
          ("(b)  Height-to-width ratio", "hw_ratio", 1, (0, 2.6), [0, 0.5, 1.0, 1.5, 2.0, 2.5], "{:.2f}"),
          ("(c)  Sky view factor", "SVF", 2, (0, 1.0), [0, 0.2, 0.4, 0.6, 0.8, 1.0], "{:.2f}")]

fig, axes = plt.subplots(1, 3, figsize=(190 * MM, 62 * MM), sharey=True)
fig.subplots_adjust(left=0.20, right=0.985, top=0.86, bottom=0.24, wspace=0.10)
ys = np.arange(5)[::-1]
for ax, (title, col, k, xlim, xt, fmt) in zip(axes, panels):
    for y, (dong, lab, en, *rngs) in zip(ys, LCZ):
        lo, hi = rngs[k]; hi_draw = xlim[1] if hi is None else hi
        ax.add_patch(Rectangle((lo, y - 0.30), hi_draw - lo, 0.60, facecolor=SURF, edgecolor="none", zorder=0))
        if hi is None:
            ax.text(xlim[1] - 0.01 * (xlim[1] - xlim[0]), y, f"> {lo:g}", ha="right", va="center", fontsize=6.4, color=MUTED)
        q = d[d["권역"] == dong][col].dropna()
        med, q1, q3 = float(q.median()), float(q.quantile(0.25)), float(q.quantile(0.75))
        inside = (med >= lo) and (hi is None or med <= hi)
        c = INK if inside else RED
        ax.plot([q1, q3], [y, y], color=c, lw=0.9, zorder=3)
        ax.scatter([med], [y], s=22, color=c, edgecolor="white", lw=0.5, zorder=4)
        ax.text(med, y + 0.34, fmt.format(med), ha="center", va="bottom", fontsize=6.8, color=c, zorder=5)
        print(f"{title[:4]} {dong:6s} n {len(q):2d} median {med:.3f} IQR {q1:.2f}-{q3:.2f}  class {lo}-{hi}  {'in' if inside else 'OUTSIDE'}")
    ax.set_xlim(*xlim); ax.set_xticks(xt); ax.set_ylim(-0.6, 4.75)
    ax.text(0.0, 1.03, title, transform=ax.transAxes, ha="left", va="bottom", fontsize=8.2, fontweight="bold", color=INK)
axes[0].set_yticks(ys)
axes[0].set_yticklabels([f"{lab}\n{en}" for _, lab, en, *_ in LCZ], fontsize=7, linespacing=1.15, ha="right")
axes[0].tick_params(axis="y", length=0)
fig.legend(handles=[Rectangle((0, 0), 1, 1, facecolor=SURF, edgecolor="none", label="range defining the assigned LCZ class (Stewart and Oke, 2012)"),
                    Line2D([], [], marker="o", color=INK, lw=0.9, markerfacecolor=INK, markersize=4.6, markeredgecolor="none", label="site median and interquartile range"),
                    Line2D([], [], marker="o", color=RED, lw=0.9, markerfacecolor=RED, markersize=4.6, markeredgecolor="none", label="median outside the class range")],
           loc="lower center", bbox_to_anchor=(0.5, 0.02), ncol=3, frameon=False, fontsize=7, handletextpad=0.5, columnspacing=1.6)
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_lcz_form.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_lcz_form.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
