#!/usr/bin/env bash
# 1차 확인에서 남은 두 가지를 본다.
#   ① awsh.php 에 var=RN 을 주면 RE_SUM(강수 감지 분수)이 오는가
#   ② sfc_aws_day.php 로 AWS 지점 좌표표를 한 번에 받을 수 있는가
#
#   cd ~/Desktop/climax_mvp && bash scripts/check_aws2.sh
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./backend/.env; set +a
KEY="${KMA_APIHUB_KEY:-}"; [ -z "$KEY" ] && { echo "키 없음"; exit 1; }

B="https://apihub.kma.go.kr/api/typ01"
TH=$(date -v-70M +%Y%m%d%H00 2>/dev/null || date -u -d '-70 minutes' +%Y%m%d%H00)
YD=$(date -v-1d +%Y%m%d 2>/dev/null || date -u -d '-1 day' +%Y%m%d)

probe () {
  printf '\n──────────────────────────────────────────────\n%s\n' "$1"
  out=$(curl -s --max-time 30 "$2" | iconv -f euc-kr -t utf-8 2>/dev/null)
  [ -z "$out" ] && { echo "  ❌ 빈 응답"; return; }
  if printf '%s' "$out" | grep -q '"status"'; then
    echo "  ❌ 거부/미제공"; printf '%s' "$out" | head -4 | sed 's/^/     /'; return
  fi
  echo "  ✅ 응답 옴 · 총 $(printf '%s\n' "$out" | grep -cv '^#') 행"
  echo "  [컬럼 이름]"
  printf '%s\n' "$out" | grep '^#' | grep 'STN' | head -2 | sed 's/^/     /'
  echo "  [자료 2줄]"
  printf '%s\n' "$out" | grep -v '^#' | grep -v '^$' | head -2 | sed 's/^/     /'
}

probe "① awsh.php  var=RN  ← RE_SUM 있는지" \
  "$B/url/awsh.php?var=RN&tm=$TH&stn=0&help=1&authKey=$KEY"

probe "② sfc_aws_day.php  ← 지점 좌표(LON/LAT) 나오는지" \
  "$B/url/sfc_aws_day.php?tm2=$YD&obs=rn_day&stn=0&disp=0&help=1&authKey=$KEY"

printf '\n──────────────────────────────────────────────\n'
echo "①에 RE_SUM 이 보이면 약한 비 판정이 '분 단위'로 정밀해집니다."
echo "②에 LON/LAT 이 보이면 AWS 전 지점 좌표를 한 번에 채울 수 있습니다."
