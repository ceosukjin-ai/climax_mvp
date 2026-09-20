#!/usr/bin/env python3
"""ClimaX / VPTI processing chain — corrected against the deployed code, 2026-09-13.

Design: four parallel measurement branches, each a single unbroken vertical
line (input -> processing -> verification -> output), merging once into the
deterministic chain. No branching diamonds and no dangling "Excluded" boxes:
what a gate rejected is stated as a count inside the verification band, which
carries the same information without a single crossing line.

Corrections against the previous version of this chart:
  PET parameters   "1.4 met, 0.9 clo"  ->  1.37 met, 0.5 clo (summer),
                   from backend/vpti_core/config.py. There is no 0.9.
  PET source       Hoeppe (1999) alone -> VDI 3787 Part 2 / Hoeppe MEMI,
                   implemented as pythermalcomfort.pet_steady.
  Residual layer   "place axis and person axis; capped, risk-advancing only"
                   was never implemented. The deployed layer is ridge,
                   alpha = 20, five standardised predictors, no coordinates,
                   PET = PET_phys + c*delta. It is the paper's AI component,
                   so it sits in the main flow, not in a dashed aside.
  Surface temp     one surface -> pavement and wall separated, +9.3 K.
  Mean radiant T   globe reference stated explicitly.
  Buildings        307,429 -> 305,621 polygons with attributes (Busan).
  Removed          gradient-boosting learning layer, spatial-block CV, SHAP:
                   their labels derive from Street View panoramas, which the
                   platform terms exclude from training AND validation.
  Column 4         the proposed acceptance threshold has no agreed, dated
                   record, so the measured agreement is reported as a result
                   rather than drawn as a pre-registered gate.
"""
from __future__ import annotations
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Rectangle, FancyArrow

mpl.rcParams.update({
    # 2026-09-20: 논문 안에서 그림 폰트를 하나로 — 형태지표 4패널·그림 1 과 같은 Times.
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "figure.dpi": 200, "savefig.dpi": 600,
})
MIN_PT = 7.0        # Elsevier 최소 글자 크기 — 이 아래로 줄이지 않는다
INK, MUTED = "#1A1A1A", "#6B6B6B"
DATA, CHECK, FINAL = "#EAF0F7", "#F6EEE3", "#DCE8F4"
RULE = "#3C3C3C"
LW = 0.7
MM = 1 / 25.4

fig, ax = plt.subplots(figsize=(180 * MM, 214 * MM))
ax.set_xlim(-3.2, 103.2); ax.set_ylim(0, 119)
ax.axis("off")
fig.subplots_adjust(left=0.004, right=0.996, bottom=0.004, top=0.996)

COL = [13.5, 38.5, 63.5, 88.5]
W = 23.6


_TEXTS = []          # (artist, box_left, box_right, box_bottom, box_top)


def lines(x, y, rows, fs=7.0, lead=None, color=INK, w=None, h=None):
    """행들을 그리고, 틀 경계와 함께 기록해 둔다(뒤에서 자동 축소 검사)."""
    lead = lead or fs * 0.205
    y0 = y + (len(rows) - 1) * lead / 2
    drawn = []
    for i, (s, bold, col) in enumerate(rows):
        t = ax.text(x, y0 - i * lead, s, ha="center", va="center",
                    fontsize=fs + (0.6 if bold else 0),
                    fontweight="bold" if bold else "normal",
                    color=col or color, zorder=6)
        drawn.append(t)
    if w is not None:
        _TEXTS.append((drawn, x, y, w, h, lead))
    return drawn


def fit_all(pad_x=1.6, pad_y=1.0):
    """틀 밖으로 삐져나온 글자를 찾아 폰트를 줄인다. 남으면 경고를 찍는다."""
    fig.canvas.draw()
    inv = ax.transData.inverted()
    bad = []
    for drawn, cx, cy, w, h, lead in _TEXTS:
        for _ in range(14):
            over = False
            for t in drawn:
                bb = t.get_window_extent(fig.canvas.get_renderer())
                (x0, y0), (x1, y1) = inv.transform([[bb.x0, bb.y0], [bb.x1, bb.y1]])
                if (x1 - x0) > (w - pad_x):
                    over = True
            if not over:
                break
            if min(t.get_fontsize() for t in drawn) - 0.15 < MIN_PT:
                break
            for t in drawn:
                t.set_fontsize(t.get_fontsize() - 0.15)
            fig.canvas.draw()
        # 높이 검사
        n = len(drawn)
        if (n - 1) * lead + 2.2 > h - pad_y:
            bad.append(("height", drawn[0].get_text()))
        for t in drawn:
            bb = t.get_window_extent(fig.canvas.get_renderer())
            (x0, y0), (x1, y1) = inv.transform([[bb.x0, bb.y0], [bb.x1, bb.y1]])
            if (x1 - x0) > (w - pad_x) + 1e-6:
                bad.append(("width", t.get_text()))
    for k, s in bad:
        print(f"  !! {k} overflow: {s}")
    if not bad:
        print("  text fits: OK")


def rows_of(title, *rest, sub=None):
    r = [(title, True, None)]
    r += [(s, False, None) for s in rest]
    if sub:
        r += [(s, False, MUTED) for s in sub]
    return r


def para(cx, cy, w, h, rows, fc=DATA, fs=7.0, skew=2.2):
    ax.add_patch(Polygon([(cx - w/2 + skew, cy - h/2), (cx + w/2 + skew, cy - h/2),
                          (cx + w/2 - skew, cy + h/2), (cx - w/2 - skew, cy + h/2)],
                         closed=True, facecolor=fc, edgecolor=RULE, lw=LW, zorder=4))
    lines(cx, cy, rows, fs, w=w - 2 * skew, h=h)
    return dict(cx=cx, cy=cy, w=w, h=h)


def box(cx, cy, w, h, rows, fc="white", fs=7.0, ec=RULE):
    ax.add_patch(Rectangle((cx - w/2, cy - h/2), w, h, facecolor=fc,
                           edgecolor=ec, lw=LW, zorder=4))
    lines(cx, cy, rows, fs, w=w, h=h)
    return dict(cx=cx, cy=cy, w=w, h=h)


def check(cx, cy, w, h, rows, fs=7.0):
    """검증 띠 — 왼쪽에 굵은 세로선을 둬서 처리 단계와 구분한다."""
    ax.add_patch(Rectangle((cx - w/2, cy - h/2), w, h, facecolor=CHECK,
                           edgecolor="none", zorder=3))
    ax.plot([cx - w/2, cx - w/2], [cy - h/2, cy + h/2], color="#C08A4A",
            lw=1.6, zorder=5, solid_capstyle="butt")
    lines(cx, cy, rows, fs, w=w, h=h)
    return dict(cx=cx, cy=cy, w=w, h=h)


def arrow(x, y0, y1):
    ax.plot([x, x], [y0, y1 + 1.0], color=RULE, lw=LW, zorder=3)
    ax.add_patch(FancyArrow(x, y1 + 1.0, 0, -1.0, width=0, head_width=0.75,
                            head_length=0.95, length_includes_head=True,
                            color=RULE, lw=0, zorder=5))


def link(a, b):
    arrow(a["cx"], a["cy"] - a["h"]/2, b["cy"] + b["h"]/2)


Y_IN, Y_PR, Y_CK, Y_OU = 112.0, 100.0, 88.0, 75.5

IN = [rows_of("360° panoramas", "91 panoramas at 80 sites",
              "pole-mounted at 1.5 m"),
      rows_of("Thermal images", "328 radiometric images",
              "emissivity 0.93–0.95"),
      rows_of("360° panoramas", "the same panoramas",
              "read for the direct beam"),
      rows_of("Buildings and canopy", "305,621 polygons",
              "Meta/WRI canopy height")]
PR = [rows_of("Semantic segmentation", "sky, tree, building, ground",
              "Steyn 36-ring integration"),
      rows_of("Temperature retrieval", "raw radiance and the",
              "displayed value (OCR)"),
      rows_of("Sun or shade reading", "one observer, all 80 sites",
              "cloud-diffuse counted as no beam"),
      rows_of("Geometric view factors", "national building polygons",
              "joined to register heights")]
CK = [[("Verification", True, MUTED), ("difference from the analytic", False, None),
       ("solution ≤ 0.003", False, None)],
      [("Verification", True, MUTED), ("radiance linear in T$^4$", False, None),
       ("324 of 328 images passed", False, None)],
      [("Verification", True, MUTED), ("globe-temperature rise separates", False, None),
       ("the groups: 21.8 K sunlit against", False, None),
       ("9.8 K shaded, $p$ < 0.001;", False, None),
       ("two sites disagreed", False, None)],
      [("Verification", True, MUTED), ("geometric SVF vs. 79 field", False, None),
       ("panoramas: MAE 0.108,", False, None),
       ("bias +0.022, r 0.69", False, None)]]
OU = [rows_of("View factors", "sky, tree and building"),
      rows_of("Surface temperature", "pavement and wall separated",
              "mean offset +9.3 K"),
      rows_of("Sun or shade", "at the reading time"),
      rows_of("View factors", "without street view")]

for i in range(4):
    a = para(COL[i], Y_IN, W, 9.2, IN[i])
    b = box(COL[i], Y_PR, W, 9.2, PR[i])
    c = check(COL[i], Y_CK, W, 9.8, CK[i])
    d = para(COL[i], Y_OU, W, 9.2, OU[i])
    link(a, b); link(b, c); link(c, d)

# ------------------------------------------------------------- merge bus
BUS = 67.0
eb = box(50.0, 60.0, 78.0, 9.0,
         rows_of("Energy balance model",
                 "deterministic solution at the pedestrian location;",
                 "coefficients fixed at the deployed state"), fs=7.4)
for i in range(4):
    ax.plot([COL[i], COL[i]], [Y_OU - 4.6, BUS], color=RULE, lw=LW, zorder=3)
ax.plot([COL[0], COL[3]], [BUS, BUS], color=RULE, lw=LW, zorder=3)
arrow(50.0, BUS, eb["cy"] + eb["h"]/2)

mrt = para(29.0, 46.5, 34.0, 8.6,
           rows_of("Mean radiant temperature", "ISO 7726, globe reference"), fs=7.2)
pet = para(71.0, 46.5, 37.0, 10.6,
           rows_of("PET", "physiological equivalent temperature",
                   "VDI 3787 Part 2 / Höppe MEMI",
                   "1.37 met, 0.5 clo (summer)"), fs=7.2)
ax.plot([50.0, 50.0], [eb["cy"] - eb["h"]/2, 54.0], color=RULE, lw=LW, zorder=3)
ax.plot([50.0, 29.0], [54.0, 54.0], color=RULE, lw=LW, zorder=3)
arrow(29.0, 54.0, mrt["cy"] + mrt["h"]/2)
ax.plot([mrt["cx"] + mrt["w"]/2 - 1.4, pet["cx"] - pet["w"]/2 - 0.9],
        [46.5, 46.5], color=RULE, lw=LW, zorder=3)
ax.add_patch(FancyArrow(pet["cx"] - pet["w"]/2 - 0.9, 46.5, 0.95, 0, width=0,
                        head_width=0.75, head_length=0.95,
                        length_includes_head=True, color=RULE, lw=0, zorder=5))

res = box(50.0, 32.0, 80.0, 11.4,
          rows_of("Residual correction layer",
                  "ridge regression, α = 20;  five standardised predictors",
                  "sun, geometric SVF, T$_a$, pedestrian wind, engine PET"
                  ";  no coordinates",
                  "PET = PET$_{phys}$ + Δ"), fs=7.3)
ax.plot([71.0, 71.0], [pet["cy"] - pet["h"]/2, 39.5], color=RULE, lw=LW, zorder=3)
ax.plot([71.0, 50.0], [39.5, 39.5], color=RULE, lw=LW, zorder=3)
arrow(50.0, 39.5, res["cy"] + res["h"]/2)

rep = para(50.0, 19.0, 54.0, 9.6,
           rows_of("Reported PET",
                   "leave-one-neighbourhood-out MAE 1.9 °C",
                   "all 63 extreme-heat cases (PET ≥ 41 °C) detected"),
           fc=FINAL, fs=7.4)
link(res, rep)

# ---------------------------------------------------------------- legend
LY = 8.2
ax.add_patch(Polygon([(7.4, LY - 1.5), (16.6, LY - 1.5), (15.8, LY + 1.5),
                      (6.6, LY + 1.5)], closed=True, facecolor=DATA,
                     edgecolor=RULE, lw=LW))
ax.text(18.4, LY, "input or output data", fontsize=6.6, va="center", color=INK)
ax.add_patch(Rectangle((40.0, LY - 1.5), 9.2, 3.0, facecolor="white",
                       edgecolor=RULE, lw=LW))
ax.text(51.0, LY, "processing step", fontsize=6.6, va="center", color=INK)
ax.add_patch(Rectangle((68.0, LY - 1.5), 9.2, 3.0, facecolor=CHECK,
                       edgecolor="none"))
ax.plot([68.0, 68.0], [LY - 1.5, LY + 1.5], color="#C08A4A", lw=1.6,
        solid_capstyle="butt")
ax.text(79.0, LY, "verification", fontsize=6.6, va="center", color=INK)

ax.text(50.0, 2.6,
        "All values verified against the deployed engine, 2026-09-20. The "
        "street-view learning layer is not part of this pipeline: its labels "
        "derive from Street View\npanoramas, which the platform terms exclude "
        "from training and validation alike. The imagery-free view factors "
        "come from building geometry instead.",
        fontsize=6.2, color=MUTED, ha="center", va="center", linespacing=1.6)

fit_all()
fig.savefig("/tmp/claude-0/SCS_Fig_flowchart.pdf", bbox_inches="tight", pad_inches=0.03)
fig.savefig("/tmp/claude-0/SCS_Fig_flowchart.png", bbox_inches="tight", pad_inches=0.03)
print("saved")
