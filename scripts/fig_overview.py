#!/usr/bin/env python3
"""Study design and analysis overview — corrected against the data, 2026-09-13.

Layout rule: nothing is placed by hand. Every box height is derived from its
line count, every box below is placed at (previous bottom - GAP), and every
arrow is drawn from a box's recorded bottom to the next box's recorded top.
Text is measured after rendering and shrunk if it would cross a box edge.

Corrections against the previous version of this chart:
  time window   kept as the survey window 11:00-16:00 at the author's
                request. The realised span is 11:19-15:06 (08-20 12:40-13:42,
                08-23 11:19-12:27, 08-25 12:06-12:56, 08-26 12:05-15:06), so
                the stated window is an envelope, not the measured span. Added
                that 26 August carried TWO neighbourhoods (Bosu 12:05-13:12,
                Buam 1 14:10-15:06), which is why an afternoon block exists
                and why neighbourhood means are not directly comparable.
  permutation   5,000 -> 20,000. At 5,000 the p value moved between 0.005
                and 0.007 across runs; with sorted groups and 20,000 draws
                it settles at p = 0.007.
  bootstrap     "wild cluster bootstrap (5 clusters)" -> pairs bootstrap
                (4,000) plus a neighbourhood random effect. Five clusters is
                far below where a wild cluster bootstrap is trustworthy, and
                it is not what was run.
  Tier 1        "station air temperature" -> official gridded weather. No
                warning-station series exists in the archive; what is
                reproducible is the gridded weather the app was served with
                the radiation term removed: MAE 13.5 degC, 0 of 63 detected.
  residual      the deployed residual layer was missing entirely. It is the
                paper's AI component and the engine's final output, so it
                now closes the tier column.
  kappa         the geometric shade check is answered rather than listed as
                pending: kappa = 0.06, 19 of 21 shaded sites called sunlit.
  ranges        PET 35.9-53.8 degC, surface 35.3-66.4 degC, from the 80
                points in tier3_engine_output_80_v6.csv.
"""
from __future__ import annotations
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrow

mpl.rcParams.update({
    "font.family": "Liberation Sans",
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "figure.dpi": 200, "savefig.dpi": 600,
})
INK, MUTED, RULE = "#1A1A1A", "#5E5E5E", "#5A5A5A"
BLUE = ("#E9F0F8", "#9DB8D4")
GREEN = ("#E9F1E7", "#A9C3A2")
GREY = ("#F0F0F0", "#BEBEBE")
ORANGE = ("#FBEFE2", "#DDB183")
DEEP = ("#D7E5F4", "#6E97C2")
PURPLE = ("#EDE9F5", "#B0A5CE")
LW = 0.8
MM = 1 / 25.4

PAD_T, PAD_B, LEAD = 3.5, 2.7, 2.15      # 상자 안쪽 위/아래 여백, 줄 간격
GAP = 4.6                                 # 상자 사이 화살표 길이

fig, ax = plt.subplots(figsize=(190 * MM, 241 * MM))
ax.set_xlim(0, 100); ax.set_ylim(0, 128)
ax.axis("off")
fig.subplots_adjust(left=0.004, right=0.996, bottom=0.004, top=0.996)

_T = []


def block(x0, x1, ytop, title, body, palette, fs=6.1, nmin=0):
    """nmin 을 주면 그 줄 수만큼의 높이로 맞춘다 — 한 행의 상자를 같은 크기로."""
    fc, ec = palette
    h = PAD_T + PAD_B + (max(len(body), nmin) - 1) * LEAD
    y0 = ytop - h
    ax.add_patch(Rectangle((x0, y0), x1 - x0, h, facecolor=fc, edgecolor=ec,
                           lw=LW, zorder=4))
    maxw = x1 - x0 - 3.8
    t = ax.text(x0 + 2.1, ytop - PAD_T + 0.6, title, fontsize=7.2,
                fontweight="bold", va="center", ha="left", color=INK, zorder=6)
    _T.append((t, maxw))
    yy = ytop - PAD_T - 1.9
    for i, s in enumerate(body):
        tt = ax.text(x0 + 2.1, yy - i * LEAD, s, fontsize=fs, va="center",
                     ha="left", color=MUTED, zorder=6)
        _T.append((tt, maxw))
    return dict(x0=x0, x1=x1, top=ytop, bot=y0, cx=(x0 + x1) / 2)


def fit():
    fig.canvas.draw()
    inv = ax.transData.inverted()
    bad = 0
    for t, maxw in _T:
        ok = False
        for _ in range(18):
            bb = t.get_window_extent(fig.canvas.get_renderer())
            (a, _u), (c, _v) = inv.transform([[bb.x0, bb.y0], [bb.x1, bb.y1]])
            if (c - a) <= maxw:
                ok = True
                break
            t.set_fontsize(t.get_fontsize() - 0.12)
            fig.canvas.draw()
        if not ok:
            bad += 1
            print("  !! overflow:", t.get_text())
    print("  text fits: OK" if bad == 0 else f"  {bad} overflow(s)")


def arrowhead(x, y, dx, dy):
    ax.add_patch(FancyArrow(x, y, dx, dy, width=0, head_width=0.85,
                            head_length=1.05, length_includes_head=True,
                            color=RULE, lw=0, zorder=5))


def down(a, b, x=None):
    xx = a["cx"] if x is None else x
    ax.plot([xx, xx], [a["bot"], b["top"] + 1.05], color=RULE, lw=LW, zorder=3)
    arrowhead(xx, b["top"] + 1.05, 0, -1.05)


def right(a, b):
    y = (a["top"] + a["bot"]) / 2
    ax.plot([a["x1"], b["x0"] - 1.05], [y, y], color=RULE, lw=LW, zorder=3)
    arrowhead(b["x0"] - 1.05, y, 1.05, 0)


def head(n, label, x, y):
    ax.text(x, y, n, fontsize=10.5, fontweight="bold", color="#2E6FAF",
            va="center", ha="left")
    ax.text(x + 2.7, y, label, fontsize=8.6, fontweight="bold", color=INK,
            va="center", ha="left")


LX0, LX1 = 1.0, 47.0
RX0, RX1 = 52.0, 99.0
LCX, RCX = (LX0 + LX1) / 2, (RX0 + RX1) / 2

# ---------------------------------------------------------- 1. sampling
head("1", "Equity-stratified sampling design", 1.0, 125.0)
TOP1 = 121.6
s1 = block(LX0, LX1, TOP1, "Busan, 206 administrative dong",
           ["local climate zone 1–5 × ageing vulnerability",
            "(older density, living alone, welfare rate)",
            "→ five neighbourhoods"], BLUE)
s2 = block(RX0, RX1, TOP1, "Field survey of 82 sites on older adults’ routes",
           ["market streets, clinics, senior-centre approaches",
            "four days, August 2026 · 11:00–16:00",
            "26 August carried two neighbourhoods"], BLUE)
right(s1, s2)

# ------------------------------------------------------ 2 / 3 headers
HEAD23 = s1["bot"] - 3.6
head("2", "Field measurement — reference", 1.0, HEAD23)
head("3", "Three information tiers — estimates", RX0, HEAD23)

BR = HEAD23 - 3.4                 # 분기 가로선 — 제목 아래를 지난다
TOP2 = BR - GAP
XDROP = RX1 - 5.0
ax.plot([XDROP, XDROP], [s2["bot"], BR], color=RULE, lw=LW, zorder=3)
ax.plot([LCX, XDROP], [BR, BR], color=RULE, lw=LW, zorder=3)

m1 = block(LX0, LX1, TOP2, "Same site, same minute (three instruments)",
           ["globe thermometer → T$_a$, T$_g$, RH, wind",
            "      → T$_{mrt}$  (ISO 7726, globe reference)",
            "      → PET  (VDI 3787 Part 2 / Höppe MEMI)",
            "360° camera → SVF, TVF, BVF, sun / shade",
            "thermal camera → wall and pavement T$_s$"], GREEN)
m2 = block(LX0, LX1, m1["bot"] - GAP, "Physical consistency check",
           ["canyon solution · σT$^4$ · solar position",
            "80 of 82 sites pass and enter the analysis",
            "the same 80 sites and minutes as the tier estimates"], GREY)
m3 = block(LX0, LX1, m2["bot"] - GAP, "Measured outcome (reference)",
           ["PET 35.9–53.8 °C · 63 of 80 extreme (≥ 41 °C)",
            "sun 59, shade 21 · surface 35.3–66.4 °C"], GREEN)

t1 = block(RX0, RX1, TOP2, "Tier 1    official heat-warning inputs",
           ["official gridded air temperature, humidity, wind",
            "no radiation term",
            "MAE 13.5 °C · 0 of 63 extreme cases detected"], GREY)
t2 = block(RX0, RX1, t1["bot"] - GAP, "Tier 2    deployed service",
           ["gridded weather + street-view view factors",
            "engine as operated, Aug 2026 · 55 sites only",
            "MAE 7.2 °C · 25 of 63 detected"], BLUE)
t3 = block(RX0, RX1, t2["bot"] - GAP, "Tier 3    street-view-free pathway",
           ["gridded weather + ray casting on open building polygons",
            "(SVF, BVI) + satellite NDVI for trees",
            "no learning step · all 80 sites · MAE 6.6 °C"], BLUE)
t4 = block(RX0, RX1, t3["bot"] - GAP,
           "Diagnostic runs (same engine, inputs swapped)",
           ["+ measured weather · + observed sun / shade",
            "isolates what each input is worth (6.4, 5.4 °C)"], ORANGE)
t5 = block(RX0, RX1, t4["bot"] - GAP, "Residual correction layer (deployed)",
           ["ridge, α = 20 · five standardised predictors · no coordinates",
            "leave-one-neighbourhood-out MAE 2.7 °C",
            "all 63 extreme cases detected"], DEEP)

# ---- AI 학습이 일어나는 곳 표시 (파이프라인에서 학습 단계는 여기 하나뿐이다)
AI_C = "#C2410C"
ax.add_patch(Rectangle((t5["x0"] - 0.9, t5["bot"] - 0.9),
                       (t5["x1"] - t5["x0"]) + 1.8,
                       (t5["top"] - t5["bot"]) + 1.8,
                       facecolor="none", edgecolor=AI_C, lw=1.9, zorder=7))
_ymid = (t5["top"] + t5["bot"]) / 2
ax.annotate("", xy=(t5["x0"] - 1.6, _ymid), xytext=(t5["x0"] - 11.0, _ymid),
            arrowprops=dict(arrowstyle="-|>", color=AI_C, lw=1.4,
                            mutation_scale=9, shrinkA=0, shrinkB=0), zorder=7)
ax.text(t5["x0"] - 12.0, _ymid + 1.5, "AI learning step",
        fontsize=7.6, fontweight="bold", color=AI_C, ha="right", va="center")
ax.text(t5["x0"] - 12.0, _ymid - 1.3,
        "the only learned component\nin the whole pipeline",
        fontsize=6.2, color=AI_C, ha="right", va="top", linespacing=1.45)

ax.plot([LCX, LCX], [BR, m1["top"] + 1.05], color=RULE, lw=LW, zorder=3)
arrowhead(LCX, m1["top"] + 1.05, 0, -1.05)
ax.plot([XDROP, XDROP], [BR, t1["top"] + 1.05], color=RULE, lw=LW, zorder=3)
arrowhead(XDROP, t1["top"] + 1.05, 0, -1.05)
down(m1, m2); down(m2, m3)
down(t1, t2); down(t2, t3); down(t3, t4); down(t4, t5)

# ---------------------------------------------------------- 4. analysis
BUS = min(m3["bot"], t5["bot"]) - 3.8
HEAD4 = BUS - 3.6
head("4", "Analysis — measurement versus tier estimates", 1.0, HEAD4)
ax.plot([LCX, LCX], [m3["bot"], BUS], color=RULE, lw=LW, zorder=3)
ax.plot([RCX, RCX], [t5["bot"], BUS], color=RULE, lw=LW, zorder=3)
ax.plot([LCX, RCX], [BUS, BUS], color=RULE, lw=LW, zorder=3)

TOP4 = HEAD4 - 3.6
AX = [(1.0, 33.0), (33.8, 65.8), (66.6, 99.0)]
BODY = [
    ("Forecast–experience gap",
     ["decomposed on one PET scale:",
      "official gridded inputs → site air → radiation",
      "sensitivity and specificity at PET 41 °C",
      "neighbourhood permutation (20,000), p = 0.007",
      "pairs bootstrap (4,000) and a neighbourhood",
      "random effect (ICC 0.32 → 0.11)"]),
    ("Service diagnosis",
     ["MAE, bias, r and extreme detection",
      "counterfactual input swaps attribute error to",
      "gridded weather, view factors, sun–shade",
      "geometric shade vs. observed: κ = 0.06",
      "(19 of 21 shaded sites called sunlit)",
      "transfer to unseen neighbourhoods: MAE 2.7 °C"]),
    ("Equity",
     ["exposure versus social vulnerability",
      "extreme detection rate by neighbourhood",
      "reach: 55 sites with street view vs. 25 without",
      "→ design requirements for an age-friendly",
      "      heat service (Section 6)"]),
]
NMAX = max(len(bo) for _t, bo in BODY)      # 4번 행 상자 높이를 통일한다
outs = [block(x0, x1, TOP4, ti, bo, PURPLE, nmin=NMAX)
        for (x0, x1), (ti, bo) in zip(AX, BODY)]
for b in outs:
    ax.plot([b["cx"], b["cx"]], [BUS, b["top"] + 1.05], color=RULE, lw=LW, zorder=3)
    arrowhead(b["cx"], b["top"] + 1.05, 0, -1.05)
ax.plot([outs[0]["cx"], outs[-1]["cx"]], [BUS, BUS], color=RULE, lw=LW, zorder=3)

print(f"  lowest bottom {min(b['bot'] for b in outs):.1f}  (ylim 0..128)")
fit()
fig.savefig("/tmp/claude-0/SCS_Fig_overview.pdf", bbox_inches="tight", pad_inches=0.03)
fig.savefig("/tmp/claude-0/SCS_Fig_overview.png", bbox_inches="tight", pad_inches=0.03)
print("saved")
