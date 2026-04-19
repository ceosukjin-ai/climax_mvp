# ClimaX 아키텍처

## 시스템 전체 흐름

```
[사용자 앱] ──GPS 이동──▶ [WebSocket] ──▶ [VPTI API]
                                              │
                                              ▼
                         ┌────────────────────┴────────────────────┐
                         ▼                                         ▼
                  [panoId 캐시?] ──hit──▶ [Redis]             [기상 조회]
                         │                                         │
                       miss                                        ▼
                         ▼                                  [기상청 API]
              [Google Street View]                                 │
                         ▼                                         │
                [SegFormer 5-view 추론]                             │
                         ▼                                         │
               [VSI·SMTI·PWI 산출] ◀────────────────────────────────┘
                         ▼
                 [VPTI 통합 + 행동 가이드]
                         ▼
                 [Redis/DB 저장 + 사용자 응답]
```

## 핵심 설계 결정

### 1. 지수 분리 + 통합

VSI·SMTI·PWI를 독립 모듈로 구현하고 VPTI에서 합산하는 구조는 세 가지 이점이 있습니다:

- **특허 정합** — VSI와 SMTI는 각각 독립 특허. 구조를 분리함으로써 두 특허의 청구항이 코드에 1:1로 대응됩니다.
- **실증 검증성** — 각 지수를 개별적으로 논문/특허와 대조 가능합니다.
- **지역별 튜닝** — 서울은 SMTI 가중치를, 부산은 PWI 가중치를 따로 학습할 수 있습니다.

### 2. 가중치 파라미터화

공식은 `(0.5, 0.3, 0.2)` 논문 검증값이 기본이지만, `settings.vsi_weights`로 언제든 덮어쓸 수 있습니다. 특허 청구항 5의 "선형 결합" 범위 내에서 자유도 확보.

### 3. On-demand 캐싱

첫 방문자 → Street View fetch + 추론 (2~3초) → 결과 영구 저장.
이후 모든 방문자 → 캐시 히트 (<100ms).

장점: 도시 전역 사전 배치 비용 없음, 실제 사용자 동선만 채움.
단점: 첫 방문 체감 지연. → "분석 중..." UI로 보완.

### 4. 기상은 공간과 분리

공간 지수(VSI·SMTI·PWI 중 공간부분)는 panoId당 영구 캐시.
기상은 10분 TTL. VPTI 최종 합성 시점에 결합.

이 분리가 "실시간 <1초"의 핵심입니다. 공간 데이터는 안 변해요.

## 데이터 흐름 상세

### 새 panoId 방문 시 (첫 사용자)

```
1. 클라이언트: {lat, lon} 전송
2. 서버: nearest panoId 조회 (Google Street View metadata endpoint)
3. panoId로 Redis 조회 → miss
4. Google Street View Static API로 5-view fetch (~500ms)
5. SegFormer 추론 (GPU ~300ms, CPU ~3s)
6. VSI 계산 → Redis SET (TTL 없음)
7. 재질 분류 (SegFormer의 ADE20K 클래스 매핑) → Redis SET
8. 기상청 조회 (10분 캐시 확인) 
9. 태양 위치 계산 (pvlib, <10ms)
10. SMTI 계산
11. PWI 계산
12. VPTI 합성 + 행동 가이드
13. 응답 반환 (총 1~3초)
```

### 재방문 panoId (이후 모든 사용자)

```
1. 클라이언트: {lat, lon} 전송
2. panoId 조회 → Redis hit
3. VSI·SMTI 공간부분 캐시에서 즉시 로드
4. 기상만 조회 (10분 TTL, 대부분 캐시 hit)
5. VPTI 합성
6. 응답 반환 (총 <100ms)
```

## 데이터베이스 스키마 (계획)

PostgreSQL + PostGIS:

```sql
-- 영구 저장되는 panoId별 공간 지수
CREATE TABLE pano_analysis (
    pano_id TEXT PRIMARY KEY,
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    svf FLOAT NOT NULL,
    gvi FLOAT NOT NULL,
    bvi FLOAT NOT NULL,
    vsi FLOAT NOT NULL,
    materials JSONB NOT NULL,  -- [{material, fraction}, ...]
    segmentation_meta JSONB,   -- 모델 버전, 신뢰도 등
    computed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_pano_location ON pano_analysis USING GIST(location);

-- 사용자 세션별 VPTI 로그 (실증 분석용)
CREATE TABLE vpti_log (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    pano_id TEXT REFERENCES pano_analysis(pano_id),
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    vpti FLOAT NOT NULL,
    risk_level TEXT NOT NULL,
    weather JSONB NOT NULL,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_log_session ON vpti_log(session_id);
CREATE INDEX idx_log_time ON vpti_log(recorded_at);
```

## 앞으로의 확장 포인트

- **Step 2** — WebSocket 실시간 트래킹, SegFormer 통합, 기상청 연동, Redis 캐싱
- **Step 3** — NCP 배포 (Docker + NKS), CI/CD, 모니터링
- **Step 4** — Next.js 대시보드 (실증용)
- **Step 5** — React Native 모바일 앱
- **Step 6** — B2G 실증 지자체와 API 연동
