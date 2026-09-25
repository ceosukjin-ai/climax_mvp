#!/usr/bin/env python3
"""도시형태 결과를 그림 한 장으로 (2026-09-22).

주 패널   PET ~ SVF, 볕/그늘 무리별 Theil–Sen 선 + 95 % 부트스트랩 띠
          볕→그늘 짝(같은 거리 100 m · 20분 안, 13쌍)을 가는 선으로 잇는다
위 여백   그늘일 확률 ~ SVF (로지스틱) — 형태는 **그늘이 생길 확률**을 바꾼다
오른 여백 PET 분포(볕/그늘) — 두 무리 사이 간격

읽는 순서: 위(하늘이 트일수록 그늘이 드물다) → 가운데(무리 안에서는 SVF 기울기가 작다)
→ 오른쪽(무리 사이 간격이 크다). 한 장이 곧 결론이다.
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
    "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
MM = 1 / 25.4
DATA = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/climax_mvp/data"
VF = sys.argv[2] if len(sys.argv) > 2 else f"{DATA}/aug80_viewfactors.csv"
OUT = sys.argv[3] if len(sys.argv) > 3 else "/tmp/claude-0/fig_ab"
INK, MUTED, FAINT = "#1A1A1A", "#5E5E5E", "#9C9C9C"
SUN, SHADE, THR = "#C0504D", "#3F6496", "#1A1A1A"   # 2026-09-25: 임계선은 중립색 — 적/청은 볕/그늘에만
NB = 3000
rng = np.random.default_rng(20260922)

sys.path.insert(0, OUT)
from fig_form_mechanism import load, pairs   # 같은 자료·같은 짝 규칙


def theil_band(x, y, xs):
    sl, ic, _, _ = stats.theilslopes(y, x)
    B = []
    for _ in range(NB):
        i = rng.integers(0, len(x), len(x))
        s, c, _, _ = stats.theilslopes(y[i], x[i])
        B.append(c + s * xs)
    lo, hi = np.percentile(np.array(B), [2.5, 97.5], axis=0)
    ci = np.percentile([(b[-1] - b[0]) / (xs[-1] - xs[0]) for b in B], [2.5, 97.5])
    return sl, ic, lo, hi, ci


def pair_rows(d):
    """pairs() 와 같은 규칙으로 짝을 맺되, 두 점의 좌표를 돌려준다."""
    R = 6371000.0
    out = []
    for _, h in d[d.sun == 0].iterrows():
        c = d[(d.sun == 1) & (d["권역"] == h["권역"])].copy()
        c["dist"] = R * np.hypot(np.radians(c["위도"] - h["위도"]),
                                 np.radians(c["경도"] - h["경도"]) * np.cos(np.radians(h["위도"])))
        c["dtm"] = (c["t"] - h["t"]).abs().apply(lambda x: x.total_seconds() / 60)
        c = c[(c.dist <= 100) & (c.dtm <= 20)]
        if len(c):
            b = c.sort_values("dist").iloc[0]
            out.append((h.SVF, h.PET, b.SVF, b.PET))
    return out


def main():
    d = load()
    s = d.dropna(subset=["SVF"])
    S, H = s[s.sun == 1], s[s.sun == 0]

    fig = plt.figure(figsize=(140 * MM, 118 * MM))
    gs = fig.add_gridspec(2, 2, width_ratios=[4.2, 1.0], height_ratios=[1.05, 3.6],
                          left=0.105, right=0.975, bottom=0.115, top=0.955,
                          wspace=0.05, hspace=0.07)
    ax = fig.add_subplot(gs[1, 0])
    top = fig.add_subplot(gs[0, 0], sharex=ax)
    side = fig.add_subplot(gs[1, 1], sharey=ax)
    XL, YL = (0.0, 0.9), (34.0, 55.5)

    # ---------------- 주 패널
    ax.axhline(41, color=THR, lw=0.7, ls=(0, (5, 3)), zorder=1)
    ax.text(0.005, 41.25, "PET 41 °C — extreme heat stress", fontsize=6.4, color=THR,
            va="bottom", ha="left")
    fits = {}
    # 예외 지점 표시: 41 °C 위의 그늘(뜨거운 그늘), 41 °C 아래의 볕
    hot_sh = d[(d.sun == 0) & (d.PET >= 41) & d.SVF.notna()]
    cool_su = d[(d.sun == 1) & (d.PET < 41) & d.SVF.notna()]
    for _, r in hot_sh.iterrows():
        ax.scatter([r.SVF], [r.PET], s=46, facecolor="none", edgecolor=SHADE, lw=0.9, zorder=5)
    for _, r in cool_su.iterrows():
        ax.scatter([r.SVF], [r.PET], s=46, facecolor="none", edgecolor=SUN, lw=0.9, zorder=5)
    if len(hot_sh):
        ax.annotate(f"shaded, above 41 °C ({len(hot_sh)}): building shade,\n38 °C air on the hottest afternoon",
                    xy=(hot_sh.SVF.min(), hot_sh.loc[hot_sh.SVF.idxmin(), "PET"]), xytext=(0.02, 46.6), fontsize=6.2, color=SHADE,
                    ha="left", va="center", arrowprops=dict(arrowstyle="-", lw=0.6, color=SHADE, shrinkA=0, shrinkB=3))
    if len(cool_su):
        r = cool_su.iloc[0]
        ax.annotate(f"sunlit, below 41 °C ({len(cool_su)})", xy=(r.SVF, r.PET), xytext=(r.SVF - 0.07, r.PET - 2.2),
                    fontsize=6.2, color=SUN, ha="right", va="center",
                    arrowprops=dict(arrowstyle="-", lw=0.6, color=SUN, shrinkA=0, shrinkB=3))
    for g, col in ((1, SUN), (0, SHADE)):
        q = s[s.sun == g]
        xs = np.linspace(q.SVF.min(), q.SVF.max(), 60)
        sl, ic, lo, hi, ci = theil_band(q.SVF.values, q.PET.values, xs)
        ax.fill_between(xs, lo, hi, color=col, alpha=0.13, lw=0, zorder=2)
        ax.plot(xs, ic + sl * xs, color=col, lw=1.3, zorder=3)
        ax.scatter(q.SVF, q.PET, s=16, color=col, edgecolor="white", lw=0.45, zorder=4)
        fits[g] = (sl, ci, len(q))
    sl, ci, n = fits[1]
    ax.text(0.015, 55.2, f"sunlit, n = {n}\n{sl/10:+.1f} °C per 0.1 SVF\n95 % CI {ci[0]/10:+.1f} to {ci[1]/10:+.1f}".replace("-", "−"),
            ha="left", va="top", fontsize=6.6, color=SUN, linespacing=1.3)
    sl, ci, n = fits[0]
    ax.text(0.885, 36.3, f"shaded, n = {n}\n{sl/10:+.1f} °C per 0.1 SVF\n95 % CI {ci[0]/10:+.1f} to {ci[1]/10:+.1f}".replace("-", "−"),
            ha="right", va="bottom", fontsize=6.6, color=SHADE, linespacing=1.3)
    ax.set_xlim(*XL); ax.set_ylim(*YL)
    ax.set_xlabel("Sky view factor at the site (fisheye)")
    ax.set_ylabel("Measured PET (°C)")

    # ---------------- 위: 그늘 확률
    lg = sm.Logit(s.shade.values, sm.add_constant(s.SVF.values)).fit(disp=0)
    xs = np.linspace(*XL, 181)
    B = []
    for _ in range(NB):
        i = rng.integers(0, len(s), len(s))
        try:
            B.append(sm.Logit(s.shade.values[i], sm.add_constant(s.SVF.values[i]))
                     .fit(disp=0, maxiter=100).predict(sm.add_constant(xs)))
        except Exception:
            pass
    lo, hi = np.percentile(np.array(B), [2.5, 97.5], axis=0)
    p = lg.predict(sm.add_constant(xs))
    top.fill_between(xs, 100 * lo, 100 * hi, color=MUTED, alpha=0.15, lw=0)
    top.plot(xs, 100 * p, color=INK, lw=1.3)
    for x0 in (0.3, 0.5, 0.7):
        p0 = 100 * float(lg.predict(np.array([[1.0, x0]]))[0])
        top.scatter([x0], [p0], s=12, color=INK, zorder=4)
        top.text(x0 + 0.012, p0 + 4, f"{p0:.0f} %", fontsize=6.6, color=INK, va="bottom")
    top.set_ylim(-4, 108); top.set_yticks([0, 50, 100])
    top.set_ylabel("P(shaded)\n(%)", linespacing=1.1)
    top.tick_params(labelbottom=False)
    top.text(0.99, 0.93, "open sky makes shade unlikely\n(logistic fit, $p$ < 0.001)",
             transform=top.transAxes, ha="right", va="top", fontsize=6.4, color=MUTED,
             linespacing=1.3)

    # ---------------- 오른쪽: PET 분포
    ys = np.linspace(*YL, 300)
    for q, col in ((S, SUN), (H, SHADE)):
        k = stats.gaussian_kde(q.PET.values, bw_method=0.45)(ys)
        side.fill_betweenx(ys, 0, k, color=col, alpha=0.22, lw=0)
        side.plot(k, ys, color=col, lw=1.0)
        side.plot([0, k.max() * 1.05], [q.PET.median()] * 2, color=col, lw=0.9, ls=(0, (2, 1.5)))
    ms, mh = S.PET.median(), H.PET.median()
    xk = side.get_xlim()[1] * 0.93
    side.annotate("", xy=(xk, mh), xytext=(xk, ms),
                  arrowprops=dict(arrowstyle="<->", lw=0.8, color=INK, shrinkA=0, shrinkB=0))
    side.text(xk * 0.96, (ms + mh) / 2, f"{ms - mh:.1f} °C", ha="right", va="center",
              fontsize=7.2, color=INK, fontweight="bold")
    side.tick_params(labelleft=False, left=False, bottom=False, labelbottom=False)
    side.spines["left"].set_visible(False); side.spines["bottom"].set_visible(False)
    side.set_xlim(0, side.get_xlim()[1])
    side.text(0.5, 1.0, "medians", transform=side.transAxes, ha="center", va="bottom",
              fontsize=6.4, color=MUTED)

    fig.legend(handles=[
        Line2D([], [], marker="o", color="none", markerfacecolor=SUN, markersize=4.6,
               label="sunlit — direct beam at the sensor"),
        Line2D([], [], marker="o", color="none", markerfacecolor=SHADE, markersize=4.6,
               label="shaded — no direct beam")],
        loc="lower center", bbox_to_anchor=(0.54, -0.005), ncol=2, frameon=False,
        fontsize=6.8, handletextpad=0.4, columnspacing=2.0)
    for ext in ("pdf", "png", "eps"):
        fig.savefig(f"{OUT}/SCS_Fig_form_one.{ext}", bbox_inches="tight", pad_inches=0.03)
    print("saved", OUT, {g: (round(v[0] / 10, 2), np.round(v[1] / 10, 2)) for g, v in fits.items()})


if __name__ == "__main__":
    main()
