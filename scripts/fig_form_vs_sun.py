#!/usr/bin/env python3
"""Urban form (SVF + BVF + TVF) against the sun/shade label as predictors of measured PET — 80 sites.

(a) measured PET vs PET predicted from the three view factors (OLS, all sites with view factors);
    the quadrant "predicted extreme, measured not" is shaded and counted.
(b) residual (measured − predicted) of the form model and of the label model, by sun/shade:
    the form model's error is structured by the beam; the label model's is not.
(c) model ladder: RMSE and R² for four models.

Data: scs_master_80.csv (sun 62/18, view factors 2026-09-21; one site without panorama → n 79).
Output: SCS_Fig_form_vs_sun.{pdf,eps,png}. 190 mm double column, Elsevier fonts ≥ 7 pt.
"""
import sys, numpy as np, pandas as pd, matplotlib as mpl, matplotlib.pyplot as plt
import statsmodels.formula.api as smf
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"
OUT = sys.argv[2] if len(sys.argv) > 2 else "figout"
mpl.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Liberation Serif"], "mathtext.fontset": "stix",
    "font.size": 8, "axes.labelsize": 8.5, "axes.titlesize": 9, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED, FAINT, SURF = "#1A1A1A", "#5E5E5E", "#B8B8B8", "#F3F3F1"
SUN, SHADE = "#C0504D", "#2F6DB5"          # validated pair (CVD ΔE 16, normal 25)
MM = 1 / 25.4
THR = 41.0

# ------------------------------------------------------------------ data & models
d = pd.read_csv(f"{DATA}/scs_master_80.csv", encoding="utf-8-sig").dropna(subset=["SVF"]).copy()
d["sun"] = d["sun"].astype(int)
models = {
    "Sky view factor only":            smf.ols("PET ~ SVF", d).fit(),
    "Three view factors\n(sky, building, tree)": smf.ols("PET ~ SVF + BVF + TVF", d).fit(),
    "Sun/shade label":                 smf.ols("PET ~ sun", d).fit(),
    "Label + three view factors":      smf.ols("PET ~ sun + SVF + BVF + TVF", d).fit(),
}
rmse = lambda m: float(np.sqrt(np.mean(m.resid ** 2)))
m_form, m_lab = models["Three view factors\n(sky, building, tree)"], models["Sun/shade label"]
d["pred_form"], d["res_form"], d["res_lab"] = m_form.fittedvalues, m_form.resid, m_lab.resid
S, H = d[d.sun == 1], d[d.sun == 0]
false_hot = H[(H.pred_form >= THR) & (H.PET < THR)]
for k, m in models.items():
    print(f"{k.replace(chr(10), ' '):34s} R2 {m.rsquared:.3f}  RMSE {rmse(m):.2f}")
print("shaded predicted ≥41 but measured <41:", len(false_hot), "/", len(H))

# ------------------------------------------------------------------ layout
fig = plt.figure(figsize=(190 * MM, 88 * MM))
gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 0.85, 0.62], wspace=0.55, left=0.055, right=0.985, top=0.90, bottom=0.20)
ax, bx, cx = (fig.add_subplot(gs[i]) for i in range(3))

def dot(axis, x, y, col, **kw):
    axis.scatter(x, y, s=15, color=col, edgecolor="white", lw=0.5, zorder=4, **kw)

# (a) measured vs predicted ----------------------------------------------------
lo, hi = 35, 55
ax.add_patch(Rectangle((THR, lo), hi - THR, THR - lo, facecolor=SURF, edgecolor="none", zorder=0))
ax.plot([lo, hi], [lo, hi], color=FAINT, lw=0.8, zorder=1)
ax.axhline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1)
ax.axvline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1)
dot(ax, S.pred_form, S.PET, SUN); dot(ax, H.pred_form, H.PET, SHADE)
ax.set_xlim(lo, hi); ax.set_ylim(lo, hi); ax.set_box_aspect(1)
ax.set_xticks(range(35, 56, 5)); ax.set_yticks(range(35, 56, 5))
ax.set_xlabel("PET predicted from urban form (°C)\nsky, building and tree view factors")
ax.set_ylabel("Measured PET (°C)")

ax.text(hi - 0.3, hi - 0.6, "1:1", ha="right", va="top", fontsize=7, color=MUTED)
ax.text(lo + 0.4, THR + 0.35, f"{THR:.0f} °C", fontsize=7, color=MUTED, va="bottom")
ax.text(hi - 0.4, lo + 0.5,
        f"predicted extreme,\nmeasured not:\n{len(false_hot)} of {len(H)} shaded sites",
        ha="right", va="bottom", fontsize=7, color=SHADE, linespacing=1.3)
ax.text(lo + 0.4, hi - 0.4, f"n = {len(d)}\nR² = {m_form.rsquared:.2f}\nRMSE = {rmse(m_form):.1f} °C",
        ha="left", va="top", fontsize=7, color=INK, linespacing=1.3)

# (b) residuals by model and group ------------------------------------------------
rows = [("Form model\n(three view factors)", "res_form"), ("Label model\n(sun/shade)", "res_lab")]
rng = np.random.default_rng(3)
y0 = {0: 1.25, 1: 0.0}
bx.axvline(0, color=FAINT, lw=0.8, zorder=1)
for r, (name, col) in enumerate(rows):
    base = y0[r]
    for g, colr, off in ((1, SUN, +0.16), (0, SHADE, -0.16)):
        q = d[d.sun == g][col].values
        yy = base + off + rng.uniform(-0.07, 0.07, len(q))
        bx.scatter(q, yy, s=11, color=colr, edgecolor="white", lw=0.4, alpha=0.9, zorder=3)
        mu = q.mean()
        bx.plot([mu, mu], [base + off - 0.11, base + off + 0.11], color=colr, lw=1.6, zorder=5)
        ty = base + off + (0.14 if g == 1 else -0.14)
        bx.text(mu, ty, f"{mu:+.1f} °C", ha="center", va="bottom" if g == 1 else "top", fontsize=6.8, color=INK)
    bx.text(-9.3, base + 0.40, name, ha="left", va="bottom", fontsize=7.4, color=INK, linespacing=1.15)
bx.set_xlim(-9.5, 8); bx.set_ylim(-0.5, 2.1)
bx.set_yticks([]); bx.spines["left"].set_visible(False)
bx.set_xlabel("Residual, measured − predicted (°C)")


# (c) model ladder ----------------------------------------------------------------
names = list(models); vals = [rmse(models[k]) for k in names]; r2 = [models[k].rsquared for k in names]
ypos = np.arange(len(names))[::-1]
cols = [FAINT, FAINT, INK, INK]
cx.barh(ypos, vals, height=0.42, color=cols, edgecolor="none", zorder=3)
for y, v, r in zip(ypos, vals, r2):
    cx.text(v + 0.08, y, f"{v:.1f} °C   R² {r:.2f}", va="center", ha="left", fontsize=7, color=INK)
cx.set_yticks(ypos); cx.set_yticklabels(names, fontsize=7.2)
cx.set_xlim(0, 6.4); cx.set_xticks([0, 1, 2, 3, 4])
cx.set_xlabel("RMSE of PET (°C)")
cx.spines["left"].set_visible(False); cx.tick_params(axis="y", length=0)


for a_, t_ in ((ax, "(a)  Form model against measurement"), (bx, "(b)  Where each model errs"), (cx, "(c)  Model ladder")):
    x0_ = a_.get_position().x0
    fig.text(x0_, 0.93, t_, ha="left", va="bottom", fontsize=9, fontweight="bold", color=INK)
# legend --------------------------------------------------------------------------
fig.legend(handles=[Line2D([], [], marker="o", color="none", markerfacecolor=SUN, markersize=4.8, label=f"sunlit — direct beam at the sensor (n = {len(S)})"),
                    Line2D([], [], marker="o", color="none", markerfacecolor=SHADE, markersize=4.8, label=f"shaded — no direct beam (n = {len(H)})")],
           loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=2, frameon=False, fontsize=7.4, handletextpad=0.3, columnspacing=2.0)
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_form_vs_sun.{e}", bbox_inches="tight", pad_inches=0.03)
print("saved", OUT)
