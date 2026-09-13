#!/usr/bin/env python3
"""Revised SCS figures per 그림수정요청_260913.

Fig A (was 그림 2) — three panels on the street-view dependency.
  item 2  : constant-SVF null baseline added to panel (c)
  item 3  : caption sentence (text, delivered separately)
  item 5  : panel (a) kept; it is not wrong

Fig B (was 결정요인 3패널) — three panels on what actually drives PET.
  item 6  : permutation p recomputed = 0.006 (figure previously said 0.25)
  item 7  : LCZ labels removed (they contradict the measured building heights)
  item 8  : recast so it does not read as a neighbourhood ranking
  item 9  : axis-label overlap removed (two-line labels, wider axis)
  item 10 : unit added to panel (c) x-axis
"""
from __future__ import annotations
import csv
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "font.size": 7.5, "axes.labelsize": 8, "axes.titlesize": 8.5,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
    "axes.linewidth": 0.7, "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "xtick.major.size": 2.6, "ytick.major.size": 2.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
BLUE, ORANGE, PURPLE, GREEN = "#1F6FB2", "#D94801", "#7A5AA8", "#2E8B3D"
T2, T3 = "#8FBBDD", "#1F6FB2"          # ordered pair: less information -> more
INK, MUTED, GRID, RED = "#1A1A1A", "#5A5A5A", "#D8D8D8", "#B3261E"
MM = 1 / 25.4

SRC = "/mnt/user-data/uploads/climax_mvp/data/tier3_engine_output_80_v6.csv"
R = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
STATUS = {}          # 측정ID -> near / substituted / failed
for line in open("/tmp/claude-0/svstatus.csv", encoding="utf-8"):
    k, v = line.rstrip("\n").split("\t")
    STATUS[k] = v


def col(k, rows=None):
    return np.array([float(r[k]) for r in (rows or R)])


def tag(ax, s, x=-0.20, y=1.10):
    ax.text(x, y, s, transform=ax.transAxes, fontsize=9.5, fontweight="bold",
            va="top", ha="left", color=INK)


def softgrid(ax, axis="both"):
    ax.grid(True, axis=axis, color=GRID, lw=0.5, zorder=0)
    ax.set_axisbelow(True)


# ==================================================================== FIGURE A
fig, axes = plt.subplots(1, 3, figsize=(190 * MM, 74 * MM))
fig.subplots_adjust(left=0.055, right=0.995, bottom=0.265, top=0.875, wspace=0.30)

near = np.array([STATUS[r["측정ID"]] == "near" for r in R])
pet = col("PET")
eA = np.abs(col("run_A_PET") - pet)      # Tier 2, street view
eB = np.abs(col("run_B_PET") - pet)      # Tier 3, street-view-free

# ---- (a) site-level error, Tier 2 vs Tier 3
ax = axes[0]
lim = 22
ax.fill_between([0, lim], [0, lim], [lim, lim], color="#EEF3F8", zorder=1)
ax.plot([0, lim], [0, lim], color=MUTED, lw=0.9, zorder=3)
ax.scatter(eA[near], eB[near], s=22, facecolor=BLUE, edgecolor="white", lw=0.5,
           marker="o", zorder=4)
ax.scatter(eA[~near], eB[~near], s=24, facecolor=ORANGE, edgecolor="white", lw=0.5,
           marker="D", zorder=4)
softgrid(ax)
ax.set_xlim(0, lim); ax.set_ylim(0, lim); ax.set_aspect("equal")
ax.set_xlabel("|error| Tier 2, street view (°C)")
ax.set_ylabel("|error| Tier 3, street-view-free (°C)")
ax.set_title("Site-level error, Tier 2 vs Tier 3", pad=7, loc="left", color=INK)
ax.text(2.0, 19.3, "Tier 2 better", color=MUTED, fontsize=7)
ax.text(13.2, 7.0, "Tier 3 better", color=MUTED, fontsize=7)
ax.text(11.6, 5.4,
        "Tier 3 closer at\n"
        f"{100*np.mean(eB[near] < eA[near]):.0f} % of sites with street view\n"
        f"{100*np.mean(eB[~near] < eA[~near]):.0f} % of sites without",
        va="top", ha="left", fontsize=6.9, color=INK, linespacing=1.35)
tag(ax, "a")

# ---- (b) sites without street view
ax = axes[1]
m = ~near
softgrid(ax)
ax.plot([28, 60], [28, 60], color=MUTED, lw=0.9, zorder=3)
ax.axhline(41, color=RED, lw=0.8, ls="--", zorder=2)
ax.axvline(41, color=RED, lw=0.8, ls="--", zorder=2)
ax.scatter(pet[m], col("run_A_PET")[m], s=22, facecolor="none", edgecolor=T2,
           lw=1.0, marker="^", zorder=4)
ax.scatter(pet[m], col("run_B_PET")[m], s=22, facecolor=T3, edgecolor="white",
           lw=0.5, marker="o", zorder=5)
hotm = pet[m] >= 41
ax.set_xlim(28, 60); ax.set_ylim(28, 60); ax.set_aspect("equal")
ax.set_xlabel("Measured PET (°C)")
ax.set_ylabel("Reported PET (°C)")
ax.set_title(f"Sites without street view (n = {m.sum()})", pad=7, loc="left", color=INK)
ax.legend(handles=[
    Line2D([], [], color=T2, marker="^", ls="none", mfc="none", mew=1.0, ms=5,
           label=f"Tier 2: MAE {eA[m].mean():.1f}, extreme "
                 f"{int(((col('run_A_PET')[m] >= 41) & hotm).sum())}/{int(hotm.sum())}"),
    Line2D([], [], color=T3, marker="o", ls="none", ms=5,
           label=f"Tier 3: MAE {eB[m].mean():.1f}, extreme "
                 f"{int(((col('run_B_PET')[m] >= 41) & hotm).sum())}/{int(hotm.sum())}")],
    loc="upper left", frameon=False, handlelength=1.0, borderpad=0.15,
    handletextpad=0.5, labelspacing=0.3)
tag(ax, "b")

# ---- (c) SVF error by street-view status, with constant-SVF baseline
ax = axes[2]
svf = col("SVF")
grp_order = ["near", "substituted", "failed"]
labels = ["Street view\nnear site", "Substituted\npanorama", "Analysis\nfailed"]
gmask = {g: np.array([STATUS[r["측정ID"]] == g for r in R]) for g in grp_order}
e2 = {g: np.abs(col("앱_SVF")[gmask[g]] - svf[gmask[g]]).mean() for g in grp_order}
e3 = {g: np.abs(col("tier3_svf")[gmask[g]] - svf[gmask[g]]).mean() for g in grp_order}
const = np.abs(svf - svf.mean()).mean()
x = np.arange(3); w = 0.34
softgrid(ax, axis="y")
b2 = ax.bar(x - w / 2, [e2[g] for g in grp_order], w, color=T2, edgecolor="white",
            lw=0.8, zorder=3)
b3 = ax.bar(x + w / 2, [e3[g] for g in grp_order], w, color=T3, edgecolor="white",
            lw=0.8, zorder=3)
for bars in (b2, b3):
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.012,
                f"{b.get_height():.2f}", ha="center", va="bottom", fontsize=7, color=INK)
ax.axhline(const, color=MUTED, lw=1.1, ls="--", zorder=6)
ax.set_xticks(x)
ax.set_xticklabels([f"{l}\n(n = {int(gmask[g].sum())})" for l, g in zip(labels, grp_order)],
                   linespacing=1.3)
ax.set_ylim(0, 0.86)
ax.set_ylabel("SVF error vs. 360° panorama (MAE)")
ax.set_title("Sky-view-factor input vs. 360° panorama", pad=7, loc="left", color=INK)
ax.legend(handles=[Patch(fc=T2, label="Tier 2 (street view)"),
                   Patch(fc=T3, label="Tier 3 (buildings + satellite)"),
                   Line2D([], [], color=MUTED, ls="--", lw=1.1,
                          label=f"Constant SVF = sample mean {svf.mean():.2f} "
                                f"(MAE {const:.2f})")],
          loc="upper left", frameon=False, handlelength=1.3, borderpad=0.15,
          handletextpad=0.5, labelspacing=0.32)
tag(ax, "c")

fig.legend(handles=[
    Line2D([], [], color=BLUE, marker="o", ls="none", ms=5,
           label=f"Street view near site (n = {int(near.sum())})"),
    Line2D([], [], color=ORANGE, marker="D", ls="none", ms=5,
           label=f"No street view: substituted 43–110 m away, or failed "
                 f"(n = {int((~near).sum())})"),
    Line2D([], [], color=RED, ls="--", lw=0.9,
           label="Extreme heat stress threshold (PET ≥ 41 °C)")],
    loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.055),
    handlelength=1.4, columnspacing=1.8)
fig.savefig("/tmp/claude-0/SCS_Fig_streetview_rev.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("/tmp/claude-0/SCS_Fig_streetview_rev.png", bbox_inches="tight", pad_inches=0.02)
plt.close(fig)

# ==================================================================== FIGURE B
fig, axes = plt.subplots(1, 3, figsize=(190 * MM, 78 * MM),
                         gridspec_kw=dict(width_ratios=[1.42, 1.0, 1.0]))
fig.subplots_adjust(left=0.052, right=0.995, bottom=0.335, top=0.885, wspace=0.32)

META = [   # neighbourhood, date, solar elevation, air temperature (sorted by date - item 8)
    ("Seo 2",   "08-20", 64.6, 34.7),
    ("Myeong-jang", "08-23", 64.7, 35.0),
    ("Yongho 1", "08-25", 65.4, 35.3),
    ("Bosu",    "08-26", 64.6, 34.0),
    ("Buam 1",  "08-26", 51.0, 37.4),
]
KO = ["서제2동", "명장동", "용호제1동", "보수동", "부암제1동"]
gg = np.array([r["권역"] for r in R])
sun = np.array([r["볕"].strip() == "1" for r in R])
svf, tmrt, ts = col("SVF"), col("Tmrt"), col("Ts")

ax = axes[0]
softgrid(ax, axis="y")
groups = [pet[gg == k] for k in KO]
pos = np.arange(len(META))
ax.boxplot(groups, positions=pos, widths=0.54, showfliers=False, patch_artist=True,
           zorder=3, medianprops=dict(color=INK, lw=1.1),
           boxprops=dict(facecolor="#EAF1F8", edgecolor=MUTED, lw=0.7),
           whiskerprops=dict(color=MUTED, lw=0.7), capprops=dict(color=MUTED, lw=0.7))
rng = np.random.default_rng(3)
for i, v in enumerate(groups):
    ax.scatter(pos[i] + rng.uniform(-0.15, 0.15, len(v)), v, s=11, color=BLUE,
               alpha=0.85, zorder=4, lw=0)
    ax.text(pos[i], max(v) + 0.8, f"{max(v)-min(v):.1f}", ha="center", va="bottom",
            fontsize=7, color=INK)
ax.axhline(41, color=RED, lw=0.8, ls="--", zorder=2)
ax.text(len(META) - 0.42, 41.5, "extreme \u2265 41 \u00b0C", color=RED, fontsize=6.8,
        va="bottom", ha="right")
ax.set_xticks(pos)
ax.set_xticklabels([f"{m[0]}\n{m[1]}" for m in META], fontsize=6.0, linespacing=1.35)
ax.set_xlim(-0.62, len(META) - 0.38); ax.set_ylim(33, 58)
ax.set_ylabel("Measured PET (\u00b0C)")
ax.set_title("Spread within each neighbourhood", pad=7, loc="left", color=INK)
ax.text(0.0, -0.255, "Range (\u00b0C) printed above each box. Sorted by date, not by value.\n"
                     "All measured 11\u201314 h except Buam 1 (14:10\u201315:06, air 37.4 \u00b0C,\n"
                     "solar elevation 51\u00b0), so the columns are not directly comparable.",
        transform=ax.transAxes, ha="left", va="top", fontsize=6.7, color=MUTED,
        linespacing=1.45)
tag(ax, "a", x=-0.19)


def perm_p(x, y, g, n=20000, seed=0):
    """근린별 중심화 후 근린 안에서만 y 를 섞는다. 그룹 순서를 정렬해 실행마다 같은 값이 나오게 한다."""
    us = sorted(set(g))
    xc, yc = x.copy(), y.copy()
    for u in us:
        m = g == u; xc[m] -= xc[m].mean(); yc[m] -= yc[m].mean()
    obs = abs(np.corrcoef(xc, yc)[0, 1]); rg = np.random.default_rng(seed); c = 0
    for _ in range(n):
        ys = yc.copy()
        for u in us:
            m = g == u; ys[m] = rg.permutation(ys[m])
        if abs(np.corrcoef(xc, ys)[0, 1]) >= obs - 1e-12: c += 1
    return obs, (c + 1) / (n + 1)


def scat(ax, x, y):
    ax.scatter(x[sun], y[sun], s=22, facecolor=ORANGE, edgecolor="white", lw=0.5,
               marker="o", zorder=4)
    ax.scatter(x[~sun], y[~sun], s=24, facecolor=PURPLE, edgecolor="white", lw=0.5,
               marker="s", zorder=4)


# ---- (b) Tmrt vs SVF   (item 6: permutation p recomputed)
r_in, p_in = perm_p(svf, tmrt, gg)
ax = axes[1]
softgrid(ax); scat(ax, svf, tmrt)
ax.set_xlabel("Sky view factor (360\u00b0 panorama)")
ax.set_ylabel("Measured $T_{mrt}$ (\u00b0C)")
ax.set_title("$T_{mrt}$ vs. sky view factor", pad=7, loc="left", color=INK)
ax.text(0.0, -0.235,
        f"All sites r = {np.corrcoef(svf, tmrt)[0,1]:.2f}. Within neighbourhood\n"
        f"r = {r_in:.2f}, permutation p = {p_in:.3f}. With $T_s$ and sun in\n"
        f"the model, no longer significant (p = 0.149).",
        transform=ax.transAxes, ha="left", va="top", fontsize=6.7, color=MUTED,
        linespacing=1.45)
tag(ax, "b")

# ---- (c) PET vs surface temperature   (item 10: unit)
r_ts, p_ts = perm_p(ts, pet, gg)
ax = axes[2]
softgrid(ax)
b1, a1 = np.polyfit(ts, pet, 1)
xs = np.linspace(ts.min() - 1, ts.max() + 1, 10)
ax.plot(xs, a1 + b1 * xs, color=INK, lw=1.0, zorder=3)
ax.axhline(41, color=RED, lw=0.8, ls="--", zorder=2)
scat(ax, ts, pet)
ax.set_xlabel("Radiometric surface temperature,\npavement (\u00b0C)")
ax.set_ylabel("Measured PET (\u00b0C)")
ax.set_title("PET vs. surface temperature", pad=7, loc="left", color=INK)
ax.text(0.0, -0.315,
        f"PET = {a1:.1f} + {b1:.2f}\u00b7$T_s$. All sites r = "
        f"{np.corrcoef(ts, pet)[0,1]:.2f}. Within\nneighbourhood r = {r_ts:.2f}, "
        f"permutation p < 0.001.",
        transform=ax.transAxes, ha="left", va="top", fontsize=6.7, color=MUTED,
        linespacing=1.45)
tag(ax, "c")

# 볕/그늘 범례는 (b)(c) 공통이라 패널 밖 아래로 뺀다 — 패널 안에 두면 점을 가린다 (2026-09-13)
fig.legend(handles=[
    Line2D([], [], color=ORANGE, marker="o", ls="none", ms=5,
           label=f"Measured in sun (n = {int(sun.sum())})"),
    Line2D([], [], color=PURPLE, marker="s", ls="none", ms=5,
           label=f"Measured in shade (n = {int((~sun).sum())})"),
    Line2D([], [], color=RED, ls="--", lw=0.9,
           label="Extreme heat stress threshold (PET ≥ 41 °C)")],
    loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.045),
    handlelength=1.4, columnspacing=1.8)

fig.savefig("/tmp/claude-0/SCS_Fig_drivers_rev.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("/tmp/claude-0/SCS_Fig_drivers_rev.png", bbox_inches="tight", pad_inches=0.02)
print("saved both")
