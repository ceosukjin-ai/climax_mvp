"""
BTLI — 건물 외부(외피) 열부하 · 냉방부하 What-if (2026-09-05, 도시계획/신축 검토용)
v2 (2026-09-05): 냉방부하 감소율을 **건물 기하·방위·구조**에 따라 달라지게 물리화.

"이 건물 외피를 차열도료/저방사외피/그린월로 바꾸면 냉방부하가 몇 % 주나?"

── 왜 v2인가 ──────────────────────────────────────────────
v1은 감소율 = (재질 반사율비) × 고정 외피비중(0.45) 이라, 건물 크기·층수·방위·구조가
전혀 안 들어가 **모든 건물이 같은 %** 로 나왔다(차열도료면 무조건 −35%). 또 불투명 벽이
햇빛을 실내로 거의 안 통과시키는 물리를 무시해 과대평가됐다.

v2는 총냉방부하를 실제 항으로 나눈다:
  Q_total = 외피 태양취득(sol-air) + 외피 관류 + 내부발열 + 환기
  · 외피 태양취득(실내 유입) = Σ (1−R)(1−evap)·I_face·A_face · (U·u_mult / h_out)
      → 불투명 벽은 U/h_out 만큼만 실내로 (작다). 재질 R·그린월 증발산이 이 항을 줄인다.
  · 외피 관류        = U·u_mult · A_facade · ΔT       (저방사외피는 u_mult<1 로 이 항도 감소)
  · 내부발열         = q_int · A_floor                (연면적 = 대지×층수)
  · 환기             = q_vent · A_floor
  냉방부하 Δ% = (Q_env_base − Q_env_new) / Q_total_base × 100
  → 외피/연면적 비율(층수·모양)·방위(태양)·구조(U)에 따라 건물마다 달라진다.

⚠️ 계수(U_wall, q_int, q_vent, ΔT, h_out, 재질 R·evap·u_mult)는 전부 UNCONFIRMED 문헌
   보수값 — 자작 벽면센서(MLX90614) 실측으로 교정 예정. 상대비교용, 절대 kWh는 참고.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from vpti_core.solar import estimate_solar

KST = timezone(timedelta(hours=9))
ORIENTS = {"북": 0.0, "동": 90.0, "남": 180.0, "서": 270.0}

# ── 외피 열관류율 U [W/m²K] — 구조별 근사(단열 포함). 부분매칭으로 조회. ⚠️UNCONFIRMED
U_WALL_TABLE: tuple[tuple[str, float], ...] = (
    ("철골철근콘크리트", 0.45), ("철근콘크리트", 0.45), ("철골콘크리트", 0.50),
    ("철골", 0.55), ("콘크리트", 0.70), ("벽돌", 1.20), ("조적", 1.50),
    ("블록", 1.40), ("석", 1.30), ("목", 0.55), ("시멘트", 1.00),
)
U_WALL_DEFAULT = 0.80

def _u_wall(structure: str | None) -> float:
    s = structure or ""
    for key, u in U_WALL_TABLE:
        if key in s:
            return u
    return U_WALL_DEFAULT

# ── 총부하 항 계수 ⚠️UNCONFIRMED (문헌 보수값)
H_OUT = 20.0        # 외표면 열전달계수 [W/m²K] — sol-air 실내유입 비율 = U/h_out
DT_COND = 6.0       # 설계 냉방 실내외 온도차 [K] (관류)
Q_INT_WM2 = 12.0    # 내부발열 [W/m² 연면적] (주거 근사)
Q_VENT_WM2 = 8.0    # 환기·침기 현열 [W/m² 연면적]

F_SKY_VERT = 0.5    # 수직면 산란 천공시계
F_GRD_VERT = 0.5    # 수직면 지면반사 시계
R_GROUND = 0.2      # 지면 알베도


@dataclass(frozen=True)
class FacadeMaterial:
    key: str
    name: str
    reflectance: float
    evap_cool: float = 0.0   # 그린월 증발산 냉각(태양취득 추가 상쇄 비율)
    u_mult: float = 1.0      # 외피 U 배율(저방사·단열 개선이면 <1)
    note: str = ""


FACADE_PRESETS: dict[str, FacadeMaterial] = {
    "concrete": FacadeMaterial("concrete", "일반 콘크리트/도장", 0.30, note="기준"),
    "coolpaint": FacadeMaterial("coolpaint", "차열도료(쿨월)", 0.85,
                                note="고반사 도료 R0.30→0.85 (불투명벽 태양취득만 감소)"),
    "lowe": FacadeMaterial("lowe", "저방사·단열 외피", 0.55, u_mult=0.6,
                           note="저방사+단열보강 — 태양취득·관류 동시 감소"),
    "greenwall": FacadeMaterial("greenwall", "그린월(벽면녹화)", 0.20, evap_cool=0.5, u_mult=0.85,
                                note="차양+증발산 냉각, 약간의 단열"),
}


def _incidence_cos(sun_az: float, sun_el: float, wall_normal_deg: float) -> float:
    if sun_el <= 0:
        return 0.0
    daz = math.radians(sun_az - wall_normal_deg)
    return max(0.0, math.cos(math.radians(sun_el)) * math.cos(daz))


def _face_irradiance(sun, wall_normal_deg: float) -> float:
    direct = sun.dni * _incidence_cos(sun.solar_azimuth_deg, sun.solar_elevation_deg, wall_normal_deg)
    diffuse = sun.dhi * F_SKY_VERT
    ground = sun.ghi * R_GROUND * F_GRD_VERT
    return direct + diffuse + ground


def _facade_areas(footprint_area_m2: float, floors: int, floor_height_m: float = 2.8,
                  is_slab: bool = True) -> dict[str, float]:
    height = max(1, floors) * floor_height_m
    side = math.sqrt(max(footprint_area_m2, 1.0))
    long_side, short_side = (side * 1.6, side / 1.6) if is_slab else (side, side)
    return {"남": long_side * height, "북": long_side * height,
            "동": short_side * height, "서": short_side * height}


def facade_load(
    *,
    lat: float, lon: float,
    footprint_area_m2: float,
    floors: int,
    structure: str | None = None,
    material_base: str = "concrete",
    material_new: str = "coolpaint",
    hours: tuple[int, ...] = (10, 12, 14, 16, 18),
    month: int = 8,
    floor_height_m: float = 2.8,
    is_slab: bool = True,
) -> dict:
    u_wall = _u_wall(structure)
    areas = _facade_areas(footprint_area_m2, floors, floor_height_m, is_slab)
    a_facade = sum(areas.values())
    a_roof = footprint_area_m2                      # 지붕(수평) — 저층일수록 외피 비중 큼
    a_floor = footprint_area_m2 * max(1, floors)
    base = FACADE_PRESETS[material_base]
    new = FACADE_PRESETS[material_new]

    # 건물·연면적에만 의존하는 상시 부하 (재질 무관)
    q_internal = Q_INT_WM2 * a_floor
    q_vent = Q_VENT_WM2 * a_floor

    def envelope(mat: FacadeMaterial, hour: int) -> tuple[float, float]:
        """(태양취득 실내유입 W, 관류 W) — 관류는 시각 무관이라 대표로 한 번만 쓴다."""
        when = datetime(datetime.now(KST).year, month, 1, hour, 0, tzinfo=KST)
        sun = estimate_solar(lat, lon, when, cloud_fraction=0.0)
        u = u_wall * mat.u_mult
        solar_in = 0.0
        if sun.is_daytime:
            for name, normal in ORIENTS.items():
                I = _face_irradiance(sun, normal)
                solar_in += (1.0 - mat.reflectance) * (1.0 - mat.evap_cool) * I * areas[name]
            solar_in += (1.0 - mat.reflectance) * (1.0 - mat.evap_cool) * sun.ghi * a_roof
            solar_in *= (u / H_OUT)          # 불투명 외피: 흡수열 중 실내로 드는 비율
        cond = u * (a_facade + a_roof) * DT_COND
        return solar_in, cond

    by_hour = []
    qenv_base_sum = qenv_new_sum = 0.0
    for h in hours:
        sb, cb = envelope(base, h); sn, cn = envelope(new, h)
        qb = sb + cb; qn = sn + cn
        qenv_base_sum += qb; qenv_new_sum += qn
        q_total_h = qb + q_internal + q_vent
        by_hour.append({"hour": h, "q_env_base_w": round(qb), "q_env_new_w": round(qn),
                        "delta_w": round(qb - qn),
                        "pct": round((qb - qn) / q_total_h * 100, 1) if q_total_h else None})

    n = len(hours)
    q_env_base = qenv_base_sum / n
    q_env_new = qenv_new_sum / n
    q_total_base = q_env_base + q_internal + q_vent
    cooling_reduction_pct = round((q_env_base - q_env_new) / q_total_base * 100, 1) if q_total_base else None
    envelope_share_pct = round(q_env_base / q_total_base * 100, 1) if q_total_base else None

    return {
        "ok": True, "lat": lat, "lon": lon,
        "building": {"footprint_m2": footprint_area_m2, "floors": floors,
                     "height_m": round(max(1, floors) * floor_height_m, 1),
                     "structure": structure or "미상", "u_wall": u_wall,
                     "floor_area_m2": round(a_floor), "facade_area_m2": {k: round(v) for k, v in areas.items()},
                     "facade_total_m2": round(a_facade), "roof_m2": round(a_roof),
                     "envelope_share_pct": envelope_share_pct},
        "material_base": {"key": base.key, "name": base.name, "R": base.reflectance},
        "material_new": {"key": new.key, "name": new.name, "R": new.reflectance,
                         "evap_cool": new.evap_cool, "u_mult": new.u_mult, "note": new.note},
        "by_hour": by_hour,
        "load_base_w": round(q_total_base), "envelope_base_w": round(q_env_base),
        "internal_w": round(q_internal), "vent_w": round(q_vent),
        "cooling_load_reduction_pct": cooling_reduction_pct,
        "assumptions": {
            "H_OUT": H_OUT, "DT_COND": DT_COND, "Q_INT_WM2": Q_INT_WM2, "Q_VENT_WM2": Q_VENT_WM2,
            "note": ("v2 물리모델: 총부하=외피(태양+관류)+내부발열+환기. 외피/연면적 비율·방위·구조로 "
                     "건물마다 달라짐. 불투명벽은 태양취득이 작아 차열도료 효과는 지붕·유리보다 작다. "
                     "상대비교 신뢰·절대값 참고, 계수 UNCONFIRMED."),
        },
    }
