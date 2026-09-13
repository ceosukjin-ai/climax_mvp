#!/usr/bin/env python3
"""Neighbourhood PET: as measured, and after controlling for measurement conditions.

Panel (b) plots the shift from the raw neighbourhood mean deviation to the
condition-adjusted estimate. Buam 1 moves from the second-hottest position to
below average, because it was the only neighbourhood measured in the afternoon.

All explanatory text lives in the caption, not in the figure.
"""
from __future__ import annotations
import csv
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "font.size": 7.5, "axes.labelsize": 8, "axes.titlesize": 8,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.4, "ytick.major.size": 2.4,
    "xtick.direction": "out", "ytick.direction": "out",
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
BLUE, ORANGE = "#1F6FB2", "#D94801"
INK, MUTED, GRID, RED = "#1A1A1A", "#666666", "#DCDCDC", "#B3261E"
MM = 1 / 25.4

R = list(csv.DictReader(open(
    "/mnt/user-data/uploads/climax_mvp/data/tier3_engine_output_80_v6.csv",
    encoding="utf-8-sig")))
KO = ["서제2동", "명장동", "용호제1동", "보수동", "부암제1동"]
EN = ["Seo 2", "Myeong-jang", "Yongho 1", "Bosu", "Buam 1"]
DATE = ["20 Aug", "23 Aug", "25 Aug", "26 Aug", "26 Aug"]
g = np.array([r["권역"] for r in R])
col = lambda k: np.array([float(r[k]) for r in R])          # noqa: E731
pet, Ta, Ts = col("PET"), col("Ta"), col("Ts")
sun = np.array([1.0 if r["볕"].strip() == "1" else 0.0 for r in R])

D = np.stack([(g == k).astype(float) for k in KO], 1)
D = D - D.mean(0)
COV = [Ta, Ts, sun]


def fit(idx):
    X = np.column_stack([np.ones(len(idx))] + [v[idx] for v in COV]
                        + [D[idx, j] for j in range(len(KO))])
    b, *_ = np.linalg.lstsq(X, pet[idx], rcond=None)
    e = b[-len(KO):]
    return e - e.mean()


adj = fit(np.arange(len(pet)))
rng = np.random.default_rng(1)
bs = np.array([fit(rng.integers(0, len(pet), len(pet))) for _ in range(4000)])
lo, hi = np.percentile(bs, [2.5, 97.5], axis=0)
raw = np.array([pet[g == k].mean() for k in KO]) - pet.mean()

fig, axes = plt.subplots(1, 2, figsize=(180 * MM, 72 * MM),
                         gridspec_kw=dict(width_ratios=[1.06, 1.18]))
fig.subplots_adjust(left=0.068, right=0.995, bottom=0.225, top=0.90, wspace=0.36)


def tag(ax, s, x):
    ax.text(x, 1.085, s, transform=ax.transAxes, fontsize=9,
            fontweight="bold", va="top", ha="left", color=INK)


# ---------------------------------------------------------------- (a) as measured
ax = axes[0]
ax.grid(True, axis="y", color=GRID, lw=0.5, zorder=0)
ax.set_axisbelow(True)
pos = np.arange(len(KO))
groups = [pet[g == k] for k in KO]
ax.boxplot(groups, positions=pos, widths=0.5, showfliers=False, patch_artist=True,
           zorder=3, medianprops=dict(color=INK, lw=1.0),
           boxprops=dict(facecolor="#EDF3F9", edgecolor=MUTED, lw=0.6),
           whiskerprops=dict(color=MUTED, lw=0.6), capprops=dict(color=MUTED, lw=0.6))
# 점을 볕/그늘로 나눈다 — 근린 안의 퍼짐이 무엇 때문인지 이 패널에서 바로 보이게 (2026-09-13)
jit = np.random.default_rng(3)
PURPLE = "#7A5AA8"
for i, k in enumerate(KO):
    mk = g == k
    xj = pos[i] + jit.uniform(-0.15, 0.15, int(mk.sum()))
    sm = sun[mk] > 0.5
    ax.scatter(xj[sm], pet[mk][sm], s=11, facecolor=ORANGE, edgecolor="white",
               lw=0.35, marker="o", zorder=5)
    ax.scatter(xj[~sm], pet[mk][~sm], s=12, facecolor=PURPLE, edgecolor="white",
               lw=0.35, marker="s", zorder=5)
ax.axhline(41, color=RED, lw=0.7, ls=(0, (4, 2)), zorder=2)
ax.text(len(KO) - 0.45, 41.4, "PET 41 °C", color=RED, fontsize=6.8,
        va="bottom", ha="right")
ax.set_xticks(pos)
SUNPCT = [100 * sun[g == k].mean() for k in KO]
ax.set_xticklabels([f"{e}\n{d}\n{p:.0f} % sunlit"
                    for e, d, p in zip(EN, DATE, SUNPCT)], fontsize=6.5,
                   linespacing=1.5)
ax.set_xlim(-0.58, len(KO) - 0.42)
ax.set_ylim(33, 58.5)
ax.set_ylabel("Measured PET (°C)")
ax.set_title("As measured", pad=6, loc="left", color=INK)
ax.legend(handles=[
    Line2D([], [], color=ORANGE, marker="o", ls="none", ms=4.2, label="Sunlit site"),
    Line2D([], [], color=PURPLE, marker="s", ls="none", ms=4.2, label="Shaded site")],
    loc="upper left", frameon=False, handlelength=1.0, borderpad=0.1,
    handletextpad=0.4, labelspacing=0.28, bbox_to_anchor=(-0.012, 1.02))
tag(ax, "a", -0.155)

# ------------------------------------------------- (b) 볕 vs 그늘 — 핵심 결과
# 근린 순위가 아니라 **한 자리에서 볕에 서느냐**가 열부하를 정한다.
# 근린마다 볕 지점 평균과 그늘 지점 평균을 짝지어 보인다. 실측값만 쓴다(모형 없음).
ax = axes[1]
PURPLE = "#7A5AA8"
rows = []
for k, e in zip(KO, EN):
    m = g == k
    sm, hm = pet[m & (sun > .5)], pet[m & (sun <= .5)]
    rows.append((e, hm.mean(), sm.mean(), len(hm), len(sm)))
rows.append(("All sites", pet[sun <= .5].mean(), pet[sun > .5].mean(),
             int((sun <= .5).sum()), int((sun > .5).sum())))

y = np.arange(len(rows))[::-1].astype(float)
y[-1] -= 0.55                                   # 마지막 '전체' 행을 띄운다
ax.grid(True, axis="x", color=GRID, lw=0.5, zorder=0)
ax.set_axisbelow(True)
ax.axvline(41, color=RED, lw=0.8, ls=(0, (4, 2)), zorder=2)

for j, (lab, hmean, smean, nh, ns) in enumerate(rows):
    big = j == len(rows) - 1
    ax.hlines(y[j], hmean, smean, color="#B8BCC2", lw=2.6 if big else 1.8, zorder=3)
    ax.scatter(hmean, y[j], s=52 if big else 34, marker="s", facecolor=PURPLE,
               edgecolor="white", lw=0.8, zorder=5)
    ax.scatter(smean, y[j], s=56 if big else 36, marker="o", facecolor=ORANGE,
               edgecolor="white", lw=0.8, zorder=5)
    ax.text(smean + 0.45, y[j], f"+{smean - hmean:.1f}", va="center", ha="left",
            fontsize=7.4 if big else 7, color=INK,
            fontweight="bold" if big else "normal")

ax.axhline(y[-1] + 0.78, color=GRID, lw=0.7, zorder=1)
ax.set_yticks(y)
ax.set_yticklabels([f"{r[0]}\n{r[3]} shaded / {r[4]} sunlit" for r in rows],
                   fontsize=6.8, linespacing=1.35)
for t, r in zip(ax.get_yticklabels(), rows):
    if r[0] == "All sites":
        t.set_fontweight("bold")
ax.set_ylim(y[-1] - 2.15, y[0] + 0.62)   # 아래 빈 띠는 요약 상자 자리
ax.set_xlim(36.5, 53.5)
ax.set_xlabel("Mean measured PET (\u00b0C)")
ax.set_title("Sun exposure, not the neighbourhood", pad=6, loc="left", color=INK)
ax.text(41.25, y[0] + 0.50, "PET 41 \u00b0C", color=RED, fontsize=6.8, ha="left",
        va="center")

pe_s = 100 * (pet[sun > .5] >= 41).mean()
pe_h = 100 * (pet[sun <= .5] >= 41).mean()
ax.text(0.5, 0.035,
        f"Extreme heat stress (PET \u2265 41 \u00b0C)\n"
        f"{pe_s:.0f} % of sunlit sites   vs   {pe_h:.0f} % of shaded sites",
        transform=ax.transAxes, ha="center", va="bottom", fontsize=7.6, color=INK,
        linespacing=1.5,
        bbox=dict(boxstyle="round,pad=0.42", fc="#FFF6EF", ec=ORANGE, lw=0.9))
ax.legend(handles=[
    Line2D([], [], color=PURPLE, marker="s", ls="none", ms=4.6, label="Shaded sites"),
    Line2D([], [], color=ORANGE, marker="o", ls="none", ms=4.8, label="Sunlit sites")],
    loc="upper right", frameon=False, handlelength=1.0, borderpad=0.1,
    handletextpad=0.4, labelspacing=0.28)
tag(ax, "b", -0.20)

fig.savefig("/tmp/claude-0/SCS_Fig_neigh_v2.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("/tmp/claude-0/SCS_Fig_neigh_v2.png", bbox_inches="tight", pad_inches=0.02)
print("saved")
