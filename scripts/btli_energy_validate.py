#!/usr/bin/env python3
"""BTLI(건물 냉방부하) 실측 검증 — 건축HUB 건물에너지(월 전기) 대조 (2026-09-25).

왜: BTLI v2 는 계수가 전부 UNCONFIRMED 인 물리 근사다. 지자체·건설사에 "맞는다"고 말하려면
실제 에너지와 대조해야 한다. 한국은 국토부 건축HUB 가 **지번별 월 전기·가스 사용량**을 무료로 준다
(단독주택·200세대 미만 공동주택 제외). 일본은 이런 공개자료가 없다 → 한국에서 물리를 검증하고,
일본은 입력(건축연도→단열, 구조→열용량, 외벽재→반사율)만 바꿔 쓴다.

무엇을 비교하나
  관측 냉방전기 강도 = (7월+8월 − 2×봄가을평균(4·5·10월)) / 연면적   [kWh/m²]
  예측 = BTLI facade_load 의 총부하 / 연면적                               [W/m²]
  → 절대값이 아니라 **순위**(Spearman ρ)를 본다. 같은 용도(아파트)끼리.
  대조군: 층수만·준공연도만으로 순위를 매겨도 되는지 — BTLI 가 그보다 나아야 의미가 있다.

실행 (서버 — 공공데이터포털에서 「건축HUB_건물에너지정보 서비스」 활용신청이 승인된 뒤)
  ⚠️ .env.prod 를 --env-file 로 바로 주면 따옴표가 값에 그대로 붙는다(docker run 은 따옴표를 안 벗긴다).
     돌고 있는 API 컨테이너의 환경을 빌린다:
  docker exec climax-api printenv > /tmp/api.env && chmod 600 /tmp/api.env
  docker run --rm --env-file /tmp/api.env \
    -v ~/climax_mvp:/repo -v ~/climax_mvp/backend/app:/app/app:ro climax-backend:latest \
    python3 /repo/scripts/btli_energy_validate.py --probe
  … --run          (부산 8개 동, 2025년)
출력: /repo/data/btli_energy_busan_2026-09-25.csv  + 요약
"""
from __future__ import annotations
import argparse, asyncio, csv, json, math, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, "/app")

HUB_TITLE = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
# 에너지 API 주소는 활용가이드(hwp)에만 있다. 후보를 찔러 보고 되는 것을 쓴다(--probe).
ENERGY_CANDIDATES = [
    ("https://apis.data.go.kr/1613000/BldEngyHubService/getBeElctyUsgInfo", "elec"),
    ("https://apis.data.go.kr/1613000/BldEngyHubService/getBeGasUsgInfo", "gas"),
    ("https://apis.data.go.kr/1611000/BldEngyService/getBeElctyUsgInfo", "elec"),
    ("https://apis.data.go.kr/1613000/BldEngyService/getBeElctyUsgInfo", "elec"),
]
YEAR = 2025
MONTHS = (4, 5, 7, 8, 10)
# 부산 아파트 밀집 동 (좌표 → V-World 로 법정동코드). SCS 근린 포함.
SEEDS = {
    "연산동": (35.1850, 129.0800), "좌동": (35.1720, 129.1760), "명장동": (35.2040, 129.1030),
    "용호동": (35.1150, 129.1130), "화명동": (35.2350, 129.0120), "사직동": (35.1960, 129.0620),
    "대연동": (35.1360, 129.0960), "다대동": (35.0520, 128.9690),
}
OUT = "/repo/data/btli_energy_busan_2026-09-25.csv"


def _items(js):
    body = (js or {}).get("response", {}).get("body", {})
    it = body.get("items") or {}
    r = it.get("item") if isinstance(it, dict) else it
    if r is None:
        return [], int(body.get("totalCount") or 0)
    return ([r] if isinstance(r, dict) else list(r)), int(body.get("totalCount") or 0)


async def titles(client, key, sgg, bjd):
    out, page = [], 1
    while True:
        r = await client.get(HUB_TITLE, params={"serviceKey": key, "sigunguCd": sgg, "bjdongCd": bjd,
                                                "numOfRows": "100", "pageNo": str(page), "_type": "json"})
        try:
            js = r.json()
        except ValueError:
            # 공공데이터포털은 키·권한 오류를 JSON 이 아니라 XML/텍스트로 준다 — 원문을 보여준다(키는 안 찍힘)
            raise SystemExit(f"건축물대장 응답이 JSON 이 아님 (HTTP {r.status_code}): {r.text[:300]}")
        rows, total = _items(js)
        out += rows
        if not rows or len(out) >= total or page > 80:
            return out
        page += 1
        await asyncio.sleep(0.05)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def parcels_from(rows):
    g = defaultdict(list)
    for r in rows:
        g[(str(r.get("platGbCd") or "0"), str(r.get("bun") or "").zfill(4), str(r.get("ji") or "").zfill(4))].append(r)
    out = []
    for (pg, bun, ji), rs in g.items():
        apt = [r for r in rs if "아파트" in (r.get("etcPurps") or "") + (r.get("mainPurpsCdNm") or "")
               or "공동주택" in (r.get("mainPurpsCdNm") or "")]
        hh = sum(int(_f(r.get("hhldCnt"))) for r in apt)
        if not apt or hh < 200:
            continue
        yrs = [int(str(r.get("useAprDay"))[:4]) for r in apt if str(r.get("useAprDay") or "")[:4].isdigit()]
        out.append(dict(platGbCd=pg, bun=bun, ji=ji, bldgs=apt, hh=hh,
                        name=(apt[0].get("bldNm") or "").strip(),
                        tot_area=sum(_f(r.get("totArea")) for r in apt),
                        floors_max=max(int(_f(r.get("grndFlrCnt"))) for r in apt),
                        floors_mean=st.mean(int(_f(r.get("grndFlrCnt"))) or 1 for r in apt),
                        year=min(yrs) if yrs else None,
                        structure=(apt[0].get("strctCdNm") or "").strip()))
    return out


async def energy(client, key, url, sgg, bjd, p, ym):
    r = await client.get(url, params={"serviceKey": key, "sigunguCd": sgg, "bjdongCd": bjd,
                                      "platGbCd": p["platGbCd"], "bun": p["bun"], "ji": p["ji"],
                                      "useYm": ym, "numOfRows": "100", "pageNo": "1", "_type": "json"})
    if r.status_code != 200:
        return None, r.text[:300]
    try:
        rows, _ = _items(r.json())
    except ValueError:
        return None, r.text[:300]
    if not rows:
        return None, r.text[:300]
    # 사용량 칸 이름은 활용가이드에만 있다. 알려진 후보 → 없으면 이름에 Qty/Usg 가 든 숫자 칸.
    # 못 찾으면 0 으로 치지 않고 None (0 kWh 로 섞이면 검증이 조용히 망가진다).
    known = ("useQty", "useqty", "elctyUseQty", "elctyUsgQty", "usgQty", "useAmt", "gasUseQty")
    tot, found = 0.0, None
    for x in rows:
        k = next((k for k in known if k in x), None) or next(
            (k for k in x if any(t in k.lower() for t in ("qty", "usg", "usage")) and
             str(x[k]).replace(".", "", 1).lstrip("-").isdigit()), None)
        if k is None:
            continue
        found = k
        tot += _f(x[k])
    return (tot if found else None), json.dumps(rows[:1], ensure_ascii=False) + f"  [사용량 칸={found}, 행 {len(rows)}]"


def btli_intensity(p, lat, lon):
    from app.services.btli import facade_load
    q_tot = a_floor = q_env = 0.0
    for r in p["bldgs"]:
        fp, fl = _f(r.get("archArea")), int(_f(r.get("grndFlrCnt")))
        if fp <= 0 or fl <= 0:
            continue
        res = facade_load(lat=lat, lon=lon, footprint_area_m2=fp, floors=fl,
                          structure=r.get("strctCdNm"), material_base="concrete", material_new="concrete")
        q_tot += res["load_base_w"]; q_env += res["envelope_base_w"]; a_floor += fp * fl
    if a_floor <= 0:
        return None, None
    return q_tot / a_floor, q_env / a_floor


def spearman(x, y):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); rk = [0.0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[o[k]] = (i + j) / 2
            i = j + 1
        return rk
    rx, ry = rank(x), rank(y)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--energy-url", default="")
    a = ap.parse_args()
    import httpx
    from app.config import get_settings
    from app.services.building import _reverse_vworld
    key = get_settings().building_api_key
    # 에너지 API 키를 따로 받았으면 ENERGY_API_KEY 로 (없으면 건축물대장 키와 같은 것을 쓴다 —
    # 공공데이터포털 인증키는 계정당 하나라 보통 같다). 키 값은 절대 출력하지 않는다.
    ekey = os.environ.get("ENERGY_API_KEY") or key
    print("에너지 키:", "ENERGY_API_KEY(별도)" if os.environ.get("ENERGY_API_KEY") else "BUILDING_API_KEY 와 같음")
    if not key:
        print("BUILDING_API_KEY 없음 (.env.prod)"); return
    async with httpx.AsyncClient(timeout=20.0) as client:
        dongs = {}
        for nm, (la, lo) in (list(SEEDS.items())[:1] if a.probe else SEEDS.items()):
            pc = await _reverse_vworld(client, la, lo)
            if pc:
                dongs[nm] = (pc[0][:5], pc[0][5:10], la, lo)
        print("법정동:", {k: v[:2] for k, v in dongs.items()})

        if a.probe:
            nm, (sgg, bjd, la, lo) = next(iter(dongs.items()))
            rows = await titles(client, key, sgg, bjd)
            ps = parcels_from(rows)
            print(f"{nm}: 표제부 {len(rows)}건 → 200세대 이상 아파트 지번 {len(ps)}곳")
            if not ps:
                return
            p = max(ps, key=lambda x: x["hh"])
            print(f"  시험 지번: {p['name']} {p['bun']}-{p['ji']} ({p['hh']}세대, {len(p['bldgs'])}동, {p['year']})")
            for url, kind in ENERGY_CANDIDATES:
                v, raw = await energy(client, ekey, url, sgg, bjd, p, f"{YEAR}08")
                print(f"  {'✅' if v is not None else '  '} {kind} {url}  사용량={v}\n      → {raw[:900]}")
            return

        url = a.energy_url or ENERGY_CANDIDATES[0][0]
        out = []
        for nm, (sgg, bjd, la, lo) in dongs.items():
            rows = await titles(client, key, sgg, bjd)
            ps = parcels_from(rows)
            print(f"{nm}: 아파트 지번 {len(ps)}곳", flush=True)
            for p in ps:
                e = {}
                for m in MONTHS:
                    v, _ = await energy(client, ekey, url, sgg, bjd, p, f"{YEAR}{m:02d}")
                    e[m] = v
                    await asyncio.sleep(0.05)
                if any(e[m] is None for m in MONTHS) or p["tot_area"] <= 0:
                    continue
                shoulder = st.mean([e[4], e[5], e[10]])
                cool = max(0.0, e[7] + e[8] - 2 * shoulder)
                pred, pred_env = btli_intensity(p, la, lo)
                if pred is None:
                    continue
                out.append(dict(동=nm, 지번=f"{p['bun']}-{p['ji']}", 단지=p["name"], 세대=p["hh"],
                                동수=len(p["bldgs"]), 준공=p["year"], 최고층=p["floors_max"],
                                평균층=round(p["floors_mean"], 1), 구조=p["structure"],
                                연면적=round(p["tot_area"]), 봄가을월=round(shoulder), 냉방전기=round(cool),
                                obs_kwh_m2=round(cool / p["tot_area"], 3),
                                base_kwh_m2=round(shoulder / p["tot_area"], 3),
                                btli_w_m2=round(pred, 2), btli_env_w_m2=round(pred_env, 2)))
        if not out:
            print("적재된 짝이 없다 — --probe 로 에너지 주소·필드 확인"); return
        with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
        obs = [r["obs_kwh_m2"] for r in out]
        print(f"\n짝 {len(out)}곳 → {OUT}")
        print(f"  BTLI 총부하/연면적  vs 관측 냉방전기/연면적  Spearman ρ = {spearman([r['btli_w_m2'] for r in out], obs):+.2f}")
        print(f"  BTLI 외피부하/연면적 vs 관측                 ρ = {spearman([r['btli_env_w_m2'] for r in out], obs):+.2f}")
        print(f"  대조: 평균층수(낮을수록↑)                    ρ = {spearman([-r['평균층'] for r in out], obs):+.2f}")
        yr = [r for r in out if r["준공"]]
        print(f"  대조: 준공연도(오래될수록↑)                  ρ = {spearman([-r['준공'] for r in yr], [r['obs_kwh_m2'] for r in yr]):+.2f}  (n={len(yr)})")
        print(f"  대조: 봄가을 기저전기/연면적                  ρ = {spearman([r['base_kwh_m2'] for r in out], obs):+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
