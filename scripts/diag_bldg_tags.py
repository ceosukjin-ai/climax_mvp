#!/usr/bin/env python3
"""높이 결측 건물이 진짜 없는 것인가, 못 찾는 것인가 (2026-09-14).

왜:
  80점 주변 건물 30,468동 중 33.8%가 높이·층수 둘 다 없다. 부암제1동은 90%다.
  그런데 지금 코드가 표제부에서 층수를 채우는 조건이 이렇다.

      def _needs_floors(props):
          sn = str(props.get("bd_mgt_sn") or "")      # V-World 속성
          return sn[:19] if len(sn) >= 19 else None

  반면 DB 타일을 만든 build_kr_bldg_tiles.py 는 태그를 이렇게 쓴다.

      tags = {"building": bval, "src": SRC, "pnu": pnu}     # 키가 pnu

  키가 다르면 _needs_floors 가 늘 None 을 돌려주고 표제부 채우기가 아예 안 돈다.
  80점은 전부 출처가 DB 였으므로 전부 이 경로다.

무엇을 보는가:
  1. 결측 건물의 태그에 무엇이 들어 있는가 (pnu / bd_mgt_sn / src / 그 외)
  2. 그 pnu 가 bldg_register(표제부, 전국 736만 동)에 실제로 있는가
  3. 있다면 층수·높이가 얼마인가

읽는 법:
  · pnu 는 있는데 bd_mgt_sn 이 없다  -> 키 불일치가 맞다. 코드 한 줄 문제.
  · pnu 도 없다                      -> 타일 생성 단계에서 빠졌다. 재적재 필요.
  · pnu 는 있는데 표제부에 없다       -> 정말 무허가·부속. 데이터로는 못 채운다.

  docker cp scripts/diag_bldg_tags.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/diag_bldg_tags.py
"""
from __future__ import annotations
import asyncio
import csv
import sys
from collections import Counter

sys.path.insert(0, "/app")

from app.services import geo                         # noqa: E402

IN = "/tmp/tier3_engine_output_80_v6.csv"
SAMPLE_NAME = "부암"          # 결측이 가장 심한 동네부터 본다


def has_height(p: dict) -> bool:
    try:
        if p.get("height") is not None and float(p["height"]) > 0:
            return True
    except (TypeError, ValueError):
        pass
    for k in ("gro_flo_co", "building:levels"):
        try:
            if int(p.get(k) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


async def main() -> None:
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    target = [r for r in rows if SAMPLE_NAME in str(r.get("지점명", ""))] or rows[:1]
    r = target[0]
    lat, lon = float(r["위도"]), float(r["경도"])
    print(f"\n표본 지점: {r.get('지점명','')}  {lat:.5f}, {lon:.5f}")

    rings, src = await geo._rings_cached(lat, lon)
    print(f"폴리곤 {len(rings)}동  출처 {src}\n")

    miss = [p for _g, p in rings if not has_height(p)]
    ok = [p for _g, p in rings if has_height(p)]
    print(f"높이 있음 {len(ok)}  /  결측 {len(miss)}\n")

    # 1. 결측 건물의 태그 구성
    keys = Counter()
    for p in miss:
        for k in p:
            keys[k] += 1
    print("=== 결측 건물이 들고 있는 태그 (많은 순) ===")
    for k, v in keys.most_common(15):
        print(f"  {k:22} {v:5d} / {len(miss)}")

    n_pnu = sum(1 for p in miss if str(p.get("pnu") or "").strip())
    n_sn = sum(1 for p in miss if len(str(p.get("bd_mgt_sn") or "")) >= 19)
    print(f"\n  pnu 있음         {n_pnu} / {len(miss)}")
    print(f"  bd_mgt_sn 있음   {n_sn} / {len(miss)}   <- _needs_floors 가 보는 키")
    if n_pnu and not n_sn:
        print("  => 키 불일치 확정. 표제부 채우기가 이 경로에서 한 번도 안 돌았다.")

    print("\n  표본 3동의 태그 전체:")
    for p in miss[:3]:
        print(f"    {dict(p)}")
    if ok:
        print("\n  (비교) 높이 있는 건물 2동:")
        for p in ok[:2]:
            print(f"    {dict(p)}")

    # 2. 그 pnu 가 표제부에 있는가
    pnus = [str(p.get("pnu")).strip() for p in miss if str(p.get("pnu") or "").strip()]
    pnus = list(dict.fromkeys(pnus))[:500]
    if not pnus:
        print("\n  pnu 가 없어 표제부 조회를 못 한다. 타일 재적재가 필요하다.")
        return

    try:
        from app.services.skyline import _get_pool
        pool = await _get_pool()
        if pool is None:
            print("\n  DB 풀 없음 — 표제부 조회 생략")
            return
        async with pool.acquire() as c:
            recs = await c.fetch(
                "SELECT pnu, dong, floors, height FROM bldg_register WHERE pnu = ANY($1::text[])",
                pnus)
    except Exception as e:  # noqa: BLE001
        print(f"\n  표제부 조회 실패: {type(e).__name__}: {e}")
        return

    by: dict[str, list] = {}
    for rec in recs:
        by.setdefault(rec["pnu"], []).append((rec["dong"], rec["floors"], rec["height"]))

    hit = len(by)
    print(f"\n=== 2. 표제부(bldg_register) 조회 — 결측 pnu {len(pnus)}개 ===")
    print(f"  표제부에 있음   {hit} ({100*hit/len(pnus):.0f} %)")
    print(f"  없음            {len(pnus)-hit} ({100*(len(pnus)-hit)/len(pnus):.0f} %)  <- 무허가·부속")

    if hit:
        fl = [max(x[1] or 0 for x in v) for v in by.values()]
        fl = [f for f in fl if f > 0]
        if fl:
            fl.sort()
            print(f"\n  채울 수 있는 층수: 중앙값 {fl[len(fl)//2]}층, "
                  f"최대 {fl[-1]}층, 3층 이상 {sum(1 for f in fl if f >= 3)}개")
            print("  (태양고도 60도에서 3층이면 그늘 반경 약 3.7m, 10층이면 약 15m)")
        print("\n  표본 5건:")
        for pnu, v in list(by.items())[:5]:
            print(f"    {pnu}  {v[:2]}")


if __name__ == "__main__":
    asyncio.run(main())
