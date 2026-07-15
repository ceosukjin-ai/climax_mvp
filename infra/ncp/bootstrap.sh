#!/usr/bin/env bash
# =========================================================================
# ClimaX NCP Ubuntu 22.04/24.04 서버 초기 셋업 스크립트
#
# 실행:
#   ssh -i ~/.ssh/climax-was-key.pem -p 30022 ubuntu@180.210.77.87
#   (레포 클론 후) bash infra/ncp/bootstrap.sh
#
# 완료되면:
# - Docker + Docker Compose 설치
# - UFW 방화벽 규칙 (SSH + API 포트)
# - swap 2GB 생성 (메모리 부족 대비)
# - climax_mvp 레포 클론
#
# !! 중요 !!
# 이 서버들의 SSH 포트는 22가 아니라 30022입니다.
# SSH_PORT를 잘못 지정하면 UFW가 켜지는 순간 접속이 끊기고
# 다시 들어올 수 없습니다 (NCP 콘솔로만 복구 가능).
# 다른 서버에서 쓸 때는 반드시 아래 SSH_PORT를 확인하세요.
# =========================================================================
set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:-https://github.com/ceosukjin-ai/climax_mvp.git}"
INSTALL_DIR="${INSTALL_DIR:-/home/ubuntu/climax_mvp}"
SSH_PORT="${SSH_PORT:-30022}"   # 22 아님. 변경 전 반드시 확인
API_PORT="${API_PORT:-8000}"
ENABLE_UFW="${ENABLE_UFW:-0}"   # 1로 바꿔야 방화벽 설정이 실행됨 (기본: 건너뜀)

echo ">>> ClimaX NCP bootstrap starting"
echo "    Repo: $GITHUB_REPO"
echo "    Dir : $INSTALL_DIR"

# -------------------------------------------------------------------------
# 1. 시스템 업데이트
# -------------------------------------------------------------------------
echo ">>> System update"
sudo apt-get update -y
sudo apt-get upgrade -y

# -------------------------------------------------------------------------
# 2. 기본 도구 설치
# -------------------------------------------------------------------------
echo ">>> Installing base tools"
sudo apt-get install -y \
    curl \
    git \
    vim \
    htop \
    jq \
    ufw \
    unzip \
    ca-certificates \
    gnupg \
    lsb-release

# -------------------------------------------------------------------------
# 3. Docker 설치 (공식 스크립트)
# -------------------------------------------------------------------------
if ! command -v docker &> /dev/null; then
    echo ">>> Installing Docker"
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    rm get-docker.sh

    sudo usermod -aG docker "$USER"
    echo "    Added $USER to docker group (logout/login to apply)"
else
    echo ">>> Docker already installed"
fi

# -------------------------------------------------------------------------
# 4. Docker Compose plugin (v2)
# -------------------------------------------------------------------------
if ! docker compose version &> /dev/null; then
    echo ">>> Installing Docker Compose plugin"
    sudo apt-get install -y docker-compose-plugin
fi

# -------------------------------------------------------------------------
# 5. 방화벽 (UFW)
#
# 기본적으로 건너뜁니다. 활성화하려면: ENABLE_UFW=1 bash bootstrap.sh
# 순서 주의 — allow 규칙을 먼저 넣고 마지막에 enable 해야 잠기지 않습니다.
# -------------------------------------------------------------------------
if [ "$ENABLE_UFW" = "1" ]; then
    echo ">>> Configuring UFW firewall (SSH port: $SSH_PORT)"

    # 현재 접속 중인 SSH 포트가 맞는지 교차 확인 — 틀리면 중단
    ACTUAL_SSH_PORT="$(ss -tlnp 2>/dev/null | grep -oP 'sshd.*|.*:\K[0-9]+(?=\s.*sshd)' | head -1 || true)"
    if [ -n "${SSH_CONNECTION:-}" ]; then
        ACTUAL_SSH_PORT="$(echo "$SSH_CONNECTION" | awk '{print $4}')"
    fi
    if [ -n "$ACTUAL_SSH_PORT" ] && [ "$ACTUAL_SSH_PORT" != "$SSH_PORT" ]; then
        echo "[ERROR] SSH_PORT=$SSH_PORT 인데 실제 접속 포트는 $ACTUAL_SSH_PORT 입니다."
        echo "        이대로 진행하면 서버에서 잠깁니다. 중단합니다."
        echo "        올바른 값으로 재실행: SSH_PORT=$ACTUAL_SSH_PORT ENABLE_UFW=1 bash bootstrap.sh"
        exit 1
    fi

    # allow 먼저, enable 나중
    sudo ufw allow "${SSH_PORT}/tcp" comment 'SSH'
    sudo ufw allow "${API_PORT}/tcp" comment 'ClimaX API'
    # Step 4에서 프론트 추가 시 nginx가 80/443 담당
    # sudo ufw allow 80/tcp comment 'HTTP'
    # sudo ufw allow 443/tcp comment 'HTTPS'
    sudo ufw --force enable
    sudo ufw status verbose
else
    echo ">>> UFW 설정 건너뜀 (활성화하려면 ENABLE_UFW=1)"
fi

# -------------------------------------------------------------------------
# 6. Swap 파일 (NCP 소형 서버 메모리 부족 대비)
# -------------------------------------------------------------------------
if ! swapon --show | grep -q "/swapfile"; then
    echo ">>> Creating 2GB swap"
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
else
    echo ">>> Swap already configured"
fi

# -------------------------------------------------------------------------
# 7. 레포 클론 (SSH 키가 등록돼 있어야 함)
# -------------------------------------------------------------------------
if [ ! -d "$INSTALL_DIR" ]; then
    echo ">>> Cloning repository"
    git clone "$GITHUB_REPO" "$INSTALL_DIR"
else
    echo ">>> Repository already present at $INSTALL_DIR"
fi

# -------------------------------------------------------------------------
# 완료 안내
# -------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Bootstrap 완료!"
echo "============================================================"
echo ""
echo " 다음 단계:"
echo "  1. logout 후 재로그인 (docker 그룹 적용)"
echo "     exit && ssh -i ~/.ssh/climax-was-key.pem -p 30022 ubuntu@180.210.77.87"
echo ""
echo "  2. 환경변수 파일 생성"
echo "     cd $INSTALL_DIR/infra/ncp"
echo "     cp .env.prod.example .env.prod"
echo "     vim .env.prod   # API 키 등 입력"
echo ""
echo "  3. 컨테이너 실행"
echo "     bash deploy.sh"
echo ""
echo "  4. 헬스체크"
echo "     curl http://localhost:8000/api/v1/health"
echo ""
