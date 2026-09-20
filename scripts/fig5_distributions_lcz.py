#!/usr/bin/env python3
"""Fig. 5 — 80지점 실측 분포, **LCZ 1~5 색으로 구분** (2026-09-20).

원본은 scripts/v7/fig_v7.py 의 Fig 5. 점이 기기별 한 색이라 「이 값이 어느 동네에서
나왔나」를 볼 수 없었다. 점을 LCZ 로 칠하면 분포 안에서 근린이 어디에 몰려 있는지 보인다.
기기 구분은 패널 제목 아래 기기 이름으로 남긴다.

자료(전부 로컬):
  tier3_engine_output_80_v7_photo.csv  Ta RH v Tmrt PET Ts 권역
  aug80_indices.csv                    SVF GVI (2026-09-14 재산출, 158번 자세복원 실패 제외)

각주 정정: 원본의 「46 of 79 sites at 0」은 어느 기준으로도 나오지 않는다.
  정확히 0 = 20지점, 소수 둘째 자리로 0.00 이 되는 것(< 0.005) = 37지점.
"""
from __future__ import annotations
import csv, math, sys
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 7.5, "axes.labelsize": 8, "axes.titlesize": 8.5,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7,
    "axes.linewidth": 0.7, "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
INK, MUTED, GRID = "#1A1A1A", "#5A5A5A", "#D8D8D8"
MM = 1 / 25.4
DATA = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/climax_mvp/data"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/claude-0/fig5"

# LCZ 는 **모양**으로 가른다. 색은 원본처럼 기기(계측기)를 뜻한다 —
# 색까지 LCZ 로 쓰면 기기 구분이 사라진다.
LCZ = [("LCZ 1", "부암제1동", "Buam 1",     "o", 10.0),
       ("LCZ 2", "보수동",   "Bosu",       "s",  8.5),
       ("LCZ 3", "서제2동",  "Seo 2",      "^", 11.0),
       ("LCZ 4", "용호제1동", "Yongho 1",   "D",  7.0),
       ("LCZ 5", "명장동",   "Myeongjang", "v", 11.0)]
CODE = {d: (c, mk, sz) for c, d, _en, mk, sz in LCZ}


def tg_from(tmrt, ta, v):
    """흑구온도 역산 — ISO 7726 강제대류, Ø0.05 m, ε 0.95 (원본과 같은 식)."""
    v = max(v, 0.1)
    h = 1.1e8 * v ** 0.6 / (0.95 * 0.05 ** 0.4)
    lo, hi = ta - 5.0, tmrt + 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        f = ((mid + 273.15) ** 4 + h * (mid - ta)) ** 0.25 - 273.15 - tmrt
        if f < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def load():
    idx = {}
    for r in csv.DictReader(open(f"{DATA}/aug80_indices.csv", encoding="utf-8-sig")):
        bad = (r.get("비고") or "").strip()          # 158번 자세복원 실패
        idx[r["측정ID"]] = dict(
            svf=None if bad else float(r["SVF"]),
            gvi=None if bad else float(r["GVI"]))
    rows = []
    for r in csv.DictReader(open(f"{DATA}/tier3_engine_output_80_v7_photo.csv",
                                 encoding="utf-8-sig")):
        a = idx.get(r["측정ID"], {})
        ta, tmrt, v = float(r["Ta"]), float(r["Tmrt"]), float(r["v"])
        rows.append(dict(lcz=CODE[r["권역"]][0], mk=CODE[r["권역"]][1],
                         sz=CODE[r["권역"]][2],
                         Ta=ta, RH=float(r["RH"]), v=v,
                         Tg=tg_from(tmrt, ta, v), Tmrt=tmrt, PET=float(r["PET"]),
                         SVF=a.get("svf"), GVI=a.get("gvi"), Ts=float(r["Ts"])))
    return rows


BLUE, REDC, GREEN, VIOLET = "#1F6FB2", "#C8394A", "#0B9A9C", "#7B4FB5"
PANELS = [("Air temperature (°C)", "Weather meter", BLUE, "Ta", "°C", 1),
          ("Relative humidity (%)", "Weather meter", BLUE, "RH", "%", 1),
          ("Wind speed (m/s)", "Weather meter", BLUE, "v", "m/s", 2),
          ("Globe temperature (°C)", "Globe thermometer", REDC, "Tg", "°C", 1),
          ("Mean radiant temp., globe-derived (°C)", "Globe thermometer", REDC, "Tmrt", "°C", 1),
          ("Physiological equivalent temperature (°C)", "Globe thermometer", REDC, "PET", "°C", 1),
          ("Sky view factor", "360° camera", GREEN, "SVF", "", 2),
          ("Tree view factor", "360° camera", GREEN, "GVI", "", 2),
          ("Pavement surface temperature (°C)", "Thermal camera", VIOLET, "Ts", "°C", 1)]


def main():
    rows = load()
    import statistics as st
    fig, axes = plt.subplots(3, 3, figsize=(190 * MM, 122 * MM))
    fig.subplots_adjust(left=0.04, right=0.99, top=0.83, bottom=0.135,
                        hspace=1.15, wspace=0.2)

    # 원본 그대로 한 줄에 흩뿌리고 **색만** LCZ 로 바꾼다. 층지게 나누면 분포의
    # 모양(어디에 몰렸나)이 깨져서 원래 그림이 하던 말을 못 한다.
    import random
    rng = random.Random(1)

    for ax, (title, inst, ic, key, unit, dec) in zip(axes.flat, PANELS):
        xs = [(r[key], r["lcz"], r["mk"], r["sz"]) for r in rows if r[key] is not None]
        v = sorted(x for x, _l, _m, _s in xs)
        med = st.median(v)
        q1 = v[max(0, int(0.25 * (len(v) - 1)))]
        q3 = v[min(len(v) - 1, int(0.75 * (len(v) - 1)))]
        ax.axvspan(q1, q3, color=ic, alpha=0.15, zorder=1)
        ax.plot([v[0], v[-1]], [0, 0], color=ic, lw=0.8, alpha=0.6, zorder=2)
        for x, _l, mk, sz in xs:
            ax.scatter([x], [rng.uniform(-0.28, 0.28)], s=sz, marker=mk, color=ic,
                       zorder=3, lw=0)
        ax.plot([med, med], [-0.55, 0.55], color=INK, lw=1.4, zorder=4)
        ax.set_ylim(-0.7, 0.7); ax.set_yticks([])
        ax.spines["left"].set_visible(False)
        ax.set_title(title, loc="left", pad=16, fontweight="bold", color=INK)
        ax.text(0, 1.12, inst, transform=ax.transAxes, color=ic, fontsize=7,
                va="bottom", fontweight="bold")
        n = f" · n = {len(v)}" if len(v) < 80 else ""
        ax.text(1, 1.12,
                f"median {med:.{dec}f}{(' ' + unit) if unit else ''} · "
                f"{v[0]:.{dec}f}–{v[-1]:.{dec}f}{n}",
                transform=ax.transAxes, color=MUTED, fontsize=6.8, va="bottom", ha="right")
        ax.grid(True, axis="x", color=GRID, lw=0.5, zorder=0)

    fig.suptitle("Distribution of the field measurements, by instrument and local climate zone (80 sites)",
                 x=0.04, ha="left", y=0.995, fontsize=10, fontweight="bold", color=INK)
    fig.text(0.04, 0.95,
             "Same site, same minute. Each marker is one site; marker shape gives the local "
             "climate zone and colour gives the instrument. Black line = median, band = "
             "interquartile range. "
             "View factors re-derived on 2026-09-14 (fisheye stitched, tilted cube-map; "
             "79 sites — one pose failure excluded).",
             fontsize=6.8, color=MUTED)
    fig.legend(handles=[Line2D([], [], marker=mk, color="none", markerfacecolor="#4A4A4A",
                               markersize=4.6, label=f"{code} · {en}")
                        for code, _d, en, mk, _sz in LCZ],
               loc="lower center", bbox_to_anchor=(0.5, 0.055), ncol=5, frameon=False,
               fontsize=7.0, handlelength=1.0, handletextpad=0.45, columnspacing=1.8)
    fig.text(0.04, 0.012,
             "Globe temperature is back-calculated from the globe-derived T$_{mrt}$ "
             "(ISO 7726, Ø0.05 m, ε 0.95). Tree view factor: median 0.01; 20 of the 79 sites "
             "are exactly 0 and 37 round to 0.00 — the sites were chosen without street trees. "
             "Pavement temperature: 90th percentile of sunlit pavement; thermal-camera clock "
             "offset (+35 min) verified, pairing unchanged.",
             fontsize=6.4, color=MUTED, wrap=True)

    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/SCS_Fig5_distributions_lcz.{ext}", bbox_inches="tight",
                    pad_inches=0.03)
    print("saved ->", OUT)


if __name__ == "__main__":
    main()
