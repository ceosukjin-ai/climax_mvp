#!/usr/bin/env bash
# 일본 주요 도시권 건물 → PostGIS (2026-09-12). 전국(8천만 동, 40~60GB)은 디스크가 안 되므로 도시권부터.
#   nohup bash scripts/run_japan_bldg.sh > ~/jp_bldg.log 2>&1 &
# 사전: ~/data/japan-latest.osm.pbf (Geofabrik), osmium-tool
# 각 권역마다 extract → tags-filter → export → DB적재 → 중간파일 삭제(디스크 절약).
set -u
cd "$(dirname "$0")/.."
PBF=${PBF:-$HOME/data/japan-latest.osm.pbf}
DC="docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml run --rm \
 --memory 2500m -v $HOME/climax_mvp:/repo -v $HOME/data:/data api"
one() {  # one 이름 S W N E
  local name=$1 S=$2 W=$3 N=$4 E=$5 t=$HOME/data/_jp
  echo "=== $name 시작 $(date '+%F %T')"
  osmium extract -b $W,$S,$E,$N "$PBF" -o $t.osm.pbf --overwrite -q || { echo "  extract 실패"; return; }
  osmium tags-filter $t.osm.pbf w/building a/building -o $t.b.osm.pbf --overwrite -q
  osmium export -f geojsonseq --add-unique-id=type_id $t.b.osm.pbf -o $t.geojsonl --overwrite -q
  $DC python3 /repo/scripts/load_osm_bldg_to_db.py /data/_jp.geojsonl --mark-bbox $S $W $N $E --src osm-jp
  # 가로수 — 직사광을 막는 건 건물만이 아니다(並木道). 개별 나무 좌표라야 "머리 위" 판정이 된다.
  osmium tags-filter $t.osm.pbf n/natural=tree w/natural=tree_row -o $t.t.osm.pbf --overwrite -q
  osmium export -f geojsonseq --add-unique-id=type_id $t.t.osm.pbf -o $t.t.geojsonl --overwrite -q
  $DC python3 /repo/scripts/load_osm_trees.py /data/_jp.t.geojsonl --src osm-jp
  rm -f $t.osm.pbf $t.b.osm.pbf $t.geojsonl $t.t.osm.pbf $t.t.geojsonl
  echo "=== $name 끝  $(date '+%F %T')  디스크 $(df -h / | awk 'NR==2{print $5}')"
}
one 도쿄권   35.35 139.20 36.10 140.20
one 오사카권 34.55 135.00 35.10 135.90
one 나고야   35.00 136.75 35.30 137.10
one 후쿠오카 33.50 130.28 33.72 130.55
one 삿포로   42.95 141.25 43.15 141.50
one 센다이   38.20 140.80 38.35 141.05
one 히로시마 34.33 132.35 34.45 132.55
echo "=== 일본 도시권 완료 $(date '+%F %T')"
