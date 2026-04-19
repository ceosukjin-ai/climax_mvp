# NCP 배포 가이드

이 문서는 ClimaX 백엔드를 Naver Cloud Platform(NCP)에 배포하는 전 과정을 다룹니다. **MVP 단계는 NCP Server + Docker Compose**로 단순하게 운영하고, 사용자가 늘어나면 NKS(Kubernetes)로 승격하는 경로입니다.

## 아키텍처

```
    인터넷
       │
       ▼
 ┌─────────────┐
 │ Public IP   │ ← NCP Public IP 1개 할당
 └──────┬──────┘
        │
 ┌──────▼──────────────────────────────┐
 │ NCP Server (Ubuntu 22.04)            │
 │                                      │
 │  ┌─────┐  ┌───────┐  ┌──────────┐   │
 │  │ API │──│ Redis │  │ Postgres │   │  (Docker Compose)
 │  └─────┘  └───────┘  └──────────┘   │
 │                                      │
 │  HF model cache (volume)             │
 └──────────────────────────────────────┘
```

Step 4 이후 프론트는 NCP Object Storage + CDN에 정적 배포, 이 API 서버가 백엔드 역할.

## 사양 추천

### 개발·검증용 (초기)
- **Server**: Standard-g3 — vCPU 4, RAM 16GB, SSD 50GB
- 비용: 월 약 12~15만원 (시간당 약 160원)
- GPU 없이 CPU 추론 — SegFormer-B0는 CPU에서도 5장/20초 가능

### 실증용 (지자체 파일럿)
- **Server**: High-CPU C8 또는 GPU-P40 (스케일 업)
- Redis, Postgres는 **NCP Cloud DB**로 분리 권장 (HA, 백업)

## 1단계 — NCP 서버 생성

### 1.1 콘솔에서 서버 발급

1. https://console.ncloud.com/ 접속
2. 좌측 메뉴 **Services > Compute > Server**
3. **서버 생성** 클릭
4. 설정:
   - **이미지**: Ubuntu 22.04
   - **서버 타입**: Standard-g3 (또는 상위)
   - **요금제**: 월 요금제 (장기) 또는 시간 요금제 (개발)
   - **서버 이름**: `climax-dev` (또는 `climax-prod`)
   - **Storage**: SSD 50GB 이상 (모델 캐시 + Docker 이미지)
5. 인증키 생성 → .pem 파일 다운로드 (재발급 불가, 안전하게 보관)
6. ACG (방화벽) — 기본값 + 다음 규칙 추가:
   - TCP 22 from MyIP (SSH)
   - TCP 8000 from 0.0.0.0/0 (API, 개발 단계)
   - 또는 TCP 80/443 from 0.0.0.0/0 (nginx 프록시 붙이면)

### 1.2 Public IP 할당

1. **Server > Public IP** → 신규 할당
2. 방금 만든 서버에 연결

### 1.3 접속 준비

로컬 PC에서:

```bash
# .pem 파일 권한 설정
chmod 600 ~/Downloads/climax-dev.pem

# SSH config 등록 (~/.ssh/config)
cat >> ~/.ssh/config <<EOF
Host climax-ncp
    HostName <NCP_PUBLIC_IP>
    User ubuntu
    IdentityFile ~/Downloads/climax-dev.pem
    ServerAliveInterval 60
EOF

# 접속 테스트
ssh climax-ncp
```

### 1.4 VS Code Remote-SSH 연결

1. VS Code → Command Palette (Ctrl+Shift+P)
2. `Remote-SSH: Connect to Host` → `climax-ncp`
3. 이제 **VS Code로 NCP 서버 파일을 직접 편집** 가능 — 집이든 연구실이든 동일한 원격 환경

## 2단계 — 서버 Bootstrap

서버에 접속한 상태에서:

```bash
# GitHub에 먼저 SSH 키 등록 필요 (서버에서도 git clone 하려면)
ssh-keygen -t ed25519 -C "climax-ncp"
cat ~/.ssh/id_ed25519.pub
# 이 공개키를 GitHub → Settings → SSH keys에 추가

# bootstrap 스크립트 실행
cd ~
git clone git@github.com:<your-account>/climax-mvp.git
cd climax-mvp
bash infra/ncp/bootstrap.sh
```

스크립트가 하는 일:
- Docker + Compose 설치
- UFW 방화벽 설정
- 2GB swap 파일 생성 (메모리 부족 대비)

완료 후 **logout → 재접속** 필요 (docker 그룹 적용).

## 3단계 — 환경변수 설정

```bash
cd ~/climax-mvp/infra/ncp
cp .env.prod.example .env.prod
vim .env.prod
```

필수 입력:
- `POSTGRES_PASSWORD` — `openssl rand -base64 32`로 생성
- `GOOGLE_STREETVIEW_API_KEY` — Google Cloud Console에서 발급
- `KMA_API_KEY` — https://apihub.kma.go.kr/
- `CORS_ORIGINS` — 프론트 도메인들 (아직 없으면 `*`)

## 4단계 — 배포

```bash
cd ~/climax-mvp
bash infra/ncp/deploy.sh
```

스크립트가 하는 일:
1. `git pull`
2. Docker 이미지 빌드
3. Redis + Postgres + API 컨테이너 기동
4. API 헬스체크 (최대 2분 대기 — SegFormer 첫 로드)

완료 예상 시간: 처음은 **약 5~7분** (Docker 이미지 빌드 + SegFormer 다운로드), 이후는 1~2분.

## 5단계 — 검증

### 헬스체크

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok","version":"0.1.0","timestamp":"..."}
```

### 외부에서 접속

로컬 PC에서:
```bash
curl http://<NCP_PUBLIC_IP>:8000/api/v1/health
```

브라우저에서 `http://<NCP_PUBLIC_IP>:8000/docs` — Swagger UI로 모든 API 확인.

### VPTI 자동 산출 테스트

```bash
# 서울시청 좌표
curl "http://<NCP_PUBLIC_IP>:8000/api/v1/vpti/at?lat=37.5665&lon=126.9780"
```

첫 호출은 1~3초 (Street View + SegFormer), 재호출은 <100ms (Redis 캐시).

### 캐시 적중률

```bash
curl http://<NCP_PUBLIC_IP>:8000/api/v1/cache/stats
# {"pano_cached": 1, "redis_ok": true}
```

## 운영 명령어

```bash
cd ~/climax-mvp/infra/ncp

# 로그 실시간
docker compose -f docker-compose.prod.yml logs -f api

# 컨테이너 상태
docker compose -f docker-compose.prod.yml ps

# API만 재시작 (코드 수정 후)
docker compose -f docker-compose.prod.yml restart api

# 전체 재배포 (코드 + 이미지 빌드)
bash infra/ncp/deploy.sh

# 완전 중지
docker compose -f docker-compose.prod.yml down

# Redis 캐시 초기화 (문제 있을 때)
docker exec climax-redis redis-cli FLUSHALL
```

## 모니터링

### 디스크 사용량 주의

Docker 이미지, 모델 캐시, Postgres 데이터 등 누적됩니다:

```bash
# 사용량 확인
df -h
docker system df

# 안 쓰는 이미지·볼륨 청소 (주의: 신중히)
docker system prune -a --volumes
```

### 메모리 사용량

```bash
# 컨테이너별 RAM 사용량
docker stats --no-stream

# 시스템 전체
free -h
```

SegFormer-B0은 CPU 모드에서 약 800MB RAM 사용. 16GB 서버면 충분히 여유.

## 비용 최적화

- **GPU는 쓸 때만 켜기** — 시간당 2~4천원. 끄지 않으면 월 100만원.
- **Redis 캐시 적극 활용** — Google Street View 호출 비용이 가장 큼 (장당 $0.007).
- **로그 로테이션** — `/etc/docker/daemon.json`에 log 크기 제한 추가:
  ```json
  {
    "log-driver": "json-file",
    "log-opts": { "max-size": "100m", "max-file": "3" }
  }
  ```

## 트러블슈팅

### "Unable to connect to Docker daemon"
→ bootstrap 후 logout/재로그인 필요 (docker 그룹 적용).

### "Port 8000 already in use"
→ `sudo lsof -i :8000` → 기존 프로세스 종료.

### SegFormer 로드 30초 넘게 걸림
→ HuggingFace 첫 다운로드. `docker logs climax-api`에서 진행 확인. hf_cache 볼륨에 저장돼 이후는 빨라짐.

### API가 Redis에 연결 못 함
```bash
docker compose -f docker-compose.prod.yml logs redis
docker network inspect climax-net
```

### GitHub SSH 키 작동 안 함
서버에서 `ssh -T git@github.com` 실행. "Hi username!" 나오면 OK.

## 다음 Step

- **Step 4** — Next.js 대시보드 (NCP Object Storage 정적 호스팅)
- **Step 5** — React Native 앱 (OTA 업데이트 가능)
- **Step 6** — 도메인 + HTTPS (nginx + Let's Encrypt)
- **Step 7** — NCP NKS 이관 (멀티 노드·HA 필요 시점)
