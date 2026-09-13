#!/usr/bin/env python3
"""Neighbourhood comparison on a common basis — raw spread vs. condition-adjusted effect.

Why this figure exists: each neighbourhood was measured on one day in one time window,
so the raw panel cannot be read as a ranking. Air temperature, surface temperature and
sun exposure CAN be controlled for (neighbourhood dummies explain 55 % / 30 % / -- of
their variance). Time of day and solar elevation CANNOT (R^2 = 0.90 on both): the design
confounds them with neighbourhood, so that specification is not identifiable and is
reported as such rather than plotted.
"""
from __future__ import annotations
import csv, datetime as dt
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "font.size": 7.5, "axes.labelsize": 8, "axes.titlesize": 8.5,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
    "axes.linewidth": 0.7, "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "xtick.major.size": 2.6, "ytick.major.size": 2.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
BLUE, ORANGE, PURPLE = "#1F6FB2", "#D94801", "#7A5AA8"
INK, MUTED, GRID, RED = "#1A1A1A", "#5A5A5A", "#D8D8D8", "#B3261E"
MM = 1 / 25.4

R = list(csv.DictReader(open(
    "/mnt/user-data/uploads/climax_mvp/data/tier3_engine_output_80_v6.csv",
    encoding="utf-8-sig")))
KO = ["서제2동", "명장동", "용호제1동", "보수동", "부암제1동"]
EN = ["Seo 2", "Myeong-jang", "Yongho 1", "Bosu", "Buam 1"]
DATE = ["08-20", "08-23", "08-25", "08-26", "08-26"]
g = np.array([r["권역"] for r in R])


def c(k):
    return np.array([float(r[k]) for r in R])


pet, Ta, Ts = c("PET"), c("Ta"), c("Ts")
sun = np.array([1.0 if r["볕"].strip() == "1" else 0.0 for r in R])
D = np.stack([(g == k).astype(float) for k in KO], 1)
D = D - D.mean(0)

SETS = [("Air temperature", [Ta], BLUE),
        ("Air temp. + surface temp. + sun exposure", [Ta, Ts, sun], ORANGE)]


def fit(cov, idx):
    X = np.column_stack([np.ones(len(idx))] + [v[idx] for v in cov]
                        + [D[idx, j] for j in range(len(KO))])
    b, *_ = np.linalg.lstsq(X, pet[idx], rcond=None)
    e = b[-len(KO):]
    return e - e.mean()


rng = np.random.default_rng(1)
EST = {}
for lab, cov, _ in SETS:
    pt = fit(cov, np.arange(len(pet)))
    bs = np.array([fit(cov, rng.integers(0, len(pet), len(pet))) for _ in range(3000)])
    lo, hi = np.percentile(bs, [2.5, 97.5], axis=0)
    EST[lab] = (pt, lo, hi)

fig, axes = plt.subplots(1, 2, figsize=(178 * MM, 72 * MM),
                         gridspec_kw=dict(width_ratios=[1.05, 1.0]))
fig.subplots_adjust(left=0.075, right=0.995, bottom=0.30, top=0.875, wspace=0.30)


def tag(ax, s, x=-0.17):
    ax.text(x, 1.10, s, transform=ax.transAxes, fontsize=9.5, fontweight="bold",
            va="top", ha="left", color=INK)


# ---- (a) raw spread, as measured
ax = axes[0]
ax.grid(True, axis="y", color=GRID, lw=0.5, zorder=0); ax.set_axisbelow(True)
groups = [pet[g == k] for k in KO]
pos = np.arange(len(KO))
ax.boxplot(groups, positions=pos, widths=0.54, showfliers=False, patch_artist=True,
           zorder=3, medianprops=dict(color=INK, lw=1.1),
           boxprops=dict(facecolor="#EAF1F8", edgecolor=MUTED, lw=0.7),
           whiskerprops=dict(color=MUTED, lw=0.7), capprops=dict(color=MUTED, lw=0.7))
r2 = np.random.default_rng(3)
for i, v in enumerate(groups):
    ax.scatter(pos[i] + r2.uniform(-0.15, 0.15, len(v)), v, s=11, color=BLUE,
               alpha=0.85, zorder=4, lw=0)
ax.axhline(41, color=RED, lw=0.8, ls="--", zorder=2)
ax.text(len(KO) - 0.42, 41.4, "extreme ≥ 41 °C", color=RED, fontsize=6.8,
        va="bottom", ha="right")
ax.set_xticks(pos)
ax.set_xticklabels([f"{e}\n{d}" for e, d in zip(EN, DATE)], fontsize=6.6, linespacing=1.35)
ax.set_xlim(-0.62, len(KO) - 0.38); ax.set_ylim(33, 57)
ax.set_ylabel("Measured PET (°C)")
ax.set_title("As measured — conditions differ", pad=7, loc="left", color=INK)
ax.text(0.0, -0.235,
        "Each neighbourhood was measured on one day in one time window, so the\n"
        "columns are not comparable as they stand. Buam 1 was measured at\n"
        "14:10–15:06 (air 37.4 °C, solar elevation 51°); the rest 11–14 h.",
        transform=ax.transAxes, ha="left", va="top", fontsize=6.7, color=MUTED,
        linespacing=1.45)
tag(ax, "a")

# ---- (b) condition-adjusted effect
ax = axes[1]
ax.grid(True, axis="x", color=GRID, lw=0.5, zorder=0); ax.set_axisbelow(True)
ax.axvline(0, color=MUTED, lw=0.9, zorder=2)
off = 0.17
ypos = np.arange(len(KO))[::-1]
for si, (lab, cov, colr) in enumerate(SETS):
    pt, lo, hi = EST[lab]
    yy = ypos + (off if si == 0 else -off)
    ax.hlines(yy, lo, hi, color=colr, lw=1.5, zorder=4)
    ax.scatter(pt, yy, s=26, marker="o" if si == 0 else "D", facecolor="white",
               edgecolor=colr, lw=1.3, zorder=5)
    for j in range(len(KO)):
        if lo[j] * hi[j] > 0:
            ax.text(hi[j] + 0.18, yy[j], "*", color=colr, fontsize=9,
                    va="center", ha="left")
ax.set_yticks(ypos)
ax.set_yticklabels(EN, fontsize=7.2)
ax.set_ylim(-1.75, len(KO) - 0.38)   # 아래에 범례용 빈 띠
ax.set_xlim(-4.2, 4.6)
ax.set_xlabel("Adjusted PET deviation from the five-neighbourhood mean (°C)")
ax.set_title("After controlling for measurement conditions", pad=7, loc="left", color=INK)
ax.legend(handles=[Line2D([], [], color=colr, marker="o" if i == 0 else "D",
                          ls="-", lw=1.5, mfc="white", mew=1.3, ms=5, label=lab)
                   for i, (lab, _, colr) in enumerate(SETS)],
          loc="lower center", frameon=False, handlelength=1.6, borderpad=0.1,
          handletextpad=0.5, labelspacing=0.32)
ax.text(0.0, -0.235,
        "Bars are 95 % bootstrap intervals; * marks an interval that excludes zero.\n"
        "Only Yongho 1 separates from the others, and 93 % of its sites were sunlit.\n"
        "Time of day cannot be controlled: neighbourhood explains 90 % of its variance.",
        transform=ax.transAxes, ha="left", va="top", fontsize=6.7, color=MUTED,
        linespacing=1.45)
tag(ax, "b", x=-0.155)

fig.savefig("/tmp/claude-0/SCS_Fig_neigh_adjusted.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("/tmp/claude-0/SCS_Fig_neigh_adjusted.png", bbox_inches="tight", pad_inches=0.02)
print("saved")
for lab, _, _ in SETS:
    pt, lo, hi = EST[lab]
    print(f"\n[{lab}]")
    for j, e in enumerate(EN):
        star = " *" if lo[j] * hi[j] > 0 else ""
        print(f"  {e:12} {pt[j]:+6.2f}  [{lo[j]:+6.2f}, {hi[j]:+6.2f}]{star}")
