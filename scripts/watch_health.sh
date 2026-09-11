#!/usr/bin/env bash
# 서버 자가 감시 (2026-09-11 다운 사고 후). 5분마다 API 와 메모리를 보고, 위험하면 배치를 스스로 끈다.
#   crontab -e  →  */5 * * * * bash /home/ubuntu/climax_mvp/scripts/watch_health.sh >> /home/ubuntu/watch_health.log 2>&1
# 사람이 볼 알림(메일/카톡)은 나중에. 우선 "앱이 죽기 전에 배치를 죽인다"가 목적.
set -u
URL=${URL:-http://127.0.0.1:8000/api/v1/health}
MEM_LIMIT=${MEM_LIMIT:-85}          # 사용률 % 상한
ts() { date "+%F %T"; }
used=$(free | awk '/^Mem:/{printf "%d", ($2-$7)*100/$2}')
code=$(curl -s -m 10 -o /dev/null -w "%{http_code}" "$URL" || echo 000)
if [ "$used" -ge "$MEM_LIMIT" ] || [ "$code" != "200" ]; then
  n=$(docker ps -q --filter name=ncp-api-run | wc -l)
  if [ "$n" -gt 0 ]; then
    echo "$(ts) 위험(mem ${used}% / health ${code}) → 배치 ${n}개 중단"
    docker ps -q --filter name=ncp-api-run | xargs -r docker kill
    pkill -f build_skyline_grid; pkill -f fetch_vworld_tiles
  else
    echo "$(ts) 경고(mem ${used}% / health ${code}) — 배치는 이미 없음"
  fi
else
  echo "$(ts) ok mem ${used}% health ${code}"
fi
