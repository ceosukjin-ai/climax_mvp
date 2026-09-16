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

# health 는 **두 번 연속** 실패해야 위험으로 본다 (2026-09-16).
#   배포(deploy.sh)는 이미지를 다시 빌드하고 API 를 재시작한다. 그 사이 몇십 초 동안
#   health 가 200 이 아니다. 예전 규칙은 그걸 사고로 보고 **돌던 배치를 전부 죽였다.**
#   2026-09-16 에 도쿄 격자 3띠(약 20만 칸)와 전국 격자 3띠가 이렇게 조용히 사라졌다.
#   로그에도 "위험 → 배치 중단"만 남아 배포 때문인지 알 수 없었다.
#   메모리는 그대로 즉시 판정한다 — 그건 진짜로 급하고, 9/11 사고의 원인이었다.
FLAG=${FLAG:-/tmp/watch_health_bad}
if [ "$code" = "200" ]; then
  rm -f "$FLAG"
  bad=0
else
  if [ -f "$FLAG" ]; then bad=1; else touch "$FLAG"; bad=0
    echo "$(ts) health ${code} — 1회차, 배포 중일 수 있어 기다린다"
  fi
fi

if [ "$used" -ge "$MEM_LIMIT" ] || [ "$bad" = "1" ]; then
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
