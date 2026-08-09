#!/usr/bin/env python3
# =============================================================================
# ClimaX — 서울역 전체 도로 폭염 핫스팟 지도 (엔진 물리 내장, 서버·API 키 불필요)
#
# 이 파일 하나만 실행하면 OpenStreetMap에서 도로망 '전체'를 자동으로 받아,
# 각 도로를 물리(일사·복사·바람·재질·그늘)로 계산해 색칠한 지도(HTML)를 만듭니다.
# 엔진 코드를 import 하지 않고 물리를 파일 안에 담아, 파이썬 3.9에서도 동작합니다.
#
#  실행 (맥 터미널):
#    pip3 install osmnx pvlib pythermalcomfort shapely     # 처음 한 번만
#    cd ~/Desktop/climax_mvp/backend
#    python3 road_hotspot_full.py
#  → 같은 폴더에 seoul_roads_full.html 이 생기면 더블클릭해서 확인.
#  지역을 바꾸려면 아래 CONFIG 의 CENTER 좌표만 수정.
# =============================================================================
import sys, math, json
from datetime import datetime

# ---- CONFIG ----------------------------------------------------------------
CENTER = (37.5556, 126.9707)   # 서울역 (위도, 경도)
RADIUS_M = 1000                # 반경 [m]
SAMPLE_M = 45                  # 도로 계산 간격 [m]
TA, RH = 33.0, 55.0            # 가정 기온[°C]·습도[%]
WHEN = (2026, 8, 1, 14, 0)     # 평가 시각
OUT = "seoul_roads_full.html"
# ----------------------------------------------------------------------------

try:
    import pandas as pd, pvlib
    from pythermalcomfort.models import pet_steady
except ImportError:
    sys.exit("먼저 설치:  pip3 install osmnx pvlib pythermalcomfort shapely")

SIGMA = 5.670374419e-8
K = 273.15
A_K, EPS_P = 0.7, 0.97
F = {"up": 0.06, "down": 0.06, "N": 0.22, "E": 0.22, "S": 0.22, "W": 0.22}

# --- 재질 열물성 (반사율 R, 방사율 ε) ---
MAT = {
    "asphalt": (0.05, 0.95), "concrete": (0.30, 0.92), "vegetation": (0.20, 0.98),
    "glass": (0.10, 0.84), "soil": (0.17, 0.94), "brick": (0.30, 0.93),
}

def sat_vp(t): return 6.1078 * 10 ** (7.5 * t / (237.3 + t))
def sky_emis(Ta, rh, cf=0.0):
    e = sat_vp(Ta) * max(min(rh, 100), 0) / 100
    ec = min(max(0.52 + 0.065 * math.sqrt(max(e, 0)), 0), 1)
    return (1 - cf) * ec + cf * 1.0
def fanger(elev):
    b = max(elev, 0.0)
    return 0.308 * math.cos(math.radians(b * (0.998 - b * b / 50000.0)))

def ground_props(mats):
    tot = sum(f for _, f in mats)
    alb = sum(f / tot * MAT.get(m, (0.25, 0.9))[0] for m, f in mats)
    emis = sum(f / tot * MAT.get(m, (0.25, 0.9))[1] for m, f in mats)
    return alb, emis

def surface_temp(Ta, dni, dhi, elev, alb, emis, svf, gvi, wind, eps_sky):
    beta = math.radians(max(elev, 0.0))
    s_down = max(dni * math.sin(beta) + dhi * svf, 0.0)
    sw = (1 - alb) * s_down * 0.75                       # 0.75 = 지중저장(0.25) 차감
    l_down = SIGMA * (Ta + K) ** 4 * (svf * eps_sky + (1 - svf) * 0.90)
    hc = 6.0 + 4.0 * max(wind, 0)
    ts = Ta
    for _ in range(20):
        tk = ts + K
        f = sw + emis * (l_down - SIGMA * tk ** 4) - hc * (ts - Ta)
        fp = -4 * emis * SIGMA * tk ** 3 - hc
        ts -= f / fp
    g = min(max(gvi, 0), 1)
    return g * Ta + (1 - g) * ts

# --- 일사 1회 계산 (청천, 중심 좌표 기준) ---
_loc = pvlib.location.Location(CENTER[0], CENTER[1], tz="Asia/Seoul", altitude=10)
_times = pd.DatetimeIndex([datetime(*WHEN)]).tz_localize("Asia/Seoul")
_sp = _loc.get_solarposition(_times)
_cs = _loc.get_clearsky(_times, model="ineichen")
ELEV = float(_sp["apparent_elevation"].iloc[0])
DNI = float(_cs["dni"].iloc[0]); DHI = float(_cs["dhi"].iloc[0]); GHI = float(_cs["ghi"].iloc[0])
EPS_SKY = sky_emis(TA, RH)
FP = fanger(ELEV)

def vpti_at(svf, gvi, mats, base_wind=1.8):
    """물리로 한 지점의 체감기후(PET) 계산."""
    alb, emis = ground_props(mats)
    sun = max(0.0, min(1.0, svf * (1 - 0.85 * gvi)))          # 그늘: 직사광 차단
    dni = DNI * sun
    ghi = dni * math.sin(math.radians(max(ELEV, 0))) + DHI
    wind = base_wind * (0.35 + 0.8 * svf)
    tsurf = surface_temp(TA, dni, DHI, ELEV, alb, emis, svf, gvi, wind, EPS_SKY)
    # 6방향 복사속 → Tmrt
    psi_sky = {"up": svf, "down": 0.0, "N": 0.5 * svf, "E": 0.5 * svf, "S": 0.5 * svf, "W": 0.5 * svf}
    l_sky = EPS_SKY * SIGMA * (TA + K) ** 4
    l_surf = emis * SIGMA * (tsurf + K) ** 4
    sstr = A_K * FP * dni
    for d in F:
        pg = 1 - psi_sky[d]
        sstr += A_K * F[d] * (DHI * psi_sky[d])
        sstr += A_K * F[d] * (alb * ghi * pg)
        sstr += EPS_P * F[d] * (l_sky * psi_sky[d])
        sstr += EPS_P * F[d] * (l_surf * pg)
    tmrt = (sstr / (EPS_P * SIGMA)) ** 0.25 - K
    return float(pet_steady(tdb=TA, tr=tmrt, v=max(wind, 0.1), rh=RH,
                            met=1.37, clo=0.5, position="standing").pet)

# --- 도로 형태(morphology) 추정 ---
MORPH = {
    "big":   (0.85, 0.05, [("asphalt", 0.85), ("concrete", 0.15)]),
    "mid":   (0.65, 0.12, [("asphalt", 0.6), ("concrete", 0.4)]),
    "small": (0.45, 0.18, [("asphalt", 0.5), ("concrete", 0.4), ("brick", 0.1)]),
    "green": (0.50, 0.65, [("vegetation", 0.6), ("soil", 0.25), ("concrete", 0.15)]),
}
BIG = {"motorway", "trunk", "primary", "secondary"}
SMALL = {"residential", "living_street", "service", "footway", "path", "pedestrian", "steps"}
def road_class(h):
    if isinstance(h, list): h = h[0]
    return "big" if h in BIG else "small" if h in SMALL else "mid"
def tier(v):
    return "매우위험" if v >= 40 else "위험" if v >= 37 else "경고" if v >= 34 else "관심"


def main():
    try:
        import osmnx as ox
        from shapely.geometry import LineString, Point
    except ImportError:
        sys.exit("먼저 설치:  pip3 install osmnx shapely")

    print("① 도로망 내려받는 중 (OpenStreetMap)…")
    G = ox.graph_from_point(CENTER, dist=RADIUS_M, network_type="walk", simplify=True)

    parks = None
    try:
        print("② 공원·녹지 불러오는 중…")
        parks = ox.features_from_point(CENTER, tags={"leisure": ["park", "garden"],
                                       "landuse": ["grass", "forest", "recreation_ground"]}, dist=RADIUS_M)
        parks = parks[parks.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if len(parks) == 0: parks = None
    except Exception as e:
        print("   (공원 생략:", e, ")")

    def near_park(lon, lat):
        if parks is None: return False
        try: return bool(parks.intersects(Point(lon, lat).buffer(0.0006)).any())
        except Exception: return False

    print("③ 도로마다 물리 계산 중…")
    cache, segs = {}, []
    edges = list(G.edges(keys=True, data=True))
    for n, (u, v, k, data) in enumerate(edges):
        geom = data.get("geometry") or LineString(
            [(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])])
        cls = road_class(data.get("highway"))
        nseg = max(1, int(data.get("length", 50) / SAMPLE_M))
        for i in range(nseg):
            a = geom.interpolate(i / nseg, normalized=True)
            b = geom.interpolate((i + 1) / nseg, normalized=True)
            mlon, mlat = (a.x + b.x) / 2, (a.y + b.y) / 2
            c = "green" if near_park(mlon, mlat) else cls
            key = (round(mlon, 4), round(mlat, 4), c)
            if key not in cache:
                svf, gvi, mats = MORPH[c]
                cache[key] = vpti_at(svf, gvi, mats)
            vp = cache[key]
            segs.append({"a": [round(a.y, 6), round(a.x, 6)], "b": [round(b.y, 6), round(b.x, 6)],
                         "vpti": round(vp, 1), "tier": tier(vp)})
        if n % 200 == 0:
            print(f"   … {n}/{len(edges)} 도로")
    print(f"→ 도로 세그먼트 {len(segs)}개 완료")

    data_js = json.dumps(segs, ensure_ascii=False)
    vmin = min(s["vpti"] for s in segs); vmax = max(s["vpti"] for s in segs)
    html = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>ClimaX · 서울역 전체 도로 핫스팟</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>*{{margin:0}}html,body,#map{{height:100%}}.p{{position:absolute;top:12px;left:12px;z-index:999;background:#fff;
border-radius:14px;box-shadow:0 8px 24px rgba(0,0,0,.25);padding:14px 16px;font-family:sans-serif;width:250px}}
.p h1{{font-size:15px}}.p h1 span{{color:#0ea5a3}}.p p{{font-size:11.5px;color:#667;margin-top:5px}}
.lg{{font-size:11px;margin-top:8px}}.d{{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:3px}}</style>
</head><body><div id="map"></div><div class="p"><h1>Clima<span>X</span> · 서울역 전체 도로 핫스팟</h1>
<p>OSM 전체 도로 · 물리 계산 · 여름 14시 {int(TA)}°C<br>도로 구간 {len(segs)}개 · 체감 {vmin:.1f}~{vmax:.1f}°C</p>
<div class="lg"><span class="d" style="background:#b3261e"></span>매우위험 ≥40° <span class="d" style="background:#e0742b"></span>위험 37–40°<br>
<span class="d" style="background:#d99a2a"></span>경고 34–37° <span class="d" style="background:#2f9e6f"></span>관심 &lt;34°</div></div>
<script>const S={data_js};const C={{"매우위험":"#b3261e","위험":"#e0742b","경고":"#d99a2a","관심":"#2f9e6f"}};
const map=L.map('map').setView([{CENTER[0]},{CENTER[1]}],15);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'© OpenStreetMap'}}).addTo(map);
S.forEach(s=>L.polyline([s.a,s.b],{{color:C[s.tier],weight:5,opacity:0.85,lineCap:'round'}}).bindTooltip(`${{s.tier}} · 체감 ${{s.vpti}}°C`,{{sticky:true}}).addTo(map));
</script></body></html>"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ 완료!  '{OUT}' 파일을 더블클릭해서 지도를 확인하세요.")


if __name__ == "__main__":
    main()
