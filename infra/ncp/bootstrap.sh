#!/usr/bin/env bash
# =========================================================================
# ClimaX NCP Ubuntu 22.04 서버 초기 셋업 스크립트
#
# 실행:
#   ssh ubuntu@<NCP_PUBLIC_IP>
#   curl -fsSL https://raw.githubusercontent.com/<계정>/climax-mvp/main/infra/ncp/bootstrap.sh | bash
#   (또는 레포 클론 후 bash infra/ncp/bootstrap.sh)
#
# 완료되면:
# - Docker + Docker Compose 설치
# - UFW 방화벽 규칙 (SSH + API 포트)
# - swap 1GB 생성 (메모리 부족 대비)
# - climax-mvp 레포 클론
# =========================================================================
set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:-git@github.com:YOUR_ACCOUNT/climax-mvp.git}"
INSTALL_DIR="${INSTALL_DIR:-/home/ubuntu/climax-mvp}"

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
# -------------------------------------------------------------------------
echo ">>> Configuring UFW firewall"
sudo ufw --force enable
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 8000/tcp comment 'ClimaX API'
# Step 4에서 프론트 추가 시 nginx가 80/443 담당
# sudo ufw allow 80/tcp comment 'HTTP'
# sudo ufw allow 443/tcp comment 'HTTPS'
sudo ufw status verbose

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
    if [[ "$GITHUB_REPO" == *"YOUR_ACCOUNT"* ]]; then
        echo "    SKIPPED — edit GITHUB_REPO in this script first"
        echo "    Or run: GITHUB_REPO=git@github.com:you/climax-mvp.git bash bootstrap.sh"
    else
        git clone "$GITHUB_REPO" "$INSTALL_DIR"
    fi
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
echo "     exit && ssh ubuntu@..."
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
