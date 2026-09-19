#!/usr/bin/env python3
"""Urban form vs measured PET at 80 field sites — SCS figure (2026-09-19, v7 labels).

WHY THIS FILE EXISTS
--------------------
The previous version of this figure could not be reproduced: no script was in the
repository, and its sun/shade split (63/17) matched no label file. Reverse-fitting the
printed statistics showed the "sun" set was the v7 sun group (61) PLUS two sites that
every measured label — v6, v7, the 360-degree photo check and the optical sensor —
calls SHADE:

    20260826_부암제1동_05   SHADE-B (building shade)   PET 46.66   SVF 0.857
    20260823_명장제4동_01   SHADE-T (tree shade)       PET 40.42   SVF 0.772

Only the engine judged them sunlit. Including them changed the sun-only correlations
(e.g. H/W rho -0.18 instead of -0.14) and the legend counts. This script rebuilds the
figure from the photo-verified v7 labels and writes every number it prints.

DATA
----
  tier3_engine_output_80_v7_photo.csv   PET, SVF (fisheye), 볕 = photo-verified sun/shade
  tier3_width_80.csv                    width_m, hw_ratio (geometric, from building rings)

Sun = direct beam reaching the sensor, verified on the 360-degree panorama; cloud-diffuse
counts as no beam. Sun 61 / shade 19.

Street width is missing at 7 of 80 sites (no road centreline within the search radius),
so panels (a) and (b) carry n = 73 and the sun-only subset is 55, not 61. Panel (c) is
the full 80. The per-panel n is printed in each statistics box — do not quote the legend
counts as the panel n.

STATISTICS
----------
  All sites            Spearman rho over every site with the variable present.
  Sun only             Spearman rho over sunlit sites only. Shade sites sit in a narrow
                       PET band, so pooling them inflates any form-PET correlation.
  Sun, within-neighbourhood
                       The five neighbourhoods differ in both form and background PET, so
                       a pooled correlation can be driven entirely by between-neighbourhood
                       contrast. x and y are ranked WITHIN each neighbourhood and the ranks
                       are then correlated (pooled within-group Spearman). This removes any
                       neighbourhood-level offset and keeps the test non-parametric.
                       Only the p-value is printed: the rank-of-rank coefficient is not on
                       the same scale as the two above it.

Trend lines are drawn solid when p < 0.05 and dashed otherwise, so a dashed line is a
statement that the slope is not distinguishable from zero.

Output: SCS_Fig_form_v7.pdf (vector, for submission) and .png (600 dpi).
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

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "font.size": 7.5, "axes.labelsize": 8, "axes.titlesize": 8,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.7, "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "xtick.major.size": 2.6, "ytick.major.size": 2.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
INK, MUTED, GRID, RED = "#1A1A1A", "#5A5A5A", "#DCDCDC", "#B3261E"
SUN, SHADE = "#C2410C", "#5B5BD6"
MM = 1 / 25.4
PET_EXTREME = 41.0

# ── data ──────────────────────────────────────────────────────────────────────
v7 = pd.read_csv(f"{BASE}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")
wd = pd.read_csv(f"{BASE}/tier3_width_80.csv", encoding="utf-8-sig")
v7.columns = [c.strip() for c in v7.columns]
wd.columns = [c.strip() for c in wd.columns]
d = v7.merge(wd[["측정ID", "width_m", "hw_ratio"]], on="측정ID", how="left")
assert len(d) == 80, len(d)
d["sun"] = d["볕"].astype(int)
n_sun, n_shade = int(d["sun"].sum()), int((1 - d["sun"]).sum())
print(f"sites {len(d)}  sun {n_sun}  shade {n_shade}")

def spear(s, col):
    s = s[[col, "PET"]].dropna()
    r, p = stats.spearmanr(s[col], s["PET"])
    return r, p, len(s)

def within(s, col):
    """Rank x and y inside each neighbourhood, then correlate the ranks."""
    s = s[[col, "PET", "권역"]].dropna()
    x = s.groupby("권역")[col].rank()
    y = s.groupby("권역")["PET"].rank()
    r, p = stats.spearmanr(x, y)
    return r, p, len(s)

PANELS = [
    ("a", "width_m",  "Street width $W$ (m)",            "Street width"),
    ("b", "hw_ratio", "Building height / street width",  "Aspect ratio $H/W$"),
    ("c", "SVF",      "Sky view factor (measured)",      "Sky view factor"),
]

fig, axes = plt.subplots(1, 3, figsize=(190 * MM, 68 * MM))
lo, hi = 30.0, 55.5          # 아래 빈 띠는 통계 상자 자리 — 점을 가리지 않기 위해

for ax, (tag, col, xlab, title) in zip(axes, PANELS):
    ax.set_axisbelow(True)
    ax.grid(True, color=GRID, lw=0.5)
    ax.axhline(PET_EXTREME, color=RED, lw=0.9, ls=(0, (4, 2.5)), zorder=2)

    for is_sun, colr, lab in ((0, SHADE, "Shade"), (1, SUN, "Sun")):
        g = d[d["sun"] == is_sun][[col, "PET"]].dropna()
        ax.scatter(g[col], g["PET"], s=17, c=colr, alpha=0.85,
                   linewidths=0.4, edgecolors="white", zorder=4,
                   label=f"{lab} (n = {len(g)})")
        if len(g) >= 8:                       # trend: solid if significant
            r, p = stats.spearmanr(g[col], g["PET"])
            # Theil-Sen, not least squares: a robust slope belongs with a rank test,
            # and three sites sit far out on W and H/W.
            b, a0, _, _ = stats.theilslopes(g["PET"], g[col], 0.95)
            xs = np.linspace(g[col].min(), g[col].max(), 50)
            ax.plot(xs, a0 + b * xs, color=colr, lw=1.2, zorder=3,
                    ls="-" if p < 0.05 else (0, (4, 2.2)), alpha=0.9)

    ra, pa, na = spear(d, col)
    rs, ps, ns = spear(d[d["sun"] == 1], col)
    _, pw, nw = within(d[d["sun"] == 1], col)
    pf = lambda p: "< 0.001" if p < 0.001 else f"= {p:.2f}"
    txt = (f"All sites (n = {na})   $\\rho$ = {ra:+.2f}, $p$ {pf(pa)}\n"
           f"Sun only (n = {ns})   $\\rho$ = {rs:+.2f}, $p$ {pf(ps)}\n"
           f"Sun, within neighbourhood   $p$ {pf(pw)}")
    ax.text(0.035, 0.022, txt, transform=ax.transAxes, va="bottom", ha="left",
            fontsize=6.4, color=MUTED, linespacing=1.6, zorder=6,
            bbox=dict(facecolor="white", alpha=0.0, edgecolor="none"))

    ax.set_xlabel(xlab)
    ax.set_ylim(lo, hi)
    ax.set_yticks([35, 40, 45, 50, 55])
    ax.set_title(f"({tag})  {title}", loc="left", fontweight="bold", pad=6)
    print(f"({tag}) {col:9} all {ra:+.3f}/{pa:.4f} n{na} | sun {rs:+.3f}/{ps:.4f} n{ns} "
          f"| within p {pw:.4f} n{nw}")

axes[0].set_ylabel("Measured PET (°C)")
axes[0].text(0.035, (PET_EXTREME - lo) / (hi - lo) + 0.018, "Extreme heat stress, 41 °C",
             transform=axes[0].transAxes, color=RED, fontsize=6.4, va="bottom", ha="left")

handles = [Line2D([], [], marker="o", ls="", ms=4.2, mfc=SHADE, mec="white", mew=0.4,
                  label=f"Shade (n = {n_shade})"),
           Line2D([], [], marker="o", ls="", ms=4.2, mfc=SUN, mec="white", mew=0.4,
                  label=f"Sun (n = {n_sun})"),
           Line2D([], [], color=MUTED, lw=1.2, ls="-", label="Spearman $p$ < 0.05"),
           Line2D([], [], color=MUTED, lw=1.2, ls=(0, (4, 2.2)), label="not significant")]
fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 1.055), ncol=4,
           frameon=False, handlelength=1.6, columnspacing=2.2, fontsize=7.2)

fig.text(0.5, -0.085,
         "Sun/shade is the direct beam at the sensor, verified on the 360° panorama "
         "(cloud-diffuse counted as no beam). Street width is unavailable at 7 sites, so "
         "panels (a) and (b) carry n = 73.\nThe within-neighbourhood test ranks both "
         "variables inside each of the five neighbourhoods before correlating, removing "
         "any neighbourhood-level offset in form and in background PET.",
         ha="center", va="top", fontsize=6.4, color=MUTED, linespacing=1.6)

fig.tight_layout(w_pad=2.2)
fig.savefig(f"{OUT}/SCS_Fig_form_v7.pdf", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_form_v7.png", bbox_inches="tight", pad_inches=0.03)
print("saved", OUT)
