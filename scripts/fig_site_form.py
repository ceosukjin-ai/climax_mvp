#!/usr/bin/env python3
"""The five neighbourhoods: measured form at the sites against the LCZ label — one row per neighbourhood.

(a) street width class of the sites (share of sites)
(b) building height: all buildings within 60 m of the sites (light band, 5–95 % and full range) — the fabric that
    sets the dong's LCZ label; grey bar = range defining the assigned LCZ class (Stewart & Oke, 2012);
    dot + line = median and IQR of the canyon height at the sites (building register floors); red = outside the class
(c) sky view factor at the sites (360° panorama) against the class range
(d) facade material of the building nearest each site (share of sites)

Replaces SCS_Fig_form_bars (09-20; site heights were default 5.6 m) and SCS_Fig_lcz_form (09-26).
Inputs: scs_master_80.csv (width_m, hw_ratio, H_m, SVF, 권역), site_form_80.csv via master;
        NEIGH_H and MAT from scripts/lcz_form_profile.py server run 2026-09-20 (all buildings within 60 m; nearest-building material).
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
INK, MUTED, SURF, RED = "#1A1A1A", "#5E5E5E", "#CFCFCD", "#C0504D"
FAB1, FAB2, FAB3 = "#EDEDEB", "#E4E4E2", "#CFCFCD"
LCZC = "#2E75B6"                                  # LCZ class range bracket: blue                 # all buildings: full range / 5–95 %
W_GREY = ["#3F5F7F", "#7C9CBF", "#C6D4E2", "#EFEFEF"]   # alley → open (blue ramp, as in the 09-20 figure)
M_GREY = {"concrete": "#9AA5AE", "brick": "#B5714F"}
MM = 1 / 25.4

LCZ = [("부암제1동", "LCZ 1  compact high-rise", "Buam 1",     (25, None), (0.2, 0.4)),
       ("보수동",   "LCZ 2  compact mid-rise",  "Bosu",       (10, 25),   (0.3, 0.6)),
       ("서제2동",  "LCZ 3  compact low-rise",  "Seo 2",      (3, 10),    (0.2, 0.6)),
       ("용호제1동", "LCZ 4  open high-rise",    "Yongho 1",   (25, None), (0.5, 0.7)),
       ("명장동",   "LCZ 5  open mid-rise",     "Myeongjang", (10, 25),   (0.5, 0.8))]
# all buildings within 60 m of the sites (n, min, 5 %, median, 95 %, max) — lcz_form_profile.py, 2026-09-20
NEIGH_H = {"부암제1동": (5011, 3.0, 3.9, 6.9, 11.9, 88.4), "보수동": (5770, 1.5, 3.9, 8.6, 16.3, 50.6),
           "서제2동": (13475, 2.7, 3.9, 7.4, 11.9, 54.0), "용호제1동": (4402, 3.3, 3.9, 6.9, 13.7, 67.9),
           "명장동": (1810, 3.3, 3.9, 6.9, 18.2, 91.4)}
MAT = {"부암제1동": {"concrete": 11, "brick": 4}, "보수동": {"concrete": 16, "brick": 0}, "서제2동": {"concrete": 1, "brick": 16},
       "용호제1동": {"concrete": 12, "brick": 3}, "명장동": {"concrete": 15, "brick": 2}}
WNAME = ["alley < 6 m", "street 6–12 m", "street > 12 m", "open, no street"]

d = pd.read_csv(f"{DATA}/scs_master_80.csv", encoding="utf-8-sig")
def wcls(w):
    if pd.isna(w): return 3
    return 0 if w < 6 else (1 if w < 12 else 2)
d["wc"] = d.width_m.map(wcls)

fig, axes = plt.subplots(1, 4, figsize=(190 * MM, 78 * MM), sharey=True, gridspec_kw={"width_ratios": [1.0, 1.25, 1.1, 1.0]})
fig.subplots_adjust(left=0.165, right=0.99, top=0.87, bottom=0.33, wspace=0.10)
ys = np.arange(5)[::-1] * 1.45
ax_w, ax_h, ax_s, ax_m = axes

for y, (dong, lab, en, hr, sr) in zip(ys, LCZ):
    q = d[d["권역"] == dong]
    # (a) width class
    cnt = [int((q.wc == k).sum()) for k in range(4)]; left = 0
    for k, c in enumerate(cnt):
        if c == 0: continue
        pct = 100 * c / len(q)
        ax_w.barh(y, pct, left=left, height=0.6, color=W_GREY[k], edgecolor="white", lw=0.4)
        ax_w.text(left + pct / 2, y, str(c), ha="center", va="center", fontsize=6.6, color="white" if k < 2 else INK)
        left += pct
    # (b) height (log axis)
    n, mn, q05, med, q95, mx = NEIGH_H[dong]
    ax_h.add_patch(Rectangle((mn, y - 0.30), mx - mn, 0.60, facecolor=FAB1, edgecolor="none", zorder=0))
    ax_h.add_patch(Rectangle((q05, y - 0.30), q95 - q05, 0.60, facecolor=FAB3, edgecolor="none", zorder=1))
    ax_h.text(mx * 1.10, y - 0.02, f"{mx:.0f}", ha="left", va="center", fontsize=6.2, color=MUTED, zorder=7, bbox=dict(facecolor="white", edgecolor="none", pad=0.6))
    lo, hi = hr; hi_d = 200 if hi is None else hi
    yb = y + 0.42
    ax_h.plot([lo, hi_d], [yb, yb], color=LCZC, lw=1.1, zorder=6, solid_capstyle="butt", clip_on=True)
    ax_h.plot([lo, lo], [yb - 0.09, yb + 0.09], color=LCZC, lw=1.1, zorder=6)
    if hi is None:
        ax_h.annotate("", xy=(160, yb), xytext=(120, yb), arrowprops=dict(arrowstyle="-|>", color=LCZC, lw=1.1, mutation_scale=6), zorder=6)
    else:
        ax_h.plot([hi, hi], [yb - 0.09, yb + 0.09], color=LCZC, lw=1.1, zorder=6)
    H = q.H_m.dropna(); hm, h1, h3 = H.median(), H.quantile(.25), H.quantile(.75)
    inside = hm >= lo and (hi is None or hm <= hi)
    ax_h.plot([h1, h3], [y, y], color=RED, lw=3.2, zorder=4, solid_capstyle="butt")
    ax_h.scatter([hm], [y], s=30, facecolor="white", edgecolor=RED, lw=1.2, zorder=5)
    ax_h.text(hm, y - 0.36, f"{hm:.1f}", ha="center", va="top", fontsize=6.8, color=RED, fontweight="bold", zorder=6)
    # (c) SVF
    lo, hi = sr
    S = q.SVF.dropna(); sm, s1, s3 = S.median(), S.quantile(.25), S.quantile(.75)
    s_in = lo <= sm <= hi
    yb = y + 0.42
    ax_s.plot([lo, hi], [yb, yb], color=LCZC, lw=1.1, zorder=6, solid_capstyle="butt")
    for xx in (lo, hi): ax_s.plot([xx, xx], [yb - 0.09, yb + 0.09], color=LCZC, lw=1.1, zorder=6)
    ax_s.plot([s1, s3], [y, y], color=RED, lw=3.2, zorder=3, solid_capstyle="butt")
    ax_s.scatter([sm], [y], s=30, facecolor="white", edgecolor=RED, lw=1.2, zorder=4)
    ax_s.text(sm, y - 0.36, f"{sm:.2f}", ha="center", va="top", fontsize=6.8, color=RED, fontweight="bold", zorder=5)
    # (d) material
    m = MAT[dong]; tot = sum(m.values()); left = 0
    for k in ("concrete", "brick"):
        if m[k] == 0: continue
        pct = 100 * m[k] / tot
        ax_m.barh(y, pct, left=left, height=0.6, color=M_GREY[k], edgecolor="white", lw=0.4)
        ax_m.text(left + pct / 2, y, str(m[k]), ha="center", va="center", fontsize=6.6, color="white")
        left += pct
    print(f"{dong:6s} width {cnt}  H {hm:.1f} ({h1:.1f}-{h3:.1f}) {'in' if inside else 'OUT'}  SVF {sm:.2f}  mat {m}")

ax_w.set_xlim(0, 100); ax_w.set_xticks([0, 25, 50, 75, 100]); ax_w.set_xlabel("share of sites (%)")
ax_h.set_xscale("log"); ax_h.set_xlim(2.5, 170); ax_h.set_xticks([3, 5, 10, 20, 50, 100]); ax_h.set_xticklabels(["3", "5", "10", "20", "50", "100"]); ax_h.set_xlabel("height (m, log)")
ax_h.xaxis.set_minor_locator(mpl.ticker.NullLocator())
ax_s.set_xlim(0, 1.0); ax_s.set_xticks([0, 0.2, 0.4, 0.6, 0.8, 1.0]); ax_s.set_xlabel("sky view factor")
ax_m.set_xlim(0, 100); ax_m.set_xticks([0, 25, 50, 75, 100]); ax_m.set_xlabel("share of sites (%)")
for ax, t in zip(axes, ["(a)  Street width", "(b)  Building height", "(c)  Sky view factor", "(d)  Facade material"]):
    ax.set_ylim(-0.85, 4 * 1.45 + 0.75)
    ax.text(0.0, 1.03, t, transform=ax.transAxes, ha="left", va="bottom", fontsize=8.2, fontweight="bold", color=INK)
ax_w.set_yticks(ys); ax_w.set_yticklabels([f"{lab}\n{en}" for _, lab, en, *_ in LCZ], fontsize=7, linespacing=1.15)
for ax in axes: ax.tick_params(axis="y", length=0)

h_w = [Rectangle((0, 0), 1, 1, facecolor=W_GREY[k], edgecolor="none", label=WNAME[k]) for k in range(4)]
h_h = [Line2D([], [], marker="o", color=RED, lw=3.2, markerfacecolor="white", markersize=5, markeredgecolor=RED, markeredgewidth=1.2, label="at the sites: median and interquartile range (b: canyon height; c: sky view factor)"),
       Line2D([], [], color=LCZC, lw=1.1, marker="|", markersize=5, markeredgewidth=1.1, label="range defining the assigned LCZ class (Stewart and Oke, 2012); arrow: open-ended"),
       Rectangle((0, 0), 1, 1, facecolor=FAB3, edgecolor="none", label="all buildings within 60 m of the sites, 5–95 %"),
       Rectangle((0, 0), 1, 1, facecolor=FAB1, edgecolor="none", label="all buildings, full range")]
h_m = [Rectangle((0, 0), 1, 1, facecolor=M_GREY[k], edgecolor="none", label=k) for k in ("concrete", "brick")]
fig.legend(handles=h_w, loc="lower left", bbox_to_anchor=(0.165, 0.0), ncol=2, frameon=False, fontsize=6.6, handletextpad=0.5, columnspacing=1.0, handlelength=1.4)
fig.legend(handles=h_h, loc="lower left", bbox_to_anchor=(0.40, 0.0), ncol=1, frameon=False, fontsize=6.6, handletextpad=0.5, labelspacing=0.25, handlelength=1.4)
fig.legend(handles=h_m, loc="lower left", bbox_to_anchor=(0.84, 0.0), ncol=1, frameon=False, fontsize=6.6, handletextpad=0.5, labelspacing=0.25, handlelength=1.4)
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_site_form.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_site_form.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
