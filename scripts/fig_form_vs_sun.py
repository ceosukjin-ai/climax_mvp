#!/usr/bin/env python3
"""Form indices (SVF+BVF+TVF) vs the sun/shade label as predictors of measured PET — 80 sites.
(a) measured PET against PET predicted from the three view factors (OLS, all 79 sites with view factors);
    colour = sun/shade read on the panorama.  (b) measured PET by sun/shade.
Numbers: R² form 0.28 · sun/shade 0.54 · both 0.56 (2026-09-25, scs_master_80.csv 62/18).
"""
import sys, numpy as np, pandas as pd, matplotlib as mpl, matplotlib.pyplot as plt
import statsmodels.formula.api as smf
from scipy import stats
from matplotlib.lines import Line2D
DATA = sys.argv[1] if len(sys.argv) > 1 else "data"; OUT = sys.argv[2] if len(sys.argv) > 2 else "figout"
mpl.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8.5, "axes.titlesize": 9, "xtick.labelsize": 8, "ytick.labelsize": 8,
    "axes.linewidth": 0.7, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 200, "savefig.dpi": 600,
    "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED = "#1A1A1A", "#5E5E5E"; SUN, SHADE = "#C0504D", "#3F6496"; MM = 1/25.4
d = pd.read_csv(f"{DATA}/scs_master_80.csv", encoding="utf-8-sig").dropna(subset=["SVF"]).copy()
d["sun"] = d["sun"].astype(int)
m_form = smf.ols("PET ~ SVF + BVF + TVF", d).fit(); d["pred"] = m_form.fittedvalues
m_sun = smf.ols("PET ~ sun", d).fit(); m_both = smf.ols("PET ~ sun + SVF + BVF + TVF", d).fit()
rmse = lambda m: np.sqrt(np.mean(m.resid**2))
print(f"R2 form {m_form.rsquared:.3f} rmse {rmse(m_form):.2f} | sun {m_sun.rsquared:.3f} rmse {rmse(m_sun):.2f} | both {m_both.rsquared:.3f}")
S, H = d[d.sun == 1], d[d.sun == 0]
fig = plt.figure(figsize=(190*MM, 95*MM))
gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 0.42], wspace=0.28)
ax = fig.add_subplot(gs[0]); bx = fig.add_subplot(gs[1], sharey=ax)
lo, hi = 35, 55
ax.plot([lo, hi], [lo, hi], color=MUTED, lw=0.7, ls=(0, (4, 3)), zorder=1)
ax.axhline(41, color=INK, lw=0.6, ls=(0, (5, 3)), zorder=1); ax.axvline(41, color=INK, lw=0.6, ls=(0, (5, 3)), zorder=1)
for q, col, lab in ((H, SHADE, "shaded"), (S, SUN, "sunlit")):
    ax.scatter(q.pred, q.PET, s=17, color=col, edgecolor="white", lw=0.45, zorder=3)
ax.set_xlim(lo, hi); ax.set_ylim(lo, hi); ax.set_aspect("equal")
ax.set_xlabel("PET predicted from urban form — SVF, BVF, TVF (°C)"); ax.set_ylabel("Measured PET (°C)")
ax.set_title("(a)  Three view factors do not separate sun from shade", loc="left", fontweight="bold")
ax.text(lo+0.4, hi-0.4, f"form model, n = {len(d)}\nR² = {m_form.rsquared:.2f}, RMSE {rmse(m_form):.1f} °C\n"
        f"sunlit sites sit above the 1:1 line (mean +{S.PET.sub(S.pred).mean():.1f} °C),\nshaded sites below (mean {H.PET.sub(H.pred).mean():+.1f} °C)",
        ha="left", va="top", fontsize=6.6, color=INK, linespacing=1.35)
ax.text(hi-0.4, lo+0.4, "1:1", ha="right", va="bottom", fontsize=6.6, color=MUTED)
ax.text(41.2, lo+0.4, "41 °C", fontsize=6.4, color=INK, va="bottom")
# (b) strip by sun/shade
rng = np.random.default_rng(1)
for i, (q, col, lab) in enumerate(((S, SUN, f"sunlit\nn = {len(S)}"), (H, SHADE, f"shaded\nn = {len(H)}"))):
    x = i + rng.uniform(-0.18, 0.18, len(q))
    bx.scatter(x, q.PET, s=17, color=col, edgecolor="white", lw=0.45, zorder=3)
    bx.plot([i-0.3, i+0.3], [q.PET.median()]*2, color=col, lw=1.4, zorder=4)
bx.axhline(41, color=INK, lw=0.6, ls=(0, (5, 3)), zorder=1)
bx.set_xlim(-0.6, 1.6); bx.set_xticks([0, 1]); bx.set_xticklabels([f"sunlit\nn = {len(S)}", f"shaded\nn = {len(H)}"])
bx.tick_params(labelleft=False); bx.spines["left"].set_visible(False); bx.tick_params(axis="y", length=0)
bx.set_title("(b)  Sun/shade label", loc="left", fontweight="bold")
dm = S.PET.median() - H.PET.median()
bx.annotate("", xy=(1.42, S.PET.median()), xytext=(1.42, H.PET.median()), arrowprops=dict(arrowstyle="<->", lw=0.8, color=INK, shrinkA=0, shrinkB=0))
bx.text(1.5, (S.PET.median()+H.PET.median())/2, f"{dm:.1f} °C", fontsize=7.2, fontweight="bold", color=INK, va="center", rotation=90)
bx.text(0.5, hi-0.4, f"R² = {m_sun.rsquared:.2f}, RMSE {rmse(m_sun):.1f} °C\nlabel + three view factors: R² = {m_both.rsquared:.2f}",
        ha="center", va="top", fontsize=6.6, color=INK, linespacing=1.35)
fig.legend(handles=[Line2D([], [], marker="o", color="none", markerfacecolor=SUN, markersize=4.6, label="sunlit — direct beam at the sensor"),
                    Line2D([], [], marker="o", color="none", markerfacecolor=SHADE, markersize=4.6, label="shaded — no direct beam")],
           loc="lower center", bbox_to_anchor=(0.5, -0.04), ncol=2, frameon=False, fontsize=7.2)
for e in ("pdf", "eps", "png"): fig.savefig(f"{OUT}/SCS_Fig_form_vs_sun.{e}", bbox_inches="tight", pad_inches=0.03)
print("saved")
