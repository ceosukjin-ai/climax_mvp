"""
핫스팟 개선 시뮬레이션 — "이 격자에 가로수·그늘막·차열포장을 넣으면 체감온도가 얼마나 내려가나"
(2026-09-05, 대표 전용 대시보드용)

원리: 격자에 쌓인 측정의 공간지표 평균(SVF·GVI·BVI)을 기준선으로 두고,
개입별로 지표를 바꿔 **실제 서비스 엔진(vpti_core.compute_vpti_thermal)** 을 다시 돌린다.
따라서 결과는 앱이 쓰는 물리(일사→MRT→PET)와 같은 식에서 나온 값이다.

비교 조건은 격자마다 다르면 안 되므로 **설계 폭염일**(맑음, 14시, 기온·습도·풍속 고정)로 통일하고,
참고로 그 격자의 실측 평균 조건에서도 한 번 더 계산한다.

개입 가정값(문헌·현장 관측 범위의 보수적 중간값 — 화면에 그대로 표기):
  · 가로수 식재  : GVI +0.20, SVF −0.15, 직달 50% 차단  (성목 기준, 100m 구간 10주)
  · 그늘막      : SVF −0.35, 직달 100% 차단 (그늘막 아래 지점)
  · 차열포장    : 지면 재질을 아스팔트→콘크리트급 반사율(0.30)로 (100m×4m)
  · 복합        : 가로수 + 차열포장

비용 단가는 프론트에서 수정 가능. 서버는 물리만 책임진다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from loguru import logger
from sqlalchemy import text

from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.vsi import ViewSegmentation

KST = timezone(timedelta(hours=9))

# 설계 폭염일 — 부산 8월 맑은 날 14시 전형값
DESIGN = {"temperature_c": 33.0, "humidity_pct": 55.0, "wind_speed_ms": 1.5,
          "wind_direction_deg": 180.0}


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    d_gvi: float = 0.0
    d_svf: float = 0.0
    direct_shade: float = 1.0
    materials: tuple[tuple[str, float], ...] | None = None
    note: str = ""


BASE_MATERIALS: tuple[tuple[str, float], ...] = (("asphalt", 0.6), ("concrete", 0.4))
COOL_MATERIALS: tuple[tuple[str, float], ...] = (("concrete", 1.0),)   # 반사율 0.30 (차열포장 상당)

SCENARIOS: list[Scenario] = [
    Scenario("trees", "가로수 식재", d_gvi=+0.20, d_svf=-0.15, direct_shade=0.5,
             note="성목 가로수, 100m 구간 10주. 직달일사 절반 차단·수관 증발산"),
    Scenario("shade", "그늘막 설치", d_svf=-0.35, direct_shade=0.0,
             note="그늘막 바로 아래 지점. 횡단보도·정류장 대기 지점용"),
    Scenario("coolpave", "차열포장", materials=COOL_MATERIALS,
             note="노면 반사율 0.05→0.30. 100m×4m=400㎡ 기준"),
    Scenario("combo", "가로수 + 차열포장", d_gvi=+0.20, d_svf=-0.15, direct_shade=0.5,
             materials=COOL_MATERIALS, note="두 개입 동시 적용"),
]


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _views(svf: float, gvi: float, bvi: float) -> list[ViewSegmentation]:
    """orchestrator._build_core_views 와 같은 역산 (up=SVF, 수평=GVI/BVI)."""
    gvi = _clip(gvi); bvi = _clip(bvi, 0.0, 1.0 - gvi)
    up = ViewSegmentation(direction="up", sky_ratio=_clip(svf), vegetation_ratio=0.0, building_ratio=0.0)
    hs = [ViewSegmentation(direction=d, sky_ratio=0.0, vegetation_ratio=gvi, building_ratio=bvi)
          for d in ("front", "back", "left", "right")]
    return [up] + hs


def _run(svf: float, gvi: float, bvi: float, mats, weather: WeatherContext,
         lat: float, lon: float, when: datetime, direct_shade: float) -> dict:
    r = compute_vpti_thermal(
        views_5=_views(svf, gvi, bvi),
        materials=[MaterialFraction(material=m, fraction=f) for m, f in mats],
        weather=weather, road_axis_deg=0.0, lat=lat, lon=lon, when=when,
        cloud_fraction=0.0, direct_shade=direct_shade,
    )
    return {"pvpti": round(float(r.vpti), 1), "mrt": round(float(r.mrt.tmrt), 1),
            "risk": str(r.risk_level), "svf": round(svf, 2), "gvi": round(gvi, 2)}


async def cell_whatif(archive, lat: float, lon: float, hours: int = 24 * 30) -> dict:
    """격자 하나의 기준선 + 개입 시나리오. archive 는 Archive 인스턴스."""
    if archive is None or not archive._ready:
        return {"ok": False, "reason": "archive 미준비"}
    lat = round(lat, 4); lon = round(lon, 4)
    sql = """
    SELECT COUNT(*) n, AVG(svf) svf, AVG(gvi) gvi, AVG(bvi) bvi,
           AVG(air_temp) ta, AVG(humidity) rh, AVG(wind_ms) wind,
           AVG(pvpti) avg_pvpti, MAX(pvpti) max_pvpti, AVG(mrt) mrt
    FROM measurement
    WHERE lat = :lat AND lon = :lon AND indoor = FALSE AND pvpti IS NOT NULL
      AND observed_at > NOW() - make_interval(hours => :hours)
    """
    try:
        async with archive._session() as s:
            row = (await s.execute(text(sql), {"lat": lat, "lon": lon, "hours": hours})).mappings().first()
    except Exception as e:  # noqa: BLE001
        logger.warning("[whatif] 조회 실패: {}: {}", type(e).__name__, e)
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    if not row or not row["n"] or row["svf"] is None:
        return {"ok": False, "reason": "이 격자에 공간지표가 있는 측정이 없음"}

    svf, gvi, bvi = float(row["svf"]), float(row["gvi"] or 0), float(row["bvi"] or 0)
    measured = {"n": int(row["n"]), "svf": round(svf, 2), "gvi": round(gvi, 2), "bvi": round(bvi, 2),
                "air_temp": _r(row["ta"]), "humidity": _r(row["rh"]), "wind_ms": _r(row["wind"]),
                "avg_pvpti": _r(row["avg_pvpti"]), "max_pvpti": _r(row["max_pvpti"]), "mrt": _r(row["mrt"])}

    # 설계 폭염일: 올해 8월 1일 14:00 KST (태양고도 계산용 — 연도는 결과에 거의 영향 없음)
    when = datetime(datetime.now(KST).year, 8, 1, 14, 0, tzinfo=KST)
    w_design = WeatherContext(**DESIGN)
    w_meas = WeatherContext(temperature_c=float(row["ta"] or DESIGN["temperature_c"]),
                            wind_speed_ms=float(row["wind"] or DESIGN["wind_speed_ms"]),
                            wind_direction_deg=180.0,
                            humidity_pct=float(row["rh"] or DESIGN["humidity_pct"]))

    def block(weather: WeatherContext) -> dict:
        base = _run(svf, gvi, bvi, BASE_MATERIALS, weather, lat, lon, when, 1.0)
        out = []
        for sc in SCENARIOS:
            r = _run(_clip(svf + sc.d_svf, 0.05), _clip(gvi + sc.d_gvi), bvi,
                     sc.materials or BASE_MATERIALS, weather, lat, lon, when, sc.direct_shade)
            d = round(r["pvpti"] - base["pvpti"], 1)
            out.append({"key": sc.key, "name": sc.name, "note": sc.note, **r,
                        "delta": d, "pct": round(d / base["pvpti"] * 100, 1) if base["pvpti"] else None})
        return {"base": base, "scenarios": out}

    try:
        return {"ok": True, "lat": lat, "lon": lon, "hours": hours, "measured": measured,
                "design_day": {**DESIGN, "when": when.isoformat(), "sky": "맑음"},
                "design": block(w_design), "at_measured": block(w_meas),
                "assumptions": [sc.note for sc in SCENARIOS]}
    except Exception as e:  # noqa: BLE001
        logger.warning("[whatif] 엔진 실패: {}: {}", type(e).__name__, e)
        return {"ok": False, "reason": f"엔진 오류 {type(e).__name__}: {e}", "measured": measured}


def _r(v, nd: int = 1):
    return None if v is None else round(float(v), nd)
