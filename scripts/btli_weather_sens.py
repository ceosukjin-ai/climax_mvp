#!/usr/bin/env python3
"""BTLI 검증 3차 — 기상 민감도 (같은 건물 안 비교) (2026-09-25).

2차까지: 단지끼리 비교하면 사람 요인(세대 규모·생활방식)이 외피 효과를 덮는다(ρ≈0).
여기서는 **같은 단지의 달마다** 비교한다 — 사람은 그대로고 날씨만 바뀐다.
  각 단지: 4~10월 월 전기 E_m = a + b·CDD_m  (CDD = Σ max(0, 일평균기온 − 24°C), 부산)
  민감도 s = b / a  → "더위 1 도일당 기저 대비 몇 % 더 쓰나". 규모·생활방식이 나눗셈으로 빠진다.
BTLI 가 맞다면 외피부하가 큰 단지일수록 s 가 커야 한다(ρ > 0).

입력: 1차 결과 CSV (단지 목록·BTLI 값) → API 는 에너지만 다시 부른다(161곳 × 월).
  docker run --rm --env-file /tmp/api.env -v ~/climax_mvp:/repo -v ~/climax_mvp/backend/app:/app/app:ro \
    climax-backend:latest python3 -u /repo/scripts/btli_weather_sens.py
출력: /repo/data/btli_weather_sens_busan_2026-09-25.csv + 요약
"""
from __future__ import annotations
import asyncio, csv, math, os, statistics as st, sys
from datetime import date

sys.path.insert(0, "/app")
sys.path.insert(0, "/repo/scripts")

IN = "/repo/data/btli_energy_busan_2026-09-25.csv"
OUT = "/repo/data/btli_weather_sens_v3_busan_2026-09-25.csv"
URL = "https://apis.data.go.kr/1613000/BldEngyHubService/getBeElctyUsgInfo"
# 3판(2026-09-25): 원 시계열 진단(40단지×29개월) 결과
#  · 전기 useYm 은 **청구월** — 정점이 8·9월(1.5·1.4배)로, 냉방도일 정점(7·8월)보다 한 달 늦다 → 1개월 지연이 정답.
#  · 2026-05 값이 평소의 ~11배로 튄다(자료 오류) — 2판의 음수 기울기는 이 한 달 때문이었다.
#  → 청구월 5~11월(사용월 4~10월)만 쓰고, 단지 중앙값의 3배 초과·1/3 미만은 버린다.
MONTHS = [(y, m) for y in (2024, 2025) for m in range(5, 12)]
BASE_T = 24.0
LAT, LON = 35.18, 129.08          # 부산 대표점 (8개 동 모두 20 km 안)


async def cdd_by_month(client):
    r = await client.get("https://archive-api.open-meteo.com/v1/archive", params={
        "latitude": LAT, "longitude": LON, "start_date": "2024-04-01", "end_date": "2026-08-31",
        "daily": "temperature_2m_mean", "timezone": "Asia/Seoul"}, timeout=60.0)
    d = r.json()["daily"]
    out = {}
    for t, v in zip(d["time"], d["temperature_2m_mean"]):
        if v is None:
            continue
        y, m = int(t[:4]), int(t[5:7])
        out[(y, m)] = out.get((y, m), 0.0) + max(0.0, v - BASE_T)
    return out


def ols(x, y):
    mx, my = st.mean(x), st.mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx == 0:
        return None, None, None
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    a = my - b * mx
    ss = sum((c - my) ** 2 for c in y)
    r2 = 1 - sum((c - (a + b * xi)) ** 2 for xi, c in zip(x, y)) / ss if ss else None
    return a, b, r2


STAT: dict = {}
MSTAT: dict = {}


async def main():
    import httpx
    from app.config import get_settings
    from app.services.building import _reverse_vworld
    from btli_energy_validate import SEEDS, _items, _f, spearman
    key = os.environ.get("ENERGY_API_KEY") or get_settings().building_api_key
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    print(f"단지 {len(rows)}곳 · 월 {len(MONTHS)}개 ({MONTHS[0]}~{MONTHS[-1]}) · 호출 약 {len(rows)*len(MONTHS)}건")
    async with httpx.AsyncClient(timeout=20.0) as client:
        cdd = await cdd_by_month(client)
        print("CDD(24°C 기준):", {f"{y}-{m:02d}": round(cdd.get((y, m), 0)) for y, m in MONTHS})
        codes = {}
        for nm, (la, lo) in SEEDS.items():
            pc = await _reverse_vworld(client, la, lo)
            if pc:
                codes[nm] = (pc[0][:5], pc[0][5:10])
        out = []
        for i, r in enumerate(rows, 1):
            if r["동"] not in codes:
                continue
            sgg, bjd = codes[r["동"]]
            bun, ji = r["지번"].split("-")
            # 2026-09-25 2판: 1판은 161곳 중 24곳만 10개월을 채웠다(R² 0.03) — 호출이 빨라 거절됐거나
            # 달이 비었을 수 있다. 재시도 3번 + 간격 0.12 s, 실패 사유를 센다.
            got = {}
            for (y, m) in MONTHS:
                q = {"serviceKey": key, "sigunguCd": sgg, "bjdongCd": bjd, "platGbCd": "0",
                     "bun": bun, "ji": ji, "useYm": f"{y}{m:02d}", "numOfRows": "10", "pageNo": "1", "_type": "json"}
                v, why = None, "empty"
                for t in range(3):
                    try:
                        resp = await client.get(URL, params=q)
                        items, _ = _items(resp.json())
                        v = sum(_f(x.get("useQty")) for x in items) if items else None
                        why = "ok" if v else "empty"
                        break
                    except Exception as e:  # noqa: BLE001
                        why = type(e).__name__
                        await asyncio.sleep(1.0 + t)
                STAT[why] = STAT.get(why, 0) + 1
                MSTAT.setdefault(f"{y}-{m:02d}", [0, 0])[0 if v else 1] += 1
                if v:
                    got[(y, m)] = v
                await asyncio.sleep(0.12)
            # 전기 사용월(useYm)이 **검침·청구 월**이면 실제 사용은 전달일 수 있다 → 0개월·1개월 지연 둘 다 맞춘다
            if got:
                med = st.median(got.values())
                got = {k: v for k, v in got.items() if med / 3 <= v <= 3 * med}
            fits = {}
            for lag in (0, 1):
                xs, ys = [], []
                for (y, m), v in got.items():
                    py, pm = (y, m - lag) if m - lag >= 1 else (y - 1, 12)
                    if (py, pm) in cdd:
                        xs.append(cdd[(py, pm)]); ys.append(v)
                fits[lag] = (xs, ys)
            xs, ys = fits[1]          # 1개월 지연(청구월→사용월)이 정본
            if len(xs) < 8:
                continue
            a, b, r2 = ols(xs, ys)
            a1, b1, r21 = ols(*fits[0]) if len(fits[0][0]) >= 10 else (None, None, None)   # 참고: 지연 없음
            if a is None or a <= 0:
                continue
            out.append(dict(동=r["동"], 지번=r["지번"], 단지=r["단지"], 세대=r["세대"], 준공=r["준공"],
                            평균층=r["평균층"], 개월=len(xs), base_kwh=round(a), slope_kwh_per_cdd=round(b, 1),
                            sens_pct_per_cdd=round(b / a * 100, 3), r2=round(r2, 2) if r2 is not None else "",
                            sens_lag0=round(b1 / a1 * 100, 3) if a1 and a1 > 0 else "",
                            r2_lag0=round(r21, 2) if r21 is not None else "",
                            btli_env_w_m2=r["btli_env_w_m2"], btli_w_m2=r["btli_w_m2"]))
            if i % 20 == 0:
                print(f"  {i}/{len(rows)}", flush=True)
    print("\n호출 결과:", STAT)
    print("월별 [있음, 없음]:", MSTAT)
    if not out:
        print("맞춘 단지 없음"); return
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    s = [o["sens_pct_per_cdd"] for o in out]
    good = [o for o in out if o["r2"] != "" and o["r2"] >= 0.5]
    print(f"\n단지 {len(out)}곳 → {OUT}")
    l1 = [o for o in out if o["r2_lag0"] != ""]
    if l1:
        print(f"  (참고: 지연 없음) 민감도 중앙값 {st.median([o['sens_lag0'] for o in l1]):.2f} %/도일 · "
              f"R² 중앙값 {st.median([o['r2_lag0'] for o in l1]):.2f}")
    print(f"  민감도 중앙값 {st.median(s):.2f} %/도일 · 적합 R² 중앙값 {st.median([o['r2'] for o in out if o['r2']!='']):.2f} "
          f"· R²≥0.5 단지 {len(good)}곳")
    for lab, sub in (("전체", out), ("R²≥0.5", good)):
        if len(sub) < 8:
            continue
        ys = [o["sens_pct_per_cdd"] for o in sub]
        print(f"[{lab}, n={len(sub)}] 민감도(%/도일) 와의 ρ")
        print(f"   BTLI 외피부하/연면적        ρ = {spearman([float(o['btli_env_w_m2']) for o in sub], ys):+.2f}")
        print(f"   BTLI 외피 비중              ρ = {spearman([float(o['btli_env_w_m2'])/float(o['btli_w_m2']) for o in sub], ys):+.2f}")
        print(f"   평균층수(낮을수록↑)          ρ = {spearman([-float(o['평균층']) for o in sub], ys):+.2f}")
        yr = [o for o in sub if o['준공']]
        print(f"   준공연도(오래될수록↑)        ρ = {spearman([-float(o['준공']) for o in yr], [o['sens_pct_per_cdd'] for o in yr]):+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
