#!/usr/bin/env python3
"""Fig. 6 — 볕이냐 그늘이냐가 보행자 열부하를 가른다 (2026-09-21).

구성
  (a) 실측 80지점. 볕 61 / 그늘 19 의 PET 분포 — 중앙값 차 7.7 K, Cliff's δ 0.83,
      극심 기준(PET 41 °C) 위아래 개수.
  (b) **같은 태양·같은 기상에 올려놓은 뒤** 근린별 볕/그늘.
      근린마다 다른 날·다른 시각에 쟀으므로 실측 PET 를 그대로 나란히 두면
      「언제 쟀는가」가 「어떻게 생겼는가」와 섞인다. 그래서 지점의 형태
      (9/14 재산출 어안 SVF·GVI·BVI)와 볕/그늘 라벨만 남기고 태양 위치·기상을
      하나로 고정해 배포 엔진으로 PET 를 다시 계산했다
      (scripts/ref_condition_80.py → data/ref_condition_80.csv).
      기준조건: 2026-08-23 12:30 KST(부산 태양고도 ≈ 65°), Ta 35.0 °C, RH 45 %,
      바람 1.0 m/s, 운량 0.

  결과: 같은 조건에서 **볕 61곳 전부가 PET 41 °C 를 넘고 그늘 19곳 전부가 밑**이다.
  그늘의 이득은 LCZ 1~5 에서 3.3~3.6 K 로 거의 같다 — 도시형태와 무관하게
  볕/그늘 구분이 열부하를 가른다.

  주의: (a) 는 실측, (b) 는 같은 조건에서의 엔진 산출. 엔진의 Δ(중앙 3.5 K)는
  실측 Δ(중앙 5.4 K)보다 작으므로 (b) 는 **근린 간 비교**용이다.

자료: tier3_engine_output_80_v7_photo.csv, aug80_indices.csv, ref_condition_80.csv
"""
from __future__ import annotations
import csv, random, statistics as st, sys
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

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
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/claude-0/fig6"
INK, MUTED = "#1A1A1A", "#5E5E5E"
SUN, SHADE, THR = "#C0504D", "#4E6E9F", "#B3261E"
THRESHOLD = 41.0

ROWS = [("LCZ 1", "부암제1동", "Buam 1"),
        ("LCZ 2", "보수동",   "Bosu"),
        ("LCZ 3", "서제2동",  "Seo 2"),
        ("LCZ 4", "용호제1동", "Yongho 1"),
        ("LCZ 5", "명장동",   "Myeongjang")]


def load_measured():
    out = []
    for r in csv.DictReader(open(f"{DATA}/tier3_engine_output_80_v7_photo.csv",
                                 encoding="utf-8-sig")):
        ta, tmrt = float(r["Ta"]), float(r["Tmrt"])
        out.append(dict(dong=r["권역"], pet=float(r["PET"]), sun=(r["볕"] == "1"),
                        rise=tmrt - ta))
    return out


def load_ref():
    """같은 태양·같은 기상에서 엔진이 준 지점별 PET."""
    g = {}
    for r in csv.DictReader(open(f"{DATA}/ref_condition_80.csv", encoding="utf-8-sig")):
        g.setdefault(r["권역"], {"s": [], "h": []})[
            "s" if r["볕"] == "1" else "h"].append(float(r["ref_PET"]))
    return g


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))]


def main():
    d = load_measured()
    ref = load_ref()
    sun = [x["pet"] for x in d if x["sun"]]
    sha = [x["pet"] for x in d if not x["sun"]]
    msun, msha = st.median(sun), st.median(sha)
    gt = sum(1 for a in sun for b in sha if a > b)
    lt = sum(1 for a in sun for b in sha if a < b)
    delta = (gt - lt) / (len(sun) * len(sha))

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(190 * MM, 84 * MM),
                                 gridspec_kw=dict(width_ratios=[1.0, 1.02]))
    fig.subplots_adjust(left=0.058, right=0.875, top=0.80, bottom=0.315, wspace=0.60)
    rng = random.Random(3)

    # ---------------------------------------------------------------- (a) 실측
    PX = (0.0, 1.50)
    for i, (vals, col, lab) in enumerate(((sun, SUN, "Sunlit"), (sha, SHADE, "Shaded"))):
        p = PX[i]
        q1, q3 = q(vals, .25), q(vals, .75)
        ax.add_patch(plt.Rectangle((p - 0.17, q1), 0.34, q3 - q1, facecolor=col,
                                   alpha=0.16, edgecolor="none", zorder=1))
        for x in vals:
            ax.scatter([p + rng.uniform(-0.145, 0.145)], [x], s=13, color=col,
                       lw=0, zorder=3)
        ax.plot([p - 0.21, p + 0.21], [st.median(vals)] * 2, color=INK, lw=1.8, zorder=4)
        over = sum(1 for x in vals if x >= THRESHOLD)
        ax.text(p, 34.2, f"{lab}\nn = {len(vals)}", ha="center", va="bottom",
                fontsize=7.4, color=INK)
        ax.text(p, 33.0, f"{over} above 41 °C", ha="center", va="bottom",
                fontsize=6.4, color=MUTED)
    for x in d:   # 사진 라벨과 흑구 상승분이 어긋난 2지점
        if x["sun"] and x["rise"] < 10:
            ax.scatter([rng.uniform(-0.145, 0.145)], [x["pet"]], s=22,
                       facecolor="none", edgecolor=INK, lw=0.9, zorder=5)
    xb = 1.94
    ax.plot([xb, xb], [msha, msun], color=INK, lw=0.8)
    for yv in (msha, msun):
        ax.plot([xb - 0.05, xb], [yv, yv], color=INK, lw=0.8)
    ax.text(1.55, 52.0, f"$\\Delta$ 7.7 K\nCliff's $\\delta$ = {delta:.2f}",
            fontsize=7.2, va="bottom", ha="center", color=INK, linespacing=1.3)
    ax.axhline(THRESHOLD, color=THR, lw=0.9, ls=(0, (5, 3)), zorder=2)
    ax.set_xlim(-0.85, 2.70); ax.set_ylim(32.5, 56)
    ax.set_xticks([]); ax.set_ylabel("Measured PET (°C)")
    ax.spines["bottom"].set_visible(False)
    ax.set_title("(a)  As measured, all 80 sites", loc="left", fontweight="bold",
                 color=INK, pad=8)

    # ------------------------------------------------- (b) 같은 태양·같은 기상
    tr = bx.get_yaxis_transform()
    LCOL, RCOL = -0.50, 1.02
    for i, (code, dong, en) in enumerate(ROWS):
        s, h = ref[dong]["s"], ref[dong]["h"]
        for x in s:
            bx.scatter([x], [i + 0.15], s=9, color=SUN, alpha=0.45, lw=0, zorder=2)
        for x in h:
            bx.scatter([x], [i - 0.15], s=9, color=SHADE, alpha=0.45, lw=0, zorder=2)
        ms = st.median(s)
        if h:
            mh = st.median(h)
            bx.plot([mh, ms], [i, i], color="#9A9A9A", lw=2.2, solid_capstyle="round",
                    zorder=3)
            bx.scatter([mh], [i], s=42, color=SHADE, edgecolor="white", lw=0.9, zorder=5)
            bx.text(RCOL, i, f"$\\Delta$ {ms - mh:.1f} K", fontsize=7.2, transform=tr,
                    va="center", color=INK, clip_on=False)
        else:
            bx.text(RCOL, i, "no shaded\nsite", fontsize=6.7, transform=tr, va="center",
                    color=MUTED, style="italic", linespacing=1.35, clip_on=False)
        bx.scatter([ms], [i], s=42, color=SUN, edgecolor="white", lw=0.9, zorder=5)
        bx.text(LCOL, i - 0.17, f"{code} · {en}", fontsize=7.3, transform=tr,
                va="center", fontweight="bold", color=INK, clip_on=False)
        bx.text(LCOL, i + 0.21, f"{len(s)} sunlit / {len(h)} shaded", fontsize=6.5,
                transform=tr, va="center", color=MUTED, clip_on=False)
    bx.axvline(THRESHOLD, color=THR, lw=0.9, ls=(0, (5, 3)), zorder=1)
    bx.text(THRESHOLD - 0.10, -0.62, "all 19 shaded sites below 41 °C", fontsize=6.5,
            color=SHADE, ha="right", va="bottom")
    bx.text(THRESHOLD + 0.10, -0.62, "all 61 sunlit sites above", fontsize=6.5,
            color=SUN, ha="left", va="bottom")
    bx.set_xlim(37.9, 44.3); bx.set_ylim(len(ROWS) - 0.30, -0.78)
    bx.set_yticks([]); bx.set_xticks([38, 39, 40, 41, 42, 43, 44])
    bx.set_xlabel("PET at the common reference condition (°C)")
    bx.spines["left"].set_visible(False)
    bx.set_title("(b)  Same sun, same weather — only the form differs",
                 loc="left", fontweight="bold", color=INK, pad=8)

    fig.legend(handles=[
        Line2D([], [], marker="o", color="none", markerfacecolor=SUN, markersize=5.2,
               label="sunlit — direct beam at the sensor"),
        Line2D([], [], marker="o", color="none", markerfacecolor=SHADE, markersize=5.2,
               label="shaded — no direct beam (building, tree or cloud)"),
        Line2D([], [], marker="o", color="none", markerfacecolor="none",
               markeredgecolor=INK, markersize=5.2,
               label="photo label disagrees with the globe rise (2 sites)"),
        Line2D([], [], color=THR, lw=0.9, ls=(0, (5, 3)),
               label="extreme heat stress, PET 41 °C")],
        loc="lower center", bbox_to_anchor=(0.5, 0.105), ncol=4, frameon=False,
        fontsize=6.7, handlelength=1.4, handletextpad=0.5, columnspacing=1.6)

    fig.text(0.058, 0.068,
             "(b) puts every site at one reference condition — 23 Aug 12:30, solar elevation "
             "65°, $T_a$ 35 °C, RH 45 %, wind 1.0 m s$^{-1}$, clear sky — keeping only its "
             "measured form (sky, tree and building view) and its sun-or-shade reading, and recomputes "
             "PET with the same engine, so that neighbourhoods surveyed on different days and "
             "at different hours can be read side by side. The engine gives a smaller sun–shade "
             "difference than the field data (median 3.5 against 5.4 K); (b) is therefore read "
             "for the comparison between neighbourhoods, not for the absolute level.",
             fontsize=6.0, color=MUTED, va="top", ha="left", wrap=True)

    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/SCS_Fig6_sun_shade.{ext}", bbox_inches="tight", pad_inches=0.03)
    print("saved ->", OUT)


if __name__ == "__main__":
    main()
