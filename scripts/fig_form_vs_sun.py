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
fig = plt.figure(figsize=(190 * MM, 80 * MM))
gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 0.85], wspace=0.62, left=0.055, right=0.985, top=0.86, bottom=0.21)
ax, bx, cx = (fig.add_subplot(gs[i]) for i in range(3))
lo, hi = 35, 55
GREY = "#7A7A7A"

def frame(axis, xlabel):
    axis.add_patch(Rectangle((THR, lo), hi - THR, THR - lo, facecolor=SURF, edgecolor="none", zorder=0))
    axis.plot([lo, hi], [lo, hi], color=FAINT, lw=0.8, zorder=1)
    axis.axhline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1)
    axis.axvline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1)
    axis.set_xlim(lo, hi); axis.set_ylim(lo, hi); axis.set_box_aspect(1)
    axis.set_xticks(range(35, 56, 5)); axis.set_yticks(range(35, 56, 5))
    axis.set_xlabel(xlabel)
    axis.text(hi - 2.0, hi - 2.9, "1:1", ha="center", va="center", fontsize=7, color=MUTED, rotation=45)
    axis.text(lo + 0.4, THR + 0.35, f"{THR:.0f} °C", fontsize=7, color=MUTED, va="bottom")

# (a) form model, no grouping ------------------------------------------------------
frame(ax, "Predicted PET (°C)\nurban-form model: sky, building, tree view factors")
ax.scatter(d.pred_form, d.PET, s=15, color=GREY, edgecolor="white", lw=0.5, zorder=4)
ax.set_ylabel("Measured PET (°C)")
fa = d[(d.pred_form >= THR) & (d.PET < THR)]
ax.text(hi - 0.4, lo + 0.5, f"predicted extreme,\nmeasured not: {len(fa)} sites", ha="right", va="bottom", fontsize=7, color=INK, linespacing=1.3)
ax.text(0.0, 1.03, f"n = {len(d)}   R² = {m_form.rsquared:.2f}   RMSE = {rmse(m_form):.1f} °C", transform=ax.transAxes, ha="left", va="bottom", fontsize=7.2, color=INK)

# (b) label model: two groups, group mean = prediction ----------------------------
d["pred_lab"] = m_lab.fittedvalues
S, H = d[d.sun == 1], d[d.sun == 0]
rng = np.random.default_rng(5)
bx.add_patch(Rectangle((-0.42, lo), 0.84, THR - lo, facecolor=SURF, edgecolor="none", zorder=0))
bx.axhline(THR, color=MUTED, lw=0.6, ls=(0, (4, 3)), zorder=1)
for i, (q, col, name) in enumerate(((S, SUN, "sunlit"), (H, SHADE, "shaded"))):
    x = i + rng.uniform(-0.22, 0.22, len(q))
    mu = q.PET.mean()
    bx.vlines(x, mu, q.PET, color=col, lw=0.45, alpha=0.45, zorder=2)      # residual = distance to the group mean
    bx.scatter(x, q.PET, s=15, color=col, edgecolor="white", lw=0.5, zorder=4)
    bx.plot([i - 0.34, i + 0.34], [mu, mu], color=col, lw=1.8, zorder=5)
    bx.text(i + 0.40, mu + (0.0 if i == 0 else -0.9), f"predicted\n{mu:.1f} °C", ha="left", va="center" if i == 0 else "top", fontsize=6.8, color=col, linespacing=1.15)
bx.set_xlim(-0.6, 1.75); bx.set_ylim(lo, hi); bx.set_box_aspect(1)
bx.set_xticks([0, 1]); bx.set_xticklabels([f"sunlit\nn = {len(S)}", f"shaded\nn = {len(H)}"])
bx.set_yticks(range(35, 56, 5)); bx.tick_params(labelleft=False)
bx.set_xlabel("Sun/shade read on the panorama\nsun/shade model: prediction = group mean")
bx.text(-0.55, THR + 0.35, f"{THR:.0f} °C", fontsize=7, color=MUTED, va="bottom")
fb = d[(d.pred_lab >= THR) & (d.PET < THR)]
bx.text(0.0, lo + 0.5, f"predicted extreme,\nmeasured not: {len(fb)} site{'s' if len(fb)!=1 else ''}", ha="center", va="bottom", fontsize=7, color=INK, linespacing=1.3)
bx.text(0.0, 1.03, f"n = {len(d)}   R² = {m_lab.rsquared:.2f}   RMSE = {rmse(m_lab):.1f} °C", transform=bx.transAxes, ha="left", va="bottom", fontsize=7.2, color=INK)
bx.text(1.0, 0.94, "thin line = error,\npoint to its group mean", transform=bx.transAxes, ha="right", va="top", fontsize=6.6, color=MUTED, linespacing=1.2)

# (c) model ladder ----------------------------------------------------------------
names = list(models); vals = [rmse(models[k]) for k in names]; r2 = [models[k].rsquared for k in names]
ypos = np.arange(len(names))[::-1]
cols = [FAINT, FAINT, INK, INK]
cx.barh(ypos, vals, height=0.42, color=cols, edgecolor="none", zorder=3)
for y, v, r in zip(ypos, vals, r2):
    cx.text(v + 0.08, y, f"{v:.1f} °C   R² {r:.2f}", va="center", ha="left", fontsize=7, color=INK)
cx.set_yticks(ypos); cx.set_yticklabels(names, fontsize=7)
cx.set_xlim(0, 6.4); cx.set_xticks([0, 1, 2, 3, 4])
cx.set_xlabel("RMSE of PET (°C)")
cx.spines["left"].set_visible(False); cx.tick_params(axis="y", length=0)

for a_, t_ in ((ax, "(a)  Urban-form model"), (bx, "(b)  Sun/shade model"), (cx, "(c)  Model ladder")):
    x0_ = a_.get_position().x0
    fig.text(x0_, 0.975, t_, ha="left", va="bottom", fontsize=9, fontweight="bold", color=INK)
fig.legend(handles=[Line2D([], [], marker="o", color="none", markerfacecolor=SUN, markersize=4.8, label="sunlit — direct beam at the sensor"),
                    Line2D([], [], marker="o", color="none", markerfacecolor=SHADE, markersize=4.8, label="shaded — no direct beam"),
                    Line2D([], [], marker="o", color="none", markerfacecolor=GREY, markersize=4.8, label="all sites, label not used (a)")],
           loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=3, frameon=False, fontsize=7.2, handletextpad=0.3, columnspacing=1.6)
for e in ("pdf", "eps", "png"):
    fig.savefig(f"{OUT}/SCS_Fig_form_vs_sun.{e}", bbox_inches="tight", pad_inches=0.03)
print("saved", OUT)
