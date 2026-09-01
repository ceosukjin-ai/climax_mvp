#!/usr/bin/env bash
# AWS 활용신청이 실제로 열렸는지, 응답 컬럼 이름이 무엇인지 확인한다.
#
# ⚠️ 맥 '터미널'에서 직접 실행하세요. (개발 샌드박스에서는 기상청으로 통신이 안 나갑니다)
#
#   cd ~/Desktop/climax_mvp && bash scripts/check_aws.sh
#
# 인증키는 backend/.env 에서 읽고 화면에 찍지 않습니다.

set -u
cd "$(dirname "$0")/.." || exit 1

if [ ! -f backend/.env ]; then echo "backend/.env 가 없습니다"; exit 1; fi
set -a; . ./backend/.env; set +a
KEY="${KMA_APIHUB_KEY:-}"
if [ -z "$KEY" ]; then echo "KMA_APIHUB_KEY 가 비어 있습니다"; exit 1; fi

B="https://apihub.kma.go.kr/api/typ01"
# 자료 지연을 감안해 70분 전
T2=$(date -v-70M +%Y%m%d%H%M 2>/dev/null || date -u -d '-70 minutes' +%Y%m%d%H%M)
T1=$(date -v-80M +%Y%m%d%H%M 2>/dev/null || date -u -d '-80 minutes' +%Y%m%d%H%M)
TH=$(date -v-70M +%Y%m%d%H00 2>/dev/null || date -u -d '-70 minutes' +%Y%m%d%H00)

probe () {   # probe <이름> <URL>
  printf '\n──────────────────────────────────────────────\n%s\n' "$1"
  out=$(curl -s --max-time 30 "$2" | iconv -f euc-kr -t utf-8 2>/dev/null)
  if [ -z "$out" ]; then echo "  ❌ 빈 응답 (통신 실패)"; return; fi
  if printf '%s' "$out" | grep -q '"status"'; then
    echo "  ❌ 거부됨 — 활용신청 상태 확인 필요"
    printf '%s' "$out" | head -3 | sed 's/^/     /'
    return
  fi
  echo "  ✅ 응답 옴 · 총 $(printf '%s\n' "$out" | grep -cv '^#') 행"
  echo "  [컬럼 이름]"
  printf '%s\n' "$out" | grep '^#' | grep 'STN' | head -2 | sed 's/^/     /'
  echo "  [자료 2줄]"
  printf '%s\n' "$out" | grep -v '^#' | grep -v '^$' | head -2 | sed 's/^/     /'
}

echo "기준시각  분자료 ${T1}~${T2} / 정시 ${TH}"

probe "1.1 AWS 매분자료  (nph-aws2_min)" \
  "$B/cgi-bin/url/nph-aws2_min?tm1=$T1&tm2=$T2&stn=0&disp=0&help=1&authKey=$KEY"

probe "3.x AWS 시간통계  (awsh.php)  ← RE_SUM 이 여기 있어야 합니다" \
  "$B/url/awsh.php?tm=$TH&stn=0&help=1&authKey=$KEY"

probe "1.8 AWS2 현천자료 (nph-aws2_min_ww1)" \
  "$B/cgi-bin/url/nph-aws2_min_ww1?tm1=$T1&tm2=$T2&stn=0&disp=0&help=1&authKey=$KEY"

probe "1.3 AWS 운고운량  (nph-aws2_min_cloud)" \
  "$B/cgi-bin/url/nph-aws2_min_cloud?tm1=$T1&tm2=$T2&stn=0&disp=0&help=1&authKey=$KEY"

probe "1.6 AWS2 시정자료 (nph-aws2_min_vis)" \
  "$B/cgi-bin/url/nph-aws2_min_vis?tm1=$T1&tm2=$T2&stn=0&disp=0&help=1&authKey=$KEY"

probe "지점정보 AWS      (nph-stn_inf inf=AWS)" \
  "$B/cgi-bin/url/nph-stn_inf?inf=AWS&tm=$T2&stn=&help=1&authKey=$KEY"

printf '\n──────────────────────────────────────────────\n'
echo "위 [컬럼 이름] 줄들을 그대로 복사해서 알려주시면 파서를 확정하겠습니다."
