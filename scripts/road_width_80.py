#!/usr/bin/env python3
"""실측 80점에 OSM 도로 등급/폭 붙이고, 잔차(실측 PET − 엔진 PET)가 폭 등급별로 다른지 확인 (2026-09-09).

사용(맥 터미널, 네트워크 필요):  python3 scripts/road_width_80.py
출력: data/tier3_road_80.csv  + 화면 요약표(그대로 붙여넣어 주면 됨)
"""
import csv, json, math, os, sys, urllib.parse, urllib.request

IN = "data/tier3_engine_output_80_v2.csv"
OUT = "data/tier3_road_80.csv"
R = 30  # 탐색 반경 m

# OSM highway 태그 → 폭 등급 (라벨 대용)
CLS = {
    "footway": "보행골목", "path": "보행골목", "steps": "보행골목", "pedestrian": "보행골목",
    "living_street": "보행골목", "service": "혼합골목", "residential": "혼합골목",
    "unclassified": "혼합골목", "tertiary": "큰길", "secondary": "큰길", "primary": "큰길",
    "trunk": "큰길", "tertiary_link": "큰길", "secondary_link": "큰길", "primary_link": "큰길",
}


def dist_m(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * 111320.0, (lon1 - lon2) * 111320.0 * math.cos(math.radians(lat1)))


def seg_dist(p, a, b):
    """점 p 에서 선분 ab 까지 거리(m). 평면 근사."""
    k = 111320.0 * math.cos(math.radians(p[0]))
    px, py = p[1] * k, p[0] * 111320.0
    ax, ay = a[1] * k, a[0] * 111320.0
    bx, by = b[1] * k, b[0] * 111320.0
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def main():
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    parts = "".join(f'way(around:{R},{r["위도"]},{r["경도"]})[highway];' for r in rows)
    q = f"[out:json][timeout:90];({parts});out tags geom;"
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=urllib.parse.urlencode({"data": q}).encode(),
                                 headers={"User-Agent": "climax-road-width/1.0"})
    d = json.load(urllib.request.urlopen(req, timeout=120))
    ways = [w for w in d["elements"] if w.get("type") == "way" and w.get("geometry")]
    print(f"OSM 도로 {len(ways)}개 수신")

    out = []
    for r in rows:
        p = (float(r["위도"]), float(r["경도"]))
        best = None
        for w in ways:
            g = [(n["lat"], n["lon"]) for n in w["geometry"]]
            dmin = min(seg_dist(p, g[i], g[i + 1]) for i in range(len(g) - 1)) if len(g) > 1 else dist_m(*p, *g[0])
            if best is None or dmin < best[0]:
                best = (dmin, w)
        tags = best[1]["tags"] if best else {}
        hw = tags.get("highway", "")
        width = tags.get("width", "")
        lanes = tags.get("lanes", "")
        cls = CLS.get(hw, "기타")
        out.append({**r, "osm_highway": hw, "osm_width": width, "osm_lanes": lanes,
                    "osm_dist_m": round(best[0], 1) if best else "", "폭등급": cls})
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    print(f"저장: {OUT}")

    # ── 요약: 폭 등급별 잔차 ──
    print("\n=== 폭등급별 (n, 평균SVF, 평균BVI, 실측PET, 엔진v2 PET bias, MAE, 그늘비율) ===")
    for c in ("보행골목", "혼합골목", "큰길", "기타"):
        g = [o for o in out if o["폭등급"] == c]
        if not g:
            continue
        e = [float(o["run_C_PET"]) - float(o["PET"]) for o in g]
        print(f"  {c:5s} n={len(g):2d}  SVF {sum(float(o['tier3_svf']) for o in g)/len(g):.2f}"
              f"  BVI {sum(float(o['tier3_bvi']) for o in g)/len(g):.2f}"
              f"  실측PET {sum(float(o['PET']) for o in g)/len(g):.1f}"
              f"  bias {sum(e)/len(e):+.2f}  MAE {sum(abs(x) for x in e)/len(e):.2f}"
            f"  그늘 {sum(1 for o in g if o['볕'].strip()!='1')/len(g):.0%}")
    print("\n=== highway 태그 분포 ===")
    from collections import Counter
    print(dict(Counter(o["osm_highway"] for o in out)))
    print("width 태그 있는 지점:", sum(1 for o in out if o["osm_width"]))

    # ── 폭등급을 잔차 AI 피처에 넣으면 LOSO가 좋아지나 ──
    try:
        import numpy as np
        from sklearn.linear_model import Ridge
    except ImportError:
        print("\n(sklearn 없음 → LOSO 비교 생략)"); return
    F = ["볕", "tier3_svf", "tier3_gvi", "Ta", "RH", "v", "태양고도", "run_C_Tmrt", "run_C_PET", "ndvi30"]
    X = np.array([[float(o[k]) for k in F] for o in out])
    onehot = np.array([[o["폭등급"] == c for c in ("보행골목", "혼합골목", "큰길")] for o in out], float)
    y = np.array([float(o["PET"]) for o in out]); ye = np.array([float(o["run_C_PET"]) for o in out])
    grp = np.array([o["권역"] for o in out])

    def loso(Xf):
        p = np.zeros(len(y))
        for gname in set(grp):
            te = grp == gname; tr = ~te
            mu = Xf[tr].mean(0); sd = Xf[tr].std(0) + 1e-9
            m = Ridge(10.0).fit((Xf[tr] - mu) / sd, (y - ye)[tr])
            p[te] = ye[te] + m.predict((Xf[te] - mu) / sd)
        return float(np.mean(np.abs(p - y)))
    print(f"\n=== 잔차 AI LOSO MAE: 폭등급 없이 {loso(X):.2f}  /  폭등급 추가 {loso(np.hstack([X, onehot])):.2f}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())
