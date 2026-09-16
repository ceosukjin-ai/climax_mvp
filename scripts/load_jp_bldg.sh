#!/usr/bin/env bash
# 일본 도시권 건물 → PostGIS bldg_poly (2026-09-16).
#
# 왜 전국이 아니라 도시권인가: bldg_poly 는 지금 동당 약 1.8 KB 다(459만 동 / 8.4 GB).
#   일본 OSM 건물은 정부 자료가 통째로 들어와 약 8,500만 동 — 그대로 넣으면 155 GB 로
#   80 GB 디스크에 애초에 안 들어간다. 그래서 **사람이 사는 곳부터** 넣는다.
#   건물이 없는 곳도 --mark-bbox 로 "적재 완료"를 찍어 두므로, 안 넣은 곳은
#   실시간 폴백이 아니라 그냥 개활로 답한다(시골은 실제로 하늘이 열려 있다).
#
# 디스크: 도로망과 같은 방식 — osmium 출력을 파이프로 바로 넣어 중간 파일을 만들지 않는다.
#
#   bash scripts/load_jp_bldg.sh yokohama
#   AREAS="yokohama osaka nagoya" nohup bash scripts/load_jp_bldg.sh > ~/jp_bldg.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
D=${D:-$HOME/data}; mkdir -p "$D"
COMPOSE="docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml"

# 이름  지방   S     W       N     E
area_def() {
  case "$1" in
    yokohama) echo "kanto  35.30 139.45 35.62 139.80" ;;   # 요코하마·가와사키
    chiba)    echo "kanto  35.55 139.90 35.75 140.15" ;;
    saitama)  echo "kanto  35.82 139.40 36.00 139.80" ;;
    osaka)    echo "kansai 34.55 135.35 34.80 135.60" ;;
    kyoto)    echo "kansai 34.93 135.68 35.08 135.83" ;;
    kobe)     echo "kansai 34.63 135.10 34.75 135.30" ;;
    nagoya)   echo "chubu  35.05 136.83 35.25 137.05" ;;
    fukuoka)  echo "kyushu 33.53 130.30 33.66 130.48" ;;
    sapporo)  echo "hokkaido 43.00 141.25 43.12 141.45" ;;
    *) echo ""; ;;
  esac
}

free_gb() { df -BG --output=avail / | tail -1 | tr -dc '0-9'; }

for A in ${AREAS:-${1:-yokohama}}; do
  DEF=$(area_def "$A")
  [ -z "$DEF" ] && { echo "!! 모르는 구역: $A"; continue; }
  set -- $DEF; R=$1; S=$2; W=$3; N=$4; E=$5
  echo "=== $A ($R) $S,$W ~ $N,$E   $(date '+%F %T')  여유 $(free_gb)GB"
  [ "$(free_gb)" -lt 4 ] && { echo "!! 디스크 부족 — 중단"; exit 1; }

  wget -q -O "$D/r.osm.pbf" "https://download.geofabrik.de/asia/japan/$R-latest.osm.pbf" \
    || { echo "  받기 실패"; rm -f "$D/r.osm.pbf"; continue; }
  osmium extract -b "$W,$S,$E,$N" -o "$D/a.osm.pbf" "$D/r.osm.pbf" && rm -f "$D/r.osm.pbf"
  osmium tags-filter -o "$D/ab.osm.pbf" "$D/a.osm.pbf" w/building a/building && rm -f "$D/a.osm.pbf"

  osmium export -f geojsonseq --add-unique-id=type_id -o - "$D/ab.osm.pbf" \
    | nice -n 19 $COMPOSE run --rm -i -v "$HOME/climax_mvp:/repo" api \
        python3 /repo/scripts/load_osm_bldg_to_db.py - --mark-bbox "$S" "$W" "$N" "$E" --src "osm-jp-$A"
  echo "  적재 rc=${PIPESTATUS[1]}"
  rm -f "$D/ab.osm.pbf"
  echo "=== $A 끝  $(date '+%F %T')  여유 $(free_gb)GB"
done
