# 데이터 출처 이용 정책 (2026-08-21)

> 출시 전 법적 리스크 검토의 코드 측 결론. 코드에서의 단일 출처는 `backend/app/data_policy.py`.
> 배경 문서: 프로젝트 문서 `출시전_법적리스크_스트리트뷰_2026-08-21.md`

## 1. 왜 있나

거리영상으로 공간지표(SVF/GVI/BVI)를 산출·영구 저장하고, 나아가 그것을 위성 학생 모델의
교사 라벨로 쓰려던 계획이 Google Maps Platform 약관을 정면으로 위반한다는 결론이 나왔다.

| 조항 | 금지 내용 | 우리 해당 행위 |
|---|---|---|
| 3.2.3(a)(i) | 파생 콘텐츠의 pre-fetch / index / **store** | 세그멘테이션 지표 영구 캐시 |
| 3.2.3(a)(ii) | Street View 이미지 **bulk download** | 격자 일괄 스캔 계획 |
| 3.2.3(c)(v) | 스트리트뷰 영상으로 **도시 단위 인덱스 구축** | GVI·SVF 산출이 구조적으로 동일 |
| 3.2.3(c)(vii) | Google Maps Content 를 **ML 학습·테스트·검증·파인튜닝**에 사용 | 교사·학생 지식증류 |
| Service Terms A.3 | — | **panoId 캐싱은 명시적 허용** (유일한 예외) |
| Service Terms A.2 | 저작자 표시 의무 | 앱·웹 "데이터 출처" 화면으로 이행 |

원천 교체(Mapillary·자체 촬영)까지 시간이 걸리므로, 그때까지 **출처를 기록하고 ML 학습
경로에서 기계적으로 차단**한다. 지금 태그를 심어두지 않으면 나중에 어떤 행이 GSV 유래인지
가려낼 수 없다.

## 2. 출처 태그

| 태그 | 뜻 | ML 학습 | 외부 재배포 |
|---|---|---|---|
| `gsv` | Google Street View | ❌ 금지 | ❌ 금지 |
| `mapillary` | Mapillary (CC BY-SA) | ✅ 허용 (모델 가중치 미배포 조건) | ❌ (share-alike 주의) |
| `own` | 자체 촬영·크라우드소싱 | ✅ | ✅ |

**미지정(`None`)은 `gsv`로 취급한다** — 구버전 데이터는 실제로 전부 GSV 유래다.

## 3. 코드에서 어떻게 강제되나

| 위치 | 무엇 |
|---|---|
| `app/data_policy.py` | 정책 단일 출처. `ml_trainable()`, `assert_ml_trainable()` |
| `app/services/cache.py` | `PanoAnalysisCache.imagery_source` (구버전 캐시는 기본값 `gsv`) |
| `app/services/street_view.py` | `GoogleStreetViewClient.IMAGERY_SOURCE = "gsv"` |
| `app/services/orchestrator.py` | 분석 결과·측정 적재에 출처 태그 전파 + **월 호출 상한 가드** |
| `app/services/archive.py` | `measurement.imagery_src` 컬럼, `ml_dataset()` 가 SQL 단계에서 GSV 배제 |
| `tests/test_data_policy.py` | 위 전부의 회귀 테스트 |

### 월 호출 상한

`STREETVIEW_MONTHLY_IMAGE_BUDGET`(기본 9,000 **이미지 요청**, 파노라마 1지점 = 5요청).
Redis 카운터 `quota:imagery:YYYYMM`. 초과하면 **신규** 거리영상 다운로드만 멈추고
이미 분석된 지점의 측정은 그대로 동작한다. 두 가지를 동시에 막는다 —
① 구글 무료 한도(SKU당 월 1만) 초과 과금, ② bulk download 로 읽힐 트래픽.

## 4. 학습 스크립트가 지켜야 할 것

```python
from app.data_policy import assert_ml_trainable

rows = await archive.ml_dataset()          # SQL 단계에서 이미 GSV 배제
assert_ml_trainable(r["imagery_src"] for r in rows)   # 2차 방어
```

원천을 교체하기 전까지 `ml_dataset()`이 **0건을 돌려주는 것이 정상**이다.
조용히 넘어가지 않도록 건수를 로그로 남긴다.

## 5. Mapillary 로 옮길 때 준수사항

1. 공식 Graph API/SDK 로만 수집 (스크래핑 금지, 약관 §5)
2. 이미지별 라이선스 필드 확인 — **CC BY-NC-SA 이미지는 상업 이용 불가**이므로 제외
3. 학습된 **모델 가중치를 외부 배포·오픈소스화하지 않음** (CC BY-SA share-alike 회피)
4. 이미지를 화면에 표시하면 **Mapillary 로고 + 링크백** (§11)
5. **파생 지표를 실시간 경로안내에 직접 결합 금지** (§5) — 쾌적경로 설계 시 주의

## 6. 원천 교체 로드맵

1. **SVF/BVI를 V-World 건물 폴리곤 기반으로 이전** — 실내 엔진 `services/geo.py` 로직 재사용.
   이것만으로 GSV 의존이 **GVI 하나**로 줄어든다.
2. Mapillary 한국 커버리지 실측 (bbox 0.01° 샘플링, `is_pano`·`captured_at` 분포)
3. 커버리지가 충분하면 GVI 를 Mapillary 로, 부족하면 Sentinel-2 NDVI proxy + 자체 촬영
4. `imagery_src = 'gsv'` 행 폐기 및 재계산 → 그 시점부터 학습 재개
