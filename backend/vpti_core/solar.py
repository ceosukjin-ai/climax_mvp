"""
일사 추정 모듈 (①) — 좌표·시각 → 청천일사(pvlib) → KMA 운량 감쇠.

가산형 VPTI는 SMTI 입력으로 [0,1] 정규화 일사만 받았으나, MRT 기반 재설계는
물리 단위(W/m²)의 직달(DNI)·산란(DHI)·전천(GHI) 일사가 필요하다. 본 모듈이
그 변환을 담당한다.

파이프라인
  1. pvlib 로 태양위치(고도·방위)와 청천 GHI/DNI/DHI 산출
     (NREL SPA + Ineichen-Perez clear-sky, 모두 ✅ 검증된 공개 모델).
  2. KMA SKY 코드(1/3/4)를 전운량 비율로 변환.
  3. Kasten & Czeplak (1980) 으로 청천 GHI 를 운량 감쇠:
        GHI = GHI_clear · (1 − a·CF^b),  a=0.75, b=3.4
  4. 감쇠된 GHI 를 Erbs et al. (1982) diffuse-fraction 모델로 DNI/DHI 재분리
     (구름이 끼면 직달이 산란보다 급격히 감소하는 물리를 반영).

야간(태양고도 ≤ 0)은 모든 일사 0, is_daytime=False.

모든 계수는 공개 문헌의 표준값이며 임의 튜닝값이 없다(config.SolarConfig 참조).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import pvlib

from .config import DEFAULT_CONFIG, SolarConfig


@dataclass(frozen=True, slots=True)
class SolarResult:
    """일사 추정 결과 — MRT 모듈(②)의 입력."""

    ghi: float                  # 전천일사 [W/m²]
    dni: float                  # 직달일사(법선면) [W/m²]
    dhi: float                  # 산란(천공)일사 [W/m²]
    ghi_clearsky: float         # 청천 GHI [W/m²] (감쇠 전, 참고용)
    solar_zenith_deg: float     # 태양 천정각 [deg]
    solar_elevation_deg: float  # 태양 고도각 β [deg]
    solar_azimuth_deg: float    # 태양 방위각 [deg] (0=북, 시계방향)
    cloud_fraction: float       # 전운량 비율 [0,1]
    sky_code: int | None        # 입력 KMA SKY 코드
    is_daytime: bool

    def as_dict(self) -> dict:
        return {
            "ghi": round(self.ghi, 1),
            "dni": round(self.dni, 1),
            "dhi": round(self.dhi, 1),
            "ghi_clearsky": round(self.ghi_clearsky, 1),
            "solar_elevation_deg": round(self.solar_elevation_deg, 2),
            "solar_azimuth_deg": round(self.solar_azimuth_deg, 2),
            "cloud_fraction": round(self.cloud_fraction, 3),
            "sky_code": self.sky_code,
            "is_daytime": self.is_daytime,
        }


def sky_code_to_cloud_fraction(
    sky_code: int | None, config: SolarConfig = DEFAULT_CONFIG.solar
) -> float:
    """KMA SKY 코드(1=맑음/3=구름많음/4=흐림) → 전운량 비율 [0,1]."""
    if sky_code is None:
        return config.default_cloud_fraction
    return config.sky_cloud_fraction.get(int(sky_code), config.default_cloud_fraction)


def kasten_czeplak_factor(
    cloud_fraction: float, config: SolarConfig = DEFAULT_CONFIG.solar
) -> float:
    """【Kasten & Czeplak 1980】 운량에 의한 GHI 감쇠계수 ∈ (0,1].

        G / G_clear = 1 − a·CF^b     (a=0.75, b=3.4)

    CF=0(맑음) → 1.0, CF=1(흐림) → 0.25.
    """
    cf = min(max(cloud_fraction, 0.0), 1.0)
    return 1.0 - config.kc_a * (cf ** config.kc_b)


def estimate_solar(
    lat: float,
    lon: float,
    when: datetime,
    sky_code: int | None = None,
    cloud_fraction: float | None = None,
    config: SolarConfig = DEFAULT_CONFIG.solar,
) -> SolarResult:
    """좌표·시각·운량 → 추정 일사(W/m²) + 태양위치.

    Args:
        lat, lon: 위경도 [deg].
        when: 평가 시각. tz-naive 면 config.timezone 으로 간주.
        sky_code: KMA SKY 코드(1/3/4). cloud_fraction 미지정 시 이걸로 운량 산출.
        cloud_fraction: 전운량 비율 [0,1] 직접 지정(있으면 sky_code 보다 우선).
        config: 일사 설정.

    Returns:
        SolarResult.
    """
    # 시각을 tz-aware DatetimeIndex 로 정규화
    if when.tzinfo is None:
        times = pd.DatetimeIndex([when]).tz_localize(config.timezone)
    else:
        times = pd.DatetimeIndex([when])

    location = pvlib.location.Location(
        latitude=lat, longitude=lon, tz=config.timezone, altitude=config.altitude_m
    )
    solpos = location.get_solarposition(times)
    zenith = float(solpos["apparent_zenith"].iloc[0])
    elevation = float(solpos["apparent_elevation"].iloc[0])
    azimuth = float(solpos["azimuth"].iloc[0])

    # 운량 결정 (직접 지정 > SKY 코드)
    cf = cloud_fraction if cloud_fraction is not None else sky_code_to_cloud_fraction(
        sky_code, config
    )
    cf = min(max(cf, 0.0), 1.0)

    # 야간: 태양이 지평선 아래면 일사 0
    if elevation <= 0.0:
        return SolarResult(
            ghi=0.0, dni=0.0, dhi=0.0, ghi_clearsky=0.0,
            solar_zenith_deg=zenith, solar_elevation_deg=elevation,
            solar_azimuth_deg=azimuth, cloud_fraction=cf,
            sky_code=sky_code, is_daytime=False,
        )

    # 청천일사 (pvlib) → 운량 감쇠 → Erbs 재분리
    clearsky = location.get_clearsky(times, model=config.clearsky_model)
    ghi_cs = float(clearsky["ghi"].iloc[0])
    ghi = ghi_cs * kasten_czeplak_factor(cf, config)

    erbs = pvlib.irradiance.erbs(
        pd.Series([ghi], index=times), solpos["apparent_zenith"], times
    )
    dni = float(erbs["dni"].iloc[0])
    dhi = float(erbs["dhi"].iloc[0])

    return SolarResult(
        ghi=ghi, dni=dni, dhi=dhi, ghi_clearsky=ghi_cs,
        solar_zenith_deg=zenith, solar_elevation_deg=elevation,
        solar_azimuth_deg=azimuth, cloud_fraction=cf,
        sky_code=sky_code, is_daytime=True,
    )


def cloud_fraction_from_obs_ghi(
    lat: float,
    lon: float,
    hour_end: datetime,
    obs_ghi_wm2: float,
    config: SolarConfig = DEFAULT_CONFIG.solar,
) -> float | None:
    """실측 시간평균 GHI → 유효 전운량 역산 (Kasten & Czeplak 역함수).

    ASOS 일사 SI(1시간 누적 MJ/m²)를 시간평균 W/m²로 바꾼 값과, 같은 관측소
    좌표·같은 1시간 구간의 청천 GHI 평균의 비(kc)를 구해 운량으로 역산한다:

        kc = GHI_obs / GHI_clear  →  CF = ((1 − kc) / a)^(1/b)   (a=0.75, b=3.4)

    이 CF 를 estimate_solar(cloud_fraction=...)에 넣으면 그 시각의 **실측 감쇠**가
    엔진 전체(단파·장파 ε_sky)에 일관되게 반영된다.

    Returns:
        CF ∈ [0,1]. 역산이 불안정한 조건(저태양고도·야간: 청천 평균 < 100 W/m²,
        또는 관측 결측)이면 None → 호출부는 전운량(CA_TOT)으로 폴백할 것.
    """
    if obs_ghi_wm2 is None or obs_ghi_wm2 < 0.0:
        return None

    if hour_end.tzinfo is None:
        end = pd.Timestamp(hour_end).tz_localize(config.timezone)
    else:
        end = pd.Timestamp(hour_end)

    # 1시간 누적 구간을 10분 간격 7점으로 샘플해 청천 GHI 평균 산출
    times = pd.date_range(end - pd.Timedelta(hours=1), end, freq="10min")
    location = pvlib.location.Location(
        latitude=lat, longitude=lon, tz=config.timezone, altitude=config.altitude_m
    )
    ghi_clear_mean = float(
        location.get_clearsky(times, model=config.clearsky_model)["ghi"].mean()
    )
    if ghi_clear_mean < 100.0:   # 일출·일몰 부근/야간 — 역산 불안정
        return None

    kc = min(max(obs_ghi_wm2 / ghi_clear_mean, 0.0), 1.0)
    if kc >= 1.0:
        return 0.0
    cf = ((1.0 - kc) / config.kc_a) ** (1.0 / config.kc_b)
    return min(max(cf, 0.0), 1.0)
