#!/usr/bin/env bash
# =========================================================================
# ClimaX — api.climaxapp.kr HTTPS 적용 (web 서버에서 실행)
#
# 실행 위치: nginx가 도는 web 서버 (공인 180.210.76.123)
#   scp -P 30022 infra/ncp/setup_https.sh ubuntu@180.210.76.123:~/
#   ssh -p 30022 ubuntu@180.210.76.123
#   sudo bash setup_https.sh
#
# 하는 일
#   1) 사전 점검 — DNS(api.climaxapp.kr), nginx, 80/443 방화벽
#   2) nginx 설정을 ACME 인증 가능한 형태로 교체 (기존 설정은 자동 백업)
#   3) Let's Encrypt 인증서 발급 (webroot 방식 — 서비스 무중단)
#   4) 443 블록 작성 + 최신 TLS 설정 + 자동 갱신 확인
#   5) 검증 — https 헬스체크, 갱신 리허설
#
# 안전장치
#   · 기존 http(IP 접속)는 그대로 살려 둔다 → 이미 설치된 구버전 앱이 안 끊긴다
#   · 도메인으로 들어오는 http만 https로 넘긴다
#   · 인증서 발급 실패 시 원래 설정으로 되돌린다
# =========================================================================
set -uo pipefail

DOMAIN="${CLIMAX_DOMAIN:-api.climaxapp.kr}"
EMAIL="${CLIMAX_LE_EMAIL:-ceosukjin@gmail.com}"
UPSTREAM="${CLIMAX_UPSTREAM:-10.20.10.205:8000}"
WEBROOT="/var/www/certbot"
CONF="/etc/nginx/sites-available/climax"
STAMP="$(date +%Y%m%d_%H%M%S)"

[ "$(id -u)" = "0" ] || { echo "❌ sudo 로 실행하세요:  sudo bash $0"; exit 1; }

say() { echo; echo "▶ $*"; }
ok()  { echo "  ✅ $*"; }
warn(){ echo "  ⚠️  $*"; }
die() { echo "  ❌ $*"; exit 1; }

# -------------------------------------------------------------------------
say "1/5 사전 점검"

command -v nginx >/dev/null || die "nginx가 없습니다 (apt-get install -y nginx)"
ok "nginx $(nginx -v 2>&1 | sed 's/.*\///')"

RESOLVED="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1)"
[ -n "$RESOLVED" ] || die "$DOMAIN 이 DNS에서 안 잡힙니다 (가비아 A레코드 확인)"
ok "$DOMAIN → $RESOLVED"
echo "     (이 서버의 공인 IP와 같아야 합니다. 다르면 지금 중단하고 A레코드를 고치세요.)"

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp  >/dev/null 2>&1
  ufw allow 443/tcp >/dev/null 2>&1
  ok "UFW 80/443 허용"
else
  ok "UFW 비활성(또는 미설치) — 건너뜀"
fi
echo "     ※ NCP 콘솔의 ACG(보안그룹)에도 443 인바운드가 열려 있어야 합니다."

# -------------------------------------------------------------------------
say "2/5 nginx 설정 준비 (ACME 인증 경로)"

mkdir -p "$WEBROOT/.well-known/acme-challenge"
chown -R www-data:www-data "$WEBROOT" 2>/dev/null || true

if [ -f "$CONF" ]; then
  cp -a "$CONF" "${CONF}.bak.${STAMP}"
  ok "기존 설정 백업: ${CONF}.bak.${STAMP}"
fi

write_conf_http_only() {
cat > "$CONF" <<EOF
# ClimaX nginx — 1단계(HTTP + ACME). setup_https.sh 가 생성.
map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }

upstream climax_api { server ${UPSTREAM}; keepalive 32; }

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location ^~ /.well-known/acme-challenge/ { root ${WEBROOT}; }

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
}
EOF
}

write_conf_https() {
cat > "$CONF" <<EOF
# ClimaX nginx — 2단계(HTTPS). setup_https.sh 가 생성.
map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }

upstream climax_api { server ${UPSTREAM}; keepalive 32; }

# 공통 프록시 설정
# (도메인 https 와 IP http 두 곳에서 같은 내용을 쓰므로 파일로 분리)

# --- 1) IP 로 들어오는 http : 구버전 앱 호환용으로 당분간 유지 ---
#     앱이 전부 https 로 바뀌면 이 블록의 location /api/ 를 지우고
#     301 리다이렉트로 바꾸면 된다.
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location ^~ /.well-known/acme-challenge/ { root ${WEBROOT}; }
    include /etc/nginx/snippets/climax_proxy.conf;
}

# --- 2) 도메인으로 들어오는 http : https 로 넘김 ---
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location ^~ /.well-known/acme-challenge/ { root ${WEBROOT}; }
    location / { return 301 https://\$host\$request_uri; }
}

# --- 3) 도메인 https (본선) ---
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
    ssl_stapling on;
    ssl_stapling_verify on;

    # 앱↔서버 구간을 https 로 고정 (6개월)
    add_header Strict-Transport-Security "max-age=15768000" always;
    add_header X-Content-Type-Options "nosniff" always;

    include /etc/nginx/snippets/climax_proxy.conf;
}
EOF

mkdir -p /etc/nginx/snippets
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
}

restore_backup_conf() {
  if [ -f "${CONF}.bak.${STAMP}" ]; then
    cp -a "${CONF}.bak.${STAMP}" "$CONF"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx
    warn "원래 설정으로 되돌렸습니다."
  fi
}

write_conf_http_only
ln -sf "$CONF" /etc/nginx/sites-enabled/climax
rm -f /etc/nginx/sites-enabled/default
nginx -t || { restore_backup_conf; die "nginx 설정 오류"; }
systemctl reload nginx
ok "HTTP + ACME 경로 준비 완료"

# -------------------------------------------------------------------------
say "3/5 Let's Encrypt 인증서 발급"

if ! command -v certbot >/dev/null; then
  apt-get update -y >/dev/null 2>&1
  apt-get install -y certbot >/dev/null 2>&1 || die "certbot 설치 실패"
fi
ok "certbot $(certbot --version 2>&1 | awk '{print $2}')"

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
  ok "이미 인증서가 있습니다 — 발급 건너뜀"
else
  certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
      --email "$EMAIL" --agree-tos --no-eff-email --non-interactive || {
    restore_backup_conf
    echo
    die "인증서 발급 실패. 가장 흔한 원인:
       ① 외부에서 이 서버의 80포트가 안 열림 (NCP ACG 인바운드 80 확인)
       ② $DOMAIN 이 다른 IP를 가리킴 (현재 $RESOLVED)
       ③ 발급 한도 초과 — 한 시간 뒤 재시도"
  }
  ok "인증서 발급 완료"
fi

# -------------------------------------------------------------------------
say "4/5 HTTPS 설정 적용"

write_conf_https
nginx -t || { restore_backup_conf; die "nginx HTTPS 설정 오류"; }
systemctl reload nginx
ok "443 적용 완료"

# -------------------------------------------------------------------------
say "5/5 검증"

HEALTH="$(curl -s -m 10 --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/api/v1/health" -o /tmp/h -w '%{http_code}')"
if [ "$HEALTH" = "200" ]; then
  ok "https 헬스체크 200 — $(head -c 120 /tmp/h)"
else
  warn "https 헬스체크 응답 코드 $HEALTH — was 서버(${UPSTREAM})가 살아 있는지 확인"
fi

EXP="$(openssl x509 -enddate -noout -in "/etc/letsencrypt/live/${DOMAIN}/cert.pem" 2>/dev/null | cut -d= -f2)"
ok "인증서 만료일: ${EXP:-확인 실패}"

if systemctl list-timers 2>/dev/null | grep -q certbot; then
  ok "자동 갱신 타이머 작동 중 (certbot.timer)"
else
  warn "자동 갱신 타이머가 안 보입니다 — systemctl enable --now certbot.timer"
fi
certbot renew --dry-run >/dev/null 2>&1 && ok "갱신 리허설 통과" || warn "갱신 리허설 실패 — certbot renew --dry-run 로 직접 확인"

cat <<EOF

============================================================
 완료. 다음은 앱 쪽입니다.
   1) 앱의 서버 주소를  https://${DOMAIN}  으로 교체
   2) Android: usesCleartextTraffic 제거
      iOS:     Info.plist 의 NSAppTransportSecurity(개발용 http 허용) 제거
   3) 플레이 데이터보안 설문의 '전송 중 암호화' → 예
   4) 앱이 전부 https 로 바뀐 뒤, 이 파일($CONF)의
      첫 번째 server 블록(IP http)을 리다이렉트로 바꾸면 http 통로가 닫힙니다.

 밖에서 확인:  curl -i https://${DOMAIN}/api/v1/health
 되돌리기:     sudo cp ${CONF}.bak.${STAMP} $CONF && sudo nginx -t && sudo systemctl reload nginx
============================================================
EOF
