#!/usr/bin/env python3
"""Engine physics figure — what the imagery-free chain computes at the pedestrian.

(a) Street-canyon cross-section: the short-wave and long-wave terms that reach the standing pedestrian and how each is
    weighted (six-direction projection factors, sky/ground view shares, direct-beam shade, canopy screening, wall reflection).
(b) Plan view: ray casting from the point to the building outlines → horizon angle β(az) → SVF, direct-beam shade test.
(c) Ground surface energy balance solved for the pavement temperature T_s.
Every term and constant is read from the deployed code: backend/vpti_core/mrt.py (compute_mrt, estimate_ground_temp,
fanger_projected_area_factor, sky_emissivity), config.py (MRTConfig), backend/app/services/geo.py (svf_geometric,
_svf_from_rings, sun_blocked_outdoor, canopy τ), routes.py::_geo_vpti_compute (wall reflection, sunlit fraction 0.45).
"""
import sys, numpy as np, matplotlib as mpl, matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon, Circle, Wedge, FancyArrowPatch, Arc, Ellipse
from matplotlib.lines import Line2D

OUT = sys.argv[1] if len(sys.argv) > 1 else "figout"
mpl.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
                     "figure.dpi": 200, "savefig.dpi": 1200, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED = "#1A1A1A", "#5E5E5E"
SW, LW, GRN, BLD, GND, SKY = "#E08A2E", "#2E75B6", "#4E9A5E", "#C9CDD2", "#B8B2A8", "#EAF1F8"
MM = 1 / 25.4
W, H = 190, 112
fig = plt.figure(figsize=(W * MM, H * MM))


def arr(ax, p0, p1, color, ls="-", lw=1.1, ms=9, z=6):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=ms, color=color, lw=lw, linestyle=ls, zorder=z, shrinkA=0, shrinkB=0))


def lab(ax, x, y, s, color=INK, fs=6.0, ha="left", va="center", bold=False, z=8):
    ax.text(x, y, s, color=color, fontsize=fs, ha=ha, va=va, fontweight="bold" if bold else "normal", zorder=z,
            bbox=dict(facecolor="white", edgecolor="none", pad=0.35, alpha=0.92))


def panel_title(ax, s):
    ax.text(0.0, 1.0, s, transform=ax.transAxes, ha="left", va="bottom", fontsize=7.6, fontweight="bold", color=INK)


# ============================================================ (a) canyon cross-section
ax = fig.add_axes([0.02, 0.09, 0.60, 0.86]); ax.set_xlim(0, 100); ax.set_ylim(0, 62); ax.set_aspect("equal", adjustable="box"); ax.axis("off")
ax.text(0, 63.2, "(a)  Radiation reaching the pedestrian — six-direction budget (VDI 3787 Part 2)", ha="left", va="bottom", fontsize=7.6, fontweight="bold", color=INK)
# sky
ax.add_patch(Rectangle((0, 8), 100, 54, facecolor=SKY, edgecolor="none", zorder=0))
# ground
ax.add_patch(Rectangle((0, 0), 100, 8, facecolor=GND, edgecolor="none", zorder=1))
ax.plot([0, 100], [8, 8], color=INK, lw=0.8, zorder=2)
# buildings: left tall (sunlit face towards the street), right lower
ax.add_patch(Rectangle((0, 8), 20, 40, facecolor=BLD, edgecolor=INK, lw=0.7, zorder=2))
ax.add_patch(Rectangle((86, 8), 14, 26, facecolor=BLD, edgecolor=INK, lw=0.7, zorder=2))
ax.plot([20, 20], [8, 48], color=SW, lw=2.2, zorder=3)          # sunlit wall
lab(ax, 21.0, 44.5, "sunlit wall\nreflects 0.45·α$_{wall}$", SW, fs=5.4)
# tree canopy on the right
ax.plot([76, 76], [8, 16], color="#6B4E2E", lw=1.6, zorder=3)
ax.add_patch(Ellipse((76, 20), 12, 9, facecolor=GRN, edgecolor="none", alpha=0.85, zorder=3))
lab(ax, 76, 27.0, "canopy above the roofline\nscreens (1 − τ), τ 0.35", GRN, fs=5.0, ha="center")
# sun
sx, sy = 27, 52
ax.add_patch(Circle((sx, sy), 2.6, facecolor="#F6C453", edgecolor=SW, lw=0.8, zorder=4))
for k in range(8):
    a = k * np.pi / 4; ax.plot([sx + 3.2 * np.cos(a), sx + 4.4 * np.cos(a)], [sy + 3.2 * np.sin(a), sy + 4.4 * np.sin(a)], color=SW, lw=0.7, zorder=4)
# pedestrian (standing, 1.5 m eye height at y≈20)
px, pe = 50, 20
ax.add_patch(Circle((px, pe + 2.2), 1.7, facecolor="white", edgecolor=INK, lw=0.8, zorder=6))
ax.add_patch(Rectangle((px - 1.8, 8), 3.6, 12.2, facecolor="white", edgecolor=INK, lw=0.8, zorder=6))
ax.plot([px - 1.8, px - 4.5], [17, 13], color=INK, lw=0.8, zorder=6); ax.plot([px + 1.8, px + 4.5], [17, 13], color=INK, lw=0.8, zorder=6)
ax.plot([px - 1.1, px - 1.6], [8, 8], color=INK, lw=0.8)
lab(ax, px, 6.0, "pedestrian, eye height 1.5 m", INK, fs=5.4, ha="center")
# --- short-wave terms (amber)
arr(ax, (sx + 2.6, sy - 1.6), (px - 1.2, pe + 3.2), SW, lw=1.6, ms=11)            # direct
lab(ax, 33.5, 44.0, "direct  a$_k$·f$_p$(β)·DNI·shade", SW, fs=5.8, ha="left", bold=True)
lab(ax, 33.5, 40.6, "f$_p$(β) = 0.308·cos[β(0.998 − β²/50000)]  (Fanger 1970)\nshade = 0 when horizon[sun azimuth] > β  (buildings only)", SW, fs=4.9, ha="left")
arr(ax, (62, 46), (px + 2.2, pe + 3.0), SW, ls=(0, (3, 2)), lw=1.0)               # diffuse sky
lab(ax, 63, 47.5, "diffuse sky  a$_k$·Σ F$_i$·DHI·ψ$_{sky,i}$", SW, fs=5.6, ha="left")
arr(ax, (41, 8.4), (px - 2.0, pe - 4.0), SW, ls=(0, (3, 2)), lw=1.0)             # ground reflected
lab(ax, 22.5, 11.0, "ground-reflected  a$_k$·Σ F$_i$·α·GHI·ψ$_{grd,i}$", SW, fs=5.4, ha="left")
arr(ax, (20.4, 30), (px - 2.2, pe + 0.5), SW, ls=(0, (3, 2)), lw=1.0)            # wall reflected
lab(ax, 24, 31.8, "wall-reflected  α$_{wall}$·0.45·GHI", SW, fs=5.4, ha="left")
# --- long-wave terms (blue)
arr(ax, (72, 53), (px + 1.6, pe + 4.2), LW, ls=(0, (1.2, 1.6)), lw=1.0)         # sky LW
lab(ax, 66, 55.2, "sky  ε$_p$·Σ F$_i$·ε$_{sky}$σT$_a^4$·ψ$_{sky,i}$", LW, fs=5.6, ha="left")
lab(ax, 66, 52.0, "ε$_{sky}$ = 0.52 + 0.065√e (Brunt), cloud-corrected", LW, fs=4.9, ha="left")
arr(ax, (60, 8.4), (px + 2.2, pe - 5.0), LW, ls=(0, (1.2, 1.6)), lw=1.0)         # ground LW
arr(ax, (85.6, 24), (px + 2.4, pe - 2.0), LW, ls=(0, (1.2, 1.6)), lw=1.0)        # wall LW
lab(ax, 56, 11.5, "surfaces  ε$_p$·Σ F$_i$·ε$_g$σT$_s^4$·ψ$_{grd,i}$\n(ground and walls; T$_s$ from panel c)", LW, fs=5.4, ha="left")
# view shares
lab(ax, 2.5, 3.2, "F$_i$: 0.22 × 4 sides, 0.06 up, 0.06 down;  a$_k$ 0.7, ε$_p$ 0.97;  ψ$_{sky}$: SVF upward, SVF/2 sideways, 0 downward;  ψ$_{grd}$ = 1 − ψ$_{sky}$", MUTED, fs=5.0, ha="left")
# horizon angle from the eye to the left roof
ax.plot([px, 20], [pe, 48], color=MUTED, lw=0.6, ls=(0, (2, 2)), zorder=5)
ax.plot([px, 30], [pe, pe], color=MUTED, lw=0.6, ls=(0, (2, 2)), zorder=5)
ax.add_patch(Arc((px, pe), 14, 14, theta1=137, theta2=180, color=MUTED, lw=0.6, zorder=5))
ax.text(41.5, pe + 2.6, "β(az)", fontsize=5.4, color=MUTED, ha="center", va="bottom", zorder=8)
# result strip
ax.text(58, 59.3, "S$_{str}$ = short-wave + long-wave;   T$_{mrt}$ = (S$_{str}$ / ε$_p$σ)$^{1/4}$ − 273.15", fontsize=6.2, color=INK, ha="center", va="center", zorder=8,
        bbox=dict(facecolor="white", edgecolor=INK, lw=0.6, pad=2.0))

# ============================================================ (b) plan view — ray casting
ax = fig.add_axes([0.655, 0.56, 0.335, 0.39]); ax.set_xlim(-30, 30); ax.set_ylim(-30, 24); ax.set_aspect("equal"); ax.axis("off")
panel_title(ax, "(b)  Sky view factor from building outlines")
rng = np.random.default_rng(2)
blds = [np.array([[-28, 6], [-10, 8], [-9, 20], [-27, 19]]), np.array([[-6, 9], [12, 10], [11, 20], [-6, 21]]),
        np.array([[14, -22], [28, -20], [27, -6], [15, -7]]), np.array([[-28, -20], [-12, -22], [-11, -9], [-27, -8]]),
        np.array([[16, 3], [28, 4], [28, 14], [17, 13]])]
hts = [24, 12, 9, 15, 30]
for b_, h_ in zip(blds, hts):
    ax.add_patch(Polygon(b_, closed=True, facecolor=BLD, edgecolor=INK, lw=0.6, zorder=2))
    ax.text(*b_.mean(axis=0), f"{h_} m", fontsize=4.8, color=INK, ha="center", va="center", zorder=3)
# rays every 2° (draw every 6° for legibility)
def hit(dx, dy):
    best = 30.0
    for b_ in blds:
        n = len(b_)
        for i in range(n):
            x1, y1 = b_[i]; x2, y2 = b_[(i + 1) % n]
            den = dx * (y2 - y1) - dy * (x2 - x1)
            if abs(den) < 1e-9: continue
            t = ((x1) * (y2 - y1) - (y1) * (x2 - x1)) / den
            u = (dx * y1 - dy * x1) / den
            if t > 0 and 0 <= u <= 1: best = min(best, t)
    return best
for k in range(0, 360, 6):
    a = np.radians(k); dx, dy = np.sin(a), np.cos(a); d = hit(dx, dy)
    ax.plot([0, dx * d], [0, dy * d], color=SW if d < 30 else "#C8C8C6", lw=0.45, zorder=1)
ax.add_patch(Circle((0, 0), 1.1, facecolor="white", edgecolor=INK, lw=0.7, zorder=5))
ax.text(1.8, -1.2, "site, snapped to the\nstreet centre line", fontsize=4.8, color=INK, ha="left", va="top", zorder=6)
ax.text(0, -23.0, "rays every 2° → d(az), H → β(az) = atan[(H − 1.5)/d]\nSVF = 1 − mean sin²β (Steyn 1980); canopy above the roofline adds (1 − τ)·sin²β\nshade: horizon[sun azimuth] > sun elevation;  width: narrowest opposing pair (5° rays)",
        fontsize=4.9, color=INK, ha="center", va="top", zorder=6)

# ============================================================ (c) ground energy balance
ax = fig.add_axes([0.655, 0.07, 0.335, 0.42]); ax.set_xlim(0, 60); ax.set_ylim(0, 44); ax.axis("off")
panel_title(ax, "(c)  Pavement temperature T$_s$ — surface energy balance")
ax.add_patch(Rectangle((3, 9), 54, 8, facecolor=GND, edgecolor=INK, lw=0.7, zorder=2))
ax.text(30, 13, "pavement:  α, ε$_g$ from the Sentinel-2 surface class (default 0.15 / 0.95)", fontsize=5.0, color=INK, ha="center", va="center", zorder=4)
arr(ax, (10, 30), (10, 17.6), SW, lw=1.3)
lab(ax, 10, 36.5, "(1 − α)·S↓·(1 − f$_{stor}$)\nS↓ = DNI·sinβ·shade + DHI·SVF\n(2 h exponential lag)", SW, fs=4.9, ha="center", va="center")
arr(ax, (26, 30), (26, 17.6), LW, ls=(0, (1.2, 1.6)), lw=1.1)
lab(ax, 26, 36.5, "ε$_g$·L↓\nL↓ = σT$_a^4$·[SVF·ε$_{sky}$ +\n(1 − SVF)·ε$_{env}$],  ε$_{env}$ 0.90", LW, fs=4.9, ha="center", va="center")
arr(ax, (40, 17.6), (40, 30), LW, ls=(0, (1.2, 1.6)), lw=1.1)
lab(ax, 40, 34.5, "ε$_g$·σT$_s^4$", LW, fs=5.2, ha="center")
arr(ax, (52, 17.6), (52, 30), "#6E6E6E", lw=1.1)
lab(ax, 52, 35.5, "h$_c$·(T$_s$ − T$_a$)\nh$_c$ = a + b·u$_p$", "#6E6E6E", fs=4.9, ha="center", va="center")
arr(ax, (20, 9), (20, 5.2), "#6E6E6E", lw=0.9)
ax.text(22, 6.8, "storage f$_{stor}$;  40 W m$^{-2}$ released when S↓ is weak", fontsize=4.8, color="#6E6E6E", ha="left", va="center")
ax.text(30, 2.2, "in = out, solved for T$_s$ by Newton iteration;  shaded ground keeps 0.74 of its sunlit rise;  vegetated share → T$_a$", fontsize=4.8, color=INK, ha="center", va="center", zorder=6)

# ============================================================ legend
fig.legend(handles=[Line2D([], [], color=SW, lw=1.4, label="short-wave (solar)"), Line2D([], [], color=SW, lw=1.0, ls=(0, (3, 2)), label="short-wave, diffuse or reflected"),
                    Line2D([], [], color=LW, lw=1.0, ls=(0, (1.2, 1.6)), label="long-wave (thermal)"), Line2D([], [], color="#6E6E6E", lw=1.0, label="convection / storage")],
           loc="lower left", bbox_to_anchor=(0.02, 0.005), ncol=4, frameon=False, fontsize=6.0, handlelength=2.2, columnspacing=1.6)
for e in ("pdf", "eps", "png", "svg"):
    fig.savefig(f"{OUT}/SCS_Fig_engine_radiation.{e}", bbox_inches="tight", pad_inches=0.03)
fig.savefig(f"{OUT}/SCS_Fig_engine_radiation.tiff", bbox_inches="tight", pad_inches=0.03, dpi=1200, pil_kwargs={"compression": "tiff_lzw"})
print("saved", OUT)
