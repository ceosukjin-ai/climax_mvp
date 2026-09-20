#!/usr/bin/env python3
"""LCZ 별 실측 도시형태·외피 — 그림 4 와 짝이 되는 네 패널 (2026-09-20).

그림 4 는 「누가 사는가(취약성)·얼마나 더웠나·스트리트뷰가 닿는가」를 보여준다.
이 그림은 그 다섯 근린이 **어떻게 생겼는지**를 같은 순서·같은 모양으로 보여준다:
  (a) 가로폭   (b) 건물 높이   (c) 외벽 재질   (d) 수관

자료: 가로폭·볕그늘은 실측 CSV, 건물/재질/수관은 엔진 DB 집계(scripts/lcz_form_profile.py).
"""
from __future__ import annotations
import csv, sys
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "figure.dpi": 200, "savefig.dpi": 600, "axes.grid": False,
})
MM = 1 / 25.4
DATA = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/climax_mvp/data"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/claude-0/fig4"
INK, MUTED, RULE = "#1A1A1A", "#5E5E5E", "#5A5A5A"

# 2026-09-20: LCZ 1~5 순서로. 라벨은 한 줄로 합쳐 둔다 — 이름 아래에 설명을 따로
# 찍으니 아랫줄 이름과 겹쳤다. 계층 이름(Compact low-rise 등)과 노인밀도는 표 1 에 있다.
ROWS = [("부암제1동", "LCZ 1 · Buam 1"),
        ("보수동",   "LCZ 2 · Bosu"),
        ("서제2동",  "LCZ 3 · Seo 2"),
        ("용호제1동", "LCZ 4 · Yongho 1"),
        ("명장동",   "LCZ 5 · Myeongjang")]

WCLS = {"보행골목(<6m)": 0, "혼합골목(6-12m)": 1, "큰길(12m+)": 2, "개방": 3}
WCOL = ["#3F5F7F", "#7C9CBF", "#C6D4E2", "#EFEFEF"]
WNAME = ["alley < 6 m", "street 6–12 m", "street > 12 m", "open, no street"]
MCOL = {"concrete": "#9AA5AE", "brick": "#B5714F"}

# 근린 건물 높이 분포 — 반경 60 m 안 모든 건물 (scripts/lcz_form_profile.py, 09-20)
#   (건물 수, 최소, 5%, 중앙, 95%, 최대)
NEIGH_H = {"부암제1동": (5011, 3.0, 3.9, 6.9, 11.9, 88.4),
           "보수동":   (5770, 1.5, 3.9, 8.6, 16.3, 50.6),
           "서제2동":  (13475, 2.7, 3.9, 7.4, 11.9, 54.0),
           "용호제1동": (4402, 3.3, 3.9, 6.9, 13.7, 67.9),
           "명장동":   (1810, 3.3, 3.9, 6.9, 18.2, 91.4)}

# scripts/lcz_form_profile.py 출력 (2026-09-20 서버 실행)
BLD = {"부암제1동": dict(floor=2, hmed=6.9, hmax=49.2, mat={"concrete": 11, "brick": 4},
                      cnp=0, ch=0.0),
       "보수동":   dict(floor=3, hmed=8.8, hmax=42.5, mat={"concrete": 16},
                      cnp=3, ch=3.1),
       "서제2동":  dict(floor=2, hmed=7.6, hmax=21.5, mat={"brick": 16, "concrete": 1},
                      cnp=0, ch=0.0),
       "용호제1동": dict(floor=2, hmed=6.9, hmax=33.8, mat={"concrete": 12, "brick": 3},
                      cnp=0, ch=0.0),
       "명장동":   dict(floor=2, hmed=6.9, hmax=65.2, mat={"concrete": 15, "brick": 2},
                      cnp=5, ch=4.5)}


def load_width():
    """폭 등급 구성과, **그 지점의 캐년을 만든 건물 높이**(H = H/W x 가로폭).

    캐년 높이는 반경 60 m 안 건물 중앙값과 다른 것을 잰다 — 보행자가 바로 옆에 두고
    선 건물이다. 논문 표 1 의 'Measured H' 가 이 값이다.
    """
    import statistics as _st
    w = {r["측정ID"]: r for r in csv.DictReader(open(f"{DATA}/tier3_width_80.csv", encoding="utf-8-sig"))}
    out = {k: [0, 0, 0, 0] for k, _n in ROWS}
    n = {k: 0 for k, _n in ROWS}
    canyon = {k: [] for k, _n in ROWS}
    tvf = {k: [] for k, _n in ROWS}
    for r in csv.DictReader(open(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")):
        k = r["권역"]; n[k] += 1
        wr = w.get(r["측정ID"], {})
        out[k][WCLS.get(wr.get("폭등급", ""), 3)] += 1
        try:
            canyon[k].append(float(wr["width_m"]) * float(wr["hw_ratio"]))
        except (KeyError, ValueError):
            pass
        # ⚠️ tier3_engine_output_*.csv 의 SVF/TVF/BVF 열은 **옛 파노라마 값**이다
        #    (2026-09-15 에 무효 판정). 정본은 aug80_indices.csv 의 9/14 재산출본.
    # 수목 시계는 9/14 재산출본(aug80_indices.csv 의 GVI)에서 읽는다
    dong = {r["측정ID"]: r["권역"]
            for r in csv.DictReader(open(f"{DATA}/tier3_engine_output_80_v7_photo.csv",
                                         encoding="utf-8-sig"))}
    for r in csv.DictReader(open(f"{DATA}/aug80_indices.csv", encoding="utf-8-sig")):
        k = dong.get(r["측정ID"])
        if k is None:
            continue
        try:
            tvf[k].append(float(r["GVI"]))
        except (KeyError, ValueError):
            pass
    cmed = {k: (min(v), _st.median(v), max(v), len(v)) for k, v in canyon.items()}
    tmed = {k: (100.0 * _st.median(v), 100.0 * max(v),
                sum(1 for x in v if x < 0.005), len(v)) for k, v in tvf.items()}
    return out, n, cmed, tmed


def style(ax, title, sub, xlabel, xmax, first):
    ax.set_yticks(range(len(ROWS)))
    if first:
        ax.set_yticklabels([nm for _k, nm in ROWS], fontsize=7.6,
                           fontweight="bold", color=INK)
    else:
        ax.set_yticklabels([""] * len(ROWS))
    ax.invert_yaxis(); ax.set_xlim(0, xmax)
    ax.tick_params(axis="x", labelsize=7.0, length=2.4, width=0.6)
    ax.tick_params(axis="y", length=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_linewidth(0.6); ax.spines["bottom"].set_color(RULE)
    ax.set_title(title, fontsize=8.2, fontweight="bold", color=INK, loc="left", pad=17.0)
    ax.text(0.0, 1.075, sub, transform=ax.transAxes, fontsize=6.6, color=MUTED,
            ha="left", va="bottom")
    ax.set_xlabel(xlabel, fontsize=6.8, color=MUTED, labelpad=2.0)


def main():
    wc, n, cmed, tmed = load_width()
    y = list(range(len(ROWS)))
    fig, axs = plt.subplots(1, 4, figsize=(190 * MM, 66 * MM))
    fig.subplots_adjust(left=0.115, right=0.995, top=0.755, bottom=0.235, wspace=0.30)

    # (a) 가로폭 구성
    left = [0.0] * len(ROWS)
    for j in range(4):
        v = [100.0 * wc[k][j] / n[k] for k, _nm in ROWS]
        axs[0].barh(y, v, left=left, height=0.58, color=WCOL[j], edgecolor="white", lw=0.5)
        for i, x in enumerate(v):
            if x >= 11:
                axs[0].text(left[i] + x / 2, i, f"{wc[ROWS[i][0]][j]}", va="center",
                            ha="center", fontsize=6.4,
                            color=("white" if j == 0 else INK))
        left = [a + b for a, b in zip(left, v)]
    style(axs[0], "(a)  Street width", "share of sites (%)", "", 100, True)
    axs[0].legend(handles=[Patch(facecolor=WCOL[j], edgecolor="white", label=WNAME[j])
                           for j in range(4)],
                  loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=2, frameon=False,
                  fontsize=6.5, handlelength=1.2, columnspacing=1.0, handletextpad=0.45)

    # (b) 건물 높이 — 중앙값 점에서 최댓값까지 선
    # 근린 건물 높이의 **분포**(옅은 막대) 위에 **실측 지점이 실제로 마주한 캐년 높이의
    # 범위**(진한 막대)를 얹는다. 높이가 3 m~91 m 로 두 자릿수 차이라 가로축은 로그.
    C_SITE, C_ALL, C_TIP = "#A33B1F", "#D6DEE6", "#5C738C"
    for i, (k, _nm) in enumerate(ROWS):
        _n, hmin, h05, hmed, h95, hmax = NEIGH_H[k]
        smin, smed, smax, _sn = cmed[k]
        axs[1].plot([hmin, hmax], [i, i], color=C_ALL, lw=7.0,
                    solid_capstyle="butt", zorder=2)
        axs[1].plot([h05, h95], [i, i], color="#AFBCCA", lw=7.0,
                    solid_capstyle="butt", zorder=3)
        axs[1].plot([smin, smax], [i, i], color=C_SITE, lw=3.4,
                    solid_capstyle="butt", zorder=4)
        axs[1].scatter([smed], [i], s=30, color="white", edgecolor=C_SITE,
                       linewidth=1.4, zorder=5)
        # 범위 라벨은 막대 **위**. 아래로 내렸더니 마지막 행에서 가로축과 겹쳤다.
        # 첫 행이 부제와 닿지 않도록 style() 에서 제목 여백과 부제 높이를 함께 키웠다.
        axs[1].text(smin * 0.92, i - 0.33, f"{smin:.1f}–{smax:.0f}", va="center",
                    ha="left", fontsize=6.4, color=C_SITE, fontweight="bold")
        axs[1].text(hmax * 1.06, i + 0.02, f"{hmax:.0f}", va="center",
                    fontsize=6.3, color=C_TIP)
    axs[1].set_xscale("log")
    axs[1].set_xticks([3, 5, 10, 20, 50, 100])
    axs[1].set_xticklabels(["3", "5", "10", "20", "50", "100"])
    axs[1].xaxis.set_minor_locator(mpl.ticker.NullLocator())
    style(axs[1], "(b)  Building height", "all buildings within 60 m vs. the sites (m, log)",
          "", 160, False)
    axs[1].set_xlim(2.2, 165)
    axs[1].legend(handles=[plt.Line2D([], [], color=C_SITE, lw=3.4,
                                      label="canyon height at the sites"),
                           plt.Line2D([], [], color="#AFBCCA", lw=6.0,
                                      label="all buildings, 5–95 %"),
                           plt.Line2D([], [], color=C_ALL, lw=6.0,
                                      label="all buildings, full range")],
                  loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=1, frameon=False,
                  fontsize=6.5, handlelength=1.3, handletextpad=0.5, labelspacing=0.38)

    # (c) 외벽 재질
    left = [0.0] * len(ROWS)
    for mat in ("concrete", "brick"):
        v = []
        for k, _nm in ROWS:
            tot = sum(BLD[k]["mat"].values())
            v.append(100.0 * BLD[k]["mat"].get(mat, 0) / tot)
        axs[2].barh(y, v, left=left, height=0.58, color=MCOL[mat], edgecolor="white", lw=0.5)
        for i, x in enumerate(v):
            if x >= 12:
                axs[2].text(left[i] + x / 2, i, f"{BLD[ROWS[i][0]]['mat'].get(mat, 0)}",
                            va="center", ha="center", fontsize=6.4, color="white")
        left = [a + b for a, b in zip(left, v)]
    style(axs[2], "(c)  Facade material", "share of sites (%)", "", 100, False)
    axs[2].legend(handles=[Patch(facecolor=MCOL[m], edgecolor="white", label=m)
                           for m in ("concrete", "brick")],
                  loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=1, frameon=False,
                  fontsize=6.5, handlelength=1.2, handletextpad=0.45)

    # (d) 수목 — **어안사진 실측 TVF** 를 막대로 쓴다.
    #   위성 수관 래스터(canopy_items)는 반경 30 m·수고 3 m 이상만 세므로 가로수 한 그루나
    #   낮은 수목을 놓친다. 그래서 래스터로는 다섯 중 셋이 0 이지만 사진에는 나무가 찍힌다.
    #   측정값을 앞세우고 래스터는 괄호로 덧붙인다.
    v = [tmed[k][0] for k, _nm in ROWS]
    axs[3].barh(y, v, height=0.58, color="#6E8B5E", edgecolor="none")
    for i, (k, _nm) in enumerate(ROWS):
        z, tot = tmed[k][2], tmed[k][3]
        axs[3].text(v[i] + 0.10, i, f"{v[i]:.1f} %   ({z}/{tot} at 0)", va="center",
                    fontsize=6.4, color=INK)
    # 위성 래스터 값(수관 화소 0~5칸)은 캡션으로 옮겼다 — 막대 옆에 두니 글자가 축 밖으로
    # 나가 가로축이 끊어져 보였다.
    style(axs[3], "(d)  Tree cover", "tree view factor at the site (%, fisheye)",
          "", 8.0, False)
    axs[3].set_xticks([0, 2, 4, 6, 8])

    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/SCS_Fig_form_bars.{ext}", bbox_inches="tight", pad_inches=0.03)
    print("saved ->", OUT)


if __name__ == "__main__":
    main()
