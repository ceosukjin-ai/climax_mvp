# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

ClimaX MVP — 무센서 기반 체감기후 인텔리전스 플랫폼. Google Street View + SegFormer 세그멘테이션 + 기상청 실황을 결합하여 위경도만으로 **VPTI(체감기후 지수)**를 실시간 산출한다. 한국어 주석·문자열·커밋 메시지가 기본이다.

현재 구현 상태: Step 1~3 완료 (backend + Docker + NCP 배포). Step 4 (Next.js 대시보드) · Step 5 (React Native) 미착수 — `web/`, `mobile/` 디렉토리는 비어있음.

## 주요 명령어 (backend)

모든 명령은 `backend/` 디렉토리에서 실행. venv 활성화 후 사용.

```bash
# 인프라 (Redis 6379, Postgres+PostGIS 5432)
docker compose up -d
docker compose down

# 테스트
pytest                              # 전체
pytest tests/test_vsi.py            # 단일 파일
pytest tests/test_vsi.py::test_name # 단일 테스트
pytest -m paper_reproduction        # 논문 재현 테스트만
pytest -m "not slow"                # ML 추론 등 느린 것 제외
pytest -m "not integration"         # 외부 API 호출 제외

# 서버 (reload 모드, .env 자동 로드)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# → http://localhost:8000/docs (production 모드에서는 /docs, /redoc 비활성)

# 린트·타입
ruff check .
mypy app
```

NCP 배포: 서버에서 `bash infra/ncp/bootstrap.sh` (처음 한 번) → `bash infra/ncp/deploy.sh` (재배포). 헬스체크는 `/api/v1/health`.

## 아키텍처 (big picture)

### 레이어 구조 — `core` → `services` → `api`

`backend/app/` 내부는 **의존성이 한 방향으로만** 흐른다:

- **`core/`** — 순수 계산 로직. 외부 I/O 없음. `vsi.py`, `smti.py`, `pwi.py`는 각자 독립 지수이고 `vpti.py`가 세 개를 합성한다. 특허·논문 수식이 1:1로 코드에 대응 — 수식 변경 시 docstring의 특허 청구항·논문 절 번호 참조를 함께 업데이트한다.
- **`services/`** — 외부 통합 (Google Street View, 기상청 KMA, Redis). `orchestrator.py`가 이들을 조립해 `(lat, lon) → VPTIResult` 파이프라인을 실행한다.
- **`ml/segformer.py`** — HuggingFace SegFormer 래퍼. ADE20K 클래스를 sky/vegetation/building/ground 4개 그룹으로 매핑하고, 재질 비율도 같은 5-view 추론에서 추출한다.
- **`api/`** — FastAPI 라우트. `routes.py`는 REST, `websocket.py`는 `/api/v1/track` 실시간 스트림. 둘 다 `app.state.orchestrator`에서 파이프라인을 꺼내 쓴다.

`core`는 `services`를 import하지 않는다 — 이 단방향성이 깨지면 core 테스트가 외부 API 없이는 돌지 않게 된다.

### VPTI 계산 수식

```
VPTI = base_temp + Δ_VSI(공간) + Δ_SMTI(재질·일사) + Δ_PWI(바람)
```

- **VSI** = `w_svf·SVF + w_gvi·(1-GVI) + w_bvi·BVI`, 기본 가중치 `(0.5, 0.3, 0.2)` — 논문 PNU 실측 검증값. `settings.vsi_weights`로 덮어쓰기 가능. `(1-GVI)` 부호 변환은 논문·특허가 일치하므로 바꾸지 말 것.
- **SMTI** — 재질별 열물성(α, c, ε) × 일사 × 음영. `app/data/material_properties.py`의 `MATERIAL_DB`가 단일 출처(SSOT). 재질 분류가 DB에 없으면 `"unknown"`으로 폴백.
- **PWI** — 풍속을 공간 구조(SVF, BVI)로 downscale.
- **계절 분기** (`WeatherContext.season`) — 여름/겨울/환절기에 따라 Δ 계수 부호·스케일이 달라진다 (여름엔 바람이 쾌적, 겨울엔 한파). 신규 지수 추가 시 계절 분기를 건드리면 `_classify_risk`와 `_generate_action_guide`도 같이 수정해야 한다.

### 실시간 파이프라인 + 캐싱 전략 (핵심 설계)

`services/orchestrator.py`의 `VPTIOrchestrator.compute(lat, lon)`가 전체 흐름:

1. 좌표 → panoId 해석 (Redis `pano:location:{lat}:{lon}` TTL 30일 → miss 시 Google Metadata API)
2. **병렬**로 공간 분석 + 기상 조회 (`asyncio.gather`)
   - 공간: panoId당 **영구 캐시** (`pano:analysis:{pano_id}`, TTL 없음) — miss 시 Street View 5-view fetch + SegFormer 추론 → 저장
   - 기상: KMA 격자(nx, ny)당 **10분 TTL** (`weather:kma:{nx}:{ny}`)
3. 집계값으로 합성 views_5 복원 (`_build_synthetic_views`) → `compute_vpti` 호출

캐시 키 네임스페이스와 TTL은 `services/cache.py`의 static 메서드(`_pano_analysis_key` 등)에 집중되어 있다. 새 키를 추가할 때도 같은 패턴으로 넣을 것.

**핵심 불변성**: 공간 데이터(VSI·SMTI 공간부분)와 기상 데이터는 반드시 분리 캐시한다. 공간은 panoId가 재촬영될 때까지 불변이고, 기상은 10분마다 바뀐다. 이 분리가 "재방문 <100ms" 응답의 근거 — 한 키에 합치면 안 된다.

### Lifespan hook의 의존성 조립

`app/main.py`의 `lifespan()`이 앱 시작 시 모든 외부 의존성(Redis, Street View, KMA, SegFormer, Orchestrator)을 한 곳에서 조립해 `app.state.cache`와 `app.state.orchestrator`에 주입한다. `.env`에 API 키가 없으면 `orchestrator`를 `None`으로 두고 `/vpti/at`·`/track`은 503을 반환하되 순수 계산 엔드포인트(`/vsi/components`, `/vsi`, `/vpti`)는 계속 동작한다. 이 분리는 의도된 것이므로 "orchestrator가 없으면 앱이 뜨지 않게" 바꾸지 말 것.

SegFormer 로드 실패는 warning만 찍고 앱은 계속 뜬다 — 핵심 엔드포인트를 보호하기 위해서다.

### 설정

`app/config.py`의 `Settings` (pydantic-settings)가 `.env`를 읽어 단일 싱글톤(`get_settings()`, `lru_cache`)으로 제공. VSI 가중치도 `.env`에서 오버라이드 가능 (`VSI_WEIGHT_SVF` 등). `is_production`일 때 `/docs`, `/redoc`이 비활성화된다.

## 저장소 내 규약

- **`.env`는 절대 커밋 금지** — `.gitignore`에 포함됨.
- **커밋 메시지는 한국어 + 의미 기반**. `"fix"`, `"update"` 금지. `"SMTI 열용량 정규화 방향 수정"`처럼 변경한 의도를 남긴다.
- **큰 파일(모델 가중치 .pt/.pth)은 git에 넣지 말기** — NCP Object Storage 경유 다운로드 방식.
- **특허·논문 대응**: `core/` 모듈의 docstring에 특허 청구항 번호·논문 절을 명시하는 관례가 있다. 수식을 고치면 해당 참조도 함께 갱신할 것.

## Windows 환경 메모

- 개발자 주 개발 OS는 Windows. venv 활성화는 `.venv\Scripts\activate`.
- Bash 도구는 Unix 문법을 쓰므로 경로는 forward slash, `/dev/null` 사용.
- `infra/ncp/*.sh`는 Ubuntu 22.04 서버에서 실행 — 로컬에서 직접 돌리지 말 것.
