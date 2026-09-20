#!/usr/bin/env python3
"""The information ladder: what each added input buys, at 80 measured sites.

Every panel adds exactly ONE thing to the panel before it, so the step change is
attributable. Scored against the same 80 field measurements (63 of them at or
above PET 41 degC, the extreme-heat-stress threshold).

  (a) Tier 1   official gridded/station weather, no radiation term at all
  (b) Tier 2   + radiation from street-view imagery            run_A
  (c) Tier 3   + radiation from buildings + satellite (no imagery)  run_B
  (d)          + weather measured at the site                  run_C
  (e)          + sun/shade observed at the site                run_E
  (f)          + recalibrated physics and the ridge residual, leave-one-site-out

The residual layer is the DEPLOYED model: ridge, alpha = 20, five standardised
predictors (sun, geometric SVF, air temperature, pedestrian wind, engine PET).
The HistGradientBoosting variant in the same file is a comparison only - it is
not deployed and must not be the number in the figure.

Tier 1 definition (previously undocumented, hence unreproducible):
    PET(tdb = Ta_official, tr = Ta_official, v = v_official, rh = RH_official)
i.e. the official forecast/observation the public warning system reports, with
the mean radiant temperature set equal to air temperature — no sky view, no
surfaces, no sun. This is what a heat warning tells a pedestrian today.

Run A-E are read from tier3_engine_output_80_v6.csv, which despite its name is
the NEWEST file (2026-09-13; v7 is 2026-09-10). The column `run_A_PET` carries
DIFFERENT values in v6, v7 and tier3_loso_predictions.csv - up to 11 degC apart
at the same site. Only v6 is used here. Do not mix the files.
"""
from __future__ import annotations
import csv
import sys
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

sys.path.insert(0, "eng/scripts/engine_aug16")
from vpti_core.comfort import compute_pet          # noqa: E402

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "font.size": 7.5, "axes.labelsize": 8, "axes.titlesize": 8,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.7, "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "xtick.major.size": 2.6, "ytick.major.size": 2.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
BLUE, ORANGE = "#1F6FB2", "#D9A441"
MARK = "#C2410C"
CONCERN = {"c": ("\u2462", "interpretability"),
           "d": ("\u2463", "field validation"),
           "f": ("\u2465", "generalisation")}
INK, MUTED, GRID, RED = "#1A1A1A", "#5A5A5A", "#DCDCDC", "#B3261E"
MM = 1 / 25.4

BASE = "/mnt/user-data/uploads/climax_mvp/data"
R = list(csv.DictReader(open("tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")))
LO = {r["측정ID"]: r for r in
      csv.DictReader(open(f"{BASE}/tier3_loso_predictions.csv", encoding="utf-8-sig"))}
near = np.array([r["앱_유효"] == "유효" for r in R])

pet = np.array([float(r["PET"]) for r in R])
hot = pet >= 41.0

# --- (a) Tier 1, reconstructed from the official weather the app was served
tier1 = np.array([compute_pet(float(r["앱_입력_기온"]), float(r["앱_입력_기온"]),
                              float(r["앱_입력_풍속"]), float(r["앱_입력_습도"])).value
                  for r in R])


def c(k):
    return np.array([float(r[k]) for r in R])


LO7 = {r["측정ID"]: r for r in csv.DictReader(open("/mnt/user-data/uploads/climax_mvp/data/loso_official_v7_sites.csv", encoding="utf-8-sig"))}
phys = np.array([float(LO7[r["측정ID"]]["물리_PET"]) for r in R])
ai = np.array([float(LO7[r["측정ID"]]["물리+AI_LONO_PET"]) for r in R])

PANELS = [
    ("a", "Tier 1", "Official weather only,\nno radiation term", tier1, None),
    ("b", "Tier 2", "+ radiation from\nstreet-view imagery", c("run_A_PET"), None),
    ("c", "Tier 3", "+ radiation from buildings\n+ satellite (no imagery)", c("run_B_PET"), None),
    ("d", "Tier 3 +", "+ weather measured\nat the site", c("run_C_PET"), None),
    ("e", "Tier 3 ++", "+ sun / shade at the site\n(photo-verified label)", c("run_E_PET"), None),
    ("f", "Deployed engine", "+ recalibrated physics\n+ ridge residual (neighbourhood held out)", ai, phys),
]

fig, axes = plt.subplots(2, 3, figsize=(180 * MM, 128 * MM))
fig.subplots_adjust(left=0.065, right=0.99, bottom=0.185, top=0.835,
                    wspace=0.22, hspace=0.92)
LOW, HIGH = 27.0, 62.0

for k, (tg, name, sub, v, extra) in enumerate(PANELS):
    ax = axes[k // 3][k % 3]
    ax.grid(True, color=GRID, lw=0.5, zorder=0)
    ax.set_axisbelow(True)
    # 놓친 극심 구역 — 실측은 41 이상인데 보고값은 41 미만
    ax.add_patch(plt.Rectangle((41, LOW), HIGH - 41, 41 - LOW, facecolor="#FBEFEF",
                               edgecolor="none", zorder=1))
    ax.plot([LOW, HIGH], [LOW, HIGH], color=INK, lw=0.8, zorder=3)
    ax.axhline(41, color=RED, lw=0.8, ls=(0, (4, 2)), zorder=2)
    ax.axvline(41, color=RED, lw=0.8, ls=(0, (4, 2)), zorder=2)
    ax.scatter(pet[near], v[near], s=20, facecolor=BLUE, edgecolor="white", lw=0.4,
               marker="o", zorder=5)
    ax.scatter(pet[~near], v[~near], s=22, facecolor=ORANGE, edgecolor="white", lw=0.4,
               marker="D", zorder=5)

    e = v - pet
    det = int(((v >= 41) & hot).sum())
    # 통계는 패널 밖 위쪽에 둔다 — 안에 두면 어느 모서리든 점을 가린다 (2026-09-13)
    ax.text(0.0, 1.035,
            f"MAE {np.abs(e).mean():.1f} °C     bias {e.mean():+.1f} °C     "
            f"r {np.corrcoef(v, pet)[0, 1]:.2f}\n"
            f"extreme heat detected {det} / {int(hot.sum())}  "
            f"({100*det/hot.sum():.0f} %)",
            transform=ax.transAxes, va="bottom", ha="left", fontsize=6.9,
            color=INK, linespacing=1.5)
    if extra is not None:
        ee = extra - pet
        dd = int(((extra >= 41) & hot).sum())
        ax.text(0.0, -0.37,
                f"Physics alone, before the ridge residual:\n"
                f"MAE {np.abs(ee).mean():.1f} °C, extreme {dd}/{int(hot.sum())}.",
                transform=ax.transAxes, va="top", ha="left", fontsize=6.5,
                color=MUTED, linespacing=1.4)

    ax.set_xlim(LOW, HIGH); ax.set_ylim(LOW, HIGH); ax.set_aspect("equal")
    ax.set_xticks([30, 40, 50, 60]); ax.set_yticks([30, 40, 50, 60])
    if k // 3 == 1:
        ax.set_xlabel("Measured PET (°C)")
    if k % 3 == 0:
        ax.set_ylabel("Reported PET (°C)")
    ax.text(0.0, 1.545, f"{tg}", transform=ax.transAxes, fontsize=9.5,
            fontweight="bold", va="top", ha="left", color=INK)
    ax.text(0.085, 1.555, name, transform=ax.transAxes, fontsize=8.2,
            fontweight="bold", va="top", ha="left", color=INK)
    ax.text(0.085, 1.435, sub, transform=ax.transAxes, fontsize=7.0, va="top",
            ha="left", color=MUTED, linespacing=1.35)
    # 2026-08 투고전략에서 지적된 세 약점이 각각 어느 칸에서 해소되는지 표시한다.
    if tg in CONCERN:
        num, lab = CONCERN[tg]
        ax.text(1.0, 1.74, f"{num}  {lab}", transform=ax.transAxes,
                fontsize=7.6, fontweight="bold", va="top", ha="right",
                color=MARK, family="DejaVu Sans")

axes[0][0].text(41.8, LOW + 1.0, "missed extreme", color=RED, fontsize=6.4,
                va="bottom", ha="left")

fig.legend(handles=[
    Line2D([], [], color=BLUE, marker="o", ls="none", ms=5,
           label=f"Street view near the site (n = {int(near.sum())})"),
    Line2D([], [], color=ORANGE, marker="D", ls="none", ms=5,
           label=f"No street view: panorama 43–110 m away, or failed "
                 f"(n = {int((~near).sum())})"),
    Line2D([], [], color=INK, lw=0.8, label="1:1"),
    Line2D([], [], color=RED, ls=(0, (4, 2)), lw=0.9,
           label="Extreme heat stress threshold (PET ≥ 41 °C)")],
    loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(0.5, -0.055),
    handlelength=1.5, columnspacing=2.4, labelspacing=0.35, fontsize=7.2)

fig.text(0.5, -0.085,
         "\u2462 the imagery-free pathway carries no learning step, so every term is a "
         "named physical quantity.   \u2463 inputs measured at the site, swapped one at a "
         "time,\nattribute the error to a specific input.   \u2465 a whole neighbourhood is "
         "held out, and the correction reverts to physics outside the training domain.",
         ha="center", va="top", fontsize=6.8, color="#5A5A5A", linespacing=1.65,
         family="DejaVu Sans")

fig.savefig("fig/out_v7/SCS_Fig_tier_ladder.pdf", bbox_inches="tight", pad_inches=0.02)
fig.savefig("fig/out_v7/SCS_Fig_tier_ladder.png", bbox_inches="tight", pad_inches=0.02)
print("saved")
for tg, name, sub, v, extra in PANELS:
    e = v - pet
    det = int(((v >= 41) & hot).sum())
    print(f"  ({tg}) {name:16} MAE {np.abs(e).mean():5.2f}  bias {e.mean():+6.2f}  "
          f"r {np.corrcoef(v, pet)[0,1]:5.2f}  extreme {det}/{int(hot.sum())}")
