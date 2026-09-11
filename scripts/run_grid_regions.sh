#!/usr/bin/env bash
# 광역권 순차 격자 배치 (2026-09-11). 사람이 사는 곳부터 — 전국 전수는 7천만 칸이라 며칠이 걸린다.
# 격자가 없는 지역도 앱은 정상 동작한다(요청 시 실시간 계산 후 저장). 격자는 "미리 채워두는 캐시".
#   nohup bash scripts/run_grid_regions.sh > ~/grid_regions.log 2>&1 &
#   진행: grep === ~/grid_regions.log  /  tail -n 1 ~/grid_kr_*.log
# 중단해도 --resume 이라 이어서 됨. 권역 추가는 아래 run 줄만 늘리면 된다.
set -u
cd "$(dirname "$0")/.."
export STEP=${STEP:-0.04} PAR=${PAR:-4} MEM=${MEM:-2500m}
run() {  # run 이름 S N W E
  echo "=== $1 시작 $(date '+%F %T')"
  S=$2 N=$3 W=$4 E=$5 bash scripts/run_grid_kr.sh grid
  echo "=== $1 끝  $(date '+%F %T')"
}
run 서울      37.40 37.72 126.76 127.20
run 인천·부천  37.34 37.60 126.56 126.80
run 경기남부   37.18 37.45 126.88 127.20
run 경기북부   37.58 37.80 126.70 127.15
run 부산      35.05 35.30 128.95 129.25
run 대구      35.78 35.95 128.45 128.72
run 대전·세종  36.27 36.62 127.20 127.52
run 광주      35.10 35.22 126.78 126.96
run 울산      35.50 35.62 129.25 129.40
run 창원      35.18 35.30 128.55 128.75
echo "=== 전 권역 완료 $(date '+%F %T')"
