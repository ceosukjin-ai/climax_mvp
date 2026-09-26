#!/usr/bin/env python3
"""Distributions of the field measurements at the 80 sites, by instrument (3 × 3 strip plots).

Each dot is one site; black line = median, band = interquartile range. Globe temperature is back-calculated from the
globe-derived Tmrt (ISO 7726, D = 0.05 m, ε = 0.95, v ≥ 0.1 m s⁻¹). View factors: 2026-09-21 consistent set (79 sites).
Replaces the deck Fig 5 (2026-09-14 view factors: SVF 0.62 / 0.01–0.90, TVF 46/79 at 0).
Input: scs_master_80.csv.
"""
import sys, numpy as np, pandas as pd, matplotlib as mpl, matplotlib.pyplot as plt
from scipy.optimize import brentq

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"; OUT = sys.argv[2] if len(sys.argv) > 2 else "figout"
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "axes.spines.top": True, "axes.spines.right": True, "xtick.direction": "in", "ytick.direction": "in",
    "figure.dpi": 200, "savefig.dpi": 1200, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED = "#1A1A1A", "#5E5E5E"
C = {"Weather meter": "#2E75B6", "Globe thermometer": "#C0504D", "360° camera": "#2A9D8F", "Thermal camera": "#7B4FB5"}
MM = 1 / 25.4

d = pd.read_csv(f"{DATA}/scs_master_80.csv", encoding="utf-8-sig")
def tmrt(tg, ta, v, D=0.05, eps=0.95): return ((tg + 273.15) ** 4 + 1.1e8 * v ** 0.6 / (eps * D ** 0.4) * (tg - ta)) ** 0.25 - 273.15
d["Tg"] = [brentq(lambda g: tmrt(g, ta, max(v, 0.1)) - tm, ta - 5, ta + 60) for ta, v, tm in zip(d.Ta, d.v, d.Tmrt)]

panels = [("Air temperature (°C)", "Ta", "Weather meter", 1), ("Relative humidity (%)", "RH", "Weather meter", 1), ("Wind speed (m s$^{-1}$)", "v", "Weather meter", 2),
          ("Globe temperature (°C)", "Tg", "Globe thermometer", 1), ("Mean radiant temperature (°C)", "Tmrt", "Globe thermometer", 1), ("PET (°C)", "PET", "Globe thermometer", 1),
          ("Sky view factor", "SVF", "360° camera", 2), ("Tree view factor", "TVF", "360° camera", 2), ("Pavement surface temperature (°C)", "Ts", "Thermal camera", 1)]

fig, axes = plt.subplots(3, 3, figsize=(190 * MM, 110 * MM))
fig.subplots_adjust(left=0.05, right=0.99, top=0.96, bottom=0.07, wspace=0.16, hspace=0.75)
rng = np.random.default_rng(7)
for ax, (title, col, inst, dec) in zip(axes.ravel(), panels):
    x = d[col].dropna().values; n = len(x)
    q1, med, q3 = np.percentile(x, [25, 50, 75])
    ax.axvspan(q1, q3, color=C[inst], alpha=0.16, lw=0, zorder=0)
    ax.axvline(med, color=C[inst], lw=0.9, zorder=3)
    ax.scatter(x, rng.uniform(-0.55, 0.55, n), s=9, color=C[inst], edgecolor="white", lw=0.3, zorder=2, alpha=0.9)
    ax.set_ylim(-1, 1); ax.set_yticks([]); ax.set_ylabel("sites", fontsize=7, color=MUTED); ax.set_xlabel(title, fontsize=7.5)
    lo, hi = x.min(), x.max(); pad = (hi - lo) * 0.06
    ax.set_xlim(lo - pad, hi + pad)
    ax.text(0.0, 1.04, inst, transform=ax.transAxes, ha="left", va="bottom", fontsize=7, color=C[inst], fontweight="bold")
    extra = f" · n = {n}" if n < 80 else ""
    ax.text(1.0, 1.04, f"median {med:.{dec}f} · {lo:.{dec}f}–{hi:.{dec}f}{extra}", transform=ax.transAxes, ha="right", va="bottom", fontsize=6.8, color=MUTED)
    print(f"{col:5s} n {n:2d} median {med:.{dec}f} IQR {q1:.{dec}f}-{q3:.{dec}f} range {lo:.{dec}f}-{hi:.{dec}f}")
print("TVF < 0.005:", int((d.TVF < 0.005).sum()), "/", int(d.TVF.notna().sum()))
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_distributions.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_distributions.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
