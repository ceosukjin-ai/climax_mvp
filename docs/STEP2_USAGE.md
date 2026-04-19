# Step 2 — 실시간 자동 파이프라인

Step 1이 엔진 + 수동 입력 API였다면, Step 2는 **실제 데이터로 자동
처리** + **WebSocket 실시간 트래킹**을 추가한 단계입니다.

## 새로 추가된 기능

| 기능 | 위치 | 설명 |
|------|------|------|
| Google Street View 클라이언트 | `app/services/street_view.py` | 5-view 자동 수집, 재시도 포함 |
| 기상청 클라이언트 | `app/services/kma.py` | 초단기실황, 격자 변환 포함 |
| SegFormer 추론 | `app/ml/segformer.py` | 5-view 세그멘테이션, ADE20K 클래스 매핑 |
| Redis 캐시 | `app/services/cache.py` | panoId 영구 + 기상 10분 TTL |
| 오케스트레이터 | `app/services/orchestrator.py` | 전체 파이프라인 조립 |
| WebSocket 트래킹 | `app/api/websocket.py` | `/api/v1/track` 실시간 엔드포인트 |
| REST auto-fetch | `app/api/routes.py` | `GET /api/v1/vpti/at?lat=..&lon=..` |

## 준비물

### 1. 로컬 인프라 (Redis + Postgres)

```bash
cd backend
docker compose up -d
# Redis: localhost:6379
# Postgres: localhost:5432 (Step 3 이후 사용)
```

상태 확인:
```bash
docker compose ps
redis-cli ping  # → PONG
```

### 2. API 키 발급

`.env` 파일에 다음을 채워야 합니다:

#### Google Street View Static API
1. https://console.cloud.google.com/ → 프로젝트 생성
2. APIs & Services → Library → "Street View Static API" 활성화
3. Credentials → Create credentials → API key
4. 제한: HTTP referrers 또는 IP address로 범위 지정 권장
5. `.env`의 `GOOGLE_STREETVIEW_API_KEY=...` 에 입력

무료 크레딧 월 $200 + 월 5,000건 무료 제공. MVP 개발 시 충분.

#### 기상청 OpenAPI Hub
1. https://apihub.kma.go.kr/ 회원가입
2. 로그인 후 마이페이지 → API 인증키 발급
3. "초단기실황(VilageFcstInfoService_2.0)" API 신청
4. `.env`의 `KMA_API_KEY=...` 에 입력

기본 무료. 하루 수천 건 허용.

### 3. Python 의존성 재설치

Step 2 추가 의존성이 있으므로:
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

GPU 서버에서는 PyTorch를 CUDA 버전으로 별도 설치:
```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

## 실행

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

시작 시 로그 예시:
```
Loading SegFormer | model=nvidia/segformer-b0-finetuned-ade-512-512 | device=cpu
SegFormer ready
Redis connection: OK
Orchestrator ready
```

## 사용 예시

### REST — 좌표만으로 VPTI 자동 조회

```bash
curl "http://localhost:8000/api/v1/vpti/at?lat=37.5665&lon=126.9780"
```

첫 호출: 1~3초 (Street View + SegFormer)
이후 호출 (같은 panoId): <100ms (Redis 히트)

### WebSocket — 실시간 트래킹

브라우저 콘솔 또는 Python 클라이언트로 테스트:

```javascript
const ws = new WebSocket("ws://localhost:8000/api/v1/track");

ws.onopen = () => {
  // 초기 위치
  ws.send(JSON.stringify({ lat: 37.5665, lon: 126.9780 }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "vpti") {
    console.log("VPTI:", msg.data.vpti, "risk:", msg.data.risk_level);
    console.log("Cache hit:", msg.telemetry.pano_cache_hit, "total:", msg.telemetry.total_ms + "ms");
  }
};

// 사용자 이동 시뮬레이션 — 25m 간격으로
setInterval(() => {
  ws.send(JSON.stringify({
    lat: 37.5665 + Math.random() * 0.0003,
    lon: 126.9780 + Math.random() * 0.0003,
  }));
}, 2000);
```

Python 테스트 클라이언트:
```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect("ws://localhost:8000/api/v1/track") as ws:
        await ws.send(json.dumps({"lat": 37.5665, "lon": 126.9780}))
        response = await ws.recv()
        print(json.loads(response))

asyncio.run(main())
```

### 캐시 상태 확인

```bash
curl http://localhost:8000/api/v1/cache/stats
# {"pano_cached": 5, "redis_ok": true}
```

## 비용·성능 체크리스트

- [ ] Google Cloud Console에서 일일 예산 한도 설정 (예: $10/day)
- [ ] API 키에 HTTP referrer 또는 IP 제한 걸기
- [ ] Redis 캐시 적중률 모니터링 — `/api/v1/cache/stats` 주기 확인
- [ ] GPU 서버는 **안 쓸 때 반드시 끄기** — NCP 시간당 과금

## 트러블슈팅

### SegFormer 첫 로드 시 오래 걸림

HuggingFace에서 모델을 다운로드 (~100MB). 한 번 받으면 `~/.cache/huggingface/`에 저장되어 재실행 시 빠름. 오프라인 환경에서는 미리 `transformers-cli download nvidia/segformer-b0-finetuned-ade-512-512`.

### "Orchestrator not initialized"

`.env`의 `GOOGLE_STREETVIEW_API_KEY` 또는 `KMA_API_KEY`가 비어있음.
설정 후 서버 재시작.

### Street View "ZERO_RESULTS" 오류

해당 좌표 근처에 Google이 촬영한 Street View가 없음. 일반 도로 위주로 있으므로 건물 내부·공원 깊숙한 곳은 실패할 수 있음. MVP 실증 지점은 주요 도로변으로 선정.

### Redis 연결 실패

```bash
docker compose logs redis
docker compose restart redis
```

### KMA API "INVALID_SERVICE_KEY"

기상청 API 키 활성화까지 최대 1시간 소요될 수 있음. 발급 직후엔 오류가 날 수 있으니 조금 기다렸다 재시도.

## 다음 Step

- **Step 3** — NCP 배포 (Docker + NKS + Cloud DB)
- **Step 4** — Next.js 대시보드 (지도 + VPTI 히트맵)
- **Step 5** — React Native 앱 (실시간 트래킹 UI)
