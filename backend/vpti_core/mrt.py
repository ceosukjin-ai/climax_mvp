"""
평균복사온도(MRT) 모듈 (②) — 추정 일사 + VSI(SVF·GVI) + SMTI(알베도·ε) → Tmrt.

표준 모델: VDI 3787 Part 2 / Höppe(1992) / Thorsson et al.(2007) 의 6방향
복사속 적분. 서 있는 사람을 둘러싼 6면(상·하·동·서·남·북)에 입사하는 단파(K)·
장파(L) 복사속을 입체각 투영계수 F_i 로 가중 합산해 평균복사속 Sstr 을 만들고,
인체 복사 평형에서 평균복사온도를 역산한다.

    Sstr = a_k·( fp·DNI + Σ_i F_i·K_i^(diff+refl) ) + ε_p·Σ_i F_i·L_i
    Tmrt = ( Sstr / (ε_p·σ) )^(1/4) − 273.15

  · a_k=0.7, ε_p=0.97, F = (측면 0.22×4, 상·하 0.06×2)  ← VDI 3787 Part 2
  · fp(β) = 0.308·cos(β(0.998−β²/50000))                ← Fanger(1970) 투영면적계수
  · K 산란/반사 : 천공 산란 DHI·천공시계(SVF), 지면 반사 albedo·GHI·(1−SVF계열)
  · L 장파      : 천공 ε_sky·σ·Ta⁴ (Brunt 청천식 + 운량보정), 지면/주변 ε·σ·Tsurf⁴

VSI·SMTI 연결(설계 요구사항):
  · SVF (VSI)      → 천공/지면 시계분배 (단파 산란·반사 + 장파 천공/지면 비율)
  · GVI (VSI)      → 식생은 표면온도가 기온에 가까워 Tsurf 를 낮춤 (음영·증발산)
  · albedo·ε (SMTI)→ 지면 반사율·방사율. 재질 점유율에서 면적가중으로 도출.

표준 상수는 전부 ✅ 공개 문헌값. ⚠️ 가정값은 지표면 승온 ΔTsurf 단순화 하나뿐
(config.MRTConfig.surface_temp_rise_max).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import DEFAULT_CONFIG, MRTConfig
from .solar import SolarResult

STEFAN_BOLTZMANN = 5.670374419e-8  # σ [W/(m²·K⁴)]
KELVIN = 273.15


@dataclass(frozen=True, slots=True)
class MRTResult:
    """평균복사온도 산출 결과 + 복사속 분해(해석·디버깅용)."""

    tmrt: float                 # 평균복사온도 [°C]
    sstr: float                 # 평균복사속 Sstr [W/m²]

    # 단파 분해 [W/m²]
    sw_direct: float            # a_k·fp·DNI (직달 흡수)
    sw_diffuse: float           # a_k·Σ F_i·(천공 산란)
    sw_reflected: float         # a_k·Σ F_i·(지면 반사)
    fp: float                   # 직달 투영면적계수

    # 장파 분해 [W/m²]
    lw_sky: float               # ε_p·Σ F_i·(천공 장파)
    lw_surface: float           # ε_p·Σ F_i·(지면/주변 장파)

    # 중간 물리량
    ground_temp_c: float        # 추정 지표면 온도 Tsurf [°C]
    sky_emissivity: float       # 천공 방사율 ε_sky
    ground_albedo: float        # 지면 알베도 (SMTI 도출)
    ground_emissivity: float    # 지면 방사율 (SMTI 도출)

    def as_dict(self) -> dict:
        return {
            "tmrt": round(self.tmrt, 2),
            "sstr": round(self.sstr, 1),
            "shortwave": {
                "direct": round(self.sw_direct, 1),
                "diffuse": round(self.sw_diffuse, 1),
                "reflected": round(self.sw_reflected, 1),
                "fp": round(self.fp, 4),
            },
            "longwave": {
                "sky": round(self.lw_sky, 1),
                "surface": round(self.lw_surface, 1),
            },
            "ground_temp_c": round(self.ground_temp_c, 2),
            "sky_emissivity": round(self.sky_emissivity, 4),
            "ground_albedo": round(self.ground_albedo, 4),
            "ground_emissivity": round(self.ground_emissivity, 4),
        }


def fanger_projected_area_factor(elevation_deg: float) -> float:
    """【Fanger 1970】 직달일사에 대한 인체 투영면적계수 fp(β).

        fp = 0.308·cos( β·(0.998 − β²/50000) )     (β: 태양 고도 [deg])

    ASHRAE 55 / VDI 3787 에서 서 있는 사람 직달 흡수에 표준 사용.
    """
    beta = max(elevation_deg, 0.0)
    return 0.308 * math.cos(math.radians(beta * (0.998 - beta * beta / 50000.0)))


def saturation_vapor_pressure_hpa(temp_c: float) -> float:
    """Tetens 식 — 포화수증기압 [hPa]."""
    return 6.1078 * 10.0 ** (7.5 * temp_c / (237.3 + temp_c))


def sky_emissivity(
    air_temp_c: float,
    humidity_pct: float,
    cloud_fraction: float,
    config: MRTConfig = DEFAULT_CONFIG.mrt,
) -> float:
    """천공 유효 방사율 ε_sky.

    【Brunt 1932】 청천:  ε_clear = c1 + c2·√e   (e: 수증기압 hPa)
    【Crawford & Duchon 1999】 운량 보정:
        ε_sky = (1 − CF)·ε_clear + CF·1.0   (구름은 흑체에 근접)
    """
    e = saturation_vapor_pressure_hpa(air_temp_c) * min(max(humidity_pct, 0.0), 100.0) / 100.0
    eps_clear = config.brunt_c1 + config.brunt_c2 * math.sqrt(max(e, 0.0))
    eps_clear = min(max(eps_clear, 0.0), 1.0)
    cf = min(max(cloud_fraction, 0.0), 1.0)
    return (1.0 - cf) * eps_clear + cf * 1.0


def estimate_ground_temp(
    air_temp_c: float,
    ghi: float,
    ground_albedo: float,
    svf: float,
    gvi: float,
    config: MRTConfig = DEFAULT_CONFIG.mrt,
) -> float:
    """⚠️ UNCONFIRMED — 일사에 의한 지표면 승온 단순화.

        ΔTsurf = ΔT_max · (1−albedo) · (GHI/GHI_ref) · SVF
        Tsurf_impervious = Ta + ΔTsurf
        Tsurf = GVI·Ta + (1−GVI)·Tsurf_impervious   ← 식생은 기온에 근접(증발산·음영)

    완전 에너지수지(대류·증발·전도) 대신 일사 비례 1차 근사.
    하늘이 막힌 곳(SVF↓)은 직사 노출이 적어 승온 작음 → SVF 비례.
    GVI 가 클수록 식생 지표라 표면온도가 기온에 가까워짐.
    """
    dt = (
        config.surface_temp_rise_max
        * (1.0 - min(max(ground_albedo, 0.0), 1.0))
        * min(max(ghi, 0.0) / config.ghi_reference, 1.5)
        * min(max(svf, 0.0), 1.0)
    )
    tsurf_impervious = air_temp_c + dt
    g = min(max(gvi, 0.0), 1.0)
    return g * air_temp_c + (1.0 - g) * tsurf_impervious


def compute_mrt(
    solar: SolarResult,
    air_temp_c: float,
    humidity_pct: float,
    svf: float,
    gvi: float,
    ground_albedo: float,
    ground_emissivity: float,
    config: MRTConfig = DEFAULT_CONFIG.mrt,
) -> MRTResult:
    """6방향 복사속 적분으로 평균복사온도 Tmrt 산출 (VDI 3787 Part 2).

    Args:
        solar: 일사 추정 결과(②의 입력, W/m²).
        air_temp_c: 기온 Ta [°C].
        humidity_pct: 상대습도 [%] (천공 방사율 계산용).
        svf: Sky View Factor [0,1] (VSI).
        gvi: Green View Index [0,1] (VSI).
        ground_albedo: 지면 알베도 [0,1] (SMTI 재질에서 도출).
        ground_emissivity: 지면 방사율 [0,1] (SMTI 재질에서 도출).
        config: MRT 설정.

    Returns:
        MRTResult.
    """
    for name, value in (("svf", svf), ("gvi", gvi),
                        ("ground_albedo", ground_albedo),
                        ("ground_emissivity", ground_emissivity)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name}={value} out of [0, 1]")

    a_k, eps_p = config.a_k, config.eps_p

    # --- 6방향 입체각 투영계수와 천공/지면 시계분배 ---
    # 천공시계 ψ_sky: 상향=SVF, 측면=0.5·SVF(수직면은 하늘 절반), 하향=0.
    # 지면시계 ψ_grd = 1 − ψ_sky (막힌 부분은 건물/지면이 채움).
    f = {"up": config.f_up, "down": config.f_down,
         "N": config.f_side, "E": config.f_side, "S": config.f_side, "W": config.f_side}
    psi_sky = {
        "up": svf, "down": 0.0,
        "N": 0.5 * svf, "E": 0.5 * svf, "S": 0.5 * svf, "W": 0.5 * svf,
    }
    psi_grd = {d: 1.0 - psi_sky[d] for d in f}

    # --- 단파 ---
    fp = fanger_projected_area_factor(solar.solar_elevation_deg)
    sw_direct = a_k * fp * solar.dni

    sw_diffuse = 0.0   # 천공 산란 (DHI) — 천공시계 비례
    sw_reflected = 0.0  # 지면 반사 (albedo·GHI) — 지면시계 비례
    for d in f:
        sw_diffuse += a_k * f[d] * (solar.dhi * psi_sky[d])
        sw_reflected += a_k * f[d] * (ground_albedo * solar.ghi * psi_grd[d])

    # --- 장파 ---
    eps_sky = sky_emissivity(air_temp_c, humidity_pct, solar.cloud_fraction, config)
    l_sky_flux = eps_sky * STEFAN_BOLTZMANN * (air_temp_c + KELVIN) ** 4

    tsurf = estimate_ground_temp(air_temp_c, solar.ghi, ground_albedo, svf, gvi, config)
    l_surf_flux = ground_emissivity * STEFAN_BOLTZMANN * (tsurf + KELVIN) ** 4

    lw_sky = 0.0
    lw_surface = 0.0
    for d in f:
        lw_sky += eps_p * f[d] * (l_sky_flux * psi_sky[d])
        lw_surface += eps_p * f[d] * (l_surf_flux * psi_grd[d])

    # --- 평균복사속 → Tmrt ---
    sstr = sw_direct + sw_diffuse + sw_reflected + lw_sky + lw_surface
    tmrt_k = (sstr / (eps_p * STEFAN_BOLTZMANN)) ** 0.25
    tmrt = tmrt_k - KELVIN

    return MRTResult(
        tmrt=tmrt,
        sstr=sstr,
        sw_direct=sw_direct,
        sw_diffuse=sw_diffuse,
        sw_reflected=sw_reflected,
        fp=fp,
        lw_sky=lw_sky,
        lw_surface=lw_surface,
        ground_temp_c=tsurf,
        sky_emissivity=eps_sky,
        ground_albedo=ground_albedo,
        ground_emissivity=ground_emissivity,
    )


def ground_properties_from_materials(materials, get_props) -> tuple[float, float]:
    """재질 점유율 → 면적가중 (알베도, 방사율). SMTI 재질 DB 연결(③ 요구사항).

    albedo  = Σ P_i·R_i,   emissivity = Σ P_i·ε_i   (Σ P_i = 1 로 정규화 후)
    """
    total = sum(m.fraction for m in materials)
    if total <= 0.0:
        raise ValueError("재질 점유율 총합이 0 이하")
    albedo = 0.0
    emis = 0.0
    for m in materials:
        p = m.fraction / total
        props = get_props(m.material)
        albedo += p * props.reflectance
        emis += p * props.emissivity
    return albedo, emis
