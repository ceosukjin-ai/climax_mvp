#!/usr/bin/env python3
"""위성 지표온도(Landsat 8·9 LST)로 엔진 노면온도 채점 — 시험 (2026-09-26).

무엇을 보나
  부산 맑은 장면 두 장(여름 1, 겨울 1)에서, 실측 지점 좌표(8월 80곳 + 3월 부산대 28곳)의
  위성 지표온도와, 같은 시각·같은 자리에서 엔진이 계산한 노면온도를 비교한다.
  여름 장면이 8월 실측일과 가까우면 열화상 4방향 노면온도(Ts4)와도 비교한다.

도구 — 추가 설치 없음 (HTTP 만)
  · 장면 검색: Planetary Computer STAC  /api/stac/v1/search
  · 화소 값:  Planetary Computer data API  /api/data/v1/item/point/{lon},{lat}
              lwir11(ST_B10: K = DN·0.00341802 + 149.0), qa_pixel(구름·그림자 비트)
  · 그 시각 기상: Open-Meteo archive (기온·습도·10 m 풍속·운량, 시간값 선형보간)

정직한 한계
  · 위성 화소는 30 m 격자(열적외 원해상도 100 m) — 도로·지붕·나무가 섞인 표면 평균이다.
    엔진은 도로 노면이므로 절대값보다 **상관·계절 차이**를 먼저 본다. SVF 구간별로 나눠 본다.
  · 엔진 볕/그늘: 그 시각 그림자 계산을 붙이지 않았다 — 볕(1)·그늘(0) 두 값을 다 적고,
    열린 곳(SVF ≥ 0.6)은 볕 값으로 비교한다.
  · 보행자 풍속 = 10 m 풍속 × 0.4 (근사).

실행 (서버, 저장소 최신으로 pull 한 뒤):
  docker exec -i climax-api python3 - < scripts/lst_pilot.py
  결과: 컨테이너 /tmp/lst_pilot_2026-09-26.csv  (docker cp 로 꺼내 data/ 에 커밋)
"""
from __future__ import annotations
import csv, io, json, math, os, statistics as st, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
from vpti_core import DEFAULT_CONFIG                                   # noqa: E402
from vpti_core.solar import estimate_solar, solar_lag_average           # noqa: E402
from vpti_core.mrt import estimate_ground_temp, sky_emissivity          # noqa: E402

PC = "https://planetarycomputer.microsoft.com/api"
REPO = "https://raw.githubusercontent.com/ceosukjin-ai/climax_mvp/feat/phi-healthkit-pvpti/data/"
LOCAL = os.environ.get("CLIMAX_REPO", "/repo")
KST = timezone(timedelta(hours=9))
BBOX = [128.95, 35.05, 129.20, 35.30]          # 부산 실측 지점을 모두 덮는 상자
ALB, EMIS = 0.08, 0.94
CFG = DEFAULT_CONFIG.mrt
OUT = "/tmp/lst_pilot_2026-09-26.csv"

# 장면 후보 기간 — 여름은 8월 실측일 가까이부터, 없으면 작년 여름
SEASONS = [("여름", ["2026-08-10/2026-09-10", "2025-07-01/2025-08-31"]),
           ("겨울", ["2025-12-01/2026-02-28", "2024-12-01/2025-02-28"])]


def http_json(url, body=None, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=(json.dumps(body).encode() if body else None),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:                                         # noqa: BLE001
            if k == tries - 1:
                raise
            time.sleep(2 + 3 * k)


def read_csv(name):
    """저장소 data/ 파일 — 컨테이너에 저장소가 마운트돼 있으면 그걸, 없으면 GitHub 원본."""
    p = os.path.join(LOCAL, "data", name)
    if os.path.exists(p):
        return list(csv.DictReader(open(p, encoding="utf-8-sig")))
    with urllib.request.urlopen(REPO + urllib.parse.quote(name), timeout=60) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))


def sites():
    out = []
    t4 = {r["측정ID"]: r for r in read_csv("aug80_ts4_2026-09-26.csv")}
    for r in read_csv("scs_master_80.csv"):
        svf = r["SVF"].strip() or r["geo_SVF"].strip()
        if not svf:
            continue
        g = r.get("geo_GVI", "").strip()
        out.append(dict(id=r["측정ID"], set="8월80", lat=float(r["위도"]), lon=float(r["경도"]),
                        svf=float(svf), gvi=float(g) if g else 0.0,
                        ts4=t4.get(r["측정ID"], {}).get("Ts4", ""), when=r["시각"]))
    for r in read_csv("pnu_28_final.csv"):
        out.append(dict(id=r["측정ID"], set="부산대3월", lat=float(r["위도"]), lon=float(r["경도"]),
                        svf=float(r["SVF"]), gvi=float(r["GVI"] or 0), ts4="", when=""))
    return out


def find_scene(ranges):
    for dt in ranges:
        body = {"collections": ["landsat-c2-l2"], "bbox": BBOX, "datetime": dt, "limit": 50,
                "query": {"eo:cloud_cover": {"lt": 20}, "platform": {"in": ["landsat-8", "landsat-9"]}}}
        feats = http_json(PC + "/stac/v1/search", body).get("features", [])
        # 부산을 온전히 덮는 장면 중 구름 적은 것
        feats = [f for f in feats if f["bbox"][0] <= BBOX[0] and f["bbox"][2] >= BBOX[2]
                 and f["bbox"][1] <= BBOX[1] and f["bbox"][3] >= BBOX[3]] or feats
        if feats:
            feats.sort(key=lambda f: f["properties"].get("eo:cloud_cover", 99))
            return feats[0]
    return None


def pixel(item_id, lon, lat):
    q = urllib.parse.urlencode({"collection": "landsat-c2-l2", "item": item_id,
                                "assets": "lwir11", "asset_bidx": "lwir11|1"})
    q2 = urllib.parse.urlencode({"collection": "landsat-c2-l2", "item": item_id,
                                 "assets": "qa_pixel", "asset_bidx": "qa_pixel|1"})
    v = http_json(f"{PC}/data/v1/item/point/{lon},{lat}?{q}")["values"][0]
    qa = int(http_json(f"{PC}/data/v1/item/point/{lon},{lat}?{q2}")["values"][0])
    if v in (None, 0):
        return None, qa
    lst = v * 0.00341802 + 149.0 - 273.15
    # qa_pixel 비트: 1 dilated cloud, 3 cloud, 4 cloud shadow, 5 snow
    bad = any(qa >> b & 1 for b in (1, 3, 4, 5))
    return (None if bad else round(lst, 2)), qa


_WX = {}


def weather(lat, lon, t_utc):
    key = (round(lat, 2), round(lon, 2), t_utc.date())
    if key not in _WX:
        d = t_utc.date().isoformat()
        u = ("https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(dict(
            latitude=key[0], longitude=key[1], start_date=d, end_date=d, timezone="UTC",
            hourly="temperature_2m,relative_humidity_2m,wind_speed_10m,cloud_cover", wind_speed_unit="ms")))
        _WX[key] = http_json(u)["hourly"]
    h = _WX[key]; x = t_utc.hour + t_utc.minute / 60
    i = min(int(x), 22); f = x - i
    lerp = lambda k: h[k][i] * (1 - f) + h[k][i + 1] * f
    return lerp("temperature_2m"), lerp("relative_humidity_2m"), lerp("wind_speed_10m"), lerp("cloud_cover") / 100


def engine_ts(s, t_kst, ta, rh, u10, cf):
    sol = estimate_solar(s["lat"], s["lon"], t_kst, cloud_fraction=cf)
    lag = solar_lag_average(s["lat"], s["lon"], t_kst, CFG.ground_lag_tau_h, cloud_fraction=cf) \
        if CFG.ground_lag_tau_h > 0 else None
    eps = sky_emissivity(ta, rh, cf)
    u = 0.4 * u10
    sun = estimate_ground_temp(ta, sol, ALB, EMIS, s["svf"], 0.0, u, eps, config=CFG, direct_shade=1.0, solar_lag=lag)
    shd = estimate_ground_temp(ta, sol, ALB, EMIS, s["svf"], 0.0, u, eps, config=CFG, direct_shade=0.0, solar_lag=lag)
    return sun, shd, sol.solar_elevation_deg


def stats(pairs):
    p = [(a, b) for a, b in pairs if a is not None and b is not None]
    if len(p) < 3:
        return f"n {len(p)}"
    e = [b - a for a, b in p]
    ma, mb = st.mean(a for a, _ in p), st.mean(b for _, b in p)
    sa = math.sqrt(sum((a - ma) ** 2 for a, _ in p)); sb = math.sqrt(sum((b - mb) ** 2 for _, b in p))
    r = sum((a - ma) * (b - mb) for a, b in p) / (sa * sb) if sa and sb else float("nan")
    return f"n {len(p):3}  MAE {st.mean(map(abs, e)):5.2f}  bias {st.mean(e):+5.2f}  r {r:+.2f}"


def main():
    S = sites()
    print(f"지점 {len(S)}곳 (8월80 {sum(s['set']=='8월80' for s in S)}, 부산대3월 {sum(s['set']=='부산대3월' for s in S)})")
    rows = []
    for season, ranges in SEASONS:
        it = find_scene(ranges)
        if not it:
            print(f"[{season}] 맑은 장면 없음"); continue
        t_utc = datetime.fromisoformat(it["properties"]["datetime"].replace("Z", "+00:00"))
        t_kst = t_utc.astimezone(KST)
        print(f"\n[{season}] {it['id']}  {t_kst:%Y-%m-%d %H:%M} KST  구름 {it['properties'].get('eo:cloud_cover')}%")
        for s in S:
            try:
                lst, qa = pixel(it["id"], s["lon"], s["lat"])
            except Exception as e:                                     # noqa: BLE001
                lst, qa = None, f"err {type(e).__name__}"
            ta, rh, u10, cf = weather(s["lat"], s["lon"], t_utc)
            sun, shd, elev = engine_ts(s, t_kst, ta, rh, u10, cf)
            rows.append(dict(season=season, scene=it["id"], time_kst=f"{t_kst:%Y-%m-%d %H:%M}",
                             id=s["id"], set=s["set"], lat=s["lat"], lon=s["lon"], svf=s["svf"], gvi=s["gvi"],
                             lst=lst, qa=qa, ta=round(ta, 1), rh=round(rh), u10=round(u10, 1), cloud=round(cf, 2),
                             elev=round(elev, 1), eng_sun=round(sun, 2), eng_shade=round(shd, 2), ts4=s["ts4"]))
        R = [r for r in rows if r["season"] == season]
        print(f"  기온 {st.mean(r['ta'] for r in R):.1f}℃  운량 {st.mean(r['cloud'] for r in R):.2f}  태양고도 {R[0]['elev']}°"
              f"  | 위성 LST 유효 {sum(r['lst'] is not None for r in R)}/{len(R)}")
        for lab, f in [("전체", lambda r: True), ("열린 곳 SVF≥0.6", lambda r: r["svf"] >= 0.6),
                       ("좁은 곳 SVF<0.6", lambda r: r["svf"] < 0.6)]:
            sub = [r for r in R if f(r)]
            print(f"  {lab:16} 위성 vs 엔진(볕)   {stats([(r['lst'], r['eng_sun']) for r in sub])}")
            print(f"  {'':16} 위성 vs 엔진(그늘) {stats([(r['lst'], r['eng_shade']) for r in sub])}")
        if season == "여름":
            sub = [r for r in R if r["ts4"] not in ("", None)]
            print(f"  참고: 위성 vs 8월 열화상 Ts4 (날짜 다름) {stats([(r['lst'], float(r['ts4'])) for r in sub])}")
        vals = [r["lst"] for r in R if r["lst"] is not None]
        if vals:
            print(f"  위성 LST 분포  중앙 {st.median(vals):.1f}  범위 {min(vals):.1f}~{max(vals):.1f}")
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
