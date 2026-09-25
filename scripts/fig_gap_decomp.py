#!/usr/bin/env python3
"""Forecast–experience gap decomposed on one PET scale, by neighbourhood (80 sites).

Three layers, each PET with the same body model as the measured value (Höppe MEMI via pythermalcomfort
pet_steady, met 1.4, clo 0.9 — the settings scs_master_80.csv PET was produced with, verified to 0.00 °C):
  ① warning inputs   KMA grid Ta / RH / wind at the site's cell (what the official warning sees), Tmrt = Ta
  ② on-site air      measured Ta / RH / wind at the pedestrian, Tmrt = Ta   (no radiation)
  ③ measured         measured Tmrt from the globe
Segment ①→② = station siting and canyon air; ②→③ = radiation not represented.
Inputs: scs_master_80.csv (Ta RH v Tmrt PET), tier3_engine_output_80_v7_photo.csv (앱_입력_*).
"""
import sys, numpy as np, pandas as pd, matplotlib as mpl, matplotlib.pyplot as plt
from matplotlib.patches import Patch
from pythermalcomfort.models import pet_steady

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"; OUT = sys.argv[2] if len(sys.argv) > 2 else "figout"
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8.5, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "axes.spines.top": True, "axes.spines.right": True, "xtick.direction": "in", "ytick.direction": "in",
    "figure.dpi": 200, "savefig.dpi": 1200, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED, FAINT = "#1A1A1A", "#5E5E5E", "#C4C4C4"
AIR, RAD = "#B8B8B8", "#C0504D"           # air term grey; radiation term = the beam colour used elsewhere
MM = 1 / 25.4; THR = 41.0

def H(a, t, v, rh): return float(pet_steady(tdb=a, tr=t, v=max(v, 0.1), rh=rh, met=1.4, clo=0.9, position="sitting").pet)

m = pd.read_csv(f"{DATA}/scs_master_80.csv", encoding="utf-8-sig")
v = pd.read_csv(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")[["측정ID", "앱_입력_기온", "앱_입력_풍속", "앱_입력_습도"]]
d = m.merge(v, on="측정ID")
chk = np.abs([H(a, t, w, r) for a, t, w, r in zip(d.Ta, d.Tmrt, d.v, d.RH)] - d.PET).max()
assert chk < 0.05, f"PET settings do not reproduce the measured PET (max diff {chk:.2f})"
d["L1"] = [H(a, a, w, r) for a, w, r in zip(d.앱_입력_기온, d.앱_입력_풍속, d.앱_입력_습도)]
d["L2"] = [H(a, a, w, r) for a, w, r in zip(d.Ta, d.v, d.RH)]

ROWS = [("All 80 sites", None), ("LCZ 1  Buam 1", "부암제1동"), ("LCZ 2  Bosu", "보수동"), ("LCZ 3  Seo 2", "서제2동"),
        ("LCZ 4  Yongho 1", "용호제1동"), ("LCZ 5  Myeongjang", "명장동")]
rows = []
for lab, dong in ROWS:
    q = d if dong is None else d[d["권역"] == dong]
    rows.append((lab, q.L1.mean(), q.L2.mean(), q.PET.mean(), int((q.PET >= THR).sum()), len(q)))
    print(f"{lab:20s} n {len(q):2d}  warning {q.L1.mean():.1f}  on-site air {q.L2.mean():.1f}  measured {q.PET.mean():.1f}  "
          f"air +{q.L2.mean()-q.L1.mean():.1f}  radiation +{q.PET.mean()-q.L2.mean():.1f}  total +{q.PET.mean()-q.L1.mean():.1f}  "
          f"extreme {int((q.PET>=THR).sum())}/{len(q)}")

fig, ax = plt.subplots(figsize=(140 * MM, 70 * MM))
fig.subplots_adjust(left=0.20, right=0.90, top=0.90, bottom=0.30)
ys = np.arange(len(rows))[::-1]
for y, (lab, l1, l2, pet, ne, n) in zip(ys, rows):
    h = 0.52
    ax.barh(y, l2 - l1, left=l1, height=h, color=AIR, edgecolor="none", zorder=2)
    ax.barh(y, pet - l2, left=l2, height=h, color=RAD, edgecolor="none", zorder=2)
    ax.text(l1 - 0.3, y, f"{l1:.1f}", ha="right", va="center", fontsize=6.8, color=INK)
    ax.text(pet + 0.3, y, f"{pet:.1f}", ha="left", va="center", fontsize=6.8, color=INK)
    ax.text((l1 + l2) / 2, y, f"+{l2-l1:.1f}", ha="center", va="center", fontsize=6.4, color=INK)
    ax.text((l2 + pet) / 2, y, f"+{pet-l2:.1f}", ha="center", va="center", fontsize=6.4, color="white")
    ax.text(1.01, y, f"{ne}/{n}", transform=ax.get_yaxis_transform(), ha="left", va="center", fontsize=6.8, color=MUTED)
ax.axvline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1)
ax.text(THR + 0.25, -0.62, "41 °C", fontsize=6.4, color=MUTED, va="bottom")
ax.axhline(ys[0] - 0.5, color=FAINT, lw=0.5, zorder=1)
ax.set_yticks(ys); ax.set_yticklabels([r[0] for r in rows], fontsize=7.2)
ax.set_xlim(28, 52); ax.set_xticks([30, 35, 40, 45, 50]); ax.set_ylim(-0.7, len(rows) - 0.3)
ax.set_xlabel("PET (°C), neighbourhood mean")
ax.text(1.01, 1.02, "measured\n≥ 41 °C", transform=ax.transAxes, ha="left", va="bottom", fontsize=6.4, color=MUTED, linespacing=1.1)
fig.legend(handles=[Patch(facecolor=AIR, edgecolor="none", label="station siting and canyon air:  warning inputs → on-site air, no radiation"),
                    Patch(facecolor=RAD, edgecolor="none", label="radiation not represented:  on-site air → measured (globe)")],
           loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=1, frameon=False, fontsize=7, handletextpad=0.5, labelspacing=0.3)
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_gap_decomp.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_gap_decomp.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
