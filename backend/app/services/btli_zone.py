"""
BTLI 존(면) 외부 열부하 — 실내 엔진용 (2026-09-24)

실내 체감 엔진의 '외부부하'를 VPTI 엔진 구성요소로 산출한다. BTLI 명세서
(다방향 영상·기상데이터 기반 건물 외부 열부하 지수 및 존별 선제 공조 제어)의 구성을
사용자 한 명의 '존' — 그 사람이 있는 층·창 방향의 외피 한 면 — 에 적용한 것.

  FSI  천공 노출 지수   층 높이에서의 하늘 개방도(SVF, 건물 GIS 광선투사)
                        + VPTI 공간지표(GVI·BVI) → 면이 보는 하늘·주변 반사 환경
  EMTI 외피 재질 열 지수  외피 재질(건축물대장 구조 → 재질 클래스)의 흡수율·방사율·
                        면적열용량 → 흡수 잠재력(α·I/h), 축열 잠재력(hc)
  FWI  외피 풍환경 지수   기상 풍속을 층 높이로 다운스케일(멱법칙) × 도시형태 감쇠(PWI)
                        × 풍상/풍하 → 외피 풍속 → 외표면 열전달계수 h_out
  음영·일사            태양 위치(pvlib) + 청천/운량 일사 분해(DNI·DHI·GHI)
                        + 이웃 건물 광선 판정(층 높이) → 면 입사 일사
  BTLI                 sol-air 온도 초과분 = T_sa − T_out  [K]  (면·존·시각별)
                        T_sa = T_out + α·I_face / h_out     (ASHRAE, 수직면 εΔR/h_o = 0)

근거가 있는 식
  · sol-air 온도: ASHRAE Handbook—Fundamentals (수직면 장파 보정 0)
  · h_out = 5.7 + 3.8·V (McAdams, 건물 외표면 대류+복사 합산 상관식)
  · 실내 표면열전달 h_in = 1/Rsi = 1/0.13 = 7.7 W/m²K (ISO 6946, 수평 열류=벽)
  · 멱법칙 풍속 연직분포 α = 0.30 (도시, PWI 엔진과 동일)
⚠️ 가정값 (실측으로 동정할 것 — UNCONFIRMED)
  · 수직면 천공계수 F_sky = 0.5·SVF(층 높이)  (개방 수직면 0.5 기준)
  · 풍하측 외피 풍속 배율 0.5, 풍상측 1.0
  · 축열 지연 lag_h = hc / 40 kJ/m²K (콘크리트 ≈ 6h, 목조 ≈ 1.4h)
"""
from __future__ import annotations

import asyncio
import math
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from loguru import logger

H_IN = 1.0 / 0.13          # ISO 6946 Rsi (벽, 수평 열류)
WIND_ALPHA = 0.30          # 도시 멱법칙 지수 (PWI 엔진 DEFAULT_PROFILE_EXPONENT 와 동일)
LEEWARD_FACTOR = 0.5       # ⚠️ 가정
LAG_HC_PER_H = 40000.0     # ⚠️ 가정 — hc[J/m²K] / 이 값 = 지연 시간[h]
FLOOR_H = 2.8

# 건축물대장 구조 → 재질 클래스 (wall_material.MATERIAL_PROPS 키)
_KR_STRUCT = (
    ("철골철근콘크리트", "concrete"), ("철근콘크리트", "concrete"), ("콘크리트", "concrete"),
    ("벽돌", "brick"), ("조적", "brick"), ("블록", "brick"), ("시멘트", "brick"),
    ("석", "stone"), ("목", "wood"),
    ("샌드위치", "metal"), ("판넬", "metal"), ("패널", "metal"), ("컨테이너", "metal"),
    ("경량철골", "metal"), ("철골", "concrete"),
)


def material_from_structure(structure: str | None) -> str:
    s = structure or ""
    for k, m in _KR_STRUCT:
        if k in s:
            return m
    return "concrete"


@dataclass
class ZoneContext:
    """존(사용자 층·창 방향 외피 한 면)의 시간 불변 속성 — 한 번 조회해 재사용."""
    lat: float
    lon: float
    floor: int | None
    height_m: float
    facing_deg: float | None        # 면 법선 방위 (None 이면 판상형 두 면 중 일사 큰 쪽)
    svf_h: float                    # 층 높이 SVF
    gvi: float
    bvi: float
    fsi: float                      # 수직면 천공계수 = 0.5·svf_h
    rho_env: float                  # 주변 반사율 (GVI·BVI 가중)
    material: str
    absorptance: float              # 1 − albedo
    emissivity: float
    hc: float                       # 면적열용량 [J/m²K]
    u_wall: float
    lag_h: float
    geom: object = None             # geo.BuildingGeometry (이웃 음영)
    sources: dict = field(default_factory=dict)


@dataclass
class FaceLoad:
    when: datetime
    sun_el: float
    sun_az: float
    normal_deg: float | None
    shade: float                    # 1 = 직달 도달, 0.35 = 이웃 건물 차폐
    i_face: float                   # 면 입사 일사 [W/m²]
    u_face: float                   # 외피 풍속 [m/s]
    h_out: float                    # FWI — 외표면 열전달계수 [W/m²K]
    emti_k: float                   # 흡수 잠재력 α·I/h_out [K]
    t_sa: float                     # sol-air 온도
    btli: float                     # T_sa − T_out [K]

    def as_dict(self) -> dict:
        return {
            "sun_el": round(self.sun_el, 1), "sun_az": round(self.sun_az, 1),
            "normal_deg": None if self.normal_deg is None else round(self.normal_deg),
            "shade": round(self.shade, 2), "i_face_wm2": round(self.i_face),
            "u_face_ms": round(self.u_face, 2), "fwi_h_out": round(self.h_out, 1),
            "emti_k": round(self.emti_k, 2), "t_sa": round(self.t_sa, 1),
            "btli_k": round(self.btli, 2),
        }


_ZONE_CACHE: dict[tuple, tuple[float, ZoneContext]] = {}
_ZONE_TTL = 24 * 3600


async def build_zone_context(
    *, lat: float, lon: float, floor: int | None, facing_deg: float | None,
    structure: str | None, geom=None, orchestrator=None,
) -> ZoneContext:
    key = (round(lat, 4), round(lon, 4), floor or 0, None if facing_deg is None else round(facing_deg))
    hit = _ZONE_CACHE.get(key)
    if hit and _time.time() - hit[0] < _ZONE_TTL:
        z = hit[1]
        if geom is not None:
            z.geom = geom
        return z

    from app.services.btli import _u_wall
    from app.services.wall_material import props_for

    height = max(2.0, ((floor or 1) - 1) * FLOOR_H + 1.5)
    src: dict = {}
    svf_h = None
    try:
        from app.services.geo import svf_geometric
        r = await asyncio.wait_for(svf_geometric(lat, lon, eye_height_m=height), timeout=10)
        svf_h = r.get("svf")
        src["svf"] = f"GIS 광선투사 @{height:.0f}m ({r.get('source')})"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"zone svf failed ({type(e).__name__}): {e}")
    gvi = bvi = None
    if orchestrator is not None:
        try:
            sp = await asyncio.wait_for(orchestrator.spatial_at(lat, lon), timeout=10)
            if sp:
                gvi, bvi = sp.get("gvi"), sp.get("bvi")
                src["gvi_bvi"] = "VPTI 공간지표"
                if svf_h is None and sp.get("svf") is not None:
                    svf_h = sp["svf"]
                    src["svf"] = "VPTI 공간지표(지상)"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"zone spatial failed ({type(e).__name__}): {e}")
    svf_h = 0.6 if svf_h is None else float(svf_h)
    gvi = 0.1 if gvi is None else float(gvi)
    bvi = max(0.0, 1.0 - svf_h - gvi) if bvi is None else float(bvi)
    rest = max(0.0, 1.0 - gvi - bvi)
    rho_env = 0.20 * gvi + 0.30 * bvi + 0.15 * rest

    mat = material_from_structure(structure)
    p = props_for(mat)
    hc = float(p.get("hc", 100000.0))
    z = ZoneContext(
        lat=lat, lon=lon, floor=floor, height_m=height, facing_deg=facing_deg,
        svf_h=svf_h, gvi=gvi, bvi=bvi, fsi=0.5 * svf_h, rho_env=rho_env,
        material=mat, absorptance=1.0 - float(p["albedo"]), emissivity=float(p["emissivity"]),
        hc=hc, u_wall=_u_wall(structure), lag_h=max(0.3, min(10.0, hc / LAG_HC_PER_H)),
        geom=geom, sources=src,
    )
    if len(_ZONE_CACHE) > 5000:
        _ZONE_CACHE.clear()
    _ZONE_CACHE[key] = (_time.time(), z)
    return z


def _normal_for(z: ZoneContext, sun_az: float) -> float | None:
    if z.facing_deg is not None:
        return z.facing_deg
    g = z.geom
    if g is not None and getattr(g, "is_slab", False):
        da = abs(((sun_az - g.facade_a_deg + 180) % 360) - 180)
        db = abs(((sun_az - g.facade_b_deg + 180) % 360) - 180)
        return g.facade_a_deg if da <= db else g.facade_b_deg
    return None


def face_load(
    z: ZoneContext, when: datetime, *, t_out: float, cloud: float,
    wind_ms: float | None, wind_dir_deg: float | None,
) -> FaceLoad:
    from vpti_core.solar import estimate_solar
    from app.core.pwi import urban_form_reduction

    sun = estimate_solar(z.lat, z.lon, when, cloud_fraction=max(0.0, min(1.0, cloud)))
    el, az = float(sun.solar_elevation_deg), float(sun.solar_azimuth_deg)
    normal = _normal_for(z, az)

    # 음영 — 층 높이에서 이웃 건물이 태양을 가리는가 (광선 판정)
    shade = 1.0
    if el > 0 and z.geom is not None:
        try:
            from app.services.geo import shading_factor
            shade, _n = shading_factor(az, el, z.geom, z.floor)
        except Exception:  # noqa: BLE001
            shade = 1.0

    # 면 입사 일사 = 직달·cosθ·음영 + 산란·F_sky + 반사·ρ_env·(1−F_sky)
    if el > 0:
        if normal is None:
            cos_t = 0.3 * math.cos(math.radians(el))     # 방위 모름: 사방 평균 근사
        else:
            daz = math.radians(az - normal)
            cos_t = max(0.0, math.cos(math.radians(el)) * math.cos(daz))
        i_face = (float(sun.dni) * cos_t * shade
                  + float(sun.dhi) * z.fsi
                  + float(sun.ghi) * z.rho_env * (1.0 - z.fsi))
    else:
        i_face = 0.0

    # FWI — 외피 풍속 → h_out
    u10 = max(0.0, wind_ms or 0.0)
    u_h = u10 * (max(z.height_m, 2.0) / 10.0) ** WIND_ALPHA
    try:
        u_h *= urban_form_reduction(max(0.0, min(1.0, z.svf_h)), max(0.0, min(1.0, z.bvi)))
    except Exception:  # noqa: BLE001
        pass
    if normal is not None and wind_dir_deg is not None:
        # 풍향 = 바람이 불어오는 방향. 면 법선과 90° 이내면 풍상측
        d = abs(((wind_dir_deg - normal + 180) % 360) - 180)
        u_h *= 1.0 if d <= 90 else LEEWARD_FACTOR
    h_out = 5.7 + 3.8 * u_h

    emti_k = z.absorptance * i_face / h_out
    t_sa = t_out + emti_k                     # 수직면: εΔR/h_o = 0 (ASHRAE)
    return FaceLoad(when=when, sun_el=el, sun_az=az, normal_deg=normal, shade=shade,
                    i_face=i_face, u_face=u_h, h_out=h_out, emti_k=emti_k,
                    t_sa=t_sa, btli=t_sa - t_out)


# 유리(창) — 복층유리 가정 ⚠️ UNCONFIRMED: U_g 2.8 W/m²K, 유리 자체 일사 흡수율 0.15
U_GLASS = 2.8
ALPHA_GLASS = 0.15


def glass_surface_pred(t_in: float, t_out: float, fl: FaceLoad) -> float:
    """창 유리 실내 표면온도 예측 — 열용량이 작아 지연 없음.
    T_si = T_in + (U_g/h_in)·(T_sa,g − T_in),  T_sa,g = T_out + α_g·I_face/h_out.
    ⚠️ 비확장 발코니(완충 공간)가 있으면 실제 바깥쪽 온도는 외기보다 높다 — 미반영."""
    t_sa_g = t_out + ALPHA_GLASS * fl.i_face / fl.h_out
    return t_in + (U_GLASS / H_IN) * (t_sa_g - t_in)


def interior_surface_pred(z: ZoneContext, t_in: float, t_sa_lag: float) -> float:
    """외벽 실내 표면온도 예측 (정상상태 관류 + 축열 지연된 sol-air).
    T_si = T_in + (U/h_in)·(T_sa(t−lag) − T_in)."""
    return t_in + (z.u_wall / H_IN) * (t_sa_lag - t_in)
