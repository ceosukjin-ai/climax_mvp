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
    w = {r["측정ID"]: r for r in csv.DictReader(open(f"{DATA}/tier3_width_80.csv", encoding="utf-8-sig"))}
    out = {k: [0, 0, 0, 0] for k, _n in ROWS}
    n = {k: 0 for k, _n in ROWS}
    for r in csv.DictReader(open(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")):
        k = r["권역"]; n[k] += 1
        out[k][WCLS.get(w.get(r["측정ID"], {}).get("폭등급", ""), 3)] += 1
    return out, n


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
    ax.set_title(title, fontsize=8.2, fontweight="bold", color=INK, loc="left", pad=12.0)
    ax.text(0.0, 1.035, sub, transform=ax.transAxes, fontsize=6.6, color=MUTED,
            ha="left", va="bottom")
    ax.set_xlabel(xlabel, fontsize=6.8, color=MUTED, labelpad=2.0)


def main():
    wc, n = load_width()
    y = list(range(len(ROWS)))
    fig, axs = plt.subplots(1, 4, figsize=(190 * MM, 66 * MM))
    fig.subplots_adjust(left=0.115, right=0.995, top=0.775, bottom=0.235, wspace=0.30)

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
    for i, (k, _nm) in enumerate(ROWS):
        b = BLD[k]
        axs[1].plot([b["hmed"], b["hmax"]], [i, i], color="#C6D4E2", lw=3.2,
                    solid_capstyle="round", zorder=2)
        axs[1].scatter([b["hmax"]], [i], s=26, color="#5C738C", zorder=3)
        axs[1].scatter([b["hmed"]], [i], s=26, color="#B5714F", zorder=4)
        axs[1].text(b["hmax"] + 2.0, i, f"{b['hmax']:.0f}", va="center",
                    fontsize=6.6, color=INK)
        axs[1].text(b["hmed"] - 2.0, i, f"{b['hmed']:.1f}", va="center", ha="right",
                    fontsize=6.6, color=INK)
    style(axs[1], "(b)  Building height", "median → tallest within 60 m (m)", "", 76, False)
    axs[1].legend(handles=[plt.Line2D([], [], marker="o", color="none",
                                      markerfacecolor="#B5714F", markersize=4.6,
                                      label="median building"),
                           plt.Line2D([], [], marker="o", color="none",
                                      markerfacecolor="#5C738C", markersize=4.6,
                                      label="tallest building")],
                  loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=1, frameon=False,
                  fontsize=6.5, handlelength=1.0, handletextpad=0.4)

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

    # (d) 수관
    v = [BLD[k]["cnp"] for k, _nm in ROWS]
    axs[3].barh(y, v, height=0.58, color="#6E8B5E", edgecolor="none")
    for i, (k, _nm) in enumerate(ROWS):
        b = BLD[k]
        axs[3].text(max(v[i], 0) + 0.25, i,
                    ("none" if b["cnp"] == 0 else f"{b['cnp']} px · {b['ch']:.1f} m"),
                    va="center", fontsize=6.6, color=(MUTED if b["cnp"] == 0 else INK))
    style(axs[3], "(d)  Tree canopy", "canopy cells within 30 m (median)", "", 9.5, False)

    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/SCS_Fig_form_bars.{ext}", bbox_inches="tight", pad_inches=0.03)
    print("saved ->", OUT)


if __name__ == "__main__":
    main()
