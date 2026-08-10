#!/bin/bash
# ClimaX — ASOS 시간자료 수신 가능성 테스트 (부산 159)
# 사용법:  cd ~/Desktop/climax_mvp && bash scripts/test_asos.sh
# 확인 내용:
#   [테스트1] 공공데이터포털 AsosHourlyInfoService — 기존 KMA_API_KEY로 열리는지 + 당일 자료 지연 여부
#   [테스트2] 기상청 API허브 지상관측(kma_sfctm2) — APIHUB_KEY 환경변수 있으면 실시간 수신 테스트
set -u

KEY=$(grep '^KMA_API_KEY=' backend/.env | cut -d= -f2-)
if [ -z "$KEY" ]; then echo "backend/.env 에서 KMA_API_KEY를 못 찾았습니다"; exit 1; fi
ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$KEY")

TODAY=$(date +%Y%m%d)
YESTERDAY=$(date -v-1d +%Y%m%d 2>/dev/null || date -d yesterday +%Y%m%d)
HOUR_NOW=$(date +%H)
BASE="https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"

echo "=============================================="
echo "[테스트 1-A] 어제(${YESTERDAY}) 05~08시 자료 — 서비스 등록 여부 확인"
echo "=============================================="
R=$(curl -s --max-time 20 "${BASE}?serviceKey=${ENC}&pageNo=1&numOfRows=5&dataType=JSON&dataCd=ASOS&dateCd=HR&startDt=${YESTERDAY}&startHh=05&endDt=${YESTERDAY}&endHh=08&stnIds=159")
echo "$R" | head -c 600; echo; echo
if echo "$R" | grep -q "SERVICE_KEY_IS_NOT_REGISTERED\|SERVICE ERROR"; then
  echo "→ ❌ 이 키는 ASOS 시간자료 서비스에 활용신청이 안 돼 있음."
  echo "   data.go.kr 로그인 → '지상(종관, ASOS) 시간자료 조회서비스' 활용신청(즉시 승인) 후 재실행."
elif echo "$R" | grep -q '"icsr"'; then
  echo "→ ✅ 서비스 열림. 일사(icsr)·전운량(dc10Tca)·지면온도(ts) 필드 확인:"
  echo "$R" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for it in d['response']['body']['items']['item']:
    print(f\"  {it['tm']}  기온 {it.get('ta')}  일사 {it.get('icsr')}MJ  전운량 {it.get('dc10Tca')}/10  지면온도 {it.get('ts')}\")"
else
  echo "→ ⚠ 예상 밖 응답 — 위 원문 확인 필요."
fi

echo
echo "=============================================="
echo "[테스트 1-B] 오늘(${TODAY}) 자료 — 실시간(당일) 제공 여부 확인 ★핵심★"
echo "=============================================="
R2=$(curl -s --max-time 20 "${BASE}?serviceKey=${ENC}&pageNo=1&numOfRows=24&dataType=JSON&dataCd=ASOS&dateCd=HR&startDt=${TODAY}&startHh=00&endDt=${TODAY}&endHh=${HOUR_NOW}&stnIds=159")
CNT=$(echo "$R2" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); print(d['response']['body'].get('totalCount',0))
except Exception: print(0)")
echo "현재 시각 ${HOUR_NOW}시 기준, 오늘 자료 ${CNT}건 수신"
if [ "$CNT" -gt 0 ]; then
  LAST=$(echo "$R2" | python3 -c "
import sys,json
d=json.load(sys.stdin); print(d['response']['body']['items']['item'][-1]['tm'])")
  echo "→ ✅ 당일 자료 제공됨. 최신 시각: ${LAST} (현재와의 차이 = 실제 지연)"
else
  echo "→ ❌ 당일 자료 없음 = 이 서비스는 지연 제공."
  echo "   실시간 보정용으로는 기상청 API허브(apihub.kma.go.kr) 키를 새로 받아야 함 → [테스트 2]"
fi

echo
echo "=============================================="
echo "[테스트 2] API허브 실시간 지상관측 (APIHUB_KEY 있을 때만)"
echo "=============================================="
if [ -n "${APIHUB_KEY:-}" ]; then
  TM=$(date -v-1H +%Y%m%d%H00 2>/dev/null || date -d '1 hour ago' +%Y%m%d%H00)
  curl -s --max-time 20 "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php?tm=${TM}&stn=159&help=1&authKey=${APIHUB_KEY}" | head -40
  echo "→ 위 표에서 SI(일사)·CA(전운량)·TS(지면온도) 컬럼과 관측시각 확인"
else
  echo "(건너뜀) apihub.kma.go.kr 무료 회원가입 → 키 발급 후:"
  echo "  APIHUB_KEY=발급키 bash scripts/test_asos.sh"
fi
