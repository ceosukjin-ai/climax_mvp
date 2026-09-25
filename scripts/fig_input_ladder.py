#!/usr/bin/env python3
"""Input ladder — four sources of pedestrian heat information, measured vs estimated PET at 80 sites.

Panels (one per source, same axes): official warning → street-view service → satellite + building geometry → + AI residual layer.
Colour = whether the site has street-view imagery of its own (55) or not (25: alleys and block interiors, substituted from
43–110 m away or failed). Each panel prints MAE for the two groups and extreme-heat detection.

Sources (tier3_engine_output_80_v7_photo.csv unless noted):
  official warning     PET from gridded Ta/RH/wind with Tmrt = Ta (no radiation), reconstructed with vpti_core.compute_pet
  street-view service  run_A  (Tier 2, engine as operated Aug 2026)
  satellite + geometry run_B  (Tier 3, no imagery; canopy height from satellite, SVF/BVF from building polygons)
  + AI residual layer  loso_official_v7_sites.csv 물리+AI_LONO_PET  (ridge α = 20, five predictors incl. on-site sun/shade,
                       Ta and wind; leave-one-neighbourhood-out)
"""
import sys, numpy as np, pandas as pd, matplotlib as mpl, matplotlib.pyplot as plt
from matplotlib.lines import Line2D
sys.path.insert(0, "backend")
from vpti_core.comfort import compute_pet

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"; OUT = sys.argv[2] if len(sys.argv) > 2 else "figout"
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8.5, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "axes.spines.top": True, "axes.spines.right": True, "xtick.direction": "in", "ytick.direction": "in",
    "figure.dpi": 200, "savefig.dpi": 1200, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED, FAINT = "#1A1A1A", "#5E5E5E", "#B8B8B8"
SV, NOSV, RED, PINK = "#2E75B6", "#E8A33D", "#C0392B", "#FBE9EC"        # street view of its own: black; none (alley / block interior): red
MM = 1 / 25.4; THR = 41.0

v7 = pd.read_csv(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")
l7 = pd.read_csv(f"{DATA}/loso_official_v7_sites.csv", encoding="utf-8-sig")[["측정ID", "물리+AI_LONO_PET"]]
d = v7.merge(l7, on="측정ID")
d["tier1"] = [compute_pet(a, a, v, rh).value for a, v, rh in zip(d.앱_입력_기온, d.앱_입력_풍속, d.앱_입력_습도)]
d["sv"] = d["앱_유효"] == "유효"
hot = d.PET >= THR; n_hot = int(hot.sum())
S, N = d[d.sv], d[~d.sv]

panels = [
    ("(a)  Official heat warning", "station network and forecast; no radiation term", "tier1"),
    ("(b)  Street-view service", "imagery where it exists; engine as operated, 2026", "run_A_PET"),
    ("(c)  Satellite + building geometry", "no imagery needed; this study, physics only", "run_B_PET"),
    ("(d)  + AI residual layer", "this study; leave-one-neighbourhood-out", "물리+AI_LONO_PET"),
]
def mae(q, c): return float((q[c] - q.PET).abs().mean())
def det(q, c): h = q.PET >= THR; return int((h & (q[c] >= THR)).sum()), int(h.sum())

lo, hi = 25, 60
fig, axes2 = plt.subplots(2, 2, figsize=(120 * MM, 132 * MM))
axes = axes2.ravel()
fig.subplots_adjust(left=0.10, right=0.985, top=0.93, bottom=0.19, wspace=0.28, hspace=0.42)
for ax, (title, sub, col) in zip(axes, panels):
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((THR, lo), hi - THR, THR - lo, facecolor=PINK, edgecolor="none", zorder=0))   # missed extreme
    ax.plot([lo, hi], [lo, hi], color=INK, lw=0.7, zorder=1)
    ax.axhline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1); ax.axvline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1)
    if col == "tier1":
        ax.scatter(d.PET, d[col], s=13, color=MUTED, edgecolor="white", lw=0.4, zorder=3)
    else:
        ax.scatter(S.PET, S[col], s=13, color=SV, edgecolor="white", lw=0.4, zorder=3)
        ax.scatter(N.PET, N[col], s=17, color=NOSV, marker="D", edgecolor="white", lw=0.4, zorder=4)
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi); ax.set_box_aspect(1)
    ax.set_xticks([30, 40, 50, 60]); ax.set_yticks([30, 40, 50, 60])
    ax.text(0.0, 1.10, title, transform=ax.transAxes, ha="left", va="bottom", fontsize=8.2, fontweight="bold", color=INK)
    ax.text(0.0, 1.02, sub, transform=ax.transAxes, ha="left", va="bottom", fontsize=6.6, color=MUTED)
    hs, ns = det(S, col); hn, nn = det(N, col)
    if col == "tier1":
        ax.text(0.04, 0.96, f"MAE {mae(d, col):.1f} °C", transform=ax.transAxes, ha="left", va="top", fontsize=7, color=INK)
    else:
        ax.text(0.04, 0.96, f"MAE {mae(S, col):.1f} °C", transform=ax.transAxes, ha="left", va="top", fontsize=7, color=SV)
        ax.text(0.04, 0.88, f"MAE {mae(N, col):.1f} °C", transform=ax.transAxes, ha="left", va="top", fontsize=7, color="#B07A1A")
    ax.text(0.96, 0.04, f"extreme detected {hs + hn}/{ns + nn}", transform=ax.transAxes, ha="right", va="bottom", fontsize=6.8, color=RED)
    print(f"{title:36s} SV {mae(S,col):.2f} ({hs}/{ns})  noSV {mae(N,col):.2f} ({hn}/{nn})")
for a_ in axes:
    a_.set_ylabel("Estimated PET (°C)"); a_.set_xlabel("Measured PET (°C)")
    a_.tick_params(labelleft=True, labelbottom=True)
axes[0].text(THR + 0.6, hi - 1.0, "41 °C", fontsize=6.4, color=MUTED, va="top")
fig.legend(handles=[Line2D([], [], marker="o", color="none", markerfacecolor=MUTED, markersize=4.6, markeredgecolor="none", label="all 80 sites — street view not involved (a)"),
                    Line2D([], [], marker="o", color="none", markerfacecolor=SV, markersize=4.6, markeredgecolor="none", label=f"street view at the site (n = {len(S)})"),
                    Line2D([], [], marker="D", color="none", markerfacecolor=NOSV, markersize=4.2, markeredgecolor="none", label=f"no street view at the site (n = {len(N)}, alley or block interior): in (b) substituted from 43–110 m away or failed")],
           loc="lower center", bbox_to_anchor=(0.5, 0.025), ncol=1, frameon=False, fontsize=7, handletextpad=0.3, labelspacing=0.3)
fig.text(0.5, 0.005, "pink quadrant: measured ≥ 41 °C but estimated below — extreme heat missed", ha="center", va="bottom", fontsize=6.6, color=RED)
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_input_ladder.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_input_ladder.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
