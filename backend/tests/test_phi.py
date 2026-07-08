"""
PHI (생리 개인화, pVPTI) 엔진 테스트.

검증 대상:
  · 대사율 환산(activity → met)과 DuBois 체표면적.
  · 성별 hr_max 콜드스타트(여성 Gulati / 남성·기본 Tanaka) + 관측 최댓값 보정.
  · 관측 HRR, 활동량 기대 HRR(%HRR≈%VO₂R), 잔차.
  · 핵심 불변성: 선선한 날 빠른 걸음은 허위경보 없음(잔차≈0),
    activity 없으면 strain=0(환경 PET 만 반영),
    같은 활동에서 심박이 초과하면 위험경계 앞당김.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from vpti_core.config import DEFAULT_CONFIG
from vpti_core.phi import (
    Biometrics,
    PhysiologyProfile,
    body_surface_area,
    compute_pvpti,
    estimate_hr_max,
    evaluate_personalized,
    expected_hrr_from_met,
    heart_rate_reserve,
    metabolic_rate_from_activity,
    residual_strain,
)
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.vsi import ViewSegmentation

PHI = DEFAULT_CONFIG.phi


def _views() -> list[ViewSegmentation]:
    return [
        ViewSegmentation("up", sky_ratio=0.45, vegetation_ratio=0.05, building_ratio=0.50),
        ViewSegmentation("front", sky_ratio=0.15, vegetation_ratio=0.12, building_ratio=0.65),
        ViewSegmentation("back", sky_ratio=0.18, vegetation_ratio=0.10, building_ratio=0.62),
        ViewSegmentation("left", sky_ratio=0.10, vegetation_ratio=0.18, building_ratio=0.68),
        ViewSegmentation("right", sky_ratio=0.12, vegetation_ratio=0.15, building_ratio=0.70),
    ]


@pytest.fixture
def thermal_kwargs() -> dict:
    """부산 여름 한낮 — compute_vpti_thermal 입력 (demo._busan_summer_noon 기반)."""
    from vpti_core.smti import MaterialFraction

    return {
        "views_5": _views(),
        "materials": [
            MaterialFraction("asphalt", 0.55),
            MaterialFraction("concrete", 0.30),
            MaterialFraction("vegetation", 0.15),
        ],
        "weather": WeatherContext(
            temperature_c=31.0, wind_speed_ms=2.5,
            wind_direction_deg=200.0, humidity_pct=65.0,
        ),
        "road_axis_deg": 30.0,
        "lat": 35.18901,
        "lon": 129.10069,
        "when": datetime(2026, 7, 15, 14, 0),
        "sky_code": 1,
        "heading_deg": 30.0,
    }


@pytest.fixture
def base(thermal_kwargs):
    return compute_vpti_thermal(**thermal_kwargs)


# =============================================================================
# ① 대사율 · 체표면적
# =============================================================================
class TestMetabolic:
    def test_body_surface_area_dubois(self) -> None:
        # 170 cm, 65 kg → DuBois ≈ 1.76 m²
        a = body_surface_area(170.0, 65.0, PHI)
        assert 1.7 < a < 1.82

    def test_body_surface_area_fallback(self) -> None:
        assert body_surface_area(None, 65.0, PHI) == PHI.default_body_surface_area
        assert body_surface_area(170.0, None, PHI) == PHI.default_body_surface_area

    def test_metabolic_rate_units(self) -> None:
        # 안정 대사 ≈ 58 W/m² ≈ 1 met. A=1.8 m² 에서 안정 소비율:
        #   58.15 W/m² × 1.8 m² = 104.67 W = 104.67/69.78 ≈ 1.5 kcal/min
        m_wm2, met = metabolic_rate_from_activity(1.5, 1.8, PHI)
        assert met == pytest.approx(1.0, abs=0.05)
        assert m_wm2 == pytest.approx(58.15, abs=3.0)

    def test_metabolic_rate_monotonic(self) -> None:
        _, low = metabolic_rate_from_activity(2.0, 1.8, PHI)
        _, high = metabolic_rate_from_activity(6.0, 1.8, PHI)
        assert high > low


# =============================================================================
# ② hr_max 성별 분기
# =============================================================================
class TestHRMax:
    def test_male_tanaka(self) -> None:
        # 208 − 0.7×40 = 180
        assert estimate_hr_max(40, "male", None, PHI) == pytest.approx(180.0)

    def test_default_uses_tanaka(self) -> None:
        # 성별 미상 → Tanaka
        assert estimate_hr_max(40, None, None, PHI) == pytest.approx(180.0)

    def test_female_gulati(self) -> None:
        # 206 − 0.88×40 = 170.8  (Tanaka 180 과 다름 → 분기 확인)
        hr = estimate_hr_max(40, "female", None, PHI)
        assert hr == pytest.approx(170.8)
        assert hr < estimate_hr_max(40, "male", None, PHI)

    def test_observed_max_wins(self) -> None:
        # 관측 최댓값이 연령식보다 크면 그것을 사용
        assert estimate_hr_max(40, "male", 195.0, PHI) == pytest.approx(195.0)

    def test_no_age_no_observed(self) -> None:
        assert estimate_hr_max(None, "male", None, PHI) is None


# =============================================================================
# ② HRR · 기대 HRR · 잔차
# =============================================================================
class TestHRR:
    def test_hrr_basic(self) -> None:
        # (120−60)/(180−60) = 0.5
        assert heart_rate_reserve(120, 60, 180) == pytest.approx(0.5)

    def test_hrr_clamped(self) -> None:
        assert heart_rate_reserve(200, 60, 180) == 1.0
        assert heart_rate_reserve(50, 60, 180) == 0.0

    def test_hrr_none_on_bad_input(self) -> None:
        assert heart_rate_reserve(None, 60, 180) is None
        assert heart_rate_reserve(120, 60, None) is None
        assert heart_rate_reserve(120, 180, 180) is None  # denom ≤ 0

    def test_expected_hrr_from_met(self) -> None:
        # (met−1)/(vo2max−1). vo2max=10 → met=1 → 0, met=5.5 → 0.5
        assert expected_hrr_from_met(1.0, PHI) == 0.0
        assert expected_hrr_from_met(5.5, PHI) == pytest.approx(0.5)

    def test_residual_clamped(self) -> None:
        assert residual_strain(0.6, 0.5) == pytest.approx(0.1)
        assert residual_strain(0.4, 0.6) == 0.0  # 활동이 심박을 다 설명


# =============================================================================
# 통합 — evaluate_personalized 핵심 불변성
# =============================================================================
class TestEvaluatePersonalized:
    def test_activity_replaces_pet_met(self, base) -> None:
        """activity 있으면 met 개인화 PET 이 기본 met PET(base_vpti)과 달라진다."""
        bio = Biometrics(hr=110, activity=4.0, hr_rest=60)
        r = evaluate_personalized(base, bio, PhysiologyProfile(age=40, sex="male"))
        assert r.metabolic_met is not None
        assert r.metabolic_met > DEFAULT_CONFIG.comfort.pet_met  # 활발 → met↑
        assert r.pvpti != r.base_vpti

    def test_no_activity_suppresses_strain(self, base) -> None:
        """activity 없으면 strain=0 (환경 PET 만 반영, HR 있어도 무시)."""
        bio = Biometrics(hr=150, activity=None, hr_rest=60)
        r = evaluate_personalized(base, bio, PhysiologyProfile(age=40, sex="male"))
        assert r.strain_index == 0.0
        assert r.expected_hrr is None
        assert r.metabolic_met is None
        assert r.risk_level == r.base_risk_level
        assert r.pvpti == pytest.approx(r.base_vpti)

    def test_activity_explained_hr_adds_no_strain(self, base) -> None:
        """활동으로 설명되는 심박은 잔차≈0 → 심박이 위험경계를 앞당기지 않음.

        핵심 불변성: 관측 HRR 이 높아도(원값이면 허위경보) 활동량으로 설명되면
        잔차=0 이라 위험도는 순수 pvpti(대사 개인화 PET)로만 결정된다.
        (활동 자체가 PET 을 올리는 것은 물리적으로 옳은 효과이며 별개.)
        """
        from vpti_core.vpti import _classify_risk_thermal

        prof = PhysiologyProfile(age=40, sex="male")
        hr_max = estimate_hr_max(40, "male", None, PHI)
        # A=1.8 에서 6.0 kcal/min ≈ 4 met, 그에 걸맞은 심박(관측≈기대) → 잔차 0
        _, met = metabolic_rate_from_activity(6.0, PHI.default_body_surface_area, PHI)
        expected = expected_hrr_from_met(min(met, PHI.met_max), PHI)
        hr = 60 + expected * (hr_max - 60)   # 관측 HRR 을 기대치와 동일하게 맞춤
        bio = Biometrics(hr=hr, activity=6.0, hr_rest=60)
        r = evaluate_personalized(base, bio, prof)

        assert r.strain_index == pytest.approx(0.0, abs=1e-6)
        assert r.observed_hrr > 0.2          # 원값 HRR 은 높음(원값이면 허위경보였을 것)
        # 잔차 0 → 위험경계 앞당김 없음: 위험도는 pvpti 만으로 결정
        assert r.risk_level == _classify_risk_thermal(r.pvpti, "pet")

    def test_excess_hr_raises_strain_and_risk(self, base) -> None:
        """같은 활동인데 심박이 초과하면 잔차>0 → 위험경계 앞당김."""
        prof = PhysiologyProfile(age=40, sex="male")
        # 가벼운 활동(2 met 상당)인데 심박은 매우 높음 → 운동으로 설명 안 됨
        bio = Biometrics(hr=170, activity=3.0, hr_rest=60)
        r = evaluate_personalized(base, bio, prof)
        assert r.strain_index > 0.0
        # 잔차가 위험경계를 앞당겨 base 보다 같거나 더 심각
        order = ["safe", "caution", "warning", "danger", "severe"]
        assert order.index(r.risk_level) >= order.index(r.base_risk_level)

    def test_as_dict_shape(self, base) -> None:
        bio = Biometrics(hr=110, activity=4.0, hr_rest=60)
        d = evaluate_personalized(base, bio, PhysiologyProfile(age=40)).as_dict()
        for k in (
            "pvpti", "base_vpti", "delta_personalization", "risk_level",
            "base_risk_level", "strain_index", "metabolic_met", "hr_max_used",
            "season", "stress_category", "comfort",
        ):
            assert k in d
        assert d["comfort"]["index"] == "pet"
        assert d["season"] in ("summer", "winter", "transition")
        assert isinstance(d["stress_category"], str) and d["stress_category"]

    def test_compute_pvpti_wrapper(self, thermal_kwargs) -> None:
        """compute_pvpti 래퍼가 thermal 파이프라인 + 개인화를 한 번에 수행."""
        bio = Biometrics(hr=115, activity=4.0, hr_rest=58)
        r = compute_pvpti(
            bio=bio, profile=PhysiologyProfile(age=35, sex="female"), **thermal_kwargs
        )
        assert r.pvpti > 0
        assert r.hr_max_used == pytest.approx(206 - 0.88 * 35)
