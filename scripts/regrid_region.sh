#!/usr/bin/env bash
# 권역 격자 재계산 (2026-09-21) — "DB 에 이미 있는 칸" 만 새 판으로 다시 계산한다.
#
# 왜 --cells 인가: --bbox 로 새로 만들면 9/11 격자 스냅 이전 행과 칸 번호가 어긋나 유령 행이 생긴다
# (부산 7,440개). DB 에 있는 칸을 그대로 뽑아 다시 쓰면 같은 cell_id 에 덮어쓴다 — 서울은 유령 1개.
#
#   1) 세기     bash scripts/regrid_region.sh count  <이름> S N W E
#   2) 뽑기     bash scripts/regrid_region.sh export <이름> S N W E [조각수=3]
#   3) 띄우기   bash scripts/regrid_region.sh run    <이름> [조각수=3]
#   진행        tail -n 1 ~/grid_<이름>_*.log
#   검수        docker exec -i -e BBOX=S,N,W,E climax-api python3 /tmp/grid_region_audit.py
#
# 안전장치: 조각당 --memory 2500m --cpus 2.0 --threads 4, nice 19, setsid(ssh 끊겨도 산다),
# V-World 키를 넘기지 않는다(--tiles-only + 키 없음 = 외부 호출 원천 차단, 9/20 406 사태 재발 방지).
set -u
cd "$(dirname "$0")/.."
NEW=${NEW:-vwtile+reg+canopy-260920}
ENVF=infra/ncp/.env.prod
val() { grep -m1 "^$1=" "$ENVF" | cut -d= -f2-; }
PSQL="docker exec -i -e PGPASSWORD=$(val DB_PASSWORD) climax-postgres psql -h $(val DB_HOST) -U climax -d climax -At"
cmd=$1; name=$2; shift 2
where() { echo "lat BETWEEN $1 AND $2 AND lon BETWEEN $3 AND $4 AND src IS DISTINCT FROM '$NEW'"; }

case "$cmd" in
  count)
    $PSQL -c "SELECT src, count(*) FROM skyline_grid WHERE $(where "$@") GROUP BY src ORDER BY 2 DESC" ;;
  export)
    K=${5:-3}
    $PSQL -c "\\copy (SELECT lat, lon FROM skyline_grid WHERE $(where "$1" "$2" "$3" "$4") ORDER BY lon, lat) TO STDOUT WITH CSV HEADER" > "data/regrid_${name}.csv"
    n=$(( $(wc -l < "data/regrid_${name}.csv") - 1 ))
    per=$(( (n + K - 1) / K ))
    for i in $(seq 1 "$K"); do
      { echo "lat,lon"; tail -n +2 "data/regrid_${name}.csv" | sed -n "$(( (i-1)*per + 1 )),$(( i*per ))p"; } > "data/regrid_${name}_${i}.csv"
      echo "  조각 $i: $(( $(wc -l < data/regrid_${name}_${i}.csv) - 1 ))칸"
    done
    echo "총 ${n}칸 → data/regrid_${name}_1..${K}.csv (경도순으로 잘라 조각끼리 타일을 덜 겹치게)" ;;
  run)
    K=${1:-3}
    DBURL="postgresql+asyncpg://climax:$(val DB_PASSWORD)@$(val DB_HOST):5432/climax"
    for i in $(seq 1 "$K"); do
      setsid nohup nice -n 19 docker run --rm --memory 2500m --memory-swap 2500m --cpus 2.0 \
        -e DATABASE_URL="$DBURL" -e BUILDING_SOURCE=db \
        -e LOCAL_TILE_CACHE_MAX=24 -e RINGS_CACHE_MAX=1500 \
        -v "$HOME/climax_mvp:/repo" -v "$HOME/climax_mvp/backend/data/buildings:/app/data/buildings:ro" \
        -v "$HOME/climax_mvp/data/canopy:/app/data/canopy:ro" climax-backend:latest \
        python3 /repo/scripts/build_skyline_grid.py --cells "/repo/data/regrid_${name}_${i}.csv" \
          --threads 4 --force --tiles-only --src-hint "$NEW" \
        < /dev/null > "$HOME/grid_${name}_${i}.log" 2>&1 &
      echo "  띄움: 조각 $i → ~/grid_${name}_${i}.log"
      sleep 2
    done ;;
  *) echo "count | export | run"; exit 1 ;;
esac
