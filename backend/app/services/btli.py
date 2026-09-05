"""
BTLI — 건물 외부(외피) 열부하 · 냉방부하 What-if (2026-09-05, 도시계획/신축 검토용)

"이 건물 외피를 차열도료/저방사유리/그린월로 바꾸면 냉방부하가 몇 % 주나?"

원리(1차 물리 모델 — 정밀 BES 아님, 상대비교용):
  면별 입사일사 I_face = DNI·cos(입사각) + DHI·F_sky_vert + GHI·R_ground·F_grd
  외피 흡수    Q_abs  = (1 − R_wall)·I_face·A_face          (R_wall = materials DB 반사율)
  실내 유입    Q_in   = Q_abs·f_cond(구조)                  (구조 U값 근사)
  냉방부하 Δ% = (Q_in_base − Q_in_new) / Q_cool_total × 100

⚠️ 계수(f_cond, F_sky_vert, R_ground, 재질 프리셋 반사율, Q_cool 비외피 상수)는
   영업비밀/UNCONFIRMED — 문헌 보수값. 실측(자작 벽면센서 MLX90614)으로 교정 예정.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from vpti_core.materials import get_properties
from vpti_core.solar import estimate_solar

KST = timezone(timedelta(hours=9))

# 4방위 법선(0=북, 시계방향). 판상형은 대개 남/북 또는 동/서지만
# 일반화를 위해 4면을 모두 계산하고 면적 배분으로 가중한다.
ORIENTS = {"북": 0.0, "동": 90.0, "남": 180.0, "서": 270.0}

# ⚠️ UNCONFIRMED — 구조별 외피 관류 근사(흡수열 중 실내로 드는 비율).
#   철근콘크리트(단열/축열 큼)일수록 작다. indoor._damping 방향과 정합.
F_COND: dict[str, float] = {
    "철근콘크리트": 0.06, "철골철근콘크리트": 0.06, "철골콘크리트": 0.07,
    "콘크리트": 0.08, "벽돌": 0.11, "조적": 0.13, "블록": 0.13,
    "목": 0.10, "시멘트": 0.12, "기타": 0.10, "unknown": 0.10,
}

# ⚠️ UNCONFIRMED — 수직면 산란 천공시계 ≈ 0.5, 지면반사 시계 ≈ 0.5, 지면 알베도 ≈ 0.2
F_SKY_VERT = 0.5
F_GRD_VERT = 0.5
R_GROUND = 0.2

# 외피 재질 프리셋 — reflectance(R)로 표현. 그린월은 차양+증발산 추가항.
#   일반콘크리트 R0.30(baseline), 차열도료 R0.85(cool wall), 저방사유리 SHGC↓를 유효 R로,
#   그린월 vegetation R0.20 + 잠열냉각(evap) 계수.
@dataclass(frozen=True)
class FacadeMaterial:
    key: str
    name: str
    reflectance: float
    evap_cool: float = 0.0   # 그린월 증발산 냉각(흡수열 추가 상쇄 비율) — UNCONFIRMED
    note: str = ""


FACADE_PRESETS: dict[str, FacadeMaterial] = {
    "concrete": FacadeMaterial("concrete", "일반 콘크리트/도장", 0.30, note="기준"),
    "coolpaint": FacadeMaterial("coolpaint", "차열도료(쿨월)", 0.85,
                                note="고반사 도료, R0.30→0.85"),
    "lowe": FacadeMaterial("lowe", "저방사(Low-E) 유리", 0.55,
                           note="SHGC↓를 유효 반사율로 근사"),
    "greenwall": FacadeMaterial("greenwall", "그린월(벽면녹화)", 0.20, evap_cool=0.35,
                                note="차양+증발산 냉각. 흡수열의 35% 추가 상쇄(UNCONFIRMED)"),
}


def _incidence_cos(sun_az: float, sun_el: float, wall_normal_deg: float) -> float:
    """수직 외피면에 대한 직달 입사각 cos. 음수(뒷면)면 0."""
    if sun_el <= 0:
        return 0.0
    daz = math.radians(sun_az - wall_normal_deg)
    c = math.cos(math.radians(sun_el)) * math.cos(daz)
    return max(0.0, c)


def _face_irradiance(sun, wall_normal_deg: float) -> float:
    """수직면 입사일사 W/m² = 직달 + 산란 + 지면반사."""
    direct = sun.dni * _incidence_cos(sun.solar_azimuth_deg, sun.solar_elevation_deg, wall_normal_deg)
    diffuse = sun.dhi * F_SKY_VERT
    ground = sun.ghi * R_GROUND * F_GRD_VERT
    return direct + diffuse + ground


def _facade_areas(footprint_area_m2: float, floors: int,
                  floor_height_m: float = 2.8,
                  is_slab: bool = True, slab_long_deg: float = 180.0) -> dict[str, float]:
    """건물 대략 치수 → 4방위 외피면적 [m²].
    footprint을 정사각/직사각 근사. 판상형이면 장축(2면)에 면적 집중.
    """
    height = max(1, floors) * floor_height_m
    side = math.sqrt(max(footprint_area_m2, 1.0))
    if is_slab:
        long_side, short_side = side * 1.6, side / 1.6   # 장단축비 1.6 근사
    else:
        long_side = short_side = side
    # 장축 외피(2면) 법선 = slab_long_deg ± 90 → 그 법선 방위에 장축길이×높이
    # 단순화: 남/북에 장축, 동/서에 단축 (slab_long_deg 남향 가정 시). 회전은 배분만 근사.
    A_long = long_side * height
    A_short = short_side * height
    return {"남": A_long, "북": A_long, "동": A_short, "서": A_short}


def facade_load(
    *,
    lat: float, lon: float,
    footprint_area_m2: float,
    floors: int,
    structure: str | None = None,
    material_base: str = "concrete",
    material_new: str = "coolpaint",
    hours: tuple[int, ...] = (10, 12, 14, 16, 18),  # 여름 주간 대표 시각(KST)
    month: int = 8,
    floor_height_m: float = 2.8,
    is_slab: bool = True,
    q_cool_nonfacade_ratio: float = 0.55,  # ⚠️UNCONFIRMED 총냉방부하 중 비-외피(관류·환기·내부발열) 비중
) -> dict:
    """외피 재질 baseline vs new → 시간대 합산 외피취득 열부하와 냉방부하 Δ%."""
    fcond = F_COND.get((structure or "unknown"), F_COND["unknown"])
    areas = _facade_areas(footprint_area_m2, floors, floor_height_m, is_slab)
    base = FACADE_PRESETS[material_base]
    new = FACADE_PRESETS[material_new]

    def q_in_at(hour: int, mat: FacadeMaterial) -> float:
        when = datetime(datetime.now(KST).year, month, 1, hour, 0, tzinfo=KST)
        sun = estimate_solar(lat, lon, when, cloud_fraction=0.0)  # 설계 폭염일=맑음
        if not sun.is_daytime:
            return 0.0
        total = 0.0
        for name, normal in ORIENTS.items():
            I = _face_irradiance(sun, normal)
            A = areas.get(name, 0.0)
            q_abs = (1.0 - mat.reflectance) * I * A
            q_abs *= (1.0 - mat.evap_cool)       # 그린월 증발산 상쇄
            total += q_abs * fcond               # 실내 유입
        return total  # W

    by_hour = []
    q_base_sum = q_new_sum = 0.0
    for h in hours:
        qb = q_in_at(h, base); qn = q_in_at(h, new)
        q_base_sum += qb; q_new_sum += qn
        by_hour.append({"hour": h, "q_in_base_w": round(qb), "q_in_new_w": round(qn),
                        "delta_w": round(qb - qn),
                        "pct": round((qb - qn) / qb * 100, 1) if qb else None})

    # 냉방부하 Δ% = 외피취득 감소 / 총냉방부하. 총 = 외피취득_base / (1 − 비외피비중)
    q_total_base = q_base_sum / max(1e-6, (1.0 - q_cool_nonfacade_ratio))
    cooling_reduction_pct = round((q_base_sum - q_new_sum) / q_total_base * 100, 1) if q_total_base else None

    return {
        "ok": True, "lat": lat, "lon": lon,
        "building": {"footprint_m2": footprint_area_m2, "floors": floors,
                     "height_m": round(max(1, floors) * floor_height_m, 1),
                     "structure": structure or "미상", "f_cond": fcond,
                     "facade_area_m2": {k: round(v) for k, v in areas.items()}},
        "material_base": {"key": base.key, "name": base.name, "R": base.reflectance},
        "material_new": {"key": new.key, "name": new.name, "R": new.reflectance,
                         "evap_cool": new.evap_cool, "note": new.note},
        "by_hour": by_hour,
        "envelope_gain_base_wsum": round(q_base_sum),
        "envelope_gain_new_wsum": round(q_new_sum),
        "cooling_load_reduction_pct": cooling_reduction_pct,
        "assumptions": {
            "q_cool_nonfacade_ratio": q_cool_nonfacade_ratio,
            "F_sky_vert": F_SKY_VERT, "R_ground": R_GROUND,
            "note": "1차 물리 근사. 상대비교(재질 A vs B) 신뢰, 절대 kWh는 참고. 계수 UNCONFIRMED.",
        },
    }
