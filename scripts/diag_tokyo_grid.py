#!/usr/bin/env python3
"""도쿄 격자 검증 — 깔린 값이 실시간과 같고, 상식과 맞는가 (2026-09-17).

배경: 도쿄 23구 격자 2,257,951점을 14시간 걸려 깔았다. 그런데 일본 체감기후는
**한 번도 실측과 맞대본 적이 없다.** 한국은 8월 80점·3월 27점으로 맞췄지만 일본은 0점이다.
앱으로 낼 거면 최소한 "값이 말이 되는지"는 확인하고 가야 한다. 심사 후에 틀린 걸 알면
고칠 때마다 심사를 다시 탄다.

실측이 없으므로 가능한 검증은 두 가지다. 둘 다 한다.

[1] 격자 == 실시간 인가
    앱이 격자를 읽는다. 격자가 `geo.svf_geometric` 과 다른 값을 내면 앱과 검증이 어긋난다.
    무작위 표본을 뽑아 실시간으로 다시 계산해 맞대 본다.
    판정: |차이| 대부분 0.01 이하. 그보다 크면 절차가 어긋난 것이다.

[2] 아는 장소가 아는 값으로 나오는가
    도쿄에서 하늘이 열린 곳과 닫힌 곳은 사람이 안다. 그 순서가 맞는지 본다.
    이건 정확도 검증이 아니라 **말이 되는지** 검증이다 — 순서가 뒤집히면 무언가 틀린 것이고,
    순서가 맞아도 절대값이 맞다는 뜻은 아니다. (절대값은 현지 실측이 있어야 한다.)

  docker cp scripts/diag_tokyo_grid.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/diag_tokyo_grid.py 2>/dev/null
"""
from __future__ import annotations
import asyncio, statistics as st, sys

sys.path.insert(0, "/app")

N_SAMPLE = 25          # 실시간 재계산은 점당 수백 ms — 25점이면 충분하고 몇 분 안에 끝난다

# 아는 장소. 기대는 '순서'지 절대값이 아니다.
LANDMARKS = [
    ("皇居前広場 (황거 앞 광장)",        35.6810, 139.7570, "매우 열림"),
    ("代々木公園 중앙 (요요기공원)",      35.6716, 139.6949, "열림(수관)"),
    ("浅草寺 경내 (센소지)",             35.7148, 139.7967, "열림"),
    ("東京駅 마루노우치 앞",             35.6812, 139.7671, "중간"),
    ("渋谷スクランブル (시부야 교차로)",  35.6595, 139.7005, "중간"),
    ("銀座4丁目 교차로",                35.6712, 139.7650, "닫힘"),
    ("新宿 도청 고층가",                35.6894, 139.6917, "닫힘"),
    ("新宿ゴールデン街 (좁은 골목)",      35.6939, 139.7046, "매우 닫힘"),
]


async def main() -> None:
    import asyncpg
    from app.config import get_settings
    from app.services.geo import svf_geometric

    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    c = await asyncpg.connect(url)

    BB = "lat>=35.5 AND lat<35.9 AND lon>=139.55 AND lon<139.92"

    # ── 분포 ────────────────────────────────────────────────
    n = await c.fetchval(f"SELECT count(*) FROM skyline_grid WHERE {BB}")
    print(f"\n도쿄 격자 {n:,}점")
    row = await c.fetchrow(
        f"SELECT avg(svf) a, stddev(svf) s, min(svf) mn, max(svf) mx, "
        f"avg(n_bld) nb, avg(width_m) w FROM skyline_grid WHERE {BB}")
    print(f"  SVF 평균 {row['a']:.3f} (표준편차 {row['s']:.3f})  범위 {row['mn']:.3f}~{row['mx']:.3f}")
    print(f"  주변 건물 수 평균 {row['nb']:.1f}   가로폭 평균 {row['w']:.1f} m")

    print("\n  SVF 분포")
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        hi = lo + 0.2
        k = await c.fetchval(
            f"SELECT count(*) FROM skyline_grid WHERE {BB} AND svf>=$1 AND svf<$2", lo, hi)
        bar = "█" * int(60 * k / max(n, 1))
        print(f"    {lo:.1f}~{hi:.1f}  {k:>9,} ({100*k/max(n,1):4.1f}%) {bar}")

    src = await c.fetch(f"SELECT src, count(*) k FROM skyline_grid WHERE {BB} GROUP BY src ORDER BY k DESC")
    print("\n  건물 원천: " + ", ".join(f"{r['src']} {r['k']:,}" for r in src))

    # ── [1] 격자 == 실시간 ──────────────────────────────────
    print(f"\n[1] 격자 vs 실시간 (무작위 {N_SAMPLE}점)")
    rows = await c.fetch(
        f"SELECT lat, lon, svf FROM skyline_grid WHERE {BB} "
        f"ORDER BY random() LIMIT {N_SAMPLE}")
    diffs = []
    for r in rows:
        d = await svf_geometric(float(r["lat"]), float(r["lon"]))
        live = d.get("svf")
        if live is None:
            print(f"  ({r['lat']:.5f},{r['lon']:.5f}) 실시간 계산 불가 — {d.get('reason')}")
            continue
        diffs.append(float(live) - float(r["svf"]))
    if diffs:
        big = [x for x in diffs if abs(x) > 0.01]
        print(f"  n={len(diffs)}  평균차 {st.mean(diffs):+.4f}  "
              f"최대 |차| {max(abs(x) for x in diffs):.4f}  0.01 초과 {len(big)}점")
        print("  판정: " + ("✅ 격자와 실시간이 같다"
                          if len(big) <= len(diffs) * 0.1
                          else "❌ 어긋난다 — 앱이 읽는 값과 검증하는 값이 다르다"))

    # ── [2] 아는 장소 ───────────────────────────────────────
    print("\n[2] 아는 장소 (기대는 '순서'지 절대값이 아니다)")
    print(f"  {'장소':<30}{'격자SVF':>9}{'건물':>6}{'가로폭':>8}   기대")
    got = []
    for nm, la, lo, expect in LANDMARKS:
        r = await c.fetchrow(
            "SELECT svf, n_bld, width_m FROM skyline_grid "
            "ORDER BY (lat-$1)^2 + (lon-$2)^2 LIMIT 1", la, lo)
        if r is None:
            print(f"  {nm:<30}{'없음':>9}")
            continue
        got.append((nm, float(r["svf"]), expect))
        w = f"{r['width_m']:.0f}m" if r["width_m"] is not None else "-"
        print(f"  {nm:<30}{r['svf']:>9.3f}{r['n_bld'] or 0:>6}{w:>8}   {expect}")

    if got:
        print("\n  SVF 높은 순:")
        for nm, s, expect in sorted(got, key=lambda x: -x[1]):
            print(f"    {s:.3f}  {nm}  ({expect})")
        print("\n  위 순서가 '매우 열림 → 열림 → 중간 → 닫힘 → 매우 닫힘' 과 대체로 맞으면")
        print("  격자가 도쿄 도시형태를 읽고 있다는 뜻이다. 뒤집혀 있으면 건물 데이터를 의심한다.")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
