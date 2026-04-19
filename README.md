# ClimaX MVP

무센서 기반 체감기후 인텔리전스 플랫폼 — NCP 기반 실시간 VPTI 서비스.

## 프로젝트 구조

```
climax-mvp/
├── backend/        # FastAPI — VPTI Core Engine, WebSocket, REST API
│   ├── app/
│   │   ├── api/           # REST + WebSocket endpoints
│   │   ├── core/          # VSI, SMTI, PWI, VPTI 엔진 (순수 로직)
│   │   ├── ml/            # SegFormer 추론, 재질 분류
│   │   ├── services/      # 외부 API (기상청, Street View, NCP)
│   │   ├── models/        # DB 모델 (PostgreSQL/PostGIS)
│   │   ├── schemas/       # Pydantic 스키마
│   │   └── data/          # 열물성 DB, 시드 데이터
│   ├── tests/      # pytest 검증 (논문 재현 포함)
│   └── scripts/    # 초기 세팅·시드 스크립트
├── web/            # Next.js 대시보드 (Step 4)
├── mobile/         # React Native 앱 (Step 5)
├── infra/ncp/      # NCP 배포 스크립트
├── data/pnu/       # PNU 28포인트 실측 시드 데이터
└── docs/           # 아키텍처·운영 문서
```

## 실시간 아키텍처 요약

VPTI는 세 공간 지수와 기상·태양 조건을 통합한 체감기후 지수입니다.

- **VSI** — 공간 구조 (SVF, GVI, BVI 기반, 논문 가중치 `0.5, 0.3, 0.2`)
- **SMTI** — 표면 재질 열적 잠재력 (재질비율 × 열물성 × 태양조건 × 음영)
- **PWI** — 보행자 높이 풍환경 (기상 + 공간 downscaling)
- **VPTI** — 위 세 지수 + 기상·시간 요인 통합 체감기후

## 캐싱 전략 (On-demand)

첫 번째 사용자가 새 panoId 방문 시 Street View fetch + SegFormer 추론 → 결과를 Redis (hot cache) + PostgreSQL (영구 저장)에 저장. 이후 모든 사용자는 캐시 히트 → <100ms 응답.

기상 데이터는 분리하여 10분 TTL로 관리 (공간 데이터와 독립).

## 개발 순서

- **Step 1** — 백엔드 Core Engine (VSI·SMTI·PWI·VPTI 로직 + API) ✅
- **Step 2** — WebSocket 실시간 트래킹 + ML 추론 + 캐싱 ✅
- **Step 3** — NCP 배포 + Docker + 운영 ✅
- **Step 4** — Next.js 대시보드
- **Step 5** — React Native 앱

## 빠른 시작 (로컬 개발)

```bash
cd backend
docker compose up -d                # Redis + Postgres
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # API 키 입력
pytest                              # 70개 테스트 실행
uvicorn app.main:app --reload       # http://localhost:8000/docs
```

## NCP 배포

```bash
# NCP 서버 접속 후
git clone git@github.com:<account>/climax-mvp.git
cd climax-mvp
bash infra/ncp/bootstrap.sh          # Docker, 방화벽, swap 자동 설정
# logout & 재접속
cp infra/ncp/.env.prod.example infra/ncp/.env.prod
vim infra/ncp/.env.prod              # API 키 입력
bash infra/ncp/deploy.sh             # 빌드 + 기동 + 헬스체크
```

## 문서

- `docs/ARCHITECTURE.md` — 전체 아키텍처
- `docs/DEVELOPMENT.md` — 집·연구실 개발 환경 구축
- `docs/STEP2_USAGE.md` — 실시간 파이프라인 사용법
- `docs/STEP3_DEPLOYMENT.md` — NCP 배포 완전 가이드
