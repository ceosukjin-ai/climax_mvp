"""데이터 출처별 이용 정책 — 약관 준수의 단일 출처 (2026-08-21).

왜 이 파일이 있나
-----------------
2026-08-21 출시 전 법적 검토에서, 우리가 거리영상으로 공간지표(SVF/GVI/BVI)를
산출·영구 저장하고 나아가 그것을 위성 학생 모델의 교사 라벨로 쓰려던 계획이
Google Maps Platform 약관을 정면으로 위반한다는 결론이 나왔다.

  · 3.2.3(a)(i)   파생 콘텐츠의 pre-fetch / index / store 금지
  · 3.2.3(a)(ii)  Street View 이미지 bulk download 금지
  · 3.2.3(c)(v)   "스트리트뷰 영상으로 도시 내 수목 위치 인덱스 구축" 금지
                  → 우리의 GVI/SVF 산출이 구조적으로 동일
  · 3.2.3(c)(vii) Google Maps Content 를 ML 모델 학습·테스트·검증·파인튜닝에
                  사용하는 것 금지 → 교사·학생 지식증류가 여기에 해당
  · 명시적 허용은 panoId 캐싱 뿐 (Service Specific Terms A.3)

원천 교체(Mapillary·자체 촬영)까지는 시간이 걸리므로, 그 전까지
**출처를 반드시 기록하고, ML 학습 경로에서는 GSV 유래를 기계적으로 차단**한다.
지금 태그를 심어두지 않으면 나중에 어떤 행이 GSV 유래인지 가려낼 수 없다.

관련 문서: 프로젝트 문서 `출시전_법적리스크_스트리트뷰_2026-08-21.md`
"""
from __future__ import annotations

# ── 거리영상 출처 태그 ────────────────────────────────────────
GSV = "gsv"              # Google Street View
MAPILLARY = "mapillary"  # Mapillary (Meta) — CC BY-SA
OWN = "own"              # 자체 촬영 / 크라우드소싱

KNOWN_IMAGERY_SOURCES = (GSV, MAPILLARY, OWN)

#: 파생 지표를 **머신러닝 학습·검증에 써도 되는** 출처.
#: GSV 는 약관 3.2.3(c)(vii) 로 금지되므로 여기 없다.
ML_TRAINABLE_SOURCES = frozenset({MAPILLARY, OWN})

#: 파생 지표를 **외부에 재배포/공개**해도 되는 출처.
#: (B2G 납품·공개 데이터셋·논문 부록 등. GSV 는 3.2.3(a)(i) 로 금지)
REDISTRIBUTABLE_SOURCES = frozenset({OWN})

# Mapillary 를 쓸 때의 추가 준수사항 — 코드에서 강제할 수 없어 여기 명문화한다.
MAPILLARY_OBLIGATIONS = (
    "공식 Graph API/SDK 로만 수집 (스크래핑 금지, 약관 §5)",
    "이미지별 라이선스 필드 확인 — CC BY-NC-SA 이미지는 상업 이용 불가이므로 제외",
    "학습된 모델 가중치를 외부 배포·오픈소스화하지 않을 것 (CC BY-SA share-alike 회피)",
    "이미지를 화면에 표시할 경우 Mapillary 로고 + 링크백 표기 (§11)",
    "파생 지표를 실시간 경로안내 기능에 직접 결합하지 말 것 (§5)",
)


class DataPolicyViolation(RuntimeError):
    """약관상 허용되지 않는 용도로 데이터를 쓰려 할 때."""


def ml_trainable(imagery_source: str | None) -> bool:
    """이 출처의 파생 지표를 ML 학습에 써도 되는가."""
    return (imagery_source or GSV) in ML_TRAINABLE_SOURCES


def redistributable(imagery_source: str | None) -> bool:
    """이 출처의 파생 지표를 외부에 재배포해도 되는가."""
    return (imagery_source or GSV) in REDISTRIBUTABLE_SOURCES


def assert_ml_trainable(sources) -> None:
    """학습 데이터셋에 금지 출처가 섞여 있으면 즉시 중단시킨다.

    위성 학생 모델(교사·학생 지식증류) 학습 스크립트는 **반드시** 이 함수를
    통과한 데이터만 써야 한다. 조용히 걸러내지 않고 예외를 던지는 이유는,
    "왜 데이터가 줄었지?" 하고 넘어가는 것보다 멈춰 서는 편이 안전해서다.

    Raises:
        DataPolicyViolation: GSV 등 학습 금지 출처가 하나라도 포함된 경우.
    """
    bad = sorted({s or GSV for s in sources if not ml_trainable(s)})
    if bad:
        raise DataPolicyViolation(
            f"머신러닝 학습에 사용할 수 없는 영상 출처가 포함되어 있습니다: {bad}. "
            "Google Street View 유래 파생 지표는 Google Maps Platform 약관 "
            "3.2.3(c)(vii)에 따라 모델 학습·테스트·검증·파인튜닝에 사용할 수 없습니다. "
            "원천을 Mapillary 또는 자체 촬영으로 교체한 뒤 학습하세요. "
            "(app/data_policy.py 참조)"
        )


def filter_ml_trainable(rows, key: str = "imagery_src") -> list:
    """dict 행 목록에서 학습 가능한 것만 남긴다(개수 차이를 로그로 남길 것)."""
    return [r for r in rows if ml_trainable(r.get(key))]
