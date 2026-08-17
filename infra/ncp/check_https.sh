#!/usr/bin/env bash
# =========================================================================
# ClimaX — HTTPS 진단 + 자동 갱신 교정 (web 서버에서 실행)
#
#   sudo bash check_https.sh
#
# 1) 443이 실제로 열려서 응답하는지, 어디서 막히는지 단계별로 확인
# 2) 90일 뒤 자동 갱신이 실패하지 않도록 갱신 방식을 교정
#    (standalone 방식은 nginx가 80을 잡고 있으면 갱신이 실패한다
#     → 갱신 직전 nginx를 잠깐 멈추고 끝나면 켜는 훅을 등록)
# =========================================================================
set -uo pipefail

DOMAIN="${CLIMAX_DOMAIN:-api.climaxapp.kr}"
UPSTREAM="${CLIMAX_UPSTREAM:-10.20.10.205:8000}"
RC="/etc/letsencrypt/renewal/${DOMAIN}.conf"

[ "$(id -u)" = "0" ] || { echo "❌ sudo 로 실행하세요"; exit 1; }
say() { echo; echo "▶ $*"; }
ok()  { echo "  ✅ $*"; }
warn(){ echo "  ⚠️  $*"; }

# -------------------------------------------------------------------------
say "1. nginx 상태"
nginx -v 2>&1 | sed 's/^/  /'
systemctl is-active nginx >/dev/null && ok "nginx 실행 중" || warn "nginx 정지 상태 — systemctl start nginx"
echo "  · 열린 포트:"
ss -tlnp 2>/dev/null | grep -E ':(80|443)\s' | sed 's/^/    /' || echo "    (80/443 리스닝 없음!)"

say "2. 어디까지 되는지 단계별"
step() { # $1=설명  $2=curl 인자들
  local desc="$1"; shift
  local code; code="$(curl -s -o /tmp/_b -m 8 -w '%{http_code}' "$@" 2>/tmp/_e)"
  if [ "$code" = "000" ]; then
    echo "  ❌ $desc → 연결 자체 실패: $(tr -d '\n' < /tmp/_e | tail -c 120)"
  else
    echo "  $([ "$code" = 200 ] && echo ✅ || echo ⚠️ ) $desc → $code  $(head -c 90 /tmp/_b)"
  fi
}
step "was 백엔드 직접 (http://${UPSTREAM})"        "http://${UPSTREAM}/api/v1/health"
step "nginx 경유 http (localhost)"                  -H "Host: ${DOMAIN}" "http://127.0.0.1/api/v1/health"
step "nginx 경유 https (localhost)"                 -k --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/api/v1/health"
step "밖에서 도메인으로 https"                       "https://${DOMAIN}/api/v1/health"

say "3. nginx가 인식 중인 서버 블록"
nginx -T 2>/dev/null | grep -nE "^\s*(server_name|listen|ssl_certificate\s)" | sed 's/^/  /' | head -30

say "4. 인증서"
if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
  openssl x509 -noout -subject -dates -in "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" | sed 's/^/  /'
else
  warn "인증서 파일 없음"
fi

# -------------------------------------------------------------------------
say "5. 자동 갱신 교정"
if [ ! -f "$RC" ]; then
  warn "갱신 설정 파일이 없습니다: $RC"
else
  AUTH="$(grep -E '^authenticator' "$RC" | head -1 | awk '{print $3}')"
  echo "  · 현재 인증 방식: ${AUTH:-불명}"
  if [ "$AUTH" = "standalone" ]; then
    if grep -q '^pre_hook' "$RC"; then
      ok "nginx 중지/재시작 훅이 이미 등록돼 있음"
    else
      cp -a "$RC" "${RC}.bak.$(date +%Y%m%d_%H%M%S)"
      sed -i '/^\[renewalparams\]/a pre_hook = systemctl stop nginx\npost_hook = systemctl start nginx' "$RC"
      ok "갱신 훅 등록 완료 (갱신 시 nginx 수 초 중지 → 자동 재시작)"
    fi
  else
    ok "webroot 방식 — 무중단 갱신. 훅 불필요"
  fi

  echo "  · 갱신 리허설 실행 중… (수 초 걸립니다)"
  if certbot renew --cert-name "$DOMAIN" --dry-run >/tmp/_renew 2>&1; then
    ok "갱신 리허설 통과 — 90일 뒤 자동 갱신됩니다"
  else
    warn "갱신 리허설 실패. 마지막 20줄:"
    tail -20 /tmp/_renew | sed 's/^/    /'
  fi
fi

echo
echo "============================================================"
echo " 이 출력 전체를 그대로 복사해서 보내주세요."
echo "============================================================"
