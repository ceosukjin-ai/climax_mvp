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
# Thumbnails (docs/figs/thumbs): site 20260825_용호제1동_05 — panorama crop, SegFormer-B0 (ADE20K) classes sky / tree / building / rest;
# building polygons = LCZ 4 panel of SCS_Fig_sites_map (national register footprints, 3 height classes).
import matplotlib.image as mpimg, os
from matplotlib.patches import Circle, Rectangle
TH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "figs", "thumbs")
def thumb(ax, name, x, y, w, h):
    img = mpimg.imread(os.path.join(TH, name)); ih, iw = img.shape[:2]
    # fit inside (x, y, w, h) keeping aspect, centred
    if iw / ih > w / h: ww, hh = w, w * ih / iw
    else: hh, ww = h, h * iw / ih
    x0, y0 = x + (w - ww) / 2, y + (h - hh) / 2
    ax.imshow(img, extent=[x0, x0 + ww, y0, y0 + hh], aspect="auto", zorder=3, interpolation="lanczos")
    ax.add_patch(Rectangle((x0, y0), ww, hh, facecolor="none", edgecolor="#8FA6BF", lw=0.5, zorder=4))

def icon_globe(ax, cx, cy, h):
    """globe thermometer on a tripod + cup anemometer, line icon"""
    r = h * 0.17
    ax.add_patch(Circle((cx, cy + h * 0.28), r, facecolor="#2B2B2B", edgecolor="none", zorder=3))
    ax.plot([cx, cx], [cy - h * 0.45, cy + h * 0.11], color=INK, lw=0.8, zorder=3)
    for dx in (-h * 0.22, h * 0.22): ax.plot([cx, cx + dx], [cy - h * 0.05, cy - h * 0.45], color=INK, lw=0.7, zorder=3)
    # anemometer on a side arm
    ax.plot([cx, cx + h * 0.36], [cy - h * 0.05, cy - h * 0.05], color=INK, lw=0.7, zorder=3)
    ax.plot([cx + h * 0.36, cx + h * 0.36], [cy - h * 0.05, cy + h * 0.15], color=INK, lw=0.7, zorder=3)
    for dx, dy in ((-h * 0.12, 0), (h * 0.12, 0), (0, h * 0.12)):
        ax.plot([cx + h * 0.36, cx + h * 0.36 + dx], [cy + h * 0.15, cy + h * 0.15 + dy], color=INK, lw=0.6, zorder=3)
        ax.add_patch(Circle((cx + h * 0.36 + dx, cy + h * 0.15 + dy), h * 0.035, facecolor="white", edgecolor=INK, lw=0.6, zorder=3))

def icon_canopy(ax, x, y, w, h):
    """30 m grid with canopy cells + a weather grid symbol"""
    n, m = 4, 7; cw, ch = w / m, h / n
    rng = np.random.default_rng(5)
    for i in range(n):
        for j in range(m):
            v = rng.random()
            fc = "#2F7D4A" if v > 0.72 else ("#A9D3A6" if v > 0.5 else "#F2F2EE")
            ax.add_patch(Rectangle((x + j * cw, y + i * ch), cw, ch, facecolor=fc, edgecolor="#B9C4CF", lw=0.35, zorder=3))
    ax.add_patch(Rectangle((x, y), w, h, facecolor="none", edgecolor="#8FA6BF", lw=0.5, zorder=4))

W, H = 190, 150
fig = plt.figure(figsize=(W * MM, H * MM)); ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
top = H - 3; hb = 7
h0, h1, h2, h3, h4 = 25, 21, 15, 17, 15                 # row heights: inputs, processing, outputs, learning, result
g01, g12, g23, g34 = 6, 10, 6, 6                        # gaps (g12 wide: verification notes sit beside the arrows)
y0 = top - hb - 4 - h0; y1 = y0 - g01 - h1; y2 = y1 - g12 - h2; y3 = y2 - g23 - h3; y4 = y3 - g34 - h4
F, T, TN, FN = 5.8, 7.2, 6.6, 5.5
AMB = "#8A5A1B"

def lane(x0, x1, ybot, title, sub, col):
    ax.add_patch(FancyBboxPatch((x0, ybot), x1 - x0, top - ybot, boxstyle="round,pad=0,rounding_size=1.2", facecolor="#F5F6F8", edgecolor="none", zorder=0))
    ax.add_patch(FancyBboxPatch((x0, top - hb), x1 - x0, hb, boxstyle="round,pad=0,rounding_size=1.2", facecolor=col, edgecolor="none", zorder=1))
    ax.add_patch(Rectangle((x0, top - hb), x1 - x0, hb / 2, facecolor=col, edgecolor="none", zorder=1))
    ax.text(x0 + 3, top - hb / 2, title, ha="left", va="center", fontsize=8.2, fontweight="bold", color="white", zorder=3)
    ax.text(x1 - 3, top - hb / 2, sub, ha="right", va="center", fontsize=6.4, color="white", zorder=3)

L0, L1, R0, R1 = 17, 100, 104, 187
lane(L0, L1, y3 - 3, "A   Field reference", "80 sites · same site, same minute", BLUE)
lane(R0, R1, y4 - 3, "B   Imagery-free service", "no street view anywhere in the chain", "#3D3D3D")
# row labels (left gutter)
for (yy, hh, lab) in ((y0, h0, "Inputs"), (y1, h1, "Processing"), (y2, h2, "Outputs"), (y3, h3, "Learning"), (y4, h4, "Result")):
    ax.text(L0 - 2.5, yy + hh / 2, lab.upper(), ha="right", va="center", fontsize=6.0, color=MUTED, fontweight="bold")

def note(ax, x, y, txt, ha="left"):
    ax.text(x, y, txt, ha=ha, va="center", fontsize=5.3, style="italic", color=AMB, linespacing=1.15, zorder=5)

# ---- lane A
xs = [19.5, 46, 72.5]; bw = 25; xc = [x + bw / 2 for x in xs]
# inputs (thumbnails)
box(ax, xs[0], y0, 51.5, h0, "$\\bf{80}$ 360° panoramas", "one per site at 1.5 m; two fisheye images stitched\nwith a per-image attitude correction", "data", fs=F, tfs=T, bottom=True)
thumb(ax, "thumb_pano.png", xs[0] + 6.5, y0 + h0 - 14.6, 38.5, 10.6)
box(ax, xs[2], y0, bw, h0, "Globe + weather meter", "$T_g$ (Ø 0.05 m), $T_a$, RH,\nwind at the reading minute", "data", fs=FN, tfs=TN, bottom=True)
icon_globe(ax, xs[2] + bw / 2, y0 + h0 - 9.6, 11)
# processing
box(ax, xs[0], y1, bw, h1, "Semantic segmentation", "SegFormer-B0 (ADE20K)\nsky, tree, building, rest", "proc", fs=FN, tfs=TN, bottom=True)
thumb(ax, "thumb_seg.png", xs[0] + 1.5, y1 + h1 - 12.6, bw - 3, 7.2)
box(ax, xs[1], y1, bw, h1, "Sun / shade reading", "two readers, $\\bf{76/80}$ agreed;\nobserver's shadow settles\nthe rest; cloud-diffuse = shade", "proc", fs=FN, tfs=TN)
box(ax, xs[2], y1, bw, h1, "$T_{mrt}$ → PET", "ISO 7726 (D 0.05 m, ε 0.95,\nforced convection) →\nHöppe MEMI, VDI 3787-2", "proc", fs=FN, tfs=TN)
# outputs
box(ax, xs[0], y2, bw, h2, "View factors · $\\bf{79}$ sites", "SVF, TVF, BVF", "data", fs=F, tfs=TN)
box(ax, xs[1], y2, bw, h2, "Sun or shade · $\\bf{62 / 18}$", "62 sunlit, 18 shaded", "data", fs=F, tfs=TN)
box(ax, xs[2], y2, bw, h2, "Reference PET · $\\bf{80}$", "35.9–53.8 °C; reference for\nevery comparison", "data", fs=FN, tfs=TN)
arrow(ax, (xc[0], y0), (xc[0], y1 + h1)); arrow(ax, (xc[1], y0), (xc[1], y1 + h1)); arrow(ax, (xc[2], y0), (xc[2], y1 + h1))
for x in xc: arrow(ax, (x, y1), (x, y2 + h2))
# verification notes beside the processing → output arrows
note(ax, xc[0] + 1.2, y1 - g12 / 2, "one pose failure → 79;\nresidual class median 0.02")
note(ax, xc[1] + 1.2, y1 - g12 / 2, "globe rise 21.7 K sunlit vs\n9.4 K shaded, p < 0.001")
# learning row: on-site inputs
box(ax, xs[1], y3, 51.5, h3, "On-site inputs to the residual layer", "sun or shade (panorama), $T_a$ and wind (weather meter)\n— read at the site, not from imagery or a grid", "data", fs=F, tfs=T)
arrow(ax, (xc[1], y2), (xc[1], y3 + h3))
gx = (L1 + R0) / 2
arrow(ax, (xs[1] + 51.5, y3 + h3 / 2), (R0 + 3, y3 + h3 / 2), color=RED, lw=1.0)

# ---- lane B
rx, rw = R0 + 3, R1 - R0 - 6; hw = (rw - 4) / 2
box(ax, rx, y0, hw, h0, "Building polygons", "national register; heights\nfloors × 3.018 m + 0.902 m", "data", fs=F, tfs=TN, bottom=True)
thumb(ax, "thumb_poly.png", rx + 1.5, y0 + h0 - 14.6, hw - 3, 10.6)
box(ax, rx + hw + 4, y0, hw, h0, "Satellite canopy + weather", "Meta/WRI canopy height (30 m);\ngridded $T_a$, RH, wind", "data", fs=F, tfs=TN, bottom=True)
icon_canopy(ax, rx + hw + 4 + 8.5, y0 + h0 - 14.4, hw - 17, 10)
box(ax, rx, y1, rw, h1, "Geometric view factors", "ray casting in 5° steps to the building skyline; tree canopy as a transmissive\nlayer; SVF and BVF at any point — no imagery needed", "proc", fs=F, tfs=T)
arrow(ax, (rx + hw / 2, y0), (rx + hw / 2, y1 + h1)); arrow(ax, (rx + hw * 1.5 + 4, y0), (rx + hw * 1.5 + 4, y1 + h1))
box(ax, rx, y2, rw, h2, "Physics PET  —  MAE $\\bf{6.6}$ °C against the reference", "energy balance at the pedestrian location → $T_{mrt}$ (ISO 7726)\n→ PET (Höppe MEMI; 1.37 met, 0.5 clo)", "proc", fs=F, tfs=T)
arrow(ax, (rx + rw / 2, y1), (rx + rw / 2, y2 + h2))
note(ax, rx + rw / 2 + 1.2, y1 - g12 / 2, "verified against the panorama SVF at the 79 field sites:\nMAE 0.112, bias +0.046, r 0.70")
box(ax, rx, y3, rw, h3, "AI residual layer · $\\bf{5}$ predictors — the only learned step", "ridge regression, α = 20; sun/shade, geometric SVF, $T_a$, wind, PET$_{phys}$; no coordinates\nPET = PET$_{phys}$ + Δ;  trained and tested leave-one-neighbourhood-out", "ai", fs=F, tfs=T)
arrow(ax, (rx + rw / 2, y2), (rx + rw / 2, y3 + h3))
box(ax, rx + 8, y4, rw - 16, h4, "Reported PET · $\\bf{63 / 63}$ extreme sites detected", "MAE $\\bf{1.8}$ °C, bias −0.0 °C, $r$ $\\bf{0.82}$ against the field reference", "data", fs=F, tfs=T)
arrow(ax, (rx + rw / 2, y3), (rx + rw / 2, y4 + h4))

# ---- legend
ly = 4.2
for x, kind, lab in [(17, "data", "input or output data"), (58, "proc", "processing step"), (95, "ai", "learned step")]:
    box(ax, x, ly - 2.6, 12, 5.2, "", None, kind); ax.text(x + 14.5, ly, lab, va="center", fontsize=6.4)
ax.text(128, ly, "verification", va="center", fontsize=6.4, style="italic", color=AMB)
ax.plot([148, 154], [ly, ly], color=RED, lw=1.0); ax.text(156, ly, "field data into the service chain", va="center", fontsize=6.4)
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
sbox(98, H - 24, 88, 16, "Field survey, 82 sites on older adults' routes", "market streets, clinics, senior-centre approaches\nfour afternoons, August 2026, 11:19–15:06 · 80 sites enter the analysis", DATA_F, DATA_E)
arrow(ax, (92, H - 16), (98, H - 16))
sec(H - 32, "2", "Field measurement — reference"); ax.text(104, H - 32, "3", fontsize=9, fontweight="bold", color=SEC, va="center"); ax.text(110, H - 32, "Three information tiers — estimates", fontsize=8.5, fontweight="bold", color=INK, va="center", zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=1.0))
sbox(4, H - 58, 88, 22, "Same site, same minute (three instruments)", "globe thermometer → $T_a$, $T_g$, RH, wind → $T_{mrt}$ (ISO 7726) → PET (Höppe MEMI)\n360° camera → SVF, TVF, BVF (79 sites); sun/shade read by two readers (76/80 agreed)\nthermal camera → pavement $T_s$ (324/328 frames)", GREEN_F, GREEN_E)
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
ax.text(tx + 2.5, H - 122.5, "ridge, α = 20 · five predictors: sun/shade, geometric SVF, $T_a$, wind, PET$_{phys}$ · no coordinates\nleave-one-neighbourhood-out MAE 1.8 °C (1.8 with imagery, 1.9 without) · all 63 extreme sites detected", fontsize=5.9, color="#3A3A3A", va="top", linespacing=1.35, zorder=3)
for ya, yb in ((H - 52, H - 56), (H - 72, H - 76), (H - 92, H - 96), (H - 112, H - 116)):
    arrow(ax, (tx + tw / 2, ya), (tx + tw / 2, yb))
arrow(ax, (142, H - 24), (142, H - 36))
elbow(ax, (48, H - 24), (48, H - 36), H - 28)
# on-site inputs from reference to residual layer
arrow(ax, (92, H - 70), (tx, H - 125), color=RED, ls=(0, (3, 2)), lw=0.7)
ax.text(95, H - 100, "on-site\nsun/shade,\n$T_a$, wind", ha="center", va="center", fontsize=5.6, color=RED, bbox=dict(facecolor="white", edgecolor="none", pad=0.3), zorder=6)

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
