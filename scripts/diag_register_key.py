#!/usr/bin/env python3
"""표제부 조회가 왜 못 채우나 — 키를 직접 맞춰본다 (2026-09-14).

앞선 진단 결과:
  부암제1동 231동 중 205동이 층수 결측(gro_flo_co='0'). 그런데 bd_mgt_sn 은 205동 전부 있다.
  즉 _needs_floors 는 PNU19 를 정상적으로 만들었고, _fill_floors_from_register_many 가
  bldg_register 를 조회했는데도 못 채웠다. 키가 안 맞았거나, 그 동네 행이 없거나 둘 중 하나다.

  2026-09-11 기록에 함정이 적혀 있다:
    "지적 PNU(GIS SHP A2)는 1=대지·2=산, 표제부·도로명주소(bd_mgt_sn)는 0=대지·1=산·2=블록."
  표본 bd_mgt_sn 2623010800106490118008521 의 11번째 자리가 '1' 이다.
  이게 지적 기준의 '대지'인지 도로명주소 기준의 '산'인지에 따라 조회 결과가 갈린다.

이 스크립트는 추측하지 않고 전부 찍는다:
  1. 결측 건물의 PNU19 를 그대로 조회 → 몇 건 맞나
  2. 11번째 자리를 0/1/2 로 바꿔 각각 조회 → 어느 형태가 맞나
  3. 그 법정동(앞 10자리)으로 표제부에 실제로 몇 행이 있고 어떤 PNU 를 쓰는지 표본 출력
  4. bldg_register 전체 규모와 부산(26) 규모

  docker cp scripts/diag_register_key.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/diag_register_key.py
"""
from __future__ import annotations
import asyncio
import csv
import sys
from collections import Counter

sys.path.insert(0, "/app")

from app.services import geo                         # noqa: E402

IN = "/tmp/tier3_engine_output_80_v6.csv"
SAMPLE_NAME = "부암"


def missing(p: dict) -> bool:
    try:
        if p.get("height") is not None and float(p["height"]) > 0:
            return False
    except (TypeError, ValueError):
        pass
    for k in ("gro_flo_co", "building:levels"):
        try:
            if int(p.get(k) or 0) > 0:
                return False
        except (TypeError, ValueError):
            continue
    return True


def swap11(pnu19: str, ch: str) -> str:
    return pnu19[:10] + ch + pnu19[11:] if len(pnu19) >= 19 else pnu19


async def main() -> None:
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    tgt = [r for r in rows if SAMPLE_NAME in str(r.get("지점명", ""))] or rows[:1]
    r = tgt[0]
    lat, lon = float(r["위도"]), float(r["경도"])
    rings, src = await geo._rings_cached(lat, lon)
    miss = [p for _g, p in rings if missing(p)]
    print(f"\n지점 {r.get('지점명','')}  {lat:.5f},{lon:.5f}   폴리곤 {len(rings)}동 ({src})   결측 {len(miss)}동")

    sns = [str(p.get("bd_mgt_sn") or "") for p in miss]
    sns = [s for s in sns if len(s) >= 19]
    pnus = list(dict.fromkeys(s[:19] for s in sns))
    print(f"PNU19 고유 {len(pnus)}개")
    print(f"  11번째 자리 분포: {dict(Counter(p[10] for p in pnus))}")
    print(f"  표본: {pnus[:3]}")

    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 풀 없음 — 중단")
        return

    async with pool.acquire() as c:
        # 0. 테이블 규모
        try:
            tot = await c.fetchval("SELECT count(*) FROM bldg_register")
            bsn = await c.fetchval("SELECT count(*) FROM bldg_register WHERE pnu LIKE '26%'")
            print(f"\n=== bldg_register 규모 ===")
            print(f"  전체 {tot:,}행   부산(26로 시작) {bsn:,}행")
        except Exception as e:  # noqa: BLE001
            print(f"\n  테이블 조회 실패: {type(e).__name__}: {e}")
            return

        # 1~2. 원본 / 11번째 자리 치환 각각 조회
        print(f"\n=== 조회 적중 ({len(pnus)}개 기준) ===")
        best = None
        for label, keys in [("원본 그대로", pnus)] + [
            (f"11번째를 '{ch}'로", [swap11(p, ch) for p in pnus]) for ch in "012"
        ]:
            recs = await c.fetch("SELECT DISTINCT pnu FROM bldg_register WHERE pnu = ANY($1::text[])",
                                 keys)
            n = len(recs)
            print(f"  {label:16} {n:4d} / {len(pnus)}  ({100*n/max(len(pnus),1):5.1f} %)")
            if best is None or n > best[1]:
                best = (label, n, keys)

        # 3. 같은 법정동에 표제부가 뭘 갖고 있나
        bjd = pnus[0][:10]
        print(f"\n=== 법정동 {bjd} 의 표제부 행 ===")
        cnt = await c.fetchval("SELECT count(*) FROM bldg_register WHERE pnu LIKE $1", bjd + "%")
        print(f"  {cnt:,}행")
        smp = await c.fetch(
            "SELECT pnu, dong, floors, height FROM bldg_register WHERE pnu LIKE $1 LIMIT 8", bjd + "%")
        for s in smp:
            print(f"    {s['pnu']}  동={s['dong'] or '-':<8} 층={s['floors']}  높이={s['height']}")
        if cnt == 0:
            print("  -> 이 동네가 표제부 적재에서 빠졌다. 키 문제가 아니라 적재 범위 문제다.")

        # 4. 맞는 키로 채울 수 있는 층수 분포
        if best and best[1] > 0:
            recs = await c.fetch(
                "SELECT pnu, max(floors) AS f, max(height) AS h FROM bldg_register "
                "WHERE pnu = ANY($1::text[]) GROUP BY pnu", best[2])
            fl = sorted(int(x["f"] or 0) for x in recs)
            fl = [f for f in fl if f > 0]
            print(f"\n=== '{best[0]}' 로 채울 수 있는 것 ===")
            if fl:
                print(f"  층수 있는 건물 {len(fl)}개  중앙값 {fl[len(fl)//2]}층  최대 {fl[-1]}층")
                print(f"  3층 이상 {sum(1 for f in fl if f >= 3)}개, "
                      f"5층 이상 {sum(1 for f in fl if f >= 5)}개")
                print("  (태양고도 60도: 3층->그늘 약 3.7m, 5층->약 8m, 10층->약 16m)")
            else:
                print("  층수가 0 인 행만 있다 — 채워도 소용없다.")


if __name__ == "__main__":
    asyncio.run(main())
