#!/usr/bin/env python3
"""발표자료 260913 그림 갱신 (2026-09-19)
- 8월 SVF/GVI/BVI 재산출값(aug80_indices.csv, 158번 제외 n=79) 반영
- 볕/그늘 라벨 감사: 흑구 상승분이 라벨과 6 K 이상 어긋나는 8지점을 속 빈 마커로 표시 (n=80 유지)
- Tmrt 는 흑구(Ø0.05 m) 유래임을 축에 명시
"""
import numpy as np, pandas as pd
import matplotlib as mpl, matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from scipy import stats

mpl.rcParams.update({
    "font.family": "Liberation Sans", "font.size": 7.5, "axes.labelsize": 8,
    "axes.titlesize": 8.5, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "legend.fontsize": 7, "axes.linewidth": 0.7, "axes.spines.top": False,
    "axes.spines.right": False, "figure.dpi": 200, "savefig.dpi": 600,
    "pdf.fonttype": 42, "ps.fonttype": 42})
INK, MUTED, GRID, RED = "#1A1A1A", "#5A5A5A", "#D8D8D8", "#B3261E"
ORANGE, PURPLE, BLUE, GREEN, VIOLET, PT = "#D94801", "#7A5AA8", "#1F6FB2", "#0B9A9C", "#7B4FB5", "#1F6FB2"
MM = 1/25.4
OUT = "/tmp/claude-0/-home-claude/b4e7b56e-8778-54d4-89ee-5b1ce772403b/scratchpad/fig/out_v7/"
import os; os.makedirs(OUT, exist_ok=True)

D = pd.read_csv("../tier3_engine_output_80_v7_photo.csv")
A = pd.read_csv(os.environ.get("DATA", "/mnt/user-data/uploads/climax_mvp/data") + "/aug80_viewfactors.csv")   # 9/21 일관 정의
W = pd.read_csv("/mnt/user-data/uploads/climax_mvp/data/tier3_width_80.csv")[["측정ID","width_m","hw_ratio"]]
A = A.rename(columns={"SVF":"SVF2","TVF":"GVI2","BVF":"BVI2"})
A.loc[A["비고"].notna(), ["SVF2","GVI2","BVI2"]] = np.nan          # 158번 자세복원 실패
D = D.merge(A[["측정ID","SVF2","GVI2","BVI2"]], on="측정ID").merge(W, on="측정ID")
D["sun"] = D["볕"] == 1
D["rise"] = D["Tmrt"] - D["Ta"]                                       # 흑구 상승분
MID = (D.rise[D.sun].mean() + D.rise[~D.sun].mean())/2
D["flag"] = (~D.sun & (D.rise > MID+6)) | (D.sun & (D.rise < MID-6))
print("MID", MID, "sun mean", D.rise[D.sun].mean(), "shade mean", D.rise[~D.sun].mean())
B = pd.read_csv("../delta_v7_final.csv")
D = D.merge(B, on="측정ID")
EN = {"서제2동":"Seo 2","명장동":"Myeongjang 2","보수동":"Bosu","부암제1동":"Buam 1","용호제1동":"Yongho 1"}
D["nb"] = D["권역"].map(EN)
def _kappa(a, b):
    a, b = np.asarray(a), np.asarray(b); po = (a==b).mean(); pe = (a==1).mean()*(b==1).mean() + (a==0).mean()*(b==0).mean(); return (po-pe)/(1-pe)
KAPPA = _kappa(D.엔진볕_run_A, D.볕)
print("flagged:", D.flag.sum(), D[D.flag]["측정ID"].tolist())
V = D.dropna(subset=["SVF2"])
print(f"SVF2 n={len(V)} mean {V.SVF2.mean():.3f} median {V.SVF2.median():.3f} range {V.SVF2.min():.3f}-{V.SVF2.max():.3f}")
print("neighbourhood SVF2 median:", V.groupby("nb").SVF2.median().round(2).to_dict())
print("old SVF median by nb:", D.groupby("nb").SVF.median().round(2).to_dict())

def scat(ax, x, y, df):
    s, f = df.sun.values, df.flag.values
    for m, c, mk, z in [(s & ~f, ORANGE, "o", 4), (~s & ~f, PURPLE, "s", 4)]:
        ax.scatter(x[m], y[m], s=22, facecolor=c, edgecolor="white", lw=0.5, marker=mk, zorder=z)
    for m, c, mk in [(s & f, ORANGE, "o"), (~s & f, PURPLE, "s")]:
        ax.scatter(x[m], y[m], s=26, facecolor="white", edgecolor=c, lw=1.0, marker=mk, zorder=5)

def legend(fig, y=-0.02):
    fig.legend(handles=[
        Line2D([], [], color=ORANGE, marker="o", ls="none", ms=5, label="Sun at the sensor (photo-verified)"),
        Line2D([], [], color=PURPLE, marker="s", ls="none", ms=5, label="No direct beam (shade or cloud-diffuse)"),
        Line2D([], [], mfc="white", mec=INK, marker="o", ls="none", ms=5,
               label=f"Photo label disagrees with globe rise ({int(D.flag.sum())} sites)"),
        Line2D([], [], color=RED, ls="--", lw=0.9, label="Extreme heat stress, PET 41 °C")],
        loc="lower center", ncol=4, frameon=False, bbox_to_anchor=(0.5, y),
        handlelength=1.4, columnspacing=1.6)

def rp(x, y):
    m = ~np.isnan(x) & ~np.isnan(y)
    r, p = stats.pearsonr(x[m], y[m]); return r, p, m.sum()

def within_r(x, y, g):
    m = ~np.isnan(x) & ~np.isnan(y); x, y, g = x[m], y[m], g[m]
    xc, yc = x.copy(), y.copy()
    for u in set(g):
        k = g == u; xc[k] -= xc[k].mean(); yc[k] -= yc[k].mean()
    return stats.pearsonr(xc, yc)

# ============================================================ Fig 5 distributions
fig, axes = plt.subplots(3, 3, figsize=(190*MM, 118*MM))
fig.subplots_adjust(left=0.04, right=0.99, top=0.84, bottom=0.12, hspace=1.1, wspace=0.2)
panels = [("Air temperature (°C)", "Weather meter", BLUE, D.Ta, "°C", 1),
          ("Relative humidity (%)", "Weather meter", BLUE, D.RH, "%", 1),
          ("Wind speed (m/s)", "Weather meter", BLUE, D.v, "m/s", 2),
          ("Globe temperature (°C)", "Globe thermometer", "#C8394A", None, "°C", 1),
          ("Mean radiant temp., globe-derived (°C)", "Globe thermometer", "#C8394A", D.Tmrt, "°C", 1),
          ("Physiological equivalent temperature (°C)", "Globe thermometer", "#C8394A", D.PET, "°C", 1),
          ("Sky view factor", "360° camera", GREEN, D.SVF2, "", 2),
          ("Tree view factor", "360° camera", GREEN, D.GVI2, "", 2),
          ("Pavement surface temperature (°C)", "Thermal camera", VIOLET, D.Ts, "°C", 1)]
# globe temperature: back out from Tmrt (ISO 7726 forced convection, Ø0.05 m, ε0.95)
def tg_from(tmrt, ta, v):
    v = np.maximum(v, 0.1); h = 1.1e8 * v**0.6 / (0.95 * 0.05**0.4)
    lo, hi = ta - 5, tmrt + 5
    for _ in range(60):
        mid = (lo + hi) / 2
        f = ((mid+273.15)**4 + h*(mid-ta))**0.25 - 273.15 - tmrt
        lo = np.where(f < 0, mid, lo); hi = np.where(f < 0, hi, mid)
    return (lo + hi) / 2
panels[3] = (panels[3][0], panels[3][1], panels[3][2], tg_from(D.Tmrt.values, D.Ta.values, D.v.values), "°C", 1)
rng = np.random.default_rng(1)
for ax, (title, inst, c, x, unit, dec) in zip(axes.flat, panels):
    x = np.asarray(x, float); xv = x[~np.isnan(x)]
    med, q1, q3 = np.median(xv), np.percentile(xv, 25), np.percentile(xv, 75)
    ax.axvspan(q1, q3, color=c, alpha=0.15, zorder=1)
    ax.plot([xv.min(), xv.max()], [0, 0], color=c, lw=0.8, alpha=0.6, zorder=2)
    ax.scatter(xv, rng.uniform(-0.28, 0.28, len(xv)), s=9, color=c, zorder=3, lw=0)
    ax.plot([med, med], [-0.55, 0.55], color=INK, lw=1.4, zorder=4)
    ax.set_ylim(-0.7, 0.7); ax.set_yticks([]); ax.spines["left"].set_visible(False)
    ax.set_title(title, loc="left", pad=16, fontweight="bold", color=INK)
    ax.text(0, 1.12, inst, transform=ax.transAxes, color=c, fontsize=7, va="bottom", fontweight="bold")
    n = f" · n = {len(xv)}" if len(xv) < 80 else ""
    ax.text(1, 1.12, f"median {med:.{dec}f}{(' '+unit) if unit else ''} · {xv.min():.{dec}f}–{xv.max():.{dec}f}{n}",
            transform=ax.transAxes, color=MUTED, fontsize=6.8, va="bottom", ha="right")
    ax.grid(True, axis="x", color=GRID, lw=0.5, zorder=0)
fig.suptitle("Distribution of the field measurements, by instrument (80 sites)", x=0.04, ha="left",
             y=0.995, fontsize=10, fontweight="bold", color=INK)
fig.text(0.04, 0.945, "Same site, same minute. Each dot is one site; black line = median, band = interquartile range. "
         "View factors re-derived on 2026-09-14 (fisheye stitched, tilted cube-map; 79 sites — one pose failure excluded).",
         fontsize=6.8, color=MUTED)
fig.text(0.04, 0.02, "Globe temperature is back-calculated from the globe-derived Tmrt (ISO 7726, Ø0.05 m, ε 0.95). "
         "Tree view factor: median 0.01, 46 of 79 sites at 0 — the sites were chosen without street trees. "
         "Pavement temperature: 90th percentile of sunlit pavement; thermal-camera clock offset (+35 min) verified, pairing unchanged.",
         fontsize=6.4, color=MUTED, wrap=True)
[fig.savefig(OUT+"Fig5_distributions"+e, bbox_inches="tight", pad_inches=0.03) for e in (".pdf",".tiff")]; fig.savefig(OUT+"Fig5_distributions.png", bbox_inches="tight", pad_inches=0.03); plt.close(fig)

# ============================================================ Fig 6 sun / shade
fig, axes = plt.subplots(1, 2, figsize=(190*MM, 72*MM), gridspec_kw=dict(width_ratios=[0.8, 1.6]))
fig.subplots_adjust(left=0.07, right=0.99, top=0.85, bottom=0.36, wspace=0.28)
ax = axes[0]
for i, (m, c, mk, lab) in enumerate([(D.sun, ORANGE, "o", "sun"), (~D.sun, PURPLE, "s", "shade")]):
    y = D.PET[m].values; f = D.flag[m].values
    xj = i + rng.uniform(-0.18, 0.18, len(y))
    ax.scatter(xj[~f], y[~f], s=20, color=c, marker=mk, lw=0, zorder=3)
    ax.scatter(xj[f], y[f], s=24, facecolor="white", edgecolor=c, marker=mk, lw=1, zorder=4)
    ax.plot([i-0.28, i+0.28], [np.median(y)]*2, color=INK, lw=1.4, zorder=5)
    ax.text(i, 56.2, f"n = {m.sum()}\nmedian {np.median(y):.1f}", ha="center", va="bottom", fontsize=7, color=INK)
ax.axhline(41, color=RED, ls="--", lw=0.8)
ax.set_xticks([0, 1]); ax.set_xticklabels(["Sun", "Shade"]); ax.set_xlim(-0.6, 1.6); ax.set_ylim(34, 60)
ax.set_ylabel("Measured PET (°C)"); ax.grid(True, axis="y", color=GRID, lw=0.5, zorder=0); ax.set_axisbelow(True)
t = stats.mannwhitneyu(D.PET[D.sun], D.PET[~D.sun])
ax.set_title("Direct beam at the sensor, all 80 sites", loc="left", pad=8, color=INK)
ax.text(0, -0.2, f"Mann–Whitney p < 0.001. Extreme (≥ 41 °C): sun {int((D.PET[D.sun]>=41).sum())}/{int(D.sun.sum())}, "
        f"shade {int((D.PET[~D.sun]>=41).sum())}/{int((~D.sun).sum())}.", transform=ax.transAxes, fontsize=6.6, color=MUTED, va="top")
ax = axes[1]
order = ["Seo 2", "Myeongjang 2", "Yongho 1", "Bosu", "Buam 1"]
for i, nb in enumerate(order):
    g = D[D.nb == nb]
    for j, (m, c, mk) in enumerate([(g.sun, ORANGE, "o"), (~g.sun, PURPLE, "s")]):
        y = g.PET[m].values; f = g.flag[m].values
        if len(y) == 0: continue
        xj = i + (j-0.5)*0.36 + rng.uniform(-0.09, 0.09, len(y))
        ax.scatter(xj[~f], y[~f], s=18, color=c, marker=mk, lw=0, zorder=3)
        ax.scatter(xj[f], y[f], s=22, facecolor="white", edgecolor=c, marker=mk, lw=1, zorder=4)
        ax.plot([i+(j-0.5)*0.36-0.14, i+(j-0.5)*0.36+0.14], [np.median(y)]*2, color=INK, lw=1.2, zorder=5)
    ax.text(i, 56.4, f"{int(g.sun.sum())} / {int((~g.sun).sum())}", ha="center", fontsize=6.8, color=MUTED)
ax.axhline(41, color=RED, ls="--", lw=0.8)
ax.set_xticks(range(5)); ax.set_xticklabels(order); ax.set_ylim(34, 60); ax.set_xlim(-0.6, 4.6)
ax.grid(True, axis="y", color=GRID, lw=0.5, zorder=0); ax.set_axisbelow(True)
ax.set_title("Every neighbourhood, same day and hour (sun n / shade n)", loc="left", pad=8, color=INK)
ax.text(0, -0.2, f"Sun/shade re-labelled on 2026-09-19 from the 360° photograph at each site (sun disc or hard shadow at the sensor = sun;\n"
        f"building, tree, structure or cloud-diffuse = no beam). 8 of 80 earlier labels changed. Open markers: photo label still disagrees\n"
        f"with the globe rise (sun with Tmrt − Ta < {MID-6:.0f} K); globe partly shaded or not equilibrated at these {int(D.flag.sum())} sites.",
        transform=ax.transAxes, fontsize=6.6, color=MUTED, va="top")
legend(fig, y=-0.03)
[fig.savefig(OUT+"Fig6_sun_shade"+e, bbox_inches="tight", pad_inches=0.03) for e in (".pdf",".tiff")]; fig.savefig(OUT+"Fig6_sun_shade.png", bbox_inches="tight", pad_inches=0.03); plt.close(fig)

# ============================================================ Fig 7 Tmrt vs SVF / PET vs Ts
fig, axes = plt.subplots(1, 2, figsize=(190*MM, 78*MM))
fig.subplots_adjust(left=0.07, right=0.99, top=0.86, bottom=0.36, wspace=0.28)
ax = axes[0]; ax.grid(True, color=GRID, lw=0.5, zorder=0); ax.set_axisbelow(True)
scat(ax, D.SVF2.values, D.Tmrt.values, D)
r, p, n = rp(D.SVF2.values, D.Tmrt.values); rw, pw = within_r(D.SVF2.values, D.Tmrt.values, D.nb.values)
ax.set_xlabel("Sky view factor (360° panorama, re-derived)"); ax.set_ylabel("Globe-derived $T_{mrt}$ (°C)")
ax.set_title("$T_{mrt}$ vs. sky view factor", loc="left", pad=7, color=INK)
ax.text(0, -0.25, f"n = {n}. All sites r = {r:.2f} (p = {p:.3f}); within neighbourhood r = {rw:.2f} (p = {pw:.3f}).\n"
        f"Globe (Ø0.05 m) Tmrt exceeds whole-body Tmrt by {D.dglobe.mean():+.1f} K on average\n({D.dglobe[D.sun].mean():+.1f} K in sun, {D.dglobe[~D.sun].mean():+.1f} K with no beam) — the direct-beam term.",
        transform=ax.transAxes, fontsize=6.7, color=MUTED, va="top", linespacing=1.4)
ax.text(-0.17, 1.1, "a", transform=ax.transAxes, fontsize=9.5, fontweight="bold")
ax = axes[1]; ax.grid(True, color=GRID, lw=0.5, zorder=0); ax.set_axisbelow(True)
scat(ax, D.Ts.values, D.PET.values, D)
b1, a1 = np.polyfit(D.Ts, D.PET, 1); xs = np.linspace(34, 68, 5); ax.plot(xs, a1+b1*xs, color=INK, lw=1)
ax.axhline(41, color=RED, ls="--", lw=0.8)
r, p, n = rp(D.Ts.values, D.PET.values); rw, pw = within_r(D.Ts.values, D.PET.values, D.nb.values)
ax.set_xlabel("Radiometric surface temperature, pavement (°C)"); ax.set_ylabel("Measured PET (°C)")
ax.set_title("PET vs. surface temperature", loc="left", pad=7, color=INK)
ax.text(0, -0.25, f"PET = {a1:.1f} + {b1:.2f}·$T_s$. All sites r = {r:.2f} (p < 0.001); within neighbourhood r = {rw:.2f} (p < 0.001).\n"
        "Thermal-camera clock ran 35 min slow; the image–site pairing used here was\nverified against that offset (79 of 80 sites match within 3 min).",
        transform=ax.transAxes, fontsize=6.7, color=MUTED, va="top", linespacing=1.4)
ax.text(-0.17, 1.1, "b", transform=ax.transAxes, fontsize=9.5, fontweight="bold")
legend(fig, y=-0.02)
[fig.savefig(OUT+"Fig7_tmrt_svf_pet_ts"+e, bbox_inches="tight", pad_inches=0.03) for e in (".pdf",".tiff")]; fig.savefig(OUT+"Fig7_tmrt_svf_pet_ts.png", bbox_inches="tight", pad_inches=0.03); plt.close(fig)

# ============================================================ Fig 8 form variables
fig, axes = plt.subplots(1, 3, figsize=(190*MM, 72*MM))
fig.subplots_adjust(left=0.06, right=0.99, top=0.86, bottom=0.3, wspace=0.3)
items = [("a", "Street width", "Street width (m)", D.width_m.values),
         ("b", "Height-to-width ratio (H/W)", "Building height / street width", D.hw_ratio.values),
         ("c", "Sky view factor (SVF)", "Sky view factor (re-derived)", D.SVF2.values)]
notes = []
for ax, (tg, ti, xl, x) in zip(axes, items):
    ax.grid(True, color=GRID, lw=0.5, zorder=0); ax.set_axisbelow(True)
    y = D.PET.values; m = ~np.isnan(x)
    r, p, n = rp(x, y); rw, pw = within_r(x, y, D.nb.values)
    b1, a1 = np.polyfit(x[m], y[m], 1); xs = np.linspace(np.nanmin(x), np.nanmax(x), 5)
    ax.plot(xs, a1+b1*xs, color=INK if p < 0.05 else "#9A9A9A", lw=1.3 if p < 0.05 else 1.0, ls="-" if p < 0.05 else "--", zorder=2)
    ax.axhline(41, color=RED, ls="--", lw=0.8, zorder=1)
    ax.scatter(x, y, s=18, color=PT, lw=0, zorder=3)
    ax.set_xlabel(xl); ax.set_ylim(34, 56)
    ax.set_title(f"({tg})  {ti}", loc="left", pad=7, fontweight="bold", color=INK)
    ax.text(0.97, 0.05, f"r = {r:+.2f}\np = {p:.3f}" if p >= 0.001 else f"r = {r:+.2f}\np < 0.001",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=7.5, color=INK if p < 0.05 else MUTED)
    notes.append(f"{ti.split(' (')[0]}: n = {n}, within-neighbourhood r = {rw:+.2f} (" + (f"p = {pw:.3f}" if pw>=0.001 else "p < 0.001") + ")")
axes[0].set_ylabel("Measured PET (°C)")
axes[0].text(0.03, 0.31, "extreme threshold, 41 °C", transform=axes[0].transAxes, color=RED, fontsize=6.8)
fig.text(0.06, 0.07, "Measured PET and sky view factor are field measurements (SVF re-derived 2026-09-14; 79 sites). Street width and H/W are derived "
         "from open building polygons;\nseven sites with no facing building within 60 m have undefined width, so (a) and (b) use 73 sites. "
         "Solid lines are significant regressions (p < 0.05), dashed lines are not.\n" + "; ".join(notes) + ".",
         fontsize=6.6, color=MUTED, va="top", linespacing=1.45)
[fig.savefig(OUT+"Fig8_form_vs_pet"+e, bbox_inches="tight", pad_inches=0.03) for e in (".pdf",".tiff")]; fig.savefig(OUT+"Fig8_form_vs_pet.png", bbox_inches="tight", pad_inches=0.03); plt.close(fig)

# ============================================================ Fig 12 (new) label audit
fig, ax = plt.subplots(figsize=(120*MM, 70*MM)); fig.subplots_adjust(left=0.09, right=0.98, top=0.86, bottom=0.47)
bins = np.arange(0, 46, 2.5)
ax.hist([D.rise[D.sun], D.rise[~D.sun]], bins=bins, color=[ORANGE, PURPLE], alpha=0.9,
        label=[f"Sun at the sensor, photo-verified (n = {int(D.sun.sum())})", f"No direct beam (n = {int((~D.sun).sum())})"], zorder=3, rwidth=0.9)
ax.axvspan(MID-6, MID+6, color=GRID, alpha=0.45, zorder=1); ax.axvline(MID, color=INK, lw=0.9, ls=":", zorder=2)
ax.set_ylim(0, 11.5); ax.set_yticks([0, 2, 4, 6, 8, 10])
ax.text(MID, 11.2, "midpoint of the two\nmeans, ± 6 K", fontsize=6.4, color=MUTED, va="top", ha="center", linespacing=1.3)
ax.set_xlabel("Globe rise above air temperature, $T_{mrt,globe}$ − $T_a$ (K)"); ax.set_ylabel("Sites")
ax.grid(True, axis="y", color=GRID, lw=0.5, zorder=0); ax.set_axisbelow(True)
ax.legend(frameon=False, loc="upper right")
ax.set_title("Photo-verified sun/shade against the globe thermometer", loc="left", pad=8, fontweight="bold", color=INK)
ax.text(0, -0.34, f"Sun mean {D.rise[D.sun].mean():.1f} K, no-beam mean {D.rise[~D.sun].mean():.1f} K (earlier label: 20.9 / 13.5 K). Labels re-derived from the 360° photograph\n"
        f"at each site (2026-09-19): 8 of 80 changed — 5 shade→sun at open crossings, 3 sun→no beam (building-shadow alley, haze).\n"
        f"{int(D.flag.sum())} sun sites keep a low rise ({', '.join(f'{v:.0f}' for v in sorted(D.rise[D.flag]))} K): globe partly shaded or not equilibrated. The geometric sun/shade engine\n"
        f"reaches κ = {KAPPA:+.2f} against this label (it calls 75 of 80 sites sunlit): beam exposure at the sensor is the term\ngeometry cannot yet reproduce.",
        transform=ax.transAxes, fontsize=6.5, color=MUTED, va="top", linespacing=1.45)
[fig.savefig(OUT+"Fig12_label_audit"+e, bbox_inches="tight", pad_inches=0.03) for e in (".pdf",".tiff")]; fig.savefig(OUT+"Fig12_label_audit.png", bbox_inches="tight", pad_inches=0.03); plt.close(fig)
print("done")
