#!/usr/bin/env python3
"""잔차를 흑구온도 공간에서 맞댄다 — 기상 오차와 풍속 불일치를 걷어낸다 (2026-09-17).

왜 다시 하는가 (2026-09-17 진단):
  지금 잔차는 세 가지가 섞여 있다.
    · 엔진이 쓴 기온이 실측보다 3.5도 낮다(복원분이라 측정 시각이 아닌 업로드 시각 기상).
    · 엔진은 u_p≈2.3 m/s 로 흑구를 예측했는데, 실측 환산은 v=0.7 m/s 로 되돌렸다.
      **서로 다른 바람**이다. 비교가 성립하지 않는다.
    · 풍속 민감도가 ±20도라 0.5 vs 1.0 읽기 차이로 부호가 뒤집힌다.

무엇을 바꾸나:
  Tmrt 공간에서 빼지 않는다. 엔진의 흑구 Tmrt(est.mrt_globe)를 **실측 기온·실측 풍속**과
  결합해 "그 흑구가 읽었을 온도 Tg_pred" 를 만들고, **실측 흑구온도 Tg_meas** 와 직접 뺀다.
  양쪽이 같은 기온·같은 바람을 쓰므로 남는 것은 복사항뿐이다.

  ISO 7726 강제대류 (Extech HT200 Ø50mm, ε0.95):
    (Tg+273.15)^4 + 1.1e8·v^0.6/(ε·D^0.4)·(Tg−Ta) = (Tmrt+273.15)^4
  Tg 에 대해 수치해를 구한다(단조증가 → 이분법).

남는 한계 (숨기지 않는다):
  est.mrt_globe 자체가 엔진 기온 26.7 로 계산됐다. 장파 성분에는 그 오차가 아직 남는다.
  완전히 걷어내려면 실측 기온을 엔진에 주입해 다시 풀어야 한다 — 그건 /field/check 수정 건이다.
  따라서 여기 결과는 "풍속 불일치를 제거한 값"이지 "완전히 깨끗한 값"은 아니다.
"""
from __future__ import annotations
import asyncio, json, sys
sys.path.insert(0, "/app")

D, EPS = 0.05, 0.95


def tg_from_tmrt(tmrt: float, ta: float, v: float) -> float:
    """ISO 7726 을 Tg 에 대해 푼다. 좌변은 Tg 에 대해 단조증가 → 이분법."""
    v = max(v, 0.05)
    rhs = (tmrt + 273.15) ** 4
    k = 1.1e8 * v ** 0.6 / (EPS * D ** 0.4)

    def f(tg):
        return (tg + 273.15) ** 4 + k * (tg - ta) - rhs

    lo, hi = min(ta, tmrt) - 30.0, max(ta, tmrt) + 30.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


async def main():
    import asyncpg
    from app.config import get_settings
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    rows = await conn.fetch(
        "SELECT observed_at, lat, lon, meas, est, note FROM field_check "
        "WHERE est ? 'mrt_globe' ORDER BY observed_at")
    await conn.close()

    print(f"\n{'지점':<10}{'Ta실측':>7}{'v실측':>6}{'Ta엔진':>7}{'v엔진':>6}"
          f"{'Tg실측':>8}{'Tg예측':>8}{'오차':>8}{'   (참고)Tmrt공간':>18}")
    errs = []
    for r in rows:
        meas = r["meas"] if isinstance(r["meas"], dict) else json.loads(r["meas"] or "{}")
        est = r["est"] if isinstance(r["est"], dict) else json.loads(r["est"] or "{}")
        if meas.get("globe_c") is None or est.get("mrt_globe") is None:
            continue
        ta, v = float(meas["ta"]), float(meas.get("wind_ms") or 0.5)
        tg_m = float(meas["globe_c"])
        tg_p = tg_from_tmrt(float(est["mrt_globe"]), ta, v)
        e = tg_p - tg_m
        errs.append(e)
        old = (est["mrt_globe"] - est["mrt_globe_obs"]) if est.get("mrt_globe_obs") else float("nan")
        name = (r["note"] or "")[:9]
        print(f"{name:<10}{ta:>7.1f}{v:>6.1f}{float(est['ta']):>7.1f}{float(est['u_p']):>6.2f}"
              f"{tg_m:>8.1f}{tg_p:>8.1f}{e:>+8.2f}{old:>+18.2f}")

    if errs:
        import statistics as st
        print(f"\n흑구온도 공간  n={len(errs)}  bias {st.mean(errs):+.2f}  "
              f"MAE {st.mean(abs(x) for x in errs):.2f}")
        print("(양쪽이 같은 기온·같은 바람을 쓴다. 남는 것은 복사항 + 엔진 기온오차의 장파 성분.)")


if __name__ == "__main__":
    asyncio.run(main())
