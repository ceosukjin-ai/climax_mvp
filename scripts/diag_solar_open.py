#!/usr/bin/env python3
"""양지·고SVF·아스팔트에서 엔진이 과소평가하는가 (2026-09-17).

주장(현장): "낮에 일사가 높은 곳 — SVF 높고 아스팔트고 그늘이 전혀 없는 곳 — 에서
엔진 체감기후가 실제보다 낮다."

가정하지 않는다. field_check 에 쌓인 실측-엔진 짝을 **조건으로 잘라서** 잔차를 본다.
비교 기준은 흑구다: 엔진의 흑구 예측(est.mrt_globe) vs 실측 흑구온도에서 ISO 7726
(Extech HT200, Ø50mm, ε0.95)으로 되돌린 Tmrt. 사람 기준(est.mrt)과 섞지 않는다.

기하(SVF·GVI·지표재질)는 시간에 무관하므로 지금 다시 계산해도 같다.
그늘 판정만 **측정 시각의 태양**으로 푼다 — 지금 태양으로 풀면 볕/그늘이 뒤집힌다.

읽는 법 (미리 정해 둔다):
  · 양지에서만 음(과소)  -> 직달 단파(DNI·흡수) 쪽. 주장이 맞다.
  · 양지·그늘 둘 다 음    -> 일사가 아니라 전역 bias. 다른 항이다.
  · 아스팔트에서만 음     -> 지표 반사(sw_reflected)·지표온도(lw_surface) 과소.
                            고칠 항이 일사가 아니다.
  · 고SVF 에서만 음       -> 천공 단파·장파 분배 문제.
  · 어느 쪽도 뚜렷하지 않음 -> 짝 수가 모자란다. 더 재야 한다.

  docker compose -f infra/ncp/docker-compose.api.yml --env-file .env.prod \
    run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/diag_solar_open.py
"""
from __future__ import annotations
import asyncio
import math
import statistics as st
import sys
from datetime import datetime

sys.path.insert(0, "/app")

from datetime import timedelta, timezone
KST = timezone(timedelta(hours=9))
MIN_N = 3          # 이보다 적은 칸은 숫자를 믿지 않는다


def globe_to_tmrt(tg: float, ta: float, v: float, d: float = 0.05, eps: float = 0.95) -> float:
    """ISO 7726 강제대류. 현장 흑구계는 Ø50mm 다(표준 150mm 아님)."""
    v = max(v, 0.05)
    return (((tg + 273.15) ** 4
             + 1.1e8 * v ** 0.6 / (eps * d ** 0.4) * (tg - ta)) ** 0.25) - 273.15


def cell(name: str, errs: list[float]) -> str:
    if not errs:
        return f"  {name:<22}      -"
    n = len(errs)
    bias = st.mean(errs)
    mae = st.mean(abs(e) for e in errs)
    mark = "" if n >= MIN_N else "   (표본 부족)"
    return f"  {name:<22}{n:>5}  bias {bias:+6.2f}  MAE {mae:5.2f}{mark}"


async def main() -> None:
    import asyncpg
    from app.config import get_settings
    from app.services.geo import svf_geometric, sun_blocked_outdoor
    from app.services.sentinel_hub import (get_surface, ndvi_to_gvi,
                                           surface_to_materials)
    from vpti_core import estimate_solar, DEFAULT_CONFIG

    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    rows = await conn.fetch(
        "SELECT observed_at, lat, lon, meas, est, note FROM field_check "
        "ORDER BY observed_at")
    await conn.close()

    import json
    recs = []
    skipped = 0
    for r in rows:
        meas = r["meas"] if isinstance(r["meas"], dict) else json.loads(r["meas"] or "{}")
        est = r["est"] if isinstance(r["est"], dict) else json.loads(r["est"] or "{}")
        if est.get("mrt_globe") is None or meas.get("globe_c") is None:
            skipped += 1
            continue
        try:
            ta = float(meas.get("ta", est.get("ta")))
            v = float(meas.get("wind_ms", est.get("u_p") or 0.5))
            obs = (float(est["mrt_globe_obs"]) if est.get("mrt_globe_obs") is not None
                   else globe_to_tmrt(float(meas["globe_c"]), ta, v))
        except (TypeError, ValueError):
            skipped += 1
            continue
        # 태양을 풀 시각. observed_at 은 **적재 시각**이다 — 캡처 복원분은 몇 시간 뒤에
        # 올라왔고, 그 시각으로 풀면 볕/그늘과 일사가 통째로 틀린다.
        # /field/check 가 when 으로 받은 실제 측정 시각이 est.solar_at 에 있다.
        t_sun = r["observed_at"]
        if est.get("solar_at"):
            try:
                t_sun = datetime.fromisoformat(str(est["solar_at"]))
            except ValueError:
                pass
        recs.append({
            "t": r["observed_at"], "t_sun": t_sun,
            "lat": float(r["lat"]), "lon": float(r["lon"]),
            "err": float(est["mrt_globe"]) - obs,
            "note": r["note"] or "",
        })

    print(f"\nfield_check {len(rows)}건 중 흑구 짝 {len(recs)}건 "
          f"(건너뜀 {skipped} — mrt_globe 또는 globe_c 없음)")
    if not recs:
        print("대조할 짝이 없다. 9/16 이전 기록에는 엔진 흑구 예측이 없다.")
        return

    # 지점별 기하는 좌표마다 한 번만 — 같은 지점을 여러 번 쟀을 수 있다
    cache: dict[tuple[float, float], dict] = {}
    for rec in recs:
        key = (round(rec["lat"], 5), round(rec["lon"], 5))
        if key not in cache:
            d = await svf_geometric(rec["lat"], rec["lon"])
            svf = d.get("svf")
            try:
                surf = await get_surface(rec["lat"], rec["lon"])
            except Exception:  # noqa: BLE001
                surf = None
            gvi = ndvi_to_gvi(surf["ndvi"]) if surf else None
            asph = 0.0
            if surf:
                for m, f in surface_to_materials(surf)[0]:
                    if m == "asphalt":
                        asph = f
            cache[key] = {"svf": svf, "gvi": gvi, "asphalt": asph}
        rec.update(cache[key])

        sol = estimate_solar(rec["lat"], rec["lon"], rec["t_sun"], config=DEFAULT_CONFIG.solar)
        blocked, _ = await sun_blocked_outdoor(
            rec["lat"], rec["lon"], sol.solar_azimuth_deg, sol.solar_elevation_deg)
        rec["night"] = sol.solar_elevation_deg <= 0.0
        rec["blocked"] = bool(blocked)
        rec["el"] = sol.solar_elevation_deg
        rec["dni"] = sol.dni
        rec["ghi"] = sol.dni * math.sin(math.radians(max(sol.solar_elevation_deg, 0.0))) + sol.dhi

    ok = [r for r in recs if r["svf"] is not None and not r["night"]]
    print(f"주간·SVF 있는 짝 {len(ok)}건\n")

    all_err = [r["err"] for r in ok]
    print(cell("전체(주간)", all_err))

    print("\n[볕/그늘]")
    print(cell("양지", [r["err"] for r in ok if not r["blocked"]]))
    print(cell("그늘", [r["err"] for r in ok if r["blocked"]]))

    print("\n[SVF]")
    for lo, hi in ((0.0, 0.4), (0.4, 0.7), (0.7, 1.01)):
        print(cell(f"SVF {lo:.1f}–{hi:.1f}",
                   [r["err"] for r in ok if lo <= r["svf"] < hi]))

    print("\n[지표 아스팔트 분율]")
    for lo, hi in ((0.0, 0.3), (0.3, 0.6), (0.6, 1.01)):
        print(cell(f"asphalt {lo:.1f}–{hi:.1f}",
                   [r["err"] for r in ok if lo <= r["asphalt"] < hi]))

    print("\n[주장 그대로: 양지 + 고SVF(≥0.6) + 아스팔트(≥0.5)]")
    hit = [r for r in ok if not r["blocked"] and r["svf"] >= 0.6 and r["asphalt"] >= 0.5]
    print(cell("해당", [r["err"] for r in hit]))
    print(cell("나머지", [r["err"] for r in ok if r not in hit]))

    print("\n[일사 세기별 — 주장이 '일사' 때문이면 여기서 기울기가 보인다]")
    for lo, hi in ((0, 300), (300, 600), (600, 2000)):
        print(cell(f"GHI {lo}–{hi} W/m²",
                   [r["err"] for r in ok if lo <= r["ghi"] < hi]))

    print(f"\n{'측정시각(KST)':<18}{'SVF':>6}{'asph':>6}{'볕':>4}{'GHI':>6}{'오차':>8}  note")
    for r in sorted(ok, key=lambda x: x["err"]):
        print(f"{r['t_sun'].astimezone(KST).strftime('%m-%d %H:%M'):<18}{r['svf']:>6.2f}{r['asphalt']:>6.2f}"
              f"{('그늘' if r['blocked'] else '양지'):>4}{r['ghi']:>6.0f}{r['err']:>+8.2f}  "
              f"{r['note'][:30]}")


if __name__ == "__main__":
    asyncio.run(main())
