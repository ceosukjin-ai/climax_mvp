#!/usr/bin/env python3
"""열스트레스 등급 비율 — 볕/그늘 × LCZ 누적 막대 (2026-09-22).

PET 등급은 Matzarakis & Mayer (1996) 의 생리적 열스트레스 구분을 따른다
(35–41 °C strong heat stress, ≥ 41 °C extreme heat stress). 80지점 실측 PET 는
35.9–53.8 °C 라 전부 이 두 등급 안에 든다.

자료: tier3_engine_output_80_v7_photo.csv (PET, 볕 v7 사진검증, 권역)
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
    "font.size": 7.5, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "axes.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "xtick.major.width": 0.6, "xtick.major.size": 2.5,
    "figure.dpi": 200, "savefig.dpi": 600, "pdf.fonttype": 42, "ps.fonttype": 42,
})
MM = 1 / 25.4
DATA = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/climax_mvp/data"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/claude-0/fig_ab"
INK, MUTED = "#1A1A1A", "#5E5E5E"
C_STRONG, C_EXTREME = "#F2B880", "#B3261E"
SUN, SHADE = "#C0504D", "#3F6496"
LCZ = [("LCZ 1", "부암제1동", "Buam 1"), ("LCZ 2", "보수동", "Bosu"),
       ("LCZ 3", "서제2동", "Seo 2"), ("LCZ 4", "용호제1동", "Yongho 1"),
       ("LCZ 5", "명장동", "Myeongjang")]


def main():
    d = pd.read_csv(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")
    d["sun"] = d["볕"].astype(int)
    d["ext"] = d.PET >= 41.0

    rows = []   # (label, sublabel, n_extreme, n, kind, group_start)
    for k, lab in ((1, "Sunlit"), (0, "Shaded")):
        g = d[d.sun == k]
        rows.append(("All sites", lab, int(g.ext.sum()), len(g), k, k == 1))
    for code, dong, en in LCZ:
        for k, lab in ((1, "Sunlit"), (0, "Shaded")):
            g = d[(d["권역"] == dong) & (d.sun == k)]
            rows.append((f"{code} · {en}", lab, int(g.ext.sum()), len(g), k, k == 1))

    fig, ax = plt.subplots(figsize=(140 * MM, 102 * MM))
    fig.subplots_adjust(left=0.24, right=0.97, top=0.90, bottom=0.12)
    y, ys, gap = 0.0, [], 0.55
    for i, (grp, lab, ne, n, k, first) in enumerate(rows):
        if first and i > 0:
            y += gap
        ys.append(y)
        col_lab = SUN if k else SHADE
        if n == 0:
            ax.text(1, y, "no shaded site", va="center", fontsize=6.6, color=MUTED, style="italic")
        else:
            pe = 100 * ne / n
            ax.barh(y, pe, height=0.72, color=C_EXTREME, edgecolor="white", lw=0.5)
            ax.barh(y, 100 - pe, left=pe, height=0.72, color=C_STRONG, edgecolor="white", lw=0.5)
            txt = f"{ne} of {n}"
            if pe >= 10:
                ax.text(pe / 2, y, txt, ha="center", va="center", fontsize=6.6, color="white",
                        fontweight="bold")
            else:
                ax.text(pe + (100 - pe) / 2, y, txt, ha="center", va="center", fontsize=6.6,
                        color=INK)
        ax.text(-1.5, y, lab, ha="right", va="center", fontsize=6.9, color=col_lab)
        if first:
            ax.text(-17, y + 0.5, grp, ha="right", va="center", fontsize=7.4, color=INK,
                    fontweight="bold" if grp == "All sites" else "normal")
        y += 1.0
    ax.set_ylim(y - 0.4, -0.7)
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Share of sites in extreme heat stress (PET ≥ 41 °C, %)")
    ax.axhline((ys[1] + ys[2]) / 2, color="#CFCFCF", lw=0.6, xmin=-0.3, clip_on=False)
    ax.legend(handles=[Patch(color=C_EXTREME, label="extreme heat stress  (PET ≥ 41 °C)"),
                       Patch(color=C_STRONG, label="strong heat stress  (PET 35–41 °C)")],
              loc="lower center", bbox_to_anchor=(0.38, 1.0), ncol=2, frameon=False,
              fontsize=6.8, handlelength=1.3, columnspacing=1.8)
    for ext in ("pdf", "png", "eps"):
        fig.savefig(f"{OUT}/SCS_Fig_stress_bars.{ext}", bbox_inches="tight", pad_inches=0.03)
    for r in rows:
        print(r[:4])


if __name__ == "__main__":
    main()
