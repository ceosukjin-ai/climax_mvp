#!/usr/bin/env python3
"""도시형태 결과를 두 그림으로 다시 짠다 (2026-09-22).

옛 4패널 산점도(폭·H/W·SVF → PET, p 값 표)는 「상관이 있나 없나」만 물어서, 볕 지점에서
세 지표가 모두 유의하지 않게 나오자 결론이 없는 그림이 되었다. 같은 자료로 두 질문을 바꾼다.

Fig. A  메커니즘 — 형태는 **그늘이 생길 확률**을 바꾼다
  (a) 그늘 확률 ~ SVF  로지스틱 회귀 + 95 % 부트스트랩 띠
  (b) PET ~ SVF 를 볕·그늘 무리별로 — 무리 안 기울기는 작고, 무리 사이 간격이 크다

Fig. B  크기 — 「몇 도 차이인가」
  볕 지점에서 각 형태지표를 사분위 범위(IQR)만큼 바꿀 때의 PET 변화(Theil–Sen × IQR)와
  볕→그늘 짝 비교(같은 동네 100 m · 20분 안)의 PET 차, 전부 95 % 부트스트랩 신뢰구간.
  짝을 맞추지 않은 전체 볕−그늘 차(7.7 K)는 측정 날·시각이 섞인 값이라 회색 참고로만.

자료 (정본)
  tier3_engine_output_80_v7_photo.csv  PET, Ta, 볕(v7 사진검증), 좌표, 시각
  aug80_viewfactors.csv                SVF (9/21 일관 정의, 158번 제외 → n 79)
  tier3_width_80.csv                   width_m, hw_ratio (7곳 결측 → n 73)
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from scipy import stats
import statsmodels.api as sm

mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 7.5, "axes.labelsize": 8, "axes.titlesize": 8.5,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.7, "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
    "axes.grid": False,
})
MM = 1 / 25.4
DATA = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/climax_mvp/data"
VF = sys.argv[2] if len(sys.argv) > 2 else f"{DATA}/aug80_viewfactors.csv"
OUT = sys.argv[3] if len(sys.argv) > 3 else "/tmp/claude-0/fig_ab"
INK, MUTED, GRID = "#1A1A1A", "#5E5E5E", "#BDBDBD"
SUN, SHADE, THR = "#C0504D", "#4E6E9F", "#B3261E"
NB = 4000
rng = np.random.default_rng(20260922)


def load():
    m = pd.read_csv(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")
    v = pd.read_csv(VF, encoding="utf-8-sig")
    v.loc[v["비고"].notna(), "SVF"] = np.nan          # 158번 자세복원 실패
    w = pd.read_csv(f"{DATA}/tier3_width_80.csv", encoding="utf-8-sig")
    d = (m[["측정ID", "시각", "권역", "위도", "경도", "Ta", "PET", "볕"]]
         .merge(v[["측정ID", "SVF"]], on="측정ID")
         .merge(w[["측정ID", "width_m", "hw_ratio"]], on="측정ID", how="left"))
    d["sun"] = d["볕"].astype(int)
    d["shade"] = 1 - d["sun"]
    d["t"] = pd.to_datetime(d["시각"])
    return d


def pairs(d, dmax=100.0, tmax=20.0):
    """그늘 지점마다 같은 동네·dmax m·tmax 분 안의 가장 가까운 볕 지점."""
    R = 6371000.0
    out = []
    for _, h in d[d.sun == 0].iterrows():
        c = d[(d.sun == 1) & (d["권역"] == h["권역"])].copy()
        c["dist"] = R * np.hypot(np.radians(c["위도"] - h["위도"]),
                                 np.radians(c["경도"] - h["경도"]) * np.cos(np.radians(h["위도"])))
        c["dtm"] = (c["t"] - h["t"]).abs().apply(lambda x: x.total_seconds() / 60)
        c = c[(c.dist <= dmax) & (c.dtm <= tmax)]
        if len(c):
            b = c.sort_values("dist").iloc[0]
            out.append(dict(dPET=b.PET - h.PET, dTa=b.Ta - h.Ta, dist=b["dist"], dt=b["dtm"]))
    return pd.DataFrame(out)


def boot(fn, *arrs):
    n = len(arrs[0])
    vals = []
    for _ in range(NB):
        i = rng.integers(0, n, n)
        vals.append(fn(*[a[i] for a in arrs]))
    return np.percentile(vals, [2.5, 97.5])


def iqr_effect(x, y):
    q1, q3 = np.percentile(x, [25, 75])
    return stats.theilslopes(y, x)[0] * (q3 - q1)


# ======================================================================== Fig A
def fig_a(d):
    s = d.dropna(subset=["SVF"])
    X = sm.add_constant(s["SVF"].values)
    lg = sm.Logit(s["shade"].values, X).fit(disp=0)
    xs = np.linspace(0.0, 0.9, 181)
    Xs = sm.add_constant(xs)
    p_fit = lg.predict(Xs)
    # 부트스트랩 띠
    B = []
    sv, sh = s["SVF"].values, s["shade"].values
    for _ in range(NB):
        i = rng.integers(0, len(s), len(s))
        try:
            b = sm.Logit(sh[i], sm.add_constant(sv[i])).fit(disp=0, maxiter=100)
            B.append(b.predict(Xs))
        except Exception:
            continue
    lo, hi = np.percentile(np.array(B), [2.5, 97.5], axis=0)

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(190 * MM, 78 * MM),
                                 gridspec_kw=dict(width_ratios=[1, 1.08]))
    fig.subplots_adjust(left=0.07, right=0.985, top=0.86, bottom=0.25, wspace=0.28)

    # (a) 그늘 확률
    ax.fill_between(xs, lo, hi, color=SHADE, alpha=0.14, lw=0, zorder=1)
    ax.plot(xs, p_fit, color=SHADE, lw=1.6, zorder=3)
    jit = rng.uniform(-0.035, 0.035, len(s))
    ax.scatter(s.SVF[s.shade == 1], 1.0 + jit[s.shade.values == 1], s=12, color=SHADE,
               lw=0, alpha=0.8, zorder=4)
    ax.scatter(s.SVF[s.shade == 0], 0.0 + jit[s.shade.values == 0], s=12, color=SUN,
               lw=0, alpha=0.8, zorder=4)
    for x0 in (0.3, 0.5, 0.7):
        p0 = float(lg.predict(np.array([[1.0, x0]]))[0])
        ax.plot([x0, x0], [-0.12, p0], color=GRID, lw=0.6, ls=(0, (2, 2)), zorder=2)
        ax.scatter([x0], [p0], s=18, color=INK, zorder=5)
        ax.text(x0 + 0.015, p0 + 0.035, f"{100*p0:.0f} %", fontsize=7.2, color=INK,
                ha="left", va="bottom")
    ax.text(0.02, 1.075, "shaded sites", fontsize=6.8, color=SHADE, va="bottom")
    ax.text(0.02, -0.075, "sunlit sites", fontsize=6.8, color=SUN, va="top")
    ax.set_xlim(0, 0.9); ax.set_ylim(-0.16, 1.16)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0]); ax.set_yticklabels(["0", "25", "50", "75", "100"])
    ax.set_xlabel("Sky view factor (fisheye)")
    ax.set_ylabel("Probability that the site is shaded (%)")
    ax.set_title("(a)  Open sky makes shade unlikely", loc="left", fontweight="bold", pad=8)
    ax.text(0.98, 0.60, f"logistic fit, n = {len(s)}\n$p$ {'< 0.001' if lg.pvalues[1] < 1e-3 else '= %.3f' % lg.pvalues[1]}",
            transform=ax.transAxes, ha="right", va="center", fontsize=6.8, color=MUTED,
            linespacing=1.35)

    # (b) 무리별 PET ~ SVF
    res = {}
    for g, col, lab in ((1, SUN, "sunlit"), (0, SHADE, "shaded")):
        q = s[s.sun == g]
        bx.scatter(q.SVF, q.PET, s=14, color=col, edgecolor="white", lw=0.4, zorder=4)
        sl, ic, _, _ = stats.theilslopes(q.PET, q.SVF)
        ci = boot(lambda x, y: stats.theilslopes(y, x)[0], q.SVF.values, q.PET.values)
        xr = np.array([q.SVF.min(), q.SVF.max()])
        bx.plot(xr, ic + sl * xr, color=col, lw=1.4, zorder=3)
        res[g] = (sl, ci, q)
    bx.axhline(41, color=THR, lw=0.8, ls=(0, (5, 3)), zorder=1)
    # 두 무리 간격 — SVF 가 겹치는 구간(0.3~0.6)의 중앙값 차
    band = s[(s.SVF >= 0.3) & (s.SVF <= 0.6)]
    ms, mh = band[band.sun == 1].PET.median(), band[band.sun == 0].PET.median()
    xg = 0.07       # 왼쪽 빈 자리 — 점과 겹치지 않는다
    bx.annotate("", xy=(xg, mh), xytext=(xg, ms),
                arrowprops=dict(arrowstyle="<->", lw=0.8, color=INK, shrinkA=0, shrinkB=0))
    bx.text(xg - 0.01, ms + 0.6, f"{ms - mh:.1f} °C  sun vs shade\nat the same sky view\n(SVF 0.3–0.6)",
            ha="left", va="bottom", fontsize=6.8, color=INK, linespacing=1.3)
    for g, yv in ((1, 55.3), (0, 34.2)):
        sl, ci, q = res[g]
        col = SUN if g else SHADE
        lab = "sunlit" if g else "shaded"
        bx.text(0.02, yv, f"{lab} (n = {len(q)}): {sl/10:+.1f} °C per 0.1 SVF  "
                f"[{ci[0]/10:+.1f}, {ci[1]/10:+.1f}]", fontsize=6.9, color=col,
                va="center", transform=bx.get_yaxis_transform() if False else bx.transData)
    bx.set_xlim(0, 0.9); bx.set_ylim(33.2, 56.3)
    bx.set_xlabel("Sky view factor (fisheye)")
    bx.set_ylabel("Measured PET (°C)")
    bx.set_title("(b)  Within sun or shade, sky view barely matters", loc="left",
                 fontweight="bold", pad=8)

    fig.legend(handles=[
        Line2D([], [], marker="o", color="none", markerfacecolor=SUN, markersize=5,
               label="sunlit — direct beam at the sensor"),
        Line2D([], [], marker="o", color="none", markerfacecolor=SHADE, markersize=5,
               label="shaded — no direct beam"),
        Line2D([], [], color=INK, lw=1.4, label="Theil–Sen fit within each group"),
        Line2D([], [], color=THR, lw=0.8, ls=(0, (5, 3)), label="PET 41 °C")],
        loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=4, frameon=False,
        fontsize=6.9, handlelength=1.6, columnspacing=1.8)
    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/SCS_FigA_form_mechanism.{ext}", bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)
    print(f"A: logit b {lg.params[1]:.2f} p {lg.pvalues[1]:.1e}; "
          f"slopes sun {res[1][0]:.2f} {res[1][1]}, shade {res[0][0]:.2f} {res[0][1]}; gap {ms-mh:.2f}")


# ======================================================================== Fig B
def fig_b(d):
    S = d[d.sun == 1]
    rows = []
    for col, lab, unit in (("SVF", "Sky view factor", ""), ("hw_ratio", "Height-to-width ratio", ""),
                           ("width_m", "Street width", " m")):
        q = S[[col, "PET"]].dropna()
        e = iqr_effect(q[col].values, q.PET.values)
        ci = boot(iqr_effect, q[col].values, q.PET.values)
        q1, q3 = np.percentile(q[col], [25, 75])
        fmt = "{:.0f}" if col == "width_m" else "{:.2f}"
        rows.append(dict(lab=lab, sub=f"sunlit sites, {fmt.format(q1)} → {fmt.format(q3)}{unit} (IQR), n = {len(q)}",
                         e=e, lo=ci[0], hi=ci[1], kind="form"))
    P = pairs(d)
    ci = boot(np.median, P.dPET.values)
    rows.append(dict(lab="Sun → shade, matched pairs",
                     sub=f"same street, ≤ 100 m and ≤ 20 min apart, n = {len(P)} pairs",
                     e=float(np.median(P.dPET)), lo=ci[0], hi=ci[1], kind="shade"))
    sun, sha = d[d.sun == 1].PET.values, d[d.sun == 0].PET.values
    diffs = [np.median(rng.choice(sun, len(sun))) - np.median(rng.choice(sha, len(sha))) for _ in range(NB)]
    rows.append(dict(lab="Sun → shade, all sites (unmatched)",
                     sub="different days and hours — includes weather differences",
                     e=float(np.median(sun) - np.median(sha)),
                     lo=np.percentile(diffs, 2.5), hi=np.percentile(diffs, 97.5), kind="ref"))

    fig, ax = plt.subplots(figsize=(140 * MM, 72 * MM))
    fig.subplots_adjust(left=0.40, right=0.97, top=0.88, bottom=0.18)
    ys = np.arange(len(rows))[::-1]
    for y, r in zip(ys, rows):
        col = {"form": INK, "shade": SHADE, "ref": "#9A9A9A"}[r["kind"]]
        ax.plot([r["lo"], r["hi"]], [y, y], color=col, lw=1.6, solid_capstyle="butt", zorder=3)
        ax.scatter([r["e"]], [y], s=34, color=col, zorder=4,
                   marker="o" if r["kind"] != "ref" else "D")
        ax.text(r["hi"] + 0.25, y, f"{r['e']:+.1f} °C".replace("-", "−"), va="center", fontsize=7.2, color=col,
                fontweight="bold" if r["kind"] == "shade" else "normal")
        ax.text(-0.02, y + 0.13, r["lab"], transform=ax.get_yaxis_transform(), ha="right",
                va="center", fontsize=7.4, color=col,
                fontweight="bold" if r["kind"] == "shade" else "normal")
        ax.text(-0.02, y - 0.20, r["sub"], transform=ax.get_yaxis_transform(), ha="right",
                va="center", fontsize=6.3, color=MUTED)
    ax.axvline(0, color=GRID, lw=0.8, zorder=1)
    ax.axhspan(ys[2] - 0.5, ys[0] + 0.5, color="#F2F2F2", zorder=0, lw=0)
    ax.text(0.99, ys[0] + 0.42, "urban form, sun held constant", transform=ax.get_yaxis_transform(),
            ha="right", va="top", fontsize=6.6, color=MUTED, style="italic")
    ax.set_yticks([]); ax.spines["left"].set_visible(False)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(-2.5, 11.0)
    ax.set_xlabel("Change in measured PET (°C), with 95 % bootstrap interval")
    ax.set_title("How much each factor moves PET", loc="left", fontweight="bold", pad=8,
                 x=-0.58)
    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/SCS_FigB_effect_sizes.{ext}", bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)
    for r in rows:
        print(f"B: {r['lab']:<36} {r['e']:+.2f}  [{r['lo']:+.2f}, {r['hi']:+.2f}]")
    print(f"B: pairs dTa median {P.dTa.median():+.2f}, dist {P.dist.median():.0f} m, dt {P.dt.median():.0f} min, "
          f"positive {(P.dPET > 0).sum()}/{len(P)}, Wilcoxon p {stats.wilcoxon(P.dPET).pvalue:.4f}")


if __name__ == "__main__":
    import os
    os.makedirs(OUT, exist_ok=True)
    d = load()
    fig_a(d)
    fig_b(d)
    print("saved ->", OUT)
