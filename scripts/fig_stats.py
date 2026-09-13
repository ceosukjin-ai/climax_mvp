#!/usr/bin/env python3
"""How much information does the neighbourhood label actually carry?

(a) Variance components from a mixed model with a neighbourhood random effect.
    Adding air temperature, surface temperature and sun exposure collapses the
    between-neighbourhood variance from 6.03 to 0.85 °C^2 (ICC 0.32 -> 0.11).
(b) Neighbourhood effects under the two estimators: ANCOVA fixed effects and the
    partially pooled estimates from the mixed model. Only Yongho 1 stays clear of zero.
"""
from __future__ import annotations
import csv
import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import statsmodels.formula.api as smf
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "font.size": 7.5, "axes.labelsize": 8, "axes.titlesize": 8,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.4, "ytick.major.size": 2.4,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
BLUE, ORANGE, PURPLE = "#1F6FB2", "#D94801", "#7A5AA8"
INK, MUTED, GRID = "#1A1A1A", "#666666", "#DCDCDC"
MM = 1 / 25.4

R = list(csv.DictReader(open(
    "/mnt/user-data/uploads/climax_mvp/data/tier3_engine_output_80_v6.csv",
    encoding="utf-8-sig")))
EN = {"서제2동": "Seo 2", "명장동": "Myeong-jang", "용호제1동": "Yongho 1",
      "보수동": "Bosu", "부암제1동": "Buam 1"}
ORDER = ["Seo 2", "Myeong-jang", "Yongho 1", "Bosu", "Buam 1"]
df = pd.DataFrame({
    "nb": [EN[r["권역"]] for r in R],
    "pet": [float(r["PET"]) for r in R],
    "Ta": [float(r["Ta"]) for r in R],
    "Ts": [float(r["Ts"]) for r in R],
    "sun": [1.0 if r["볕"].strip() == "1" else 0.0 for r in R]})

MODELS = [("No covariate", "pet ~ 1"),
          ("+ air\ntemperature", "pet ~ Ta"),
          ("+ surface\ntemperature", "pet ~ Ta + Ts"),
          ("+ sun\nexposure", "pet ~ Ta + Ts + sun")]
vb, vw, icc, fits = [], [], [], []
for lab, f in MODELS:
    m = smf.mixedlm(f, df, groups=df["nb"]).fit(reml=True)
    b, w = float(m.cov_re.iloc[0, 0]), float(m.scale)
    vb.append(b); vw.append(w); icc.append(b / (b + w)); fits.append(m)
vb, vw, icc = np.array(vb), np.array(vw), np.array(icc)

# ANCOVA fixed effects (sum-to-zero neighbourhood coding) + bootstrap CI
g = df["nb"].to_numpy()
pet = df["pet"].to_numpy()
COV = [df["Ta"].to_numpy(), df["Ts"].to_numpy(), df["sun"].to_numpy()]
D = np.stack([(g == k).astype(float) for k in ORDER], 1)
D = D - D.mean(0)


def anc(idx):
    X = np.column_stack([np.ones(len(idx))] + [v[idx] for v in COV]
                        + [D[idx, j] for j in range(len(ORDER))])
    b, *_ = np.linalg.lstsq(X, pet[idx], rcond=None)
    e = b[-len(ORDER):]
    return e - e.mean()


fx = anc(np.arange(len(pet)))
rng = np.random.default_rng(1)
bs = np.array([anc(rng.integers(0, len(pet), len(pet))) for _ in range(4000)])
lo, hi = np.percentile(bs, [2.5, 97.5], axis=0)
re_ = fits[-1].random_effects
blup = np.array([float(re_[k].iloc[0]) for k in ORDER])
blup = blup - blup.mean()

fig, axes = plt.subplots(1, 2, figsize=(180 * MM, 74 * MM),
                         gridspec_kw=dict(width_ratios=[1.0, 1.05]))
fig.subplots_adjust(left=0.072, right=0.995, bottom=0.235, top=0.875, wspace=0.30)


def tag(ax, s, x):
    ax.text(x, 1.085, s, transform=ax.transAxes, fontsize=9, fontweight="bold",
            va="top", ha="left", color=INK)


# ------------------------------------------------------------- (a) variance split
ax = axes[0]
ax.grid(True, axis="y", color=GRID, lw=0.5, zorder=0)
ax.set_axisbelow(True)
x = np.arange(len(MODELS))
ax.bar(x, vb, 0.6, color=ORANGE, edgecolor="white", lw=0.8, zorder=3,
       label="Between neighbourhoods")
ax.bar(x, vw, 0.6, bottom=vb, color="#C9D6E3", edgecolor="white", lw=0.8, zorder=3,
       label="Within neighbourhood (residual)")
for j in range(len(MODELS)):
    ax.text(x[j], vb[j] / 2, f"{vb[j]:.2f}", ha="center", va="center", fontsize=7,
            color="white", fontweight="bold", zorder=5)
    ax.text(x[j], vb[j] + vw[j] + 0.45, f"ICC {icc[j]:.2f}", ha="center", va="bottom",
            fontsize=7.2, color=INK, zorder=5)
ax.annotate("", xy=(2.92, vb[3] + 0.9), xytext=(0.30, vb[0] + 0.9),
            arrowprops=dict(arrowstyle="-|>", color=ORANGE, lw=1.3,
                            mutation_scale=9, shrinkA=2, shrinkB=2,
                            connectionstyle="arc3,rad=-0.12"), zorder=7)
ax.text(1.6, 24.2, "Between-neighbourhood variance falls 86 %",
        ha="center", va="top", fontsize=7.6, color=ORANGE, fontweight="bold")
ax.set_xticks(x)
ax.set_xticklabels([m[0] for m in MODELS], fontsize=7, linespacing=1.35)
ax.set_ylim(0, 25.4)
ax.set_ylabel("Variance of measured PET (°C$^2$)")
ax.set_title("The neighbourhood label loses its information", pad=6, loc="left",
             color=INK)
ax.legend(handles=[Patch(fc=ORANGE, label="Between neighbourhoods"),
                   Patch(fc="#C9D6E3", label="Within neighbourhood (residual)")],
          loc="upper right", frameon=False, handlelength=1.1, borderpad=0.1,
          handletextpad=0.5, labelspacing=0.3, bbox_to_anchor=(1.012, 0.875))
tag(ax, "a", -0.155)

# ------------------------------------------------------- (b) two estimators
ax = axes[1]
y = np.arange(len(ORDER))[::-1]
ax.grid(True, axis="x", color=GRID, lw=0.5, zorder=0)
ax.set_axisbelow(True)
ax.axvline(0, color=INK, lw=0.7, zorder=2)
for j in range(len(ORDER)):
    ax.hlines(y[j], min(fx[j], blup[j]), max(fx[j], blup[j]), color="#C2C7CC",
              lw=1.0, zorder=3)
ax.hlines(y, lo, hi, color=ORANGE, lw=1.2, zorder=4)
for j in range(len(ORDER)):
    ax.vlines([lo[j], hi[j]], y[j] - 0.10, y[j] + 0.10, color=ORANGE, lw=1.2, zorder=4)
ax.scatter(fx, y, s=30, marker="D", facecolor=ORANGE, edgecolor="white", lw=0.7,
           zorder=6)
ax.scatter(blup, y, s=30, marker="o", facecolor=BLUE, edgecolor="white", lw=0.7,
           zorder=6)
for j in range(len(ORDER)):
    star = "*" if lo[j] * hi[j] > 0 else ""
    ax.text(3.45, y[j], f"{fx[j]:+.2f}{star}   {blup[j]:+.2f}", fontsize=7,
            va="center", ha="left", color=INK)
ax.text(3.45, len(ORDER) - 0.30, "ANCOVA   Mixed", fontsize=6.9, va="center",
        ha="left", color=MUTED)
ax.set_yticks(y)
ax.set_yticklabels(ORDER, fontsize=7.5)
ax.set_ylim(-1.35, len(ORDER) - 0.20)
ax.set_xlim(-3.2, 6.4)
ax.set_xticks([-3, -2, -1, 0, 1, 2, 3])
ax.spines["bottom"].set_bounds(-3.2, 3.15)
ax.set_xlabel("Neighbourhood effect on PET (°C)")
ax.set_title("Only one neighbourhood survives", pad=6, loc="left", color=INK)
ax.legend(handles=[
    Line2D([], [], color=ORANGE, marker="D", ls="-", lw=1.2, ms=4.4,
           label="ANCOVA, fixed effects (95 % CI)"),
    Line2D([], [], color=BLUE, marker="o", ls="none", ms=4.6,
           label="Mixed model, partially pooled")],
    loc="lower left", ncol=1, frameon=False, handlelength=1.5, borderpad=0.1,
    handletextpad=0.5, labelspacing=0.3, bbox_to_anchor=(-0.008, -0.015))
tag(ax, "b", -0.145)

fig.savefig("/tmp/claude-0/SCS_Fig_stats.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("/tmp/claude-0/SCS_Fig_stats.png", bbox_inches="tight", pad_inches=0.02)
print("saved")
for j, k in enumerate(ORDER):
    print(f"  {k:12} ANCOVA {fx[j]:+6.2f} ({lo[j]:+.2f}, {hi[j]:+.2f})   "
          f"mixed {blup[j]:+6.2f}")
print("\nvariance:", [f"{b:.2f}" for b in vb], " ICC:", [f"{i:.3f}" for i in icc])
print("M3 fixed effects:\n", fits[-1].fe_params.round(3).to_string())
