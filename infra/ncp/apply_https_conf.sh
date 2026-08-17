#!/usr/bin/env bash
# =========================================================================
# ClimaX — HTTPS 설정을 nginx가 "실제로 읽는 위치"에 적용 (web 서버에서 실행)
#
#   sudo bash apply_https_conf.sh
#
# 배경: 이 서버의 nginx(1.30.x, nginx.org 패키지)는 우분투식 sites-enabled/ 를
#       읽지 않고 conf.d/ 만 읽는다. 그래서 sites-available 에 쓴 설정이
#       그대로 무시돼 443이 안 열렸다. 이 스크립트는 include 방식을 직접 확인해
#       올바른 위치에 쓰고, 충돌하는 기존 파일을 비활성화한다.
#
# 인증서는 이미 발급돼 있어야 한다(certbot). 여기서는 발급하지 않는다.
# =========================================================================
set -uo pipefail

DOMAIN="${CLIMAX_DOMAIN:-api.climaxapp.kr}"
UPSTREAM="${CLIMAX_UPSTREAM:-10.20.10.205:8000}"
WEBROOT="/var/www/certbot"
STAMP="$(date +%Y%m%d_%H%M%S)"

[ "$(id -u)" = "0" ] || { echo "❌ sudo 로 실행하세요"; exit 1; }
say() { echo; echo "▶ $*"; }
ok()  { echo "  ✅ $*"; }
warn(){ echo "  ⚠️  $*"; }
die() { echo "  ❌ $*"; exit 1; }

[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ] \
  || die "인증서가 없습니다 — setup_https.sh 를 먼저 실행하세요"

# -------------------------------------------------------------------------
say "1/5 nginx가 어떤 폴더를 읽는지 확인"

MAIN=/etc/nginx/nginx.conf
TARGET=""; LINK=""
if grep -qE '^\s*include\s+/etc/nginx/conf\.d/' "$MAIN"; then
  TARGET=/etc/nginx/conf.d/climax.conf
  ok "conf.d 방식 → $TARGET"
elif grep -qE '^\s*include\s+/etc/nginx/sites-enabled/' "$MAIN"; then
  TARGET=/etc/nginx/sites-available/climax
  LINK=/etc/nginx/sites-enabled/climax
  ok "sites-enabled 방식 → $TARGET"
else
  grep -nE '^\s*include' "$MAIN" | sed 's/^/    /'
  die "include 구문을 알 수 없습니다 (위 목록 확인 필요)"
fi

# -------------------------------------------------------------------------
say "2/5 충돌하는 기존 설정 정리"

CONFLICTS="$(nginx -T 2>/dev/null | awk '/^# configuration file /{f=$4; sub(/:$/,"",f)} /listen[[:space:]]+(80|443)/{print f}' | sort -u)"
for f in $CONFLICTS; do
  [ "$f" = "$TARGET" ] && continue
  if [ "$f" = "$MAIN" ]; then
    warn "nginx.conf 안에 직접 80 서버 블록이 있습니다 — 수동 확인 필요: $MAIN"
    continue
  fi
  mv "$f" "${f}.disabled.${STAMP}"
  ok "비활성화: $f → $(basename "${f}.disabled.${STAMP}")"
done
[ -n "$CONFLICTS" ] || ok "충돌 없음"

# -------------------------------------------------------------------------
say "3/5 설정 작성"

mkdir -p "$WEBROOT/.well-known/acme-challenge" /etc/nginx/snippets
chown -R www-data:www-data "$WEBROOT" 2>/dev/null || true
[ -f "$TARGET" ] && cp -a "$TARGET" "${TARGET}.bak.${STAMP}"

cat > /etc/nginx/snippets/climax_proxy.conf <<EOF
client_max_body_size 20m;
proxy_connect_timeout 10s;
proxy_send_timeout 120s;
proxy_read_timeout 120s;

location /api/ {
    proxy_pass http://climax_api;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection \$connection_upgrade;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_buffering off;
}
location = /nginx-health { access_log off; return 200 "ok\n"; add_header Content-Type text/plain; }
root /var/www/climax;
index index.html;
location / { try_files \$uri \$uri/ =404; }
EOF

cat > "$TARGET" <<EOF
# ClimaX nginx — apply_https_conf.sh 생성 (${STAMP})
map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }

upstream climax_api { server ${UPSTREAM}; keepalive 32; }

# 1) IP 로 들어오는 http — 구버전 앱 호환용. 앱이 전부 https 로 바뀌면 제거.
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    location ^~ /.well-known/acme-challenge/ { root ${WEBROOT}; }
    include /etc/nginx/snippets/climax_proxy.conf;
}

# 2) 도메인 http → https
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    location ^~ /.well-known/acme-challenge/ { root ${WEBROOT}; }
    location / { return 301 https://\$host\$request_uri; }
}

# 3) 도메인 https (본선)
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=15768000" always;
    add_header X-Content-Type-Options "nosniff" always;

    include /etc/nginx/snippets/climax_proxy.conf;
}
EOF

[ -n "$LINK" ] && ln -sf "$TARGET" "$LINK"
ok "작성 완료: $TARGET"

# -------------------------------------------------------------------------
say "4/5 문법 검사 후 반영"

if ! nginx -t 2>/tmp/_ngt; then
  sed 's/^/    /' /tmp/_ngt
  # 되돌리기
  [ -f "${TARGET}.bak.${STAMP}" ] && cp -a "${TARGET}.bak.${STAMP}" "$TARGET" || rm -f "$TARGET"
  for f in /etc/nginx/conf.d/*.disabled."${STAMP}" /etc/nginx/sites-available/*.disabled."${STAMP}"; do
    [ -f "$f" ] && mv "$f" "${f%.disabled.${STAMP}}"
  done
  nginx -t >/dev/null 2>&1 && systemctl reload nginx
  die "설정 오류 — 원래대로 되돌렸습니다"
fi
systemctl reload nginx
sleep 1
ok "반영 완료"

# -------------------------------------------------------------------------
say "5/5 검증"

echo "  · 열린 포트:"
ss -tlnp 2>/dev/null | grep -E ':(80|443)\s' | awk '{print "    "$1" "$4}'

check() {
  local desc="$1"; shift
  local code; code="$(curl -s -o /tmp/_b -m 10 -w '%{http_code}' "$@" 2>/dev/null)"
  [ "$code" = "200" ] && echo "  ✅ $desc → 200 $(head -c 80 /tmp/_b)" \
                      || echo "  ⚠️  $desc → ${code}"
}
check "http (IP, 구버전 앱 경로)"  -H "Host: 180.210.76.123" "http://127.0.0.1/api/v1/health"
check "https (localhost)"          -k --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/api/v1/health"
check "https (밖에서 도메인)"       "https://${DOMAIN}/api/v1/health"

echo
echo "============================================================"
echo " 되돌리기: sudo mv ${TARGET}.bak.${STAMP} ${TARGET} && sudo nginx -t && sudo systemctl reload nginx"
echo "============================================================"
