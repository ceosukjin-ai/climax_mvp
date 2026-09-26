#!/usr/bin/env python3
"""Two flowcharts, numbers from the 62/18 canonical set (2026-09-26).

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


# ============================================================ Fig: pipeline
# The 09-20 flowchart kept (four imagery/geometry columns, wide stack below), corrected: no field data enter the
# energy balance; the field imagery columns end in the analysis, and only sun/shade (+ Ta, wind) enter the residual layer.
W, H = 190, 176
fig = plt.figure(figsize=(W * MM, H * MM)); ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
cw, bh, gap = 42, 15, 6; step = bh + gap
xs = [4, 51, 98, 145]; xc = [x + cw / 2 for x in xs]
y0 = H - 4 - bh; ys = [y0 - i * step for i in range(8)]; y0, y1, y2, y3, y4, y5, y6, y7 = ys
F, T = 5.8, 7.0
# ---- inputs
box(ax, xs[0], y0, cw, bh, "360° panoramas", "one per site at 80 sites,\npole-mounted at 1.5 m", "data", fs=F, tfs=T)
box(ax, xs[1], y0, cw, bh, "Thermal images", "323 radiometric images,\nwall and pavement at the sites", "data", fs=F, tfs=T)
box(ax, xs[2], y0, cw, bh, "360° panoramas", "the same panoramas,\nread for the direct beam", "data", fs=F, tfs=T)
box(ax, xs[3], y0, cw, bh, "Buildings and canopy", "national register polygons and floors;\nMeta/WRI canopy height (30 m)", "data", fs=F, tfs=T)
# ---- processing
box(ax, xs[0], y1, cw, bh, "Semantic segmentation", "SegFormer-B0 (ADE20K): sky, tree, building;\nSteyn (1980) sin 2θ integration, 1° grid", "proc", fs=F, tfs=T)
box(ax, xs[1], y1, cw, bh, "Temperature retrieval", "radiometric values read from the\nimage record; wall and pavement separated", "proc", fs=F, tfs=T)
box(ax, xs[2], y1, cw, bh, "Sun or shade reading", "two readers, all 80 sites;\ncloud-diffuse counted as no beam", "proc", fs=F, tfs=T)
box(ax, xs[3], y1, cw, bh, "Geometric view factors", "ray casting in 5° steps to the building\nskyline; canopy as a transmissive layer", "proc", fs=F, tfs=T)
# ---- verification
box(ax, xs[0], y2, cw, bh, "Verification", "one pose failure → 79 sites;\nresidual class median 0.02", "ver", fs=F, tfs=T)
box(ax, xs[1], y2, cw, bh, "Verification", "pavement 35.3–66.4 °C at the 80 sites,\nordered with the globe reading", "ver", fs=F, tfs=T)
box(ax, xs[2], y2, cw, bh, "Verification", "76 of 80 agreed; globe-temperature rise\n21.7 K sunlit vs 9.4 K shaded, $p$ < 0.001", "ver", fs=F, tfs=T)
box(ax, xs[3], y2, cw, bh, "Verification", "geometric vs. panorama SVF at the 79\nfield sites: MAE 0.112, bias +0.046, $r$ 0.70", "ver", fs=F, tfs=T)
# ---- outputs
box(ax, xs[0], y3, cw, bh, "View factors", "sky, tree and building (79 sites)", "data", fs=F, tfs=T)
box(ax, xs[1], y3, cw, bh, "Surface temperature", "pavement and wall, at the reading time", "data", fs=F, tfs=T)
box(ax, xs[2], y3, cw, bh, "Sun or shade", "62 sunlit, 18 shaded", "data", fs=F, tfs=T)
box(ax, xs[3], y3, cw, bh, "View factors", "without street view — any point", "data", fs=F, tfs=T)
for x in xc:
    arrow(ax, (x, y0), (x, y1 + bh)); arrow(ax, (x, y1), (x, y2 + bh)); arrow(ax, (x, y2), (x, y3 + bh))
# ---- field imagery → analysis only (merge bar under columns 1–2)
yb = y3 - 3.5
ax.plot([xc[0], xc[0], xc[1], xc[1]], [y3, yb, yb, y3], color=INK, lw=0.8, zorder=4)
amx = (xc[0] + xc[1]) / 2
arrow(ax, (amx, yb), (amx, y4 + bh))
box(ax, xs[0] + 8, y4, cw * 2 + 5 - 16, bh, "Field analysis only", "reference view factors and surface temperatures are compared with the\nservice (Sections 4–5); they are not inputs to it", "data", fs=F, tfs=T)
# ---- service chain under column 4, fed only by the geometric view factors
box(ax, xs[3], y4, cw, bh, "Energy balance model", "deterministic solution at the pedestrian\nlocation; coefficients fixed as deployed", "proc", fs=F, tfs=T)
box(ax, xs[3], y5, cw, bh, "$T_{mrt}$ and PET, physics", "ISO 7726 → Höppe MEMI (1.37 met, 0.5 clo)\nMAE 6.6 °C against the field reference", "data", fs=F, tfs=T)
arrow(ax, (xc[3], y3), (xc[3], y4 + bh)); arrow(ax, (xc[3], y4), (xc[3], y5 + bh))
# ---- residual layer (columns 3–4) and result; sun or shade comes straight down column 3
rx0, rx1 = xs[2], xs[3] + cw
box(ax, rx0, y6, rx1 - rx0, bh + 3, "Residual correction layer — the only learned step", "ridge regression, α = 20; five standardised predictors: sun or shade, geometric SVF, $T_a$, pedestrian wind,\nPET$_{phys}$; no coordinates;  PET = PET$_{phys}$ + Δ;  trained and tested leave-one-neighbourhood-out", "ai", fs=F, tfs=T)
arrow(ax, (xc[3], y5), (xc[3], y6 + bh + 3))
arrow(ax, (xc[2], y3), (xc[2], y6 + bh + 3), color=RED, lw=1.0)
ax.text(xc[2] + 1.5, (y3 + y6 + bh + 3) / 2, "sun or shade, with $T_a$ and\nwind from the weather meter", ha="left", va="center", fontsize=5.4, style="italic", color=RED, linespacing=1.15)
box(ax, xs[2] + 12, y7, cw * 2 + 5 - 24, bh, "Reported PET", "MAE 1.8 °C, bias −0.0 °C, $r$ 0.82 against the field reference\n(leave-one-neighbourhood-out); all 63 extreme-heat sites (PET ≥ 41 °C) detected", "data", fs=F, tfs=T)
arrow(ax, ((rx0 + rx1) / 2, y6), ((rx0 + rx1) / 2, y7 + bh))
# ---- legend
ly = 5
for x, kind, lab in [(20, "data", "input or output data"), (62, "proc", "processing step"), (100, "ver", "verification"), (134, "ai", "learned step")]:
    box(ax, x, ly - 2.6, 13, 5.2, "", None, kind); ax.text(x + 15.5, ly, lab, va="center", fontsize=6.4)
ax.plot([164, 170], [ly, ly], color=RED, lw=1.0); ax.text(172, ly, "field data used", va="center", fontsize=6.4)
for e in ("pdf", "eps", "png", "svg"):
    fig.savefig(f"{OUT}/SCS_Fig_pipeline.{e}", bbox_inches="tight", pad_inches=0.03)
plt.close(fig)

# ============================================================ Fig: study design
W, H = 190, 172
fig = plt.figure(figsize=(W * MM, H * MM)); ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
SEC = BLUE
def sec(y, n, t): ax.text(4, y, f"{n}", fontsize=9, fontweight="bold", color=SEC, va="center", zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=1.0)); ax.text(10, y, t, fontsize=8.5, fontweight="bold", color=INK, va="center", zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=1.0))
GREEN_F, GREEN_E = "#E8F1E6", "#7FA37A"; GREY_F, GREY_E = "#EFEFED", "#A8A8A6"; ANA_F, ANA_E = "#F3F0E8", "#B9AE95"
def sbox(x, y, w, h, title, body, f, e, fs=6.1):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.6", facecolor=f, edgecolor=e, lw=0.8, zorder=2))
    ax.text(x + 2.5, y + h - 3.2, title, fontsize=7.0, fontweight="bold", color=INK, va="center", zorder=3)
    ax.text(x + 2.5, y + h - 6.5, body, fontsize=fs, color="#3A3A3A", va="top", linespacing=1.35, zorder=3)

sec(H - 5, "1", "Equity-stratified sampling design")
sbox(4, H - 24, 88, 16, "Busan, 206 administrative dong", "local climate zone 1–5 × ageing vulnerability\n(older density, living alone, welfare rate) → five neighbourhoods", DATA_F, DATA_E)
sbox(98, H - 24, 88, 16, "Field survey, 80 sites on older adults' routes", "market streets, clinics, senior-centre approaches\nfour clear afternoons, 20–26 August 2026, 11:19–15:06", DATA_F, DATA_E)
arrow(ax, (92, H - 16), (98, H - 16))
sec(H - 32, "2", "Field measurement — reference"); ax.text(104, H - 32, "3", fontsize=9, fontweight="bold", color=SEC, va="center"); ax.text(110, H - 32, "Three information tiers — estimates", fontsize=8.5, fontweight="bold", color=INK, va="center", zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=1.0))
sbox(4, H - 58, 88, 22, "Same site, same minute (three instruments)", "globe thermometer → $T_a$, $T_g$, RH, wind → $T_{mrt}$ (ISO 7726) → PET (Höppe MEMI)\n360° camera → SVF, TVF, BVF (79 sites); sun/shade read by two readers (76/80 agreed)\nthermal camera → pavement and wall $T_s$ (323 images)", GREEN_F, GREEN_E)
sbox(4, H - 78, 88, 16, "Measured outcome (reference)", "PET 35.9–53.8 °C · 63 of 80 extreme (≥ 41 °C)\nsunlit 62, shaded 18 · sun–shade difference 7.4 K (Cliff's δ 0.86)", GREEN_F, GREEN_E)
arrow(ax, (48, H - 58), (48, H - 62))
sbox(4, H - 98, 88, 16, "Neighbourhood form (Fig. Y)", "street width 2.3–66 m; canyon height 7–11 m at the sites against 25 m+ blocks in the\nhigh-rise dong; LCZ used as a sampling frame, not as an explanatory variable", GREY_F, GREY_E)
arrow(ax, (48, H - 78), (48, H - 82))

tx = 98; tw = 88
sbox(tx, H - 52, tw, 16, "Tier 1   official heat-warning inputs", "gridded $T_a$, RH, wind at the site's cell; no radiation term\nMAE 13.5 °C · 0 of 63 extreme sites detected", DATA_F, DATA_E)
sbox(tx, H - 72, tw, 16, "Tier 2   street-view service (as operated, Aug 2026)", "gridded weather + view factors from street-view imagery; 55 sites have imagery of their own\nMAE 7.2 °C (7.0 with imagery, 7.7 without) · 25 of 63 detected", DATA_F, DATA_E, fs=5.9)
sbox(tx, H - 92, tw, 16, "Tier 3   imagery-free pathway (physics)", "gridded weather + ray casting on building polygons + satellite canopy; all 80 sites\nMAE 6.6 °C (6.7 with imagery, 6.2 without) · 29 of 63 detected", DATA_F, DATA_E, fs=5.9)
sbox(tx, H - 112, tw, 16, "Diagnostic runs (same engine, inputs swapped)", "+ on-site weather → MAE 6.4 °C;  + observed sun/shade → 5.1 °C\nisolates what each input is worth; the observed sun/shade is not a deployable input", VER_F, VER_E, fs=5.9)
ax.add_patch(FancyBboxPatch((tx, H - 134), tw, 18, boxstyle="round,pad=0,rounding_size=0.6", facecolor=AI_F, edgecolor=RED, lw=1.6, zorder=2))
ax.text(tx + 2.5, H - 119.2, "Residual correction layer — the only learned step", fontsize=7.0, fontweight="bold", color=RED, va="center", zorder=3)
ax.text(tx + 2.5, H - 122.5, "ridge, α = 20 · five predictors: sun/shade, geometric SVF, $T_a$, wind, PET$_{phys}$ · no coordinates\nleave-one-neighbourhood-out: MAE 1.8 °C (1.8 with imagery, 1.9 without) · 63 of 63 extreme sites detected", fontsize=5.9, color="#3A3A3A", va="top", linespacing=1.35, zorder=3)
for ya, yb in ((H - 52, H - 56), (H - 72, H - 76), (H - 92, H - 96), (H - 112, H - 116)):
    arrow(ax, (tx + tw / 2, ya), (tx + tw / 2, yb))
arrow(ax, (142, H - 24), (142, H - 36))
elbow(ax, (48, H - 24), (48, H - 36), H - 28)
# on-site inputs from reference to residual layer (elbow through the gutter)
ax.plot([92, 95, 95], [H - 70, H - 70, H - 125], color=RED, lw=0.8, ls=(0, (3, 2)), zorder=4)
arrow(ax, (95, H - 125), (tx, H - 125), color=RED, ls=(0, (3, 2)), lw=0.8)
ax.text(93, H - 108, "on-site sun/shade,\n$T_a$ and wind →", ha="right", va="center", fontsize=5.8, color=RED, linespacing=1.2, zorder=6)

ay = 6; ah = 17; aw = 58; bus = ay + ah + 5
ax.plot([48, 48], [H - 98, bus], color=INK, lw=0.8, zorder=1)
ax.plot([142, 142], [H - 134, bus], color=INK, lw=0.8, zorder=1)
ax.plot([33, 157], [bus, bus], color=INK, lw=0.8, zorder=1)
for xa in (33, 95, 157):
    arrow(ax, (xa, bus), (xa, ay + ah))
ax.text(4, bus + 6, "4", fontsize=9, fontweight="bold", color=SEC, va="center", zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=1.0))
ax.text(10, bus + 6, "Analysis — measurement against the tier estimates", fontsize=8.5, fontweight="bold", color=INK, va="center", zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=1.0))
sbox(4, ay, aw, ah, "Forecast–experience gap", "one PET scale: warning inputs 33.7 → on-site air 37.3\n→ measured 46.1 °C; radiation 71 % of the 12.4 K gap\nper neighbourhood (Fig. gap)", ANA_F, ANA_E, fs=5.8)
sbox(66, ay, aw, ah, "Service diagnosis", "MAE, bias, r, extreme detection by tier and by street-\nview availability (Fig. ladder); geometric shade vs\nobserved: κ −0.01 (17 of 18 shaded sites called sunlit)", ANA_F, ANA_E, fs=5.8)
sbox(128, ay, aw, ah, "Form, shade and equity", "form indices vs sun/shade as predictors (Fig. X)\nreach: 55 sites with street view vs 25 without\n→ design requirements for an age-friendly heat service", ANA_F, ANA_E, fs=5.8)
for e in ("pdf", "eps", "png", "svg"):
    fig.savefig(f"{OUT}/SCS_Fig_design.{e}", bbox_inches="tight", pad_inches=0.03)
print("saved", OUT)
