#!/usr/bin/env bash
# 스카이라인 격자 배치 드라이버 (2026-09-11). 위도 0.1° 띠로 잘라 병렬 실행, 각 띠는 --resume 이라 중단 후 재실행 가능.
#
# ⚠️ 2026-09-11 사고: 한도 없이 8개를 돌려 16GB 를 소진 → 서버 다운(운영 앱 4시간 정지, 재부팅으로 복구).
#    그래서 배치는 반드시 **메모리 한도 + 낮은 우선순위 + 소수 병렬**로 돈다.
#    `docker compose run` 에는 --memory 옵션이 없어서 `docker run` 을 직접 쓴다(이미지: climax-backend:latest).
#    DB 는 2026-09-11 부터 별도 서버(lbs-climax-db)라 compose 네트워크가 필요 없다.
#
#   타일:  nohup bash scripts/run_grid_kr.sh tiles > ~/tiles_kr.log 2>&1 &
#   격자:  nohup bash scripts/run_grid_kr.sh grid  > ~/grid_kr.log  2>&1 &
#   부산만: S=35.05 N=35.30 W=128.95 E=129.25 nohup bash scripts/run_grid_kr.sh grid > ~/grid_busan.log 2>&1 &
#   진행:  tail -n 2 ~/grid_kr_*.log      중단: pkill -f build_skyline_grid; docker ps -q --filter ancestor=climax-backend | xargs -r docker kill
set -u
cd "$(dirname "$0")/.."
S=${S:-33.10}; N=${N:-38.65}; W=${W:-125.90}; E=${E:-129.60}; STEP=${STEP:-0.1}
PAR=${PAR:-4}; MEM=${MEM:-1500m}; CPUS=${CPUS:-1.0}; IMG=${IMG:-climax-backend:latest}
ENVF=infra/ncp/.env.prod
val() { grep -m1 "^$1=" "$ENVF" | cut -d= -f2-; }
DBURL="postgresql+asyncpg://climax:$(val DB_PASSWORD)@$(val DB_HOST):5432/climax"
DC="nice -n 19 docker run --rm --memory $MEM --memory-swap $MEM --cpus $CPUS \
 -e DATABASE_URL=$DBURL -e VWORLD_API_KEY=$(val VWORLD_API_KEY) \
 -e LOCAL_TILE_CACHE_MAX=${TILE_CACHE:-24} -e RINGS_CACHE_MAX=${RING_CACHE:-3000} \
 -v $HOME/climax_mvp:/repo -v $HOME/climax_mvp/backend/data/buildings:/app/data/buildings:ro $IMG"

case "${1:-}" in
  tiles)
    $DC python3 /repo/scripts/fetch_vworld_tiles.py --bbox $S $W $N $E --threads 4 --out /repo/backend/data/buildings ;;
  grid)
    python3 -c "
s,n,st=$S,$N,$STEP
x=s
while x<n:
    print(f'{x:.2f} {min(x+st,n):.2f}'); x=round(x+st,2)
" | xargs -P $PAR -L 1 bash -c '
      s=$0; n=$1; tag=${s/./_}
      '"$DC"' python3 /repo/scripts/build_skyline_grid.py --bbox $s '"$W"' $n '"$E"' \
        --step 0.0002 --threads 2 --resume --near-roads 25 --tiles-only --src-hint vwtile+reg \
        > ~/grid_kr_$tag.log 2>&1
      echo "띠 $s~$n 끝: $(tail -n 1 ~/grid_kr_$tag.log)"' ;;
  *) echo "usage: $0 tiles|grid"; exit 1 ;;
esac
