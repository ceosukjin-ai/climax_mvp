#!/usr/bin/env python3
"""One panel: measured PET distribution + model-adjusted neighbourhood means.

The boxes are what was measured. The markers are what each neighbourhood would
have shown at common conditions (mean air temperature, mean surface temperature,
mean sun exposure), from two estimators:
  ANCOVA        ordinary least squares, neighbourhood as fixed effects, 95 % bootstrap CI
  Mixed model   neighbourhood as a random effect (REML), partially pooled estimate

Buam 1 falls from a measured mean of 49.4 °C to an adjusted 45.2 °C: it was the
only neighbourhood surveyed in the afternoon.
"""
from __future__ import annotations
import csv
import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import statsmodels.formula.api as smf
from matplotlib.lines import Line2D

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "font.size": 8, "axes.labelsize": 8.5, "axes.titlesize": 8.5,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 7.4,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.4, "ytick.major.size": 2.4,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
BLUE, ORANGE, PURPLE = "#1F6FB2", "#D94801", "#7A5AA8"
INK, MUTED, GRID, RED = "#1A1A1A", "#666666", "#DCDCDC", "#B3261E"
MM = 1 / 25.4

R = list(csv.DictReader(open(
    "/mnt/user-data/uploads/climax_mvp/data/tier3_engine_output_80_v6.csv",
    encoding="utf-8-sig")))
KO = ["서제2동", "명장동", "용호제1동", "보수동", "부암제1동"]
EN = ["Seo 2", "Myeong-jang", "Yongho 1", "Bosu", "Buam 1"]
DATE = ["20 Aug", "23 Aug", "25 Aug", "26 Aug", "26 Aug"]
WIN = ["12:40–13:42", "11:19–12:27", "12:06–12:56",
       "12:05–13:12", "14:10–15:06"]
LATE = 4

g = np.array([r["권역"] for r in R])
col = lambda k: np.array([float(r[k]) for r in R])          # noqa: E731
pet, Ta, Ts = col("PET"), col("Ta"), col("Ts")
sun = np.array([1.0 if r["볕"].strip() == "1" else 0.0 for r in R])
gm = pet.mean()

# --- ANCOVA: neighbourhood fixed effects, sum-to-zero, bootstrap CI
D = np.stack([(g == k).astype(float) for k in KO], 1)
D = D - D.mean(0)
COV = [Ta, Ts, sun]


def anc(idx):
    X = np.column_stack([np.ones(len(idx))] + [v[idx] for v in COV]
                        + [D[idx, j] for j in range(len(KO))])
    b, *_ = np.linalg.lstsq(X, pet[idx], rcond=None)
    e = b[-len(KO):]
    return e - e.mean()


fx = anc(np.arange(len(pet)))
rng = np.random.default_rng(1)
bs = np.array([anc(rng.integers(0, len(pet), len(pet))) for _ in range(4000)])
lo, hi = np.percentile(bs, [2.5, 97.5], axis=0)

# --- Mixed model: neighbourhood random effect
df = pd.DataFrame({"nb": [dict(zip(KO, EN))[k] for k in g], "pet": pet,
                   "Ta": Ta, "Ts": Ts, "sun": sun})
m0 = smf.mixedlm("pet ~ 1", df, groups=df["nb"]).fit(reml=True)
m3 = smf.mixedlm("pet ~ Ta + Ts + sun", df, groups=df["nb"]).fit(reml=True)
blup = np.array([float(m3.random_effects[e].iloc[0]) for e in EN])
blup = blup - blup.mean()
icc0 = float(m0.cov_re.iloc[0, 0]) / (float(m0.cov_re.iloc[0, 0]) + float(m0.scale))
icc3 = float(m3.cov_re.iloc[0, 0]) / (float(m3.cov_re.iloc[0, 0]) + float(m3.scale))

raw = np.array([pet[g == k].mean() for k in KO])
adj_a, adj_m = gm + fx, gm + blup

fig, ax = plt.subplots(figsize=(140 * MM, 92 * MM))
fig.subplots_adjust(left=0.105, right=0.995, bottom=0.245, top=0.955)
ax.grid(True, axis="y", color=GRID, lw=0.5, zorder=0)
ax.set_axisbelow(True)
pos = np.arange(len(KO))

ax.axvspan(LATE - 0.46, LATE + 0.46, color="#FFF3EA", zorder=1)
ax.boxplot([pet[g == k] for k in KO], positions=pos, widths=0.62, showfliers=False,
           patch_artist=True, zorder=3, medianprops=dict(color=MUTED, lw=0.9),
           boxprops=dict(facecolor="#F2F6FA", edgecolor="#B9C2CB", lw=0.6),
           whiskerprops=dict(color="#B9C2CB", lw=0.6),
           capprops=dict(color="#B9C2CB", lw=0.6))
jit = np.random.default_rng(3)
for i, k in enumerate(KO):
    mk = g == k
    xj = pos[i] + jit.uniform(-0.17, 0.17, int(mk.sum()))
    sm = sun[mk] > 0.5
    ax.scatter(xj[sm], pet[mk][sm], s=13, facecolor=ORANGE, edgecolor="white",
               lw=0.35, marker="o", zorder=4)
    ax.scatter(xj[~sm], pet[mk][~sm], s=14, facecolor=PURPLE, edgecolor="white",
               lw=0.35, marker="s", zorder=4)

# model-adjusted means, drawn on top of the measured distribution
for i in range(len(KO)):
    ax.annotate("", xy=(pos[i], adj_a[i]), xytext=(pos[i], raw[i]),
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.0,
                                mutation_scale=7, shrinkA=3, shrinkB=4), zorder=6)
ax.scatter(pos, raw, s=34, marker="_", color=INK, lw=1.6, zorder=7)
ax.vlines(pos, gm + lo, gm + hi, color=INK, lw=1.3, zorder=7)
ax.hlines(np.r_[gm + lo, gm + hi], np.r_[pos, pos] - 0.07, np.r_[pos, pos] + 0.07,
          color=INK, lw=1.3, zorder=7)
ax.scatter(pos, adj_a, s=44, marker="D", facecolor=INK, edgecolor="white", lw=0.8,
           zorder=8)
ax.scatter(pos + 0.115, adj_m, s=40, marker="o", facecolor="white",
           edgecolor=INK, lw=1.2, zorder=8)
ax.text(pos[LATE] + 0.26, (raw[LATE] + adj_a[LATE]) / 2,
        f"{adj_a[LATE] - raw[LATE]:+.1f} °C", fontsize=7.6, color=INK,
        va="center", ha="left", fontweight="bold")

ax.axhline(41, color=RED, lw=0.8, ls=(0, (4, 2)), zorder=2)
ax.text(len(KO) - 0.48, 41.4, "extreme heat stress, PET 41 °C", color=RED,
        fontsize=7.2, va="bottom", ha="right")
ax.axhline(gm, color=MUTED, lw=0.7, ls=(0, (1, 2)), zorder=2)
ax.text(1.5, gm + 0.28, "grand mean", color=MUTED, fontsize=7.0,
        va="bottom", ha="center")

ax.text(pos[LATE], 57.0, "surveyed in the afternoon\nsolar elevation 51°",
        ha="center", va="top", fontsize=7.0, color=ORANGE, linespacing=1.3, zorder=9)

SUNPCT = [100 * sun[g == k].mean() for k in KO]
ax.set_xticks(pos)
ax.set_xticklabels([f"{e}\n{d}  {w}\n{p:.0f} % sunlit"
                    for e, d, w, p in zip(EN, DATE, WIN, SUNPCT)], fontsize=6.8,
                   linespacing=1.55)
ax.get_xticklabels()[LATE].set_color(ORANGE)
ax.set_xlim(-0.62, len(KO) - 0.38)
ax.set_ylim(33.5, 63.0)   # 위쪽 빈 띠는 범례 자리
ax.set_ylabel("PET (°C)")

ax.legend(handles=[
    Line2D([], [], color=ORANGE, marker="o", ls="none", ms=4.4, label="Sunlit site"),
    Line2D([], [], color=PURPLE, marker="s", ls="none", ms=4.4, label="Shaded site"),
    Line2D([], [], color=INK, marker="_", ls="none", ms=7, mew=1.6,
           label="Measured mean"),
    Line2D([], [], color=INK, marker="D", ls="-", lw=1.3, ms=4.6,
           label="ANCOVA adjusted (95 % CI)"),
    Line2D([], [], color=INK, marker="o", ls="none", mfc="white", mew=1.2, ms=4.8,
           label="Mixed model, partially pooled")],
    loc="upper left", ncol=2, frameon=False, handlelength=1.2, borderpad=0.1,
    handletextpad=0.5, labelspacing=0.3, columnspacing=1.5,
    bbox_to_anchor=(-0.012, 1.01))

ax.text(0.5, -0.215,
        "Markers are adjusted to common conditions (mean air temperature, surface "
        "temperature and sun exposure).\n"
        f"Neighbourhood variance falls from {float(m0.cov_re.iloc[0,0]):.2f} to "
        f"{float(m3.cov_re.iloc[0,0]):.2f} °C$^2$ under the mixed model "
        f"(ICC {icc0:.2f} → {icc3:.2f}).",
        transform=ax.transAxes, ha="center", va="top", fontsize=7.0, color=MUTED,
        linespacing=1.5)

fig.savefig("/tmp/claude-0/SCS_Fig_one.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("/tmp/claude-0/SCS_Fig_one.png", bbox_inches="tight", pad_inches=0.02)
print("saved")
for i, e in enumerate(EN):
    print(f"  {e:12} measured {raw[i]:5.2f}  ANCOVA {adj_a[i]:5.2f} "
          f"({gm+lo[i]:5.2f}, {gm+hi[i]:5.2f})  mixed {adj_m[i]:5.2f}")
print(f"grand mean {gm:.2f}  ICC {icc0:.3f} -> {icc3:.3f}")
