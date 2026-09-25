#!/usr/bin/env python3
"""Input ladder — accuracy and reach at each step from the official warning to on-site sun/shade (80 sites).

(a) accuracy: MAE of PET against the globe reference, and how many of the 63 extreme sites (PET ≥ 41 °C) are detected
(b) reach: how many of the 80 sites receive an estimate from their own location (street view exists only at 55)

Steps and sources (tier3_engine_output_80_v7_photo.csv unless noted; run A–E = same engine, inputs swapped):
  1 official warning inputs      PET from gridded Ta/RH/wind with Tmrt = Ta (no radiation)   reconstructed, vpti_core.compute_pet
  2 + street-view radiation      run_A  (Tier 2, deployed Aug 2026; 25 sites substituted from 43–110 m away or failed)
  3 + building geometry          run_B  (Tier 3, no imagery; all 80 sites)
  4 + weather measured on site   run_C
  5 + sun/shade read on site     run_E  (label 61/19 at run time — rerun with 62/18 pending)
  6 + residual layer (deployed)  loso_official_v7_sites.csv 물리+AI_LONO_PET  (leave-one-neighbourhood-out)
"""
import sys, numpy as np, pandas as pd, matplotlib as mpl, matplotlib.pyplot as plt
sys.path.insert(0, "backend")
from vpti_core.comfort import compute_pet

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"; OUT = sys.argv[2] if len(sys.argv) > 2 else "figout"
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8.5, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "axes.spines.top": True, "axes.spines.right": True, "xtick.direction": "in", "ytick.direction": "in",
    "figure.dpi": 200, "savefig.dpi": 1200, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED, FAINT, SURF = "#1A1A1A", "#5E5E5E", "#B8B8B8", "#EDEDEB"
ACC = "#2F6DB5"         # accent for the on-site steps
MM = 1 / 25.4; THR = 41.0

v7 = pd.read_csv(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")
l7 = pd.read_csv(f"{DATA}/loso_official_v7_sites.csv", encoding="utf-8-sig")[["측정ID", "물리+AI_LONO_PET"]]
d = v7.merge(l7, on="측정ID")
d["tier1"] = [compute_pet(a, a, v, rh).value for a, v, rh in zip(d.앱_입력_기온, d.앱_입력_풍속, d.앱_입력_습도)]
d["sv"] = d["앱_유효"] == "유효"
hot = d.PET >= THR; n_hot = int(hot.sum()); n = len(d)

steps = [
    ("Official heat warning\nstation network,\nsupercomputer forecast", "tier1", n, ""),
    ("Street-view service\nimagery where\nit exists (2026)", "run_A_PET", int(d.sv.sum()), ""),
    ("Satellite + building\ngeometry\nno imagery needed", "run_B_PET", n, ""),
    ("+ AI residual layer\ndeployed engine", "물리+AI_LONO_PET", n, ""),
]
mae = [float((d[c] - d.PET).abs().mean()) for _, c, _, _ in steps]
hit = [int((hot & (d[c] >= THR)).sum()) for _, c, _, _ in steps]
reach = [r for _, _, r, _ in steps]
for (lab, c, r, _), m_, h_ in zip(steps, mae, hit):
    print(f"{lab.replace(chr(10),' '):28s} MAE {m_:5.2f}  detected {h_}/{n_hot}  reach {r}/{n}")

x = np.arange(len(steps))
fig = plt.figure(figsize=(120 * MM, 95 * MM))
gs = fig.add_gridspec(2, 1, height_ratios=[1.0, 0.6], hspace=0.38, left=0.10, right=0.985, top=0.93, bottom=0.20)
ax = fig.add_subplot(gs[0]); bx = fig.add_subplot(gs[1], sharex=ax)
cols = [FAINT, FAINT, ACC, INK]

# (a) accuracy
ax.bar(x, mae, width=0.58, color=cols, edgecolor="none", zorder=3)
for xi, m_, h_ in zip(x, mae, hit):
    ax.text(xi, m_ + 0.35, f"{m_:.1f}", ha="center", va="bottom", fontsize=7.4, color=INK)
    ax.text(xi, 0.45, f"{h_}/{n_hot}", ha="center", va="bottom", fontsize=6.8, color="white" if m_ > 2.5 else INK, zorder=4)
ax.set_ylim(0, 15.5); ax.set_yticks([0, 5, 10, 15])
ax.set_ylabel("MAE of PET (°C)")
ax.tick_params(labelbottom=False)
ax.text(0.99, 0.95, f"in bar: extreme sites detected, of {n_hot} measured at PET ≥ {THR:.0f} °C",
        transform=ax.transAxes, ha="right", va="top", fontsize=6.8, color=MUTED)
ax.text(-0.02, 1.02, "(a)  Accuracy at the pedestrian, against the globe reference (80 sites)", transform=ax.transAxes, ha="left", va="bottom", fontsize=8.5, fontweight="bold", color=INK)

# (b) reach
ax_r = bx
ax_r.bar(x, reach, width=0.58, color=cols, edgecolor="none", zorder=3)
ax_r.bar(x, [n - r for r in reach], bottom=reach, width=0.58, facecolor="white", edgecolor=MUTED, lw=0.6, hatch="////", zorder=3)
for xi, r, c_ in zip(x, reach, cols):
    ax_r.text(xi, 6, f"{r}/{n}", ha="center", va="bottom", fontsize=7.2, color="white" if c_ != FAINT else INK, zorder=4)
sv_i = 1
nb = d.groupby("권역").sv.agg(["sum", "count"]); worst = nb["sum"].div(nb["count"]).idxmin()
ax_r.annotate(f"{n - reach[sv_i]} sites without street view —\nsubstituted from 43–110 m away, or failed\n(fewest in Buam 1: {int(nb.loc[worst,'sum'])} of {int(nb.loc[worst,'count'])})",
              xy=(sv_i + 0.3, n - 10), xytext=(sv_i + 0.45, n + 56), fontsize=6.8, color=INK, ha="left", va="top", linespacing=1.25,
              arrowprops=dict(arrowstyle="-", lw=0.6, color=MUTED, shrinkA=0, shrinkB=2))
ax_r.set_ylim(0, 142); ax_r.set_yticks([0, 40, 80])
ax_r.set_ylabel("Sites reached\n(of 80)", fontsize=7.4)
ax_r.set_xticks(x); ax_r.set_xticklabels([s[0] for s in steps], fontsize=7.0, linespacing=1.15)
ax_r.text(-0.02, 1.04, "(b)  Reach: sites that receive an estimate for their own location", transform=ax_r.transAxes, ha="left", va="bottom", fontsize=8.5, fontweight="bold", color=INK)
from matplotlib.patches import Patch
ax.legend(handles=[Patch(facecolor=FAINT, label="existing information"), Patch(facecolor=ACC, label="this study: physics on open data"),
                   Patch(facecolor=INK, label="this study: physics + AI")],
          loc="upper right", bbox_to_anchor=(0.995, 0.86), frameon=False, fontsize=6.8, handlelength=1.2, handleheight=0.9, labelspacing=0.35)
ax_r.text(0, n + 6, "everywhere,\nbut no street-level term", ha="center", va="bottom", fontsize=6.4, color=MUTED, linespacing=1.15)
for a_ in (ax, ax_r):
    a_.set_xlim(-0.6, len(steps) - 0.4)

for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_input_ladder.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_input_ladder.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
