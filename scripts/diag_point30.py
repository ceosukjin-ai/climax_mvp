#!/usr/bin/env python3
"""point30 이 왜 10.8도 과소인가 — 네 지점의 원자료를 통째로 편다 (2026-09-17).

배경: 9/16 짝 4건에서 point30 −10.84, point31 +8.68 로 부호가 갈렸다.
SVF 와 오차 사이에 방향성이 없다. 계통 오차가 아니라 **지점별 입력이 틀린다**는 신호다.

가정하지 않고, 잔차를 만드는 모든 입력을 지점마다 나란히 놓는다:
  · 실측 원자료(ta, rh, wind_ms, globe_c, surface_c)
  · 엔진 산출(mrt_globe, ta, u_p) — 그리고 엔진이 쓴 기온과 실측 기온의 차이
  · 실측 흑구 → Tmrt 환산의 **풍속 민감도** (v 를 0.1/0.3/0.5/1.0/2.0 으로 바꿔본다)
  · SVF 분해 — 수관 끈 값, 건물 개수, 수관 개수
  · 지표(NDVI/알베도/재질)

읽는 법:
  · 풍속 민감도가 크면 -> 잔차의 상당 부분이 **실측 환산**에서 온다. 엔진 탓이 아니다.
  · 엔진 ta 와 실측 ta 가 벌어져 있으면 -> 기상 입력이 그 지점 실황과 다르다.
  · SVF 가 수관 끄면 확 뛰면 -> 수관 차폐가 그 지점을 지배한다.
  · surface_c(실측 지표온도)가 높은데 엔진이 낮게 보면 -> 지표 복사항 과소.
"""
from __future__ import annotations
import asyncio, json, sys
sys.path.insert(0, "/app")


def g2t(tg, ta, v, d=0.05, eps=0.95):
    v = max(v, 0.05)
    return (((tg + 273.15) ** 4 + 1.1e8 * v ** 0.6 / (eps * d ** 0.4) * (tg - ta)) ** 0.25) - 273.15


async def main():
    import asyncpg
    from app.config import get_settings
    from app.services.geo import svf_geometric, canopy_items
    from app.services.sentinel_hub import get_surface, surface_to_materials

    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    rows = await conn.fetch(
        "SELECT observed_at, lat, lon, meas, est, note FROM field_check "
        "WHERE est ? 'mrt_globe' ORDER BY observed_at")
    await conn.close()

    for r in rows:
        meas = r["meas"] if isinstance(r["meas"], dict) else json.loads(r["meas"] or "{}")
        est = r["est"] if isinstance(r["est"], dict) else json.loads(r["est"] or "{}")
        print("=" * 72)
        print(f"{r['note']}   ({r['lat']:.6f}, {r['lon']:.6f})")
        print(f"  실측 {json.dumps(meas, ensure_ascii=False)}")
        print(f"  엔진 {json.dumps(est, ensure_ascii=False)}")

        ta_m = meas.get("ta"); ta_e = est.get("ta")
        if ta_m is not None and ta_e is not None:
            print(f"  기온차(엔진−실측) {float(ta_e) - float(ta_m):+.2f} °C")

        tg = meas.get("globe_c")
        if tg is not None and ta_m is not None:
            print(f"  [풍속 민감도] 실측 흑구 {tg} °C, 기온 {ta_m} °C → 환산 Tmrt")
            for v in (0.1, 0.3, 0.5, 1.0, 2.0, 3.0):
                o = g2t(float(tg), float(ta_m), v)
                mark = "  ← 실측 풍속" if abs(v - float(meas.get("wind_ms") or -9)) < 0.06 else ""
                d = (f"  잔차 {est['mrt_globe'] - o:+7.2f}" if est.get("mrt_globe") else "")
                print(f"      v={v:4.1f} m/s  →  {o:7.2f} °C{d}{mark}")

        d = await svf_geometric(float(r["lat"]), float(r["lon"]))
        print(f"  SVF {d.get('svf')}  (근거 {d.get('reason')})")
        for k in ("n_bldg", "n_canopy", "svf_bldg_only", "eye_height_m", "snap_m", "source"):
            if k in d:
                print(f"      {k} = {d[k]}")
        try:
            ci = canopy_items(float(r["lat"]), float(r["lon"]))
            print(f"      수관 항목 {len(ci)}개")
        except Exception as e:  # noqa: BLE001
            print(f"      수관 항목 조회 실패: {e}")

        try:
            s = await get_surface(float(r["lat"]), float(r["lon"]))
            print(f"  지표 NDVI {s['ndvi']:.2f} 알베도 {s['albedo']:.2f} "
                  f"재질 {surface_to_materials(s)[0]}")
        except Exception as e:  # noqa: BLE001
            print(f"  지표 조회 실패: {e}")


if __name__ == "__main__":
    asyncio.run(main())
