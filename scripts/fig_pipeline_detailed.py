#!/usr/bin/env python3
"""Detailed pipeline (content specification for Table 2 and the radiation cross-section figure)

Originally the pipeline block of fig_flowcharts.py at 905d959. Two flowcharts, numbers from the 62/18 canonical set (2026-09-26).

SCS_Fig_pipeline  — methods: field reference chain (left) and the imagery-free service chain (right), with the
                    verification links between them and the one AI step (residual layer).
SCS_Fig_design    — study design: sampling → field reference → three information tiers → analysis.
Numbers: docs/SCS_숫자원장.md, loso_official_v7.json, geo_svf_80.csv (09-25), site_form_80.csv.
"""
import sys, numpy as np, matplotlib as mpl, matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon, FancyArrowPatch

OUT = sys.argv[1] if len(sys.argv) > 1 else "figout"
mpl.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
                     "figure.dpi": 200, "savefig.dpi": 1200, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED, RED, BLUE = "#1A1A1A", "#5E5E5E", "#C0504D", "#2E75B6"
DATA_F, DATA_E = "#E8EEF5", "#8FA6BF"      # input / output data (parallelogram)
PROC_F, PROC_E = "#FFFFFF", "#1A1A1A"      # processing step
VER_F, VER_E = "#FBF1E3", "#D9A25F"        # verification
AI_F, AI_E = "#FDECEA", RED                # the learned step
MM = 1 / 25.4


def box(ax, x, y, w, h, title, body, kind="proc", fs=6.4, tfs=7.0, skew=0.0, bottom=False):
    f, e = {"data": (DATA_F, DATA_E), "proc": (PROC_F, PROC_E), "ver": (VER_F, VER_E), "ai": (AI_F, AI_E)}[kind]
    if kind == "data":
        s = 2.2
        ax.add_patch(Polygon([(x + s, y), (x + w, y), (x + w - s, y + h), (x, y + h)], closed=True, facecolor=f, edgecolor=e, lw=0.8, zorder=2))
    else:
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.6", facecolor=f, edgecolor=e, lw=1.4 if kind == "ai" else 0.8, zorder=2))
    cy = y + h / 2
    tcol = {"ver": "#8A5A1B", "ai": RED}.get(kind, INK)
    if body:
        ax.text(x + w / 2, y + h - 3.0, title, ha="center", va="center", fontsize=tfs, fontweight="bold", color=tcol, zorder=3)
        if bottom: ax.text(x + w / 2, y + 1.8, body, ha="center", va="bottom", fontsize=fs, color=INK, linespacing=1.22, zorder=3)
        else: ax.text(x + w / 2, y + h - 5.6, body, ha="center", va="top", fontsize=fs, color=INK if kind != "ver" else "#5A4630", linespacing=1.22, zorder=3)
    else:
        ax.text(x + w / 2, cy, title, ha="center", va="center", fontsize=tfs, fontweight="bold", color=tcol, zorder=3)


def arrow(ax, p0, p1, color=INK, ls="-", lw=0.8, txt=None, tpos=0.5, toff=(0, 1.2), fs=6.0):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=7, color=color, lw=lw, linestyle=ls, zorder=4, shrinkA=0, shrinkB=0))
    if txt:
        x = p0[0] + (p1[0] - p0[0]) * tpos + toff[0]; y = p0[1] + (p1[1] - p0[1]) * tpos + toff[1]
        ax.text(x, y, txt, ha="center", va="center", fontsize=fs, color=color, zorder=5, bbox=dict(facecolor="white", edgecolor="none", pad=0.4))


def elbow(ax, p0, p1, ymid, color=INK, ls="-", lw=0.8):
    ax.plot([p0[0], p0[0], p1[0]], [p0[1], ymid, ymid], color=color, lw=lw, ls=ls, zorder=4, solid_capstyle="butt")
    ax.add_patch(FancyArrowPatch((p1[0], ymid), p1, arrowstyle="-|>", mutation_scale=7, color=color, lw=lw, linestyle=ls, zorder=4, shrinkA=0, shrinkB=0))


# ============================================================ Fig: pipeline (detailed)
# Every box reads from the deployed code: backend/app/services/geo.py (svf_geometric, sun_blocked_outdoor,
# street_width_geometric, dominant_wall_material), backend/vpti_core/{solar,mrt,pwi,comfort,pet_residual}.py,
# backend/app/api/routes.py::_geo_vpti_compute; field column from the manuscript §2 and VPTI_데이터처리_결과_260905.
# Accuracy numbers: 62/18 canonical set, engine state 2026-09-25 (docs/SCS_숫자원장.md; ladder table run_B / loso_official_v7).
from matplotlib.patches import Ellipse, Rectangle
W, H = 190, 214
fig = plt.figure(figsize=(W * MM, H * MM)); ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
AMB = "#8A5A1B"; GRN = "#2A7D4A"
lx, lw_ = 4, 62; rx, rw = 72, 114          # narrow field column (left), wide service column (right)
lc, rc = lx + lw_ / 2, rx + rw / 2
F, T, FS = 5.6, 7.0, 5.3

def cyl(ax, x, y, w, h, title, body, fs=FS, tfs=6.4):
    """database / external dataset (cylinder)"""
    e = 1.6
    ax.add_patch(Rectangle((x, y + e), w, h - 2 * e, facecolor=DATA_F, edgecolor="none", zorder=2))
    ax.plot([x, x], [y + e, y + h - e], color=DATA_E, lw=0.8, zorder=3); ax.plot([x + w, x + w], [y + e, y + h - e], color=DATA_E, lw=0.8, zorder=3)
    ax.add_patch(Ellipse((x + w / 2, y + e), w, 2 * e, facecolor=DATA_F, edgecolor=DATA_E, lw=0.8, zorder=2))
    ax.add_patch(Ellipse((x + w / 2, y + h - e), w, 2 * e, facecolor="#DCE5EF", edgecolor=DATA_E, lw=0.8, zorder=3))
    ax.text(x + w / 2, y + h - e - 3.0, title, ha="center", va="center", fontsize=tfs, fontweight="bold", color=INK, zorder=4)
    ax.text(x + w / 2, y + h - e - 5.4, body, ha="center", va="top", fontsize=fs, color=INK, linespacing=1.2, zorder=4)

def head(ax, x, w, y, txt, col):
    ax.add_patch(Rectangle((x, y), w, 6, facecolor=col, edgecolor="none", zorder=2))
    ax.text(x + w / 2, y + 3, txt, ha="center", va="center", fontsize=7.6, fontweight="bold", color="white", zorder=3)

top = H - 3
head(ax, lx, lw_, top - 6, "A   Field reference (80 sites)", BLUE)
head(ax, rx, rw, top - 6, "B   Imagery-free service chain — as deployed", "#3D3D3D")

# ------------------------------------------------------------------ B (right) — the engine, step by step
y = top - 10
# B1 inputs: three cylinders
h1 = 21; cw3 = (rw - 6) / 3
cyl(ax, rx, y - h1, cw3, h1, "Building register", "footprint polygons + floors\n$H$ = 3.018·floors + 0.902 m\n(no floors → 2 storeys)")
cyl(ax, rx + cw3 + 3, y - h1, cw3, h1, "Canopy height raster", "Meta/WRI 1 m → 30 m cells\neffective screening (1 − τ),\nτ = 0.35")
cyl(ax, rx + rw - cw3, y - h1, cw3, h1, "Gridded weather", "$T_a$, RH, wind speed and\ndirection at the site's cell;\ncloud fraction")
yB1 = y - h1
# B2 geometry (left, wide) and solar (right, narrow) side by side
y = yB1 - 5; h2 = 28; gw = 78; sx = rx + 81; sw = rw - 81
box(ax, rx, y - h2, gw, h2, "Geometry at the pedestrian (eye height 1.5 m)", "snap the point outside buildings and to the street centre line (≤ 30 m)\nray casting every 2° in azimuth to the building outlines → horizon angle β(az);\ncanopy above the roofline screens (1 − τ);  SVF = 1 − mean sin²β (Steyn 1980); BVF\nstreet width and H/W from the narrowest opposing pair (5° rays, ≤ 60 m)\nsun blocked?  horizon[sun azimuth] > sun elevation → direct-beam shade 0/1\n(buildings only; tree shade off in Korea)\ndominant wall material → wall albedo and emissivity (height / distance weighted)", "proc", fs=FS, tfs=T)
box(ax, sx, y - h2, sw, h2, "Solar position and irradiance", "pvlib: NREL SPA position;\nIneichen–Perez clear sky\n→ Kasten & Czeplak (1980)\ncloud attenuation, a 0.75, b 3.4\n→ Erbs et al. (1982) diffuse\nfraction → GHI, DNI, DHI\nbeam turbidity 0.6 (Busan haze)", "proc", fs=FS, tfs=6.4)
for k in range(2):
    arrow(ax, (rx + k * (cw3 + 3) + cw3 / 2, yB1), (rx + k * (cw3 + 3) + cw3 / 2, y))
arrow(ax, (rx + rw - cw3 / 2, yB1), (rx + rw - cw3 / 2, y))
yB2 = y - h2; yB3 = yB2; h3 = h2; gc = rx + gw / 2; sc = sx + sw / 2
# B4 pedestrian wind
y = yB3 - 5; h4 = 13
box(ax, rx, y - h4, gw, h4, "Pedestrian wind at 1.5 m (PWI)", "reference wind → von Mises weighting of wind direction against the street axis (κ 2)\n× exponential partial coefficients for sky opening (SVF), building and vegetation\nscreening → $u_p$   (bypassed when an on-site wind reading is supplied)", "proc", fs=FS, tfs=T)
arrow(ax, (gc, yB3), (gc, y))
yB4 = y - h4
# B5 ground surface energy balance
y = yB4 - 5; h5 = 20
box(ax, rx, y - h5, rw, h5, "Ground surface energy balance → $T_s$", "(1 − α)·S↓·(1 − f$_{stor}$) + ε$_g$·(L↓ − σT$_s^4$) − h$_c$·(T$_s$ − T$_a$) = 0,  solved by Newton iteration\nS↓ = DNI·sinβ·shade + DHI·SVF, with a 2 h exponential lag and a 40 W m$^{-2}$ storage release;  L↓ = σT$_a^4$·[SVF·ε$_{sky}$ + (1 − SVF)·ε$_{env}$]\nh$_c$ = a + b·$u_p$;  α, ε$_g$ from Sentinel-2 surface class (default 0.15 / 0.95);  shaded ground keeps 0.74 of its sunlit rise;  vegetated share → $T_a$", "proc", fs=FS, tfs=T)
arrow(ax, (gc, yB4), (gc, y)); arrow(ax, (sc, yB2), (sc, y))
yB5 = y - h5
# B6 six-direction radiation → Tmrt
y = yB5 - 5; h6 = 22
box(ax, rx, y - h6, rw, h6, "Six-direction radiation budget → $T_{mrt}$  (VDI 3787 Part 2)", "S$_{str}$ = a$_k$·[ f$_p$(β)·DNI·shade + Σ$_i$ F$_i$·(DHI·ψ$_{sky,i}$ + α·GHI·ψ$_{grd,i}$) ] + ε$_p$·Σ$_i$ F$_i$·(L$_{sky}$·ψ$_{sky,i}$ + L$_{surf}$·ψ$_{grd,i}$)\na$_k$ 0.7, ε$_p$ 0.97; F = 0.22 × 4 sides + 0.06 up + 0.06 down; f$_p$(β) = 0.308·cos[β(0.998 − β²/50000)] (Fanger 1970)\nψ$_{sky}$ = SVF up, SVF/2 sideways; L$_{sky}$ = ε$_{sky}$σT$_a^4$, ε$_{sky}$ by Brunt (1932) + Crawford & Duchon (1999); L$_{surf}$ = ε$_g$σT$_s^4$\nsunlit walls reflect short-wave (wall albedo × 0.45 sunlit fraction);  $T_{mrt}$ = (S$_{str}$ / ε$_p$σ)$^{1/4}$ − 273.15", "proc", fs=FS, tfs=T)
arrow(ax, (rc, yB5), (rc, y))
yB6 = y - h6
# B7 PET physics
y = yB6 - 5; h7 = 13
box(ax, rx, y - h7, rw, h7, "PET, physics  —  MAE 6.6 °C against the field reference (gridded inputs, all 80 sites)", "pythermalcomfort pet_steady (Höppe MEMI, VDI 3787 Part 2): $T_a$, $T_{mrt}$, $u_p$, RH; 1.37 met, 0.5 clo, standing\n29 of 63 extreme-heat sites detected", "proc", fs=FS, tfs=T)
arrow(ax, (rc, yB6), (rc, y))
yB7 = y - h7
# B8 residual layer
y = yB7 - 5; h8 = 20
box(ax, rx, y - h8, rw, h8, "Residual correction layer — the only learned step", "five predictors, standardised: sun or shade, geometric SVF, $T_a$, wind, PET$_{phys}$ — no coordinates\nridge regression, α = 20, trained on the 80 field sites against the reference PET;  Δ = residual estimate\nconfidence c from the standardised distance to the training set: c = 1 within RMS-z 1.5, falling to 0 at 4 (physics fallback)\nPET = PET$_{phys}$ + c·Δ;  evaluated leave-one-neighbourhood-out (the whole neighbourhood held out)", "ai", fs=FS, tfs=T)
arrow(ax, (rc, yB7), (rc, y))
yB8 = y - h8
# B9 reported
y = yB8 - 5; h9 = 13
box(ax, rx + 12, y - h9, rw - 24, h9, "Reported PET", "MAE 1.8 °C, bias −0.0 °C, $r$ 0.82 against the field reference (leave-one-neighbourhood-out)\nall 63 extreme-heat sites (PET ≥ 41 °C) detected", "data", fs=FS, tfs=T)
arrow(ax, (rc, yB8), (rc, y))
yB9 = y - h9

# ------------------------------------------------------------------ A (left) — field reference
y = top - 10
def lbox(y, h, title, body, kind, fs=FS):
    box(ax, lx, y - h, lw_, h, title, body, kind, fs=fs, tfs=6.6); return y - h
yA0 = lbox(y, 19, "Campaign", "82 site visits, five neighbourhoods (LCZ 1–5)\n20, 23, 25, 26 August 2026, 11:19–15:06\nsolar elevation 46–67°; $T_a$ 32.0–38.7 °C\n80 sites enter the analysis (one panorama, one\nglobe reading missing)", "data")
y = yA0 - 5
yA1 = lbox(y, 19, "Instruments at 1.5 m, same minute", "globe thermometer Ø 0.05 m, ε 0.95 → $T_g$\nweather meter → $T_a$, RH, wind\n360° camera → two fisheye images\nthermal camera → 328 radiometric images\n(324 decoded), wall and pavement", "data")
arrow(ax, (lc, yA0), (lc, y))
y = yA1 - 5
yA2 = lbox(y, 27, "Panorama processing", "stitch with per-image attitude correction;\nproject to 4 horizontal + 4 tilted + nadir faces\nSegFormer-B0 (ADE20K) → sky, tree, building,\nresidual (median 0.02); 1° zenith–azimuth grid,\nSteyn (1980) sin 2θ hemispheric integration\n→ SVF, TVF, BVF, sum = 1\none pose failure → 79 sites", "proc")
arrow(ax, (lc, yA1), (lc, y))
y = yA2 - 5
yA3 = lbox(y, 19, "Sun or shade at the globe", "two independent readers on the same\npanorama, 76 of 80 agreed; disagreements\nsettled by the globe rise; cloud-diffuse = shade\n→ 62 sunlit, 18 shaded\ncheck: globe rise 21.7 K sunlit vs 9.4 K shaded", "proc")
arrow(ax, (lc, yA2), (lc, y))
y = yA3 - 5
yA4 = lbox(y, 21, "Reference $T_{mrt}$ and PET", "ISO 7726 forced convection:\n$T_{mrt}$ = [($T_g$+273)$^4$ + 1.1·10$^8$·$v^{0.6}$/(εD$^{0.4}$)·($T_g$−$T_a$)]$^{1/4}$ − 273\nPET: pet_steady, 1.4 met, 0.9 clo, sitting\n→ 35.9–53.8 °C; 63 of 80 at ≥ 41 °C", "proc")
arrow(ax, (lc, yA3), (lc, y))
y = yA4 - 5
yA5 = lbox(y, 17, "Field reference", "view factors (79) · sun or shade (62/18)\nsurface temperature · reference PET (80)\nthe reference for every comparison;\nnot an input to the service", "data")
arrow(ax, (lc, yA4), (lc, y))

# ------------------------------------------------------------------ links between the columns
# (1) verification: geometric SVF against panorama SVF
yv = yB3 + h3 / 2
ax.plot([lx + lw_, rx], [yA2 + 13.5, yA2 + 13.5], color=AMB, lw=0.8, ls=(0, (3, 2)), zorder=1) if False else None
ax.annotate("", xy=(rx, yB3 + 4), xytext=(lx + lw_, yA2 + 4), arrowprops=dict(arrowstyle="-|>", color=AMB, lw=0.8, ls=(0, (3, 2)), mutation_scale=7, connectionstyle="arc3,rad=0"), zorder=1)
ax.text(rx + gw / 2, yB2 + 2.2, "SVF verified against the panorama SVF at the 79 field sites: MAE 0.112, bias +0.046, r 0.70", ha="center", va="bottom", fontsize=5.0, style="italic", color=AMB, zorder=5)
# (2) on-site inputs into the residual layer (red, solid)
ax.plot([lc, lc, rx], [yA5, yB8 + h8 / 2, yB8 + h8 / 2], color=RED, lw=1.0, zorder=4)
ax.add_patch(FancyArrowPatch((rx - 3, yB8 + h8 / 2), (rx, yB8 + h8 / 2), arrowstyle="-|>", mutation_scale=7, color=RED, lw=1.0, zorder=4, shrinkA=0, shrinkB=0))
ax.text(lc + 2, yB8 + h8 / 2 + 1.2, "on-site sun or shade, $T_a$, wind;\nreference PET = training target", ha="left", va="bottom", fontsize=5.3, style="italic", color=RED, linespacing=1.2)
# (3) comparison of the physics PET and the reported PET against the reference (dashed amber, from the field reference box)
ax.annotate("", xy=(rx, yB7 + h7 / 2), xytext=(lx + lw_, yA5 + 8.5), arrowprops=dict(arrowstyle="-|>", color=AMB, lw=0.8, ls=(0, (3, 2)), mutation_scale=7), zorder=1)
ax.text(lx + lw_ + 1.5, yA5 + 10.5, "compared", ha="left", va="bottom", fontsize=5.0, style="italic", color=AMB, zorder=5)

# ------------------------------------------------------------------ legend
ly = 4.5
for x, kind, lab in [(6, "data", "measured or reported data"), (52, "proc", "computation"), (86, "ai", "learned step")]:
    box(ax, x, ly - 2.4, 11, 4.8, "", None, kind); ax.text(x + 13, ly, lab, va="center", fontsize=6.0)
cyl(ax, 118, ly - 2.4, 11, 4.8, "", "", fs=1, tfs=1); ax.text(131, ly, "external dataset", va="center", fontsize=6.0)
ax.plot([158, 163], [ly, ly], color=RED, lw=1.0); ax.text(164.5, ly, "field data in", va="center", fontsize=6.0)
ax.plot([158, 163], [ly - 3.2, ly - 3.2], color=AMB, lw=0.8, ls=(0, (3, 2))); ax.text(164.5, ly - 3.2, "compared", va="center", fontsize=6.0)
for e in ("pdf", "eps", "png", "svg"):
    fig.savefig(f"{OUT}/SCS_Fig_pipeline_detailed.{e}", bbox_inches="tight", pad_inches=0.03)
plt.close(fig)

print("saved", OUT)
