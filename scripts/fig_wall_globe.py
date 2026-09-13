#!/usr/bin/env python3
"""SCS figure — wall temperature, globe-vs-body definition offset, and model ranking.

Three findings, one figure (Elsevier double-column, 190 mm):
  (a) Thermal imagery shows wall surfaces are ~11 K cooler than the pavement,
      so the common "wall = ground temperature" closure is wrong.
  (b) The globe-minus-body mean radiant temperature offset, and that it is
      carried by the direct beam (sunlit points ~13 K, shaded ~5 K).
  (c) Once the reference is matched (globe predicted vs globe measured), the
      physically resolved wall raises accuracy instead of lowering it.

Design: categorical palette validated (lightness band / chroma floor / CVD
separation / normal-vision floor / contrast all PASS). Marker shape carries
identity alongside colour. No dual axes; every axis carries its unit.
"""
from __future__ import annotations
import csv
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
    "figure.dpi": 200, "savefig.dpi": 600,
    "pdf.fonttype": 42, "ps.fonttype": 42,
})

BLUE, ORANGE, PURPLE, GREEN = "#1F6FB2", "#D94801", "#7A5AA8", "#2E8B3D"
INK, MUTED, GRID = "#1A1A1A", "#5A5A5A", "#D8D8D8"

MM = 1 / 25.4
fig, axes = plt.subplots(1, 3, figsize=(190 * MM, 66 * MM))
fig.subplots_adjust(left=0.055, right=0.995, bottom=0.17, top=0.865, wspace=0.30)


def panel_tag(ax, s):
    ax.text(-0.20, 1.10, s, transform=ax.transAxes, fontsize=9.5,
            fontweight="bold", va="top", ha="left", color=INK)


def softgrid(ax, axis="both"):
    ax.grid(True, axis=axis, color=GRID, lw=0.5, zorder=0)
    ax.set_axisbelow(True)


# ---------------------------------------------------------------- (a) wall vs ground
rows = list(csv.DictReader(open("/tmp/claude-0/thermal_wall_23.csv", encoding="utf-8")))
gw = np.array([[float(r["grd_med"]), float(r["wall_mean"])] for r in rows])
ax = axes[0]
lo, hi = 28, 66
ax.plot([lo, hi], [lo, hi], color=MUTED, lw=0.9, ls="--", zorder=2)
ax.text(63.5, 64.2, "1:1", color=MUTED, fontsize=7, ha="right", va="bottom", rotation=41)
ax.scatter(gw[:, 0], gw[:, 1], s=26, facecolor=BLUE, edgecolor="white",
           linewidth=0.6, zorder=4, marker="o")
softgrid(ax)
ax.set_xlim(lo, hi); ax.set_ylim(lo, hi); ax.set_aspect("equal")
ax.set_xlabel("Pavement surface temperature (°C)")
ax.set_ylabel("Wall surface temperature (°C)")
ax.set_xticks([30, 40, 50, 60]); ax.set_yticks([30, 40, 50, 60])
ax.set_title("Walls are not the pavement", pad=7, loc="left", color=INK)
d = gw[:, 0] - gw[:, 1]
ax.text(0.045, 0.955,
        f"n = {len(gw)} scenes\npavement − wall {d.mean():+.1f} K\n{100*(d>0).mean():.0f} % below 1:1",
        transform=ax.transAxes, va="top", ha="left", fontsize=7, color=INK,
        bbox=dict(boxstyle="round,pad=0.33", fc="white", ec=GRID, lw=0.6))
panel_tag(ax, "a")

# ---------------------------------------------------------------- (b) globe - body
RAW = open("/tmp/claude-0/delta.txt", encoding="utf-8").read().strip()
el, dl, sun = [], [], []
for rec in RAW.split(";"):
    p = rec.split(",")
    el.append(float(p[0])); dl.append(float(p[1])); sun.append(p[2].strip() == "1")
el, dl, sun = np.array(el), np.array(dl), np.array(sun)
ax = axes[1]
softgrid(ax, axis="y")
ax.scatter(el[sun], dl[sun], s=24, facecolor=ORANGE, edgecolor="white", lw=0.5,
           marker="o", zorder=4, label="Sunlit")
ax.scatter(el[~sun], dl[~sun], s=26, facecolor=PURPLE, edgecolor="white", lw=0.5,
           marker="^", zorder=4, label="Shaded")
for m, c, lab, dy in ((sun, ORANGE, "Sunlit", 1.15), (~sun, PURPLE, "Shaded", -1.45)):
    ax.axhline(dl[m].mean(), color=c, lw=0.9, ls=":", zorder=3)
    ax.text(43.9, dl[m].mean() + dy, f"{lab}  mean {dl[m].mean():.1f} K",
            color=c, fontsize=7, va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.9))
ax.set_xlim(43.4, 68.6); ax.set_ylim(0, 16.4)
ax.set_xlabel("Solar elevation (°)")
ax.set_ylabel("Globe − body $T_{mrt}$ (K)")
ax.set_title("The offset is the direct beam", pad=7, loc="left", color=INK)
ax.text(0.975, 0.045, f"n = {len(dl)} points\noverall {dl.mean():+.1f} K",
        transform=ax.transAxes, va="bottom", ha="right", fontsize=7, color=INK,
        bbox=dict(boxstyle="round,pad=0.33", fc="white", ec=GRID, lw=0.6))
panel_tag(ax, "b")

# ---------------------------------------------------------------- (c) model ranking
cfg = [
    ("Wall = pavement (deployed)",   5.46, 4.44),
    ("+ wall shortwave reflection",  5.23, 4.14),
    ("+ resolved wall temperature",  3.92, 1.79),
    ("+ wall thermal mass",          3.78, 1.44),
]
names = [c[0] for c in cfg]
mae = np.array([c[1] for c in cfg])
bias = np.array([c[2] for c in cfg])
y = np.arange(len(cfg))[::-1]
ax = axes[2]
softgrid(ax, axis="x")
ax.barh(y, mae, height=0.42, color=BLUE, edgecolor="white", lw=0.8, zorder=3)
ax.scatter(bias, y, s=36, marker="D", facecolor="white", edgecolor=ORANGE,
           lw=1.3, zorder=5)
for yi, m, b, nm in zip(y, mae, bias, names):
    ax.text(m + 0.12, yi, f"{m:.2f}", va="center", ha="left", fontsize=7.2, color=INK)
    ax.text(b, yi - 0.30, f"{b:+.2f}", va="top", ha="center", fontsize=6.8, color=ORANGE)
    ax.text(0.10, yi + 0.30, nm, va="bottom", ha="left", fontsize=7.2, color=INK)
ax.set_yticks([]); ax.set_ylim(-0.75, len(cfg) - 0.20)
ax.set_xlim(0, 6.7)
ax.set_xlabel("PET error vs. globe reference (°C)")
ax.set_title("Matched reference reverses the ranking", pad=7, loc="left", color=INK)
ax.legend(handles=[
    Line2D([], [], color=BLUE, lw=5, label="MAE"),
    Line2D([], [], color=ORANGE, marker="D", ls="none", mfc="white", mew=1.3,
           ms=5, label="Bias")],
    loc="lower right", frameon=False, handlelength=1.3, borderpad=0.15,
    handletextpad=0.5, labelspacing=0.3)
panel_tag(ax, "c")

fig.savefig("/tmp/claude-0/SCS_Fig_wall_globe.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("/tmp/claude-0/SCS_Fig_wall_globe.png", bbox_inches="tight", pad_inches=0.02)
print("saved")
