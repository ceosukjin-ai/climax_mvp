#!/usr/bin/env bash
# =========================================================================
# ClimaX 배포 스크립트
#
# NCP 서버에서 실행하면:
#   1. 최신 코드 pull
#   2. Docker 이미지 빌드
#   3. 컨테이너 재시작
#   4. 헬스체크
#
# 사용법:
#   bash infra/ncp/deploy.sh
# =========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.prod.yml"
ENV_FILE="$SCRIPT_DIR/.env.prod"

echo ">>> ClimaX deploy"
echo "    Repo root : $REPO_ROOT"
echo "    Backend   : $BACKEND_DIR"
echo "    Compose   : $COMPOSE_FILE"

# -------------------------------------------------------------------------
# Sanity checks
# -------------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    echo "[ERROR] $ENV_FILE not found"
    echo "         cp .env.prod.example .env.prod  &&  vim .env.prod"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo "[ERROR] docker not installed. Run bootstrap.sh first."
    exit 1
fi

# -------------------------------------------------------------------------
# 1. 최신 코드 (git pull은 선택적 — CI/CD 쓰면 생략)
# -------------------------------------------------------------------------
if [ -d "$REPO_ROOT/.git" ]; then
    echo ">>> git pull"
    cd "$REPO_ROOT"
    git pull --ff-only || echo "    (pull skipped — may be detached or dirty)"
fi

# -------------------------------------------------------------------------
# 2. Docker 이미지 빌드
# -------------------------------------------------------------------------
# 배치가 돌고 있으면 알려준다 (2026-09-16).
# 배포는 API 를 재시작하고, 그 사이 health 가 200 이 아니다. watch_health 가 그걸 보고
# **돌던 배치를 죽인다**(이제 2회 연속 실패로 완화했지만 5분 안에 안 끝나면 여전히 죽는다).
# 배치가 있으면 사람이 알고 시작하게 한다 — 조용히 20만 칸을 날리지 않기 위해서다.
_batch=$(docker ps -q --filter name=ncp-api-run | wc -l)
if [ "${_batch:-0}" -gt 0 ]; then
    echo ""
    echo "!! 배치 컨테이너 ${_batch}개가 돌고 있다."
    echo "   배포하면 watch_health 가 이걸 죽일 수 있다(격자는 --resume 이라 재개는 된다)."
    echo "   그래도 진행하려면 5초 안에 Ctrl-C 를 누르지 말 것."
    sleep 5
fi

echo ">>> Building API image"
cd "$BACKEND_DIR"
docker build -t climax-backend:latest .

# -------------------------------------------------------------------------
# 3. 스택 기동 (pull + up -d)
# -------------------------------------------------------------------------
echo ">>> Starting stack"
cd "$SCRIPT_DIR"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull redis postgres
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d

# -------------------------------------------------------------------------
# 4. 헬스체크 — 최대 2분 대기 (SegFormer 로드 시간)
# -------------------------------------------------------------------------
echo ">>> Waiting for API health"
for i in {1..24}; do
    if curl -fs http://localhost:8000/api/v1/health > /dev/null 2>&1; then
        echo "    API is healthy"
        break
    fi
    if [ "$i" -eq 24 ]; then
        echo "[ERROR] API did not become healthy within 2 minutes"
        docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail=50 api
        exit 1
    fi
    sleep 5
done

# -------------------------------------------------------------------------
# 5. 상태 요약
# -------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Deploy 완료"
echo "============================================================"
curl -s http://localhost:8000/api/v1/health | jq .
echo ""
echo " 로그 보기  : docker compose -f $COMPOSE_FILE logs -f api"
echo " 컨테이너   : docker compose -f $COMPOSE_FILE ps"
echo " 재시작     : docker compose -f $COMPOSE_FILE restart api"
echo " 중지       : docker compose -f $COMPOSE_FILE down"
echo ""
