#!/usr/bin/env python3
"""Sun exposure sets pedestrian heat load — a restrained two-panel figure.

(a) All 80 measured sites, split by whether the observer stood in sun or shade.
    One reference line (PET 41 °C, extreme heat stress). Two numbers.
(b) The same contrast inside each neighbourhood, where day, hour and weather are
    held constant by construction.

Design: direct labels instead of legend boxes, no gridlines, one accent colour
pair, axis furniture trimmed to the data range.
"""
from __future__ import annotations
import csv
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "font.size": 8, "axes.labelsize": 8.5,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 3.0, "ytick.major.size": 0,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
SUN, SHADE = "#D94801", "#7A5AA8"
INK, MUTED, FAINT, RED = "#1A1A1A", "#6B6B6B", "#D9D9D9", "#B3261E"
MM = 1 / 25.4

R = list(csv.DictReader(open(
    "/mnt/user-data/uploads/climax_mvp/data/tier3_engine_output_80_v6.csv",
    encoding="utf-8-sig")))
KO = ["서제2동", "명장동", "용호제1동", "보수동", "부암제1동"]
EN = ["Seo 2", "Myeong-jang", "Yongho 1", "Bosu", "Buam 1"]
g = np.array([r["권역"] for r in R])
pet = np.array([float(r["PET"]) for r in R])
sun = np.array([r["볕"].strip() == "1" for r in R])

fig, axes = plt.subplots(1, 2, figsize=(180 * MM, 76 * MM),
                         gridspec_kw=dict(width_ratios=[1.18, 1.0]))
fig.subplots_adjust(left=0.055, right=0.985, bottom=0.185, top=0.845, wspace=0.135)


def tag(ax, s, x):
    ax.text(x, 1.16, s, transform=ax.transAxes, fontsize=9, fontweight="bold",
            va="top", ha="left", color=INK)


XL, XR = 32.4, 56.5

# ============================================================ (a) pooled contrast
ax = axes[0]
rng = np.random.default_rng(7)
rows = [("Sunlit", pet[sun], SUN, 1.0), ("Shaded", pet[~sun], SHADE, 0.0)]
for lab, v, colr, y0 in rows:
    ax.scatter(v, y0 + rng.uniform(-0.16, 0.16, len(v)), s=17, facecolor=colr,
               edgecolor="white", lw=0.4, alpha=0.9, zorder=4)
    ax.plot([v.mean(), v.mean()], [y0 - 0.30, y0 + 0.30], color=INK, lw=1.6, zorder=6)
    ax.text(v.mean(), y0 + 0.40, f"{v.mean():.1f}", ha="center", va="bottom",
            fontsize=8, color=INK, fontweight="bold")
    ax.text(XL + 0.2, y0, f"{lab}\nn = {len(v)}", ha="left", va="center",
            fontsize=8.5, color=colr, linespacing=1.35, fontweight="bold")

ax.annotate("", xy=(pet[sun].mean(), 1.62), xytext=(pet[~sun].mean(), 1.62),
            arrowprops=dict(arrowstyle="<|-|>", color=INK, lw=0.9,
                            mutation_scale=8, shrinkA=0, shrinkB=0), zorder=6)
ax.text((pet[sun].mean() + pet[~sun].mean()) / 2, 1.72,
        f"{pet[sun].mean() - pet[~sun].mean():+.1f} °C", ha="center", va="bottom",
        fontsize=8.5, color=INK, fontweight="bold")

ax.axvline(41, color=RED, lw=0.8, ls=(0, (4, 2)), zorder=2)
ax.text(41 - 0.4, 2.02, "extreme heat stress\nPET 41 °C", color=RED,
        fontsize=7.4, ha="right", va="top", linespacing=1.3)
ax.text(XR - 0.3, -0.72,
        f"Above the threshold:  {100*(pet[sun] >= 41).mean():.0f} % of sunlit sites,"
        f"   {100*(pet[~sun] >= 41).mean():.0f} % of shaded sites",
        color=INK, fontsize=7.8, ha="right", va="bottom")
ax.set_yticks([]); ax.set_ylim(-1.05, 2.25)
ax.set_xlim(XL, XR); ax.set_xticks([35, 40, 45, 50, 55])
ax.spines["bottom"].set_bounds(XL, XR)
ax.set_xlabel("Measured PET (\u00b0C)")
ax.set_ylabel("Sun exposure at the site", labelpad=8)
ax.set_title("Where the pedestrian stood, all 80 sites", loc="left", pad=10,
             fontsize=8.5, color=INK)
tag(ax, "a", -0.045)

# ================================================== (b) same contrast, per place
ax = axes[1]
order = list(range(len(KO)))
y = np.arange(len(KO))[::-1]
for i, k in zip(order, y):
    m = g == KO[i]
    s_, h_ = pet[m & sun], pet[m & ~sun]
    ax.plot([h_.mean(), s_.mean()], [k, k], color="#C4C9CE", lw=2.2, zorder=3,
            solid_capstyle="round")
    ax.scatter(h_.mean(), k, s=34, facecolor=SHADE, edgecolor="white", lw=0.7,
               zorder=5)
    ax.scatter(s_.mean(), k, s=34, facecolor=SUN, edgecolor="white", lw=0.7,
               zorder=5)
    ax.text(s_.mean() + 0.45, k, f"+{s_.mean() - h_.mean():.1f}", fontsize=7.6,
            va="center", ha="left", color=INK)
    ax.text(XL + 0.2, k, EN[i], fontsize=8, va="center", ha="left", color=INK)
    ax.text(XL + 0.2, k - 0.34, f"{len(h_)} shaded / {len(s_)} sunlit", fontsize=6.8,
            va="center", ha="left", color=MUTED)

ax.axvline(41, color=RED, lw=0.8, ls=(0, (4, 2)), zorder=2)
ax.set_yticks([]); ax.set_ylim(-0.85, len(KO) - 0.30)
ax.set_xlim(XL, XR); ax.set_xticks([35, 40, 45, 50, 55])
ax.spines["bottom"].set_bounds(XL, XR)
ax.set_xlabel("Mean measured PET (\u00b0C)")
ax.set_ylabel("Neighbourhood", labelpad=8)
ax.set_title("Every neighbourhood, same day and hour", loc="left", pad=10,
             fontsize=8.5, color=INK)
ax.text(XR - 0.3, len(KO) - 0.45,
        "shade → sun", ha="right", va="center", fontsize=7.6, color=MUTED)
tag(ax, "b", -0.045)

fig.savefig("/tmp/claude-0/SCS_Fig_clean.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("/tmp/claude-0/SCS_Fig_clean.png", bbox_inches="tight", pad_inches=0.02)
print("saved")
