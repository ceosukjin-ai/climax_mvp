#!/usr/bin/env python3
"""Urban form vs measured PET at 80 field sites — SCS figure (v7 photo-verified labels).

FOUR PANELS, and why (d) exists
-------------------------------
Panels (a)-(c) put the three canyon form indices against PET. Only sky view factor
survives among sunlit sites, so the text has to say why that relation is not the
radiative effect it looks like. That argument used to sit in the body text with a
number the reader could not find in the figure. Panel (d) puts it in the figure:
the same x-axis as (c), mean radiant temperature on y. Among sunlit sites the slope
is flat (rho = +0.02), so sky openness is not moving the radiant load once the beam
is present. Every number quoted in the Results is now readable off this figure.

WHY THIS FILE EXISTS
--------------------
The earlier version of this figure could not be reproduced: no script was in the
repository, and its sun/shade split (63/17) matched no label file. Reverse-fitting the
printed statistics showed the "sun" set was the v7 sun group (61) PLUS two sites that
every measured label — v6, v7, the 360-degree photo check and the optical sensor —
calls SHADE:

    20260826_부암제1동_05   SHADE-B (building shade)   PET 46.66   SVF 0.857
    20260823_명장제4동_01   SHADE-T (tree shade)       PET 40.42   SVF 0.772

Only the engine judged them sunlit. Any figure still showing "sun n = 63" is the old one.

DATA
----
  tier3_engine_output_80_v7_photo.csv   PET, 볕 = photo-verified sun/shade
  scs_master_80.csv                     SVF (fisheye, consistent view factors 2026-09-21; n = 79)
  scs_master_80.csv                     width_m, hw_ratio (site_form_80.csv 2026-09-26: building-register floors)

Sun = direct beam reaching the sensor, verified on the 360-degree panorama; cloud-diffuse
counts as no beam. Sun 62 / shade 18 (2026-09-25; sun label read from scs_master_80.csv). The five neighbourhoods are one per LCZ class
(LCZ 1-5, LCZ Generator; Demuzere et al. 2021), selected for density of older adults
living alone.

Street width is missing at 7 of 80 sites, so panels (a) and (b) carry n = 73 and the
sun-only subset is 55. Panel (c) is the full 80. Per-panel n is printed in each box.

STATISTICS
----------
  All sites   Spearman rho over every site with the variable present.
  Sunlit only Spearman rho over sunlit sites only.
  Sunlit, within LCZ
              x and y are ranked WITHIN each LCZ class and the ranks correlated
              (pooled within-group Spearman), removing any class-level offset.
              Only the p-value is printed; the rank-of-rank coefficient is not on the
              same scale as the two above it.

Trend lines are Theil-Sen slopes (robust, and the matching companion to a rank test),
solid where p < 0.05 and dashed otherwise.

ELSEVIER / SCS ARTWORK COMPLIANCE
---------------------------------
  * 190 mm double-column width, drawn 1:1 so printed sizes equal the sizes set here.
  * Fonts: Times New Roman where installed, else Liberation Serif (metric-compatible).
    Elsevier permits Arial/Helvetica, Courier, Symbol, Times/Times New Roman.
  * No lettering below 7 pt (Elsevier minimum for normal text).
  * Vector output: EPS (Elsevier's preferred vector format) and PDF. The PNG is a
    600 dpi preview only - do not submit it.
  * No caption text inside the artwork; the caption is supplied in the manuscript.
  * No grid lines.

Output: SCS_Fig_form_v7.eps / .pdf / .png
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from scipy import stats

BASE = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/climax_mvp/data"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/claude-0/figout"

SERIF = ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"]
mpl.rcParams.update({
    "font.family": "serif", "font.serif": SERIF,
    "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8.5, "axes.titlesize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "axes.linewidth": 0.7, "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "xtick.major.size": 2.6, "ytick.major.size": 2.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
INK, MUTED, RED = "#1A1A1A", "#4A4A4A", "#B3261E"
SUN, SHADE = "#C2410C", "#5B5BD6"
MM = 1 / 25.4
PET_EXTREME = 41.0
STAT_PT = 7.0                     # Elsevier minimum for normal text

v7 = pd.read_csv(f"{BASE}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")
wd = pd.read_csv(f"{BASE}/scs_master_80.csv", encoding="utf-8-sig")
v7.columns = [c.strip() for c in v7.columns]
wd.columns = [c.strip() for c in wd.columns]
d = v7.merge(wd[["측정ID", "width_m", "hw_ratio"]], on="측정ID", how="left")
# 2026-09-21: v7_photo 의 SVF 열은 9/15 폐기된 옛 파노라마 값이다. 정본(scs_master_80.csv,
# 일관 정의 뷰팩터, 158번 결측)으로 바꾼다.
# 2026-09-25: 볕/그늘도 정본(scs_master sun, 62/18 — 부암제1동_05 재판독)에서 읽는다.
_m = pd.read_csv(f"{BASE}/scs_master_80.csv", encoding="utf-8-sig")[["측정ID", "SVF", "sun"]]
d = d.drop(columns=["SVF"]).merge(_m, on="측정ID", how="left")
assert len(d) == 80, len(d)
d["sun"] = d["sun"].astype(int)
n_sun, n_shade = int(d["sun"].sum()), int((1 - d["sun"]).sum())
print(f"sites {len(d)}  sun {n_sun}  shade {n_shade}  font -> {SERIF[0]} if present")

def spear(s, col, ycol="PET"):
    s = s[[col, ycol]].dropna()
    r, p = stats.spearmanr(s[col], s[ycol])
    return r, p, len(s)

def within(s, col, ycol="PET"):
    s = s[[col, ycol, "권역"]].dropna()
    r, p = stats.spearmanr(s.groupby("권역")[col].rank(),
                           s.groupby("권역")[ycol].rank())
    return r, p, len(s)

# (tag, x column, x label, title, y column, y label, y limits, y ticks)
PET_LIM, PET_TK = (29.5, 55.5), [35, 40, 45, 50, 55]      # low band = statistics block
MRT_LIM, MRT_TK = (25.0, 80.0), [40, 50, 60, 70, 80]
PANELS = [
    ("a", "width_m",  "Street width (m)",               "Street width vs PET",
     "PET",  "Measured PET (\u00b0C)", PET_LIM, PET_TK),
    ("b", "hw_ratio", "Building height / street width", "Height-to-width ratio vs PET",
     "PET",  "Measured PET (\u00b0C)", PET_LIM, PET_TK),
    ("c", "SVF",      "Sky view factor (measured)",     "Sky view factor vs PET",
     "PET",  "Measured PET (\u00b0C)", PET_LIM, PET_TK),
    ("d", "SVF",      "Sky view factor (measured)",     "Sky view factor vs $T_{mrt}$",
     "Tmrt", "Mean radiant temperature (\u00b0C)", MRT_LIM, MRT_TK),
]

fig, axgrid = plt.subplots(2, 2, figsize=(190 * MM, 135 * MM))
axes = axgrid.ravel()

for ax, (tag, col, xlab, title, ycol, ylab, ylim, ytk) in zip(axes, PANELS):
    lo, hi = ylim
    if ycol == "PET":
        ax.axhline(PET_EXTREME, color=RED, lw=0.9, ls=(0, (4, 2.5)), zorder=2)
    for is_sun, colr, lab in ((0, SHADE, "shade"), (1, SUN, "sunlit")):
        g = d[d["sun"] == is_sun][[col, ycol]].dropna()
        # EPS 는 투명도를 지원하지 않는다. 셋(eps·pdf·png)이 같은 그림이어야 하므로
        # alpha 를 쓰지 않고, 겹친 점은 흰 테두리로 분리한다.
        ax.scatter(g[col], g[ycol], s=17, c=colr,
                   linewidths=0.45, edgecolors="white", zorder=4)
        if len(g) >= 8:
            _, p = stats.spearmanr(g[col], g[ycol])
            b, a0, *_ = stats.theilslopes(g[ycol], g[col], 0.95)
            xs = np.linspace(g[col].min(), g[col].max(), 50)
            ax.plot(xs, a0 + b * xs, color=colr, lw=1.2, zorder=3,
                    ls="-" if p < 0.05 else (0, (4, 2.2)))

    ra, pa, na = spear(d, col, ycol)
    rs, ps, ns = spear(d[d["sun"] == 1], col, ycol)
    _, pw, _ = within(d[d["sun"] == 1], col, ycol)
    def pf(p):                       # 0.002 가 "= 0.00" 으로 찍히지 않게
        if p < 0.001: return "$<$ 0.001"
        if p < 0.01:  return f"= {p:.3f}"
        return f"= {p:.2f}"
    ax.text(0.035, 0.028,
            f"all sites (n = {na})   $\\rho$ = {ra:+.2f}, $p$ {pf(pa)}\n"
            f"sunlit only (n = {ns})   $\\rho$ = {rs:+.2f}, $p$ {pf(ps)}\n"
            f"sunlit, within LCZ   $p$ {pf(pw)}",
            transform=ax.transAxes, va="bottom", ha="left",
            fontsize=STAT_PT, color=MUTED, linespacing=1.55, zorder=6)

    ax.set_xlabel(xlab)
    ax.set_ylabel(ylab)
    ax.set_ylim(lo, hi)
    ax.set_yticks(ytk)
    ax.set_title(f"({tag})  {title}", loc="left", fontweight="bold", pad=6)
    print(f"({tag}) {col:9} all {ra:+.3f}/{pa:.4f} n{na} | sun {rs:+.3f}/{ps:.4f} n{ns} "
          f"| within p {pw:.4f}")

axes[0].set_ylabel("Measured PET (°C)")
axes[0].text(0.030, (PET_EXTREME - lo) / (hi - lo) + 0.015,
             "extreme heat stress, 41 °C", transform=axes[0].transAxes,
             color=RED, fontsize=STAT_PT, va="bottom", ha="left", zorder=6)

handles = [Line2D([], [], marker="o", ls="", ms=4.2, mfc=SHADE, mec="white", mew=0.4,
                  label=f"shade (n = {n_shade})"),
           Line2D([], [], marker="o", ls="", ms=4.2, mfc=SUN, mec="white", mew=0.4,
                  label=f"sunlit (n = {n_sun})"),
           Line2D([], [], color=MUTED, lw=1.2, ls="-", label="Spearman $p$ $<$ 0.05"),
           Line2D([], [], color=MUTED, lw=1.2, ls=(0, (4, 2.2)), label="not significant")]
fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 1.035), ncol=4,
           frameon=False, handlelength=1.6, columnspacing=2.2)

fig.tight_layout(w_pad=2.4, h_pad=2.6)
for ext in ("eps", "pdf", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_form_v7.{ext}", bbox_inches="tight", pad_inches=0.03)
print("saved eps / pdf / png ->", OUT)
