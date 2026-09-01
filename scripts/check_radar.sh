#!/usr/bin/env bash
# 레이더 API 확인.  맥 터미널에서:  cd ~/Desktop/climax_mvp && bash scripts/check_radar.sh
#
# 1순위는 AWS 지점별 레이더값이다 — 지상 관측과 같은 지점이라 바로 대조할 수 있고,
# 격자(13~46MB)를 5분마다 받는 무거운 구조가 필요 없다.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./backend/.env; set +a
KEY="${KMA_APIHUB_KEY:-}"; [ -z "$KEY" ] && { echo "키 없음"; exit 1; }

B="https://apihub.kma.go.kr/api/typ01"
B2="https://apihub.kma.go.kr/api/typ02/openApi"

# 레이더는 5분 주기. 생산 지연을 감안해 40분 전으로 내림.
RAW=$(date -v-40M +%Y%m%d%H%M 2>/dev/null || date -u -d '-40 minutes' +%Y%m%d%H%M)
MM=$(( 10#${RAW:10:2} / 5 * 5 ))
TM="${RAW:0:10}$(printf '%02d' "$MM")"
TM1="${RAW:0:10}00"

probe () {   # probe <이름> <URL> [바이트]
  printf '\n──────────────────────────────────────────────\n%s\n' "$1"
  out=$(curl -s --max-time 40 "$2" 2>/dev/null | head -c "${3:-1500}" \
        | iconv -f euc-kr -t utf-8 2>/dev/null || true)
  [ -z "$out" ] && out=$(curl -s --max-time 40 "$2" 2>/dev/null | head -c "${3:-1500}" | tr -cd '\11\12\15\40-\176')
  if [ -z "$out" ]; then echo "  ❌ 빈 응답"; return; fi
  if printf '%s' "$out" | grep -qi '"status"\|returnReasonCode\|등록되지\|SERVICE.*ERROR'; then
    echo "  ❌ 거부/미제공"; printf '%s' "$out" | head -c 300 | sed 's/^/     /'; echo; return
  fi
  echo "  ✅ 응답 옴"
  printf '%s' "$out" | sed 's/^/     /'
  echo
}

echo "기준시각 $TM"

probe "★ 전 지점 레이더값 · HSP (nph-rdr_cmp_aws_all_pt_data)" \
  "$B/cgi-bin/url/nph-rdr_cmp_aws_all_pt_data?tm=$TM&qcd=EXT&cmp=HSP&help=1&authKey=$KEY" 1800

probe "★ 전 지점 레이더값 · HSR 로도 되나" \
  "$B/cgi-bin/url/nph-rdr_cmp_aws_all_pt_data?tm=$TM&qcd=MSK&cmp=HSR&help=1&authKey=$KEY" 1200

probe "한 지점 시계열 (nph-rdr_cmp_aws_pt_data) — 부산 159" \
  "$B/cgi-bin/url/nph-rdr_cmp_aws_pt_data?tm1=$TM1&tm2=$TM&itv=5&qcd=EXT&cmp=HSP&stn=159&help=1&authKey=$KEY" 1500

probe "2.1 격자 메타정보 (nph-rdr_cmp_inf)" \
  "$B/cgi-bin/url/nph-rdr_cmp_inf?tm=$TM&cmp=HSR&qcd=MSK&authKey=$KEY" 900

probe "2.2.1 HSR 격자 앞부분만 (nph-rdr_cmp1_api)" \
  "$B/cgi-bin/url/nph-rdr_cmp1_api?tm=$TM&cmp=HSR&qcd=MSK&obs=ECHO&map=HB&disp=A&authKey=$KEY" 300

probe "3.2 행정구역 조회 — 서울 샘플" \
  "$B2/WthrRadarInfoService/getCompCappiQcdArea?pageNo=1&numOfRows=5&dataType=JSON&dateTime=$TM&compType=HSP&dataTypeCd=RN&dongCode=1100000000&authKey=$KEY" 1000

printf '\n──────────────────────────────────────────────\n'
echo "4.2 격자 위경도 NetCDF (4.1 이 error(-11) 이라 대안 확인)"
hdr=$(curl -sI --max-time 40 "$B/url/rdr_latlon_file_down.php?cmp=HSR&authKey=$KEY" 2>/dev/null \
      | tr -d '\r' | grep -iE 'HTTP/|content-type|content-length|content-disposition')
if [ -n "$hdr" ]; then printf '%s\n' "$hdr" | sed 's/^/     /'; else echo "     ❌ 응답 없음"; fi

probe "3.2 행정구역 — 다른 조합 (CPP + CZ)" \
  "$B2/WthrRadarInfoService/getCompCappiQcdArea?pageNo=1&numOfRows=5&dataType=JSON&dateTime=$TM&compType=CPP&dataTypeCd=CZ&dongCode=1100000000&authKey=$KEY" 800

printf '\n──────────────────────────────────────────────\n'
echo "맨 위 두 개(★)가 살아 있으면 격자 없이 바로 붙일 수 있습니다."
