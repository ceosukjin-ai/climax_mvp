#!/usr/bin/env python3
"""Fig. 6 재작성 — 볕/그늘이 보행자 열부하를 가른다 (2026-09-21).

옛 그림의 문제:
  (a)(b) 둘 다 점 구름이라 **같은 것을 두 번** 보여줬다. (b) 는 다섯 근린 × 두 무리 =
  열 개의 구름이라 무엇을 비교하라는 건지 한눈에 안 들어왔다. 효과 크기(몇 도 차이인가)가
  그림 어디에도 숫자로 없었다.

다시 만든 것:
  (a) 볕/그늘 분포 — 중앙값·사분위와 함께 **차이 7.7 K 를 괄호로 명시**.
      극심 기준(PET 41 °C) 위에 몇 곳인지도 숫자로.
  (b) LCZ 1~5 **아령 그림** — 근린마다 그늘 중앙값 → 볕 중앙값을 선으로 잇고 Δ 를 적는다.
      열 개 구름이 다섯 줄로 줄고, 「어느 동네에서나 같은 일이 일어난다」가 바로 읽힌다.
      LCZ 1 만 Δ 2.1 K 로 튄다 — 그 동네 그늘 3곳이 오후 늦은 고온 시각의 「뜨거운 그늘」.

자료: tier3_engine_output_80_v7_photo.csv (v7 사진검증 라벨, 볕 61 / 그늘 19).
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
INK, MUTED, RULE = "#1A1A1A", "#5E5E5E", "#5A5A5A"
SUN, SHADE, THR = "#C0504D", "#4E6E9F", "#B3261E"
THRESHOLD = 41.0

# 측정 창을 같이 적는다. 하루에 한 근린씩 쟀고 **8/26 만 두 근린**(보수 오전, 부암 오후).
# 부암 1 의 Δ 가 작은 것은 LCZ 의 성질이 아니라 **늦은 오후에 쟀기 때문**이다 —
# 그늘 3곳이 기온 36.5~38.7 °C 시각에 잡혔다. 시각을 안 적으면 오독된다.
ROWS = [("LCZ 1", "부암제1동", "Buam 1",     "26 Aug, 14:10–15:06"),
        ("LCZ 2", "보수동",   "Bosu",        "26 Aug, 12:05–13:12"),
        ("LCZ 3", "서제2동",  "Seo 2",       "20 Aug, 12:40–13:42"),
        ("LCZ 4", "용호제1동", "Yongho 1",   "25 Aug, 12:06–12:56"),
        ("LCZ 5", "명장동",   "Myeongjang", "23 Aug, 11:19–12:27")]


def load():
    out = []
    for r in csv.DictReader(open(f"{DATA}/tier3_engine_output_80_v7_photo.csv",
                                 encoding="utf-8-sig")):
        ta, tmrt = float(r["Ta"]), float(r["Tmrt"])
        out.append(dict(dong=r["권역"], pet=float(r["PET"]), sun=(r["볕"] == "1"),
                        over=float(r["PET"]) - ta,   # 기온 위 초과분 — 근린 간 기온차 제거
                        rise=tmrt - ta))
    return out


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))]


def main():
    d = load()
    sun = [x["pet"] for x in d if x["sun"]]
    sha = [x["pet"] for x in d if not x["sun"]]
    msun, msha = st.median(sun), st.median(sha)
    gt = sum(1 for a in sun for b in sha if a > b)
    lt = sum(1 for a in sun for b in sha if a < b)
    delta = (gt - lt) / (len(sun) * len(sha))

    # (b) 는 **왼쪽 이름칸 · 가운데 자료 · 오른쪽 해설칸** 세 칸으로 나눈다.
    # 이름과 점이 같은 x 범위에 있으면 반드시 겹친다 — 칸을 아예 분리해서 없앤다.
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(190 * MM, 82 * MM),
                                 gridspec_kw=dict(width_ratios=[1.0, 1.0]))
    fig.subplots_adjust(left=0.058, right=0.855, top=0.80, bottom=0.185, wspace=0.56)
    rng = random.Random(3)

    # ---------------------------------------------------------------- (a)
    PX = (0.0, 1.32)   # 두 무리를 벌려 아래 설명글이 서로 닿지 않게 한다
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
                fontsize=6.8, color=MUTED)
    # 속 빈 표식 — 사진 라벨과 흑구 상승분이 어긋난 2지점
    for x in d:
        if x["sun"] and x["rise"] < 10:
            ax.scatter([rng.uniform(-0.145, 0.145)], [x["pet"]], s=22,
                       facecolor="none", edgecolor=INK, lw=0.9, zorder=5)
    # 차이 괄호
    xb = 1.76
    ax.plot([xb, xb], [msha, msun], color=INK, lw=0.8)
    for yv in (msha, msun):
        ax.plot([xb - 0.05, xb], [yv, yv], color=INK, lw=0.8)
    ax.text(xb + 0.07, (msha + msun) / 2, f"$\\Delta$ 7.7 K\nCliff's $\\delta$ = {delta:.2f}",
            fontsize=7.4, va="center", ha="left", color=INK)
    # 기준선 설명은 범례에 있다 — 그림 안에 또 쓰면 점 구름을 덮는다
    ax.axhline(THRESHOLD, color=THR, lw=0.9, ls=(0, (5, 3)), zorder=2)
    ax.set_xlim(-0.62, 3.15); ax.set_ylim(32.5, 56)
    ax.set_xticks([]); ax.set_ylabel("Measured PET (°C)")
    ax.spines["bottom"].set_visible(False)
    ax.set_title("(a)  Direct beam at the sensor, all 80 sites",
                 loc="left", fontweight="bold", color=INK, pad=8)

    # ---------------------------------------------------------------- (b)
    tr = bx.get_yaxis_transform()   # x 는 축 비율, y 는 자료값 — 칸 밖 글자에 쓴다
    LCOL, RCOL = -0.52, 1.03
    for i, (code, dong, en, when) in enumerate(ROWS):
        g = [x for x in d if x["dong"] == dong]
        # 근린마다 기온이 달라(33~38 °C) PET 절대값으로는 나란히 못 둔다.
        # **기온 위 초과분**으로 보면 측정 조건이 정규화되어 다섯 근린이 같은 자에 놓인다.
        s = [x["over"] for x in g if x["sun"]]
        h = [x["over"] for x in g if not x["sun"]]
        for x in s:
            bx.scatter([x], [i + 0.14], s=9, color=SUN, alpha=0.42, lw=0, zorder=2)
        for x in h:
            bx.scatter([x], [i - 0.14], s=9, color=SHADE, alpha=0.42, lw=0, zorder=2)
        ms = st.median(s)
        if h:
            mh = st.median(h)
            bx.plot([mh, ms], [i, i], color="#9A9A9A", lw=2.2, solid_capstyle="round",
                    zorder=3)
            bx.scatter([mh], [i], s=42, color=SHADE, edgecolor="white", lw=0.9, zorder=5)
            bx.text(RCOL, i, f"shade buys\n{ms - mh:.1f} K", fontsize=7.2, transform=tr,
                    va="center", color=("#B3261E" if ms - mh < 4 else INK),
                    linespacing=1.35, clip_on=False,
                    fontweight=("bold" if ms - mh < 4 else "normal"))
        else:
            bx.text(RCOL, i, "no shaded\nsite", fontsize=6.9, transform=tr, va="center",
                    color=MUTED, style="italic", linespacing=1.35, clip_on=False)
        bx.scatter([ms], [i], s=42, color=SUN, edgecolor="white", lw=0.9, zorder=5)
        # 이름칸 — 자료 칸 왼쪽 바깥. 위 줄 이름, 아래 줄 개수와 측정 시각
        bx.text(LCOL, i - 0.17, f"{code} · {en}", fontsize=7.3, transform=tr,
                va="center", fontweight="bold", color=INK, clip_on=False)
        bx.text(LCOL, i + 0.21, f"{len(s)} / {len(h)} sites · {when.split(',')[1].strip()}"
                f", {when.split(',')[0]}", fontsize=6.4, transform=tr, va="center",
                clip_on=False, color=("#B3261E" if code == "LCZ 1" else MUTED))
    # 볕 지점의 전체 중앙값 — 다섯 근린이 여기에 나란히 모인다
    allsun = st.median([x["over"] for x in d if x["sun"]])
    bx.axvline(allsun, color=SUN, lw=0.8, ls=(0, (4, 3)), zorder=1, alpha=0.8)
    bx.text(allsun, -0.60, "sunlit median, all sites  %.1f K" % allsun, fontsize=6.6,
            color=SUN, ha="center", va="bottom")
    bx.text(-0.52, -0.35,
            "LCZ 1 was measured in the late afternoon — its shade had already warmed up.",
            fontsize=6.3, transform=bx.transAxes, va="center", color="#B3261E",
            clip_on=False)
    bx.set_xlim(2.2, 19.4); bx.set_ylim(len(ROWS) - 0.30, -0.78)
    bx.set_yticks([]); bx.set_xticks([4, 6, 8, 10, 12, 14, 16, 18])
    bx.set_xlabel("PET above the air temperature measured at the site (K)")
    bx.spines["left"].set_visible(False)
    bx.set_title("(b)  Put on a common footing — the heat load above air temperature",
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
        loc="lower center", bbox_to_anchor=(0.5, -0.015), ncol=4, frameon=False,
        fontsize=6.9, handlelength=1.4, handletextpad=0.5, columnspacing=1.6)

    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/SCS_Fig6_sun_shade.{ext}", bbox_inches="tight", pad_inches=0.03)
    print("saved ->", OUT)


if __name__ == "__main__":
    main()
