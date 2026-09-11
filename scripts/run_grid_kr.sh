#!/usr/bin/env bash
# 전국 스카이라인 격자 배치 드라이버 (2026-09-11). 위도 0.1° 띠로 잘라 8개씩 병렬, 각 띠는 --resume 이라 중단 후 재실행 가능.
#   1) 타일:  nohup bash scripts/run_grid_kr.sh tiles  > ~/tiles_kr.log 2>&1 &
#   2) 격자:  nohup bash scripts/run_grid_kr.sh grid   > ~/grid_kr.log  2>&1 &
# 진행:  grep -h "완료" ~/grid_kr_*.log | wc -l   (총 띠 수는 아래 S..N 범위/0.1)
set -u
cd "$(dirname "$0")/.."
S=${S:-33.10}; N=${N:-38.65}; W=${W:-125.90}; E=${E:-129.60}; STEP=${STEP:-0.1}; PAR=${PAR:-8}
DC="docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml run --rm -v $HOME/climax_mvp:/repo api"
case "${1:-}" in
  tiles)
    $DC python3 /repo/scripts/fetch_vworld_tiles.py --bbox $S $W $N $E --threads 6 ;;
  grid)
    python3 -c "
s,n,st=$S,$N,$STEP
x=s
while x<n:
    print(f'{x:.2f} {min(x+st,n):.2f}'); x=round(x+st,2)
" | xargs -P $PAR -L 1 bash -c '
      s=$0; n=$1; tag=${s/./_}
      '"$DC"' python3 /repo/scripts/build_skyline_grid.py --bbox $s '"$W"' $n '"$E"' --step 0.0002 --threads 2 --resume --near-roads 25 --tiles-only --src-hint vwtile+reg > ~/grid_kr_$tag.log 2>&1
      echo "띠 $s~$n 끝: $(tail -n 1 ~/grid_kr_$tag.log)"' ;;
  *) echo "usage: $0 tiles|grid"; exit 1 ;;
esac
