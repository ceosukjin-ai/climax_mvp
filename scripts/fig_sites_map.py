#!/usr/bin/env python3
"""Fig. 3 재작성 — 측정 지점 지도 (2026-09-20).

왜 다시 그리는가:
  옛 그림(SCS_Fig1_sites_slope)을 그린 코드가 저장소에도 드라이브에도 없다. 그리고 그
  지도는 **아파트 단지를 하얗게 비워 두고 있었다** — 엔진은 같은 자리에서 40~65 m 건물을
  읽고 있는데 그림에는 아무것도 없었다. 표 2 의 「반경 60 m 건물 수」와도 어긋난다.
  그래서 지도를 **엔진이 쓰는 바로 그 DB(bldg_poly)** 에서 그린다. 같은 원천이면 어긋날
  일이 없다.

무엇을 그리는가 (LCZ 1~5, 한 근린에 한 패널):
  건물   높이 3구간 음영 (<10 m / 10~25 m / >25 m) — LCZ 계층 높이 구분과 같은 경계
  도로   osm_way highway
  지점   모양 = 볕(원) / 그늘(사각), 색 = 표고

표고는 Open-Meteo Elevation API 에서 한 번 받아 data/elev80.json 에 캐시한다.

실행(서버, 배치와 같이 돌려도 되게 가볍게):
  cd ~/climax_mvp && docker run --rm --memory 1500m --cpus 1.0 \
    -e DATABASE_URL="postgresql+asyncpg://climax:$(grep -m1 '^DB_PASSWORD=' infra/ncp/.env.prod | cut -d= -f2-)@$(grep -m1 '^DB_HOST=' infra/ncp/.env.prod | cut -d= -f2-):5432/climax" \
    -v "$HOME/climax_mvp:/repo" climax-backend:latest python3 /repo/scripts/fig_sites_map.py
"""
from __future__ import annotations
import asyncio, csv, json, math, os, sys, urllib.request

sys.path.insert(0, "/app")
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon, Patch
from matplotlib.lines import Line2D

mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "figure.dpi": 200, "savefig.dpi": 600,
})
MM = 1 / 25.4
DATA = "/repo/data"
OUT = "/repo/docs/figs"

LCZ = {"부암제1동": ("LCZ 1", "Compact high-rise", "Buam 1"),
       "보수동":    ("LCZ 2", "Compact mid-rise",  "Bosu"),
       "서제2동":   ("LCZ 3", "Compact low-rise",  "Seo 2"),
       "용호제1동": ("LCZ 4", "Open high-rise",    "Yongho 1"),
       "명장동":    ("LCZ 5", "Open mid-rise",     "Myeongjang")}
ORDER = ["부암제1동", "보수동", "서제2동", "용호제1동", "명장동"]

# 건물 높이 음영 — 경계는 LCZ 계층 정의(3~10 / 10~25 / >25 m)와 같게 둔다
# 2026-09-20: 건물을 청회색으로 두니 지점 표식(청록~남색)과 구분이 안 됐다.
# 배경은 **중립 회색**으로 내리고 지점은 노랑~보라(plasma)로 — 색상환에서 멀리 떨어뜨린다.
H_BINS = [(0.0, 10.0, "#E8E8E6"), (10.0, 25.0, "#C4C4C0"), (25.0, 1e9, "#8E8E88")]
ROAD_C = "#D2D2CE"
INK, MUTED = "#1A1A1A", "#5E5E5E"


def load_sites():
    w = {r["측정ID"]: r for r in csv.DictReader(open(f"{DATA}/tier3_width_80.csv", encoding="utf-8-sig"))}
    out = []
    for r in csv.DictReader(open(f"{DATA}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")):
        out.append(dict(id=r["측정ID"], dong=r["권역"], lat=float(r["위도"]), lon=float(r["경도"]),
                        sun=(r["볕"] == "1"), width=w.get(r["측정ID"], {}).get("width_m", "")))
    return out


def elevations(sites):
    cache = f"{DATA}/elev80.json"
    if os.path.isfile(cache):
        d = json.load(open(cache))
        if len(d) == len(sites):
            return d
    la = ",".join(f"{s['lat']:.5f}" for s in sites)
    lo = ",".join(f"{s['lon']:.5f}" for s in sites)
    url = f"https://api.open-meteo.com/v1/elevation?latitude={la}&longitude={lo}"
    d = json.load(urllib.request.urlopen(url, timeout=40))["elevation"]
    json.dump(d, open(cache, "w"))
    print(f"  표고 {len(d)}점 수신 → {cache}")
    return d


async def fetch_layers(bbox):
    """bbox=(s,w,n,e) 안의 건물(외곽선+높이)과 도로."""
    from app.services.skyline import _get_pool
    from app.services.geo import _height_m_from_props, _fill_floors_from_register_many
    s, w, n, e = bbox
    pool = await _get_pool()
    async with pool.acquire() as c:
        brows = await c.fetch(
            "SELECT tags::text AS tags, ST_AsGeoJSON(geom) AS g FROM bldg_poly "
            "WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)", w, s, e, n)
        rrows = await c.fetch(
            "SELECT ST_AsGeoJSON(geom) AS g FROM osm_way "
            "WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326) AND tags ? 'highway' "
            "AND GeometryType(geom)='LINESTRING'", w, s, e, n)
    props = [json.loads(r["tags"]) for r in brows]
    await _fill_floors_from_register_many(props)
    blds = []
    for r, p in zip(brows, props):
        g = json.loads(r["g"]); t = g.get("type"); co = g.get("coordinates") or []
        rings = [co[0]] if t == "Polygon" else [pp[0] for pp in co if pp]
        h = _height_m_from_props(p, default_floors=2) or 0.0
        for ring in rings:
            if len(ring) >= 4:
                blds.append((ring, h))
    roads = [json.loads(r["g"]).get("coordinates") or [] for r in rrows]
    return blds, roads


def panel(ax, sites, blds, roads, bbox, title, sub, elev_lim, cmap):
    s, w, n, e = bbox
    latm = (s + n) / 2
    kx = math.cos(math.radians(latm))
    ax.set_xlim(w, e); ax.set_ylim(s, n)
    ax.set_aspect(1.0 / kx)
    for xs in roads:
        ax.plot([p[0] for p in xs], [p[1] for p in xs], color=ROAD_C, lw=0.35, zorder=1,
                solid_capstyle="round")
    for ring, h in blds:
        for lo_, hi_, col in H_BINS:
            if lo_ <= h < hi_:
                ax.add_patch(MplPolygon(ring, closed=True, facecolor=col, edgecolor="none",
                                        lw=0, zorder=2))
                break
    for st in sites:
        # 흰 테두리 + 얇은 검정 외곽 — 밝은 건물 위에서도 표식 경계가 살아 있게
        ax.scatter(st["lon"], st["lat"], s=40, marker=("o" if st["sun"] else "s"),
                   c=[st["elev"]], cmap=cmap, vmin=elev_lim[0], vmax=elev_lim[1],
                   edgecolor="white", linewidth=1.2, zorder=5)
        ax.scatter(st["lon"], st["lat"], s=40, marker=("o" if st["sun"] else "s"),
                   facecolor="none", edgecolor="#333333", linewidth=0.35, zorder=6)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_linewidth(0.6); sp.set_color("#8A8A8A")
    # 제목과 부제가 겹치지 않게 — 제목을 위로 올리고 부제를 그 아래 한 줄에 둔다
    ax.set_title(title, fontsize=8.0, fontweight="bold", color=INK, pad=14.0, loc="left")
    ax.text(0.0, 1.012, sub, transform=ax.transAxes, fontsize=7.0, color=MUTED,
            va="bottom", ha="left")
    # 축척 200 m
    span_m = (e - w) * 111320.0 * kx
    frac = 200.0 / span_m
    x0, y0 = w + (e - w) * 0.06, s + (n - s) * 0.06
    ax.plot([x0, x0 + (e - w) * frac], [y0, y0], color=INK, lw=1.4, zorder=6,
            solid_capstyle="butt")
    ax.text(x0 + (e - w) * frac / 2, y0 + (n - s) * 0.015, "200 m", fontsize=6.6,
            ha="center", va="bottom", color=INK, zorder=6)
    # 북침
    ax.annotate("", xy=(e - (e - w) * 0.07, n - (n - s) * 0.07),
                xytext=(e - (e - w) * 0.07, n - (n - s) * 0.17),
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=0.9, mutation_scale=7), zorder=6)
    ax.text(e - (e - w) * 0.07, n - (n - s) * 0.055, "N", fontsize=6.8, ha="center",
            va="bottom", color=INK, zorder=6)


async def main():
    sites = load_sites()
    ev = elevations(sites)
    for st, v in zip(sites, ev):
        st["elev"] = v
    elo, ehi = min(ev), max(ev)
    # 지점 색표 — 보라 계열을 쓰지 않는다. YlOrRd 의 옅은 끝(거의 흰색)은 잘라내서
    # 회색 건물 위에서도 가장 낮은 지점까지 또렷하게 보이게 한다.
    import numpy as _np
    from matplotlib.colors import LinearSegmentedColormap
    _base = plt.get_cmap("YlOrRd")
    cmap = LinearSegmentedColormap.from_list("elev", _base(_np.linspace(0.22, 0.95, 256)))

    fig, axes = plt.subplots(2, 3, figsize=(190 * MM, 138 * MM))
    fig.subplots_adjust(left=0.012, right=0.988, top=0.925, bottom=0.02, wspace=0.07, hspace=0.30)
    axs = axes.ravel()

    for i, dong in enumerate(ORDER):
        g = [x for x in sites if x["dong"] == dong]
        la = [x["lat"] for x in g]; lo = [x["lon"] for x in g]
        mla, mlo = (max(la) + min(la)) / 2, (max(lo) + min(lo)) / 2
        half = max((max(la) - min(la)) / 2, (max(lo) - min(lo)) / 2 * math.cos(math.radians(mla))) * 1.45
        half = max(half, 0.0018)
        bbox = (mla - half, mlo - half / math.cos(math.radians(mla)),
                mla + half, mlo + half / math.cos(math.radians(mla)))
        blds, roads = await fetch_layers(bbox)
        code, kind, name = LCZ[dong]
        sun = sum(1 for x in g if x["sun"])
        panel(axs[i], g, blds, roads, bbox,
              f"({'abcdef'[i]})  {code}  {kind}",
              f"{name} · n = {len(g)}, {sun} sunlit · "
              f"{min(x['elev'] for x in g):.0f}–{max(x['elev'] for x in g):.0f} m a.s.l.",
              (elo, ehi), cmap)
        print(f"  {code} {name}: 건물 {len(blds)} 도로 {len(roads)}", flush=True)

    lg = axs[5]
    lg.axis("off")
    h1 = [Patch(facecolor=c, edgecolor="none",
                label=(f"building < {hi:.0f} m" if lo_ == 0 else
                       (f"building {lo_:.0f}–{hi:.0f} m" if hi < 1e8 else f"building > {lo_:.0f} m")))
          for lo_, hi, c in H_BINS]
    h1.append(Line2D([], [], color=ROAD_C, lw=1.0, label="street"))
    h2 = [Line2D([], [], marker="o", color="none", markerfacecolor="#E8703A",
                 markeredgecolor="white", markersize=6.5, label="measurement site — sunlit"),
          Line2D([], [], marker="s", color="none", markerfacecolor="#E8703A",
                 markeredgecolor="white", markersize=6.5, label="measurement site — shaded")]
    lg.legend(handles=h1 + h2, loc="upper left", frameon=False, fontsize=7.2,
              handlelength=1.5, borderpad=0.0, labelspacing=0.55,
              bbox_to_anchor=(0.02, 0.98))
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(elo, ehi))
    cax = fig.add_axes([0.71, 0.09, 0.22, 0.022])
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.set_label("Elevation of the measurement site (m)", fontsize=7.2, color=INK, labelpad=2.0)
    cb.ax.tick_params(labelsize=6.8, length=2, width=0.6)
    cb.outline.set_linewidth(0.6)

    os.makedirs(OUT, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(f"{OUT}/SCS_Fig_sites_map.{ext}", bbox_inches="tight", pad_inches=0.03)
    print(f"saved -> {OUT}/SCS_Fig_sites_map.pdf / .png")


if __name__ == "__main__":
    asyncio.run(main())
