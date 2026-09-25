#!/usr/bin/env python3
"""Street-view service vs imagery-free pathway, by street-view availability (80 sites).

(a) site-level |error|, street-view service (run_A) against imagery-free pathway (run_B); below the 1:1 line the
    imagery-free pathway is closer to the globe reference.
(b) the 25 sites without street view of their own: measured vs estimated PET for both pathways.
Same style as SCS_Fig_input_ladder (blue = street view at the site, amber = none).
"""
import sys, numpy as np, pandas as pd, matplotlib as mpl, matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"; OUT = sys.argv[2] if len(sys.argv) > 2 else "figout"
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8.5, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "axes.spines.top": True, "axes.spines.right": True, "xtick.direction": "in", "ytick.direction": "in",
    "figure.dpi": 200, "savefig.dpi": 1200, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED, FAINT, SURF = "#1A1A1A", "#5E5E5E", "#B8B8B8", "#EDEDEB"
SV, NOSV, AMBER_TXT = "#2E75B6", "#E8A33D", "#B07A1A"
MM = 1 / 25.4; THR = 41.0

d = pd.read_csv(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")
d["sv"] = d["앱_유효"] == "유효"
d["e2"] = (d.run_A_PET - d.PET).abs(); d["e3"] = (d.run_B_PET - d.PET).abs()
S, N = d[d.sv], d[~d.sv]
closer_S = float((S.e3 < S.e2).mean() * 100); closer_N = float((N.e3 < N.e2).mean() * 100)
def mae(q, c): return float((q[c] - q.PET).abs().mean())
def det(q, c): h = q.PET >= THR; return int((h & (q[c] >= THR)).sum()), int(h.sum())
print(f"imagery-free closer: SV {closer_S:.0f} %  noSV {closer_N:.0f} %")
print(f"noSV: street-view MAE {mae(N,'run_A_PET'):.1f} det {det(N,'run_A_PET')} | imagery-free MAE {mae(N,'run_B_PET'):.1f} det {det(N,'run_B_PET')}")

fig, (ax, bx) = plt.subplots(1, 2, figsize=(140 * MM, 72 * MM))
fig.subplots_adjust(left=0.09, right=0.985, top=0.82, bottom=0.30, wspace=0.34)

# (a)
lim = 25
ax.add_patch(Rectangle((0, 0), lim, lim, facecolor="none", edgecolor="none"))
ax.fill_between([0, lim], [0, lim], [lim, lim], color=SURF, zorder=0)
ax.plot([0, lim], [0, lim], color=INK, lw=0.7, zorder=1)
ax.scatter(S.e2, S.e3, s=13, color=SV, edgecolor="white", lw=0.4, zorder=3)
ax.scatter(N.e2, N.e3, s=17, color=NOSV, marker="D", edgecolor="white", lw=0.4, zorder=4)
ax.set_xlim(0, lim); ax.set_ylim(0, lim); ax.set_box_aspect(1)
ax.set_xticks([0, 5, 10, 15, 20, 25]); ax.set_yticks([0, 5, 10, 15, 20, 25])
ax.set_xlabel("|error|, street-view service (°C)"); ax.set_ylabel("|error|, imagery-free pathway (°C)")
ax.text(0.04, 0.96, "street-view service closer", transform=ax.transAxes, ha="left", va="top", fontsize=6.6, color=MUTED)
ax.text(0.96, 0.04, "imagery-free closer", transform=ax.transAxes, ha="right", va="bottom", fontsize=6.6, color=MUTED)
ax.text(0.0, 1.025, f"imagery-free pathway closer at {closer_S:.0f} % of sites with street view\nand {closer_N:.0f} % of sites without",
        transform=ax.transAxes, ha="left", va="bottom", fontsize=6.6, color=INK, linespacing=1.25)
ax.text(0.0, 1.16, "(a)  Site-level error, both pathways", transform=ax.transAxes, ha="left", va="bottom", fontsize=8.2, fontweight="bold", color=INK)

# (b)
lo, hi = 25, 60
bx.add_patch(Rectangle((THR, lo), hi - THR, THR - lo, facecolor=SURF, edgecolor="none", zorder=0))
bx.plot([lo, hi], [lo, hi], color=INK, lw=0.7, zorder=1)
bx.axhline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1); bx.axvline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1)
bx.scatter(N.PET, N.run_A_PET, s=20, facecolor="none", edgecolor=NOSV, marker="^", lw=0.8, zorder=3)
bx.scatter(N.PET, N.run_B_PET, s=17, color=NOSV, marker="D", edgecolor="white", lw=0.4, zorder=4)
bx.set_xlim(lo, hi); bx.set_ylim(lo, hi); bx.set_box_aspect(1)
bx.set_xticks([30, 40, 50, 60]); bx.set_yticks([30, 40, 50, 60])
bx.set_xlabel("Measured PET (°C)"); bx.set_ylabel("Estimated PET (°C)")
h2, n2 = det(N, "run_A_PET"); h3, n3 = det(N, "run_B_PET")
bx.text(0.0, 1.025, f"street-view service  MAE {mae(N,'run_A_PET'):.1f} °C, extreme {h2}/{n2}\nimagery-free pathway  MAE {mae(N,'run_B_PET'):.1f} °C, extreme {h3}/{n3}",
        transform=bx.transAxes, ha="left", va="bottom", fontsize=6.6, color=INK, linespacing=1.25)
bx.text(THR + 0.5, lo + 0.8, "41 °C", fontsize=6.4, color=MUTED, va="bottom")
bx.text(0.0, 1.16, f"(b)  Sites without street view (n = {len(N)})", transform=bx.transAxes, ha="left", va="bottom", fontsize=8.2, fontweight="bold", color=INK)

fig.legend(handles=[
    Line2D([], [], marker="o", color="none", markerfacecolor=SV, markersize=4.6, markeredgecolor="none", label=f"street view at the site (n = {len(S)})"),
    Line2D([], [], marker="D", color="none", markerfacecolor=NOSV, markersize=4.2, markeredgecolor="none", label=f"no street view at the site (n = {len(N)}); in (b) imagery-free pathway"),
    Line2D([], [], marker="^", color="none", markerfacecolor="none", markeredgecolor=NOSV, markersize=5, markeredgewidth=0.8, label="in (b) street-view service, imagery substituted from 43–110 m away or failed"),
], loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=1, frameon=False, fontsize=6.8, handletextpad=0.3, labelspacing=0.3)
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_sv_reach.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_sv_reach.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
