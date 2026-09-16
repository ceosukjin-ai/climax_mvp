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
#   도시 하나:  bash scripts/load_jp_bldg.sh yokohama
#   일본 전국:  AREAS="all-kanto all-kansai all-chubu all-kyushu all-tohoku all-chugoku all-shikoku all-hokkaido" \
#                 nohup bash scripts/load_jp_bldg.sh > ~/jp_bldg_all.log 2>&1 &
#   ⚠️ 전국은 DB 쓰기가 무겁다. 스카이라인 격자 배치와 **같이 돌리지 말 것** — 둘 다 같은 DB 를 때린다.
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
    # 지방 통째로 (2026-09-16). 도시만 넣었더니 그 사이와 시골이 비어 답이 안 나온다
    # (벳푸·나하·후지산기슭·지치부 산간 모두 실패). bldg_poly 는 동당 0.41 KB
    # (2,056만 동 / 8.4 GB)라 일본 전국 약 8,500만 동이 35 GB — 80 GB 안에 들어간다.
    # ⚠️ 2026-09-16 정정: 앞서 동당 1.8 KB 로 보고 "전국 155 GB, 불가"라고 판단했다.
    #    459만 동으로 나눈 계산 착오였다. 전국이 가능하다.
    # bbox 는 각 지방을 넉넉히 덮는다. 겹쳐도 UPSERT 라 문제없다.
    all-hokkaido) echo "hokkaido 41.20 139.20 45.70 146.10" ;;
    all-tohoku)   echo "tohoku   36.70 139.00 41.70 142.20" ;;
    all-kanto)    echo "kanto    34.80 138.30 37.30 141.00" ;;
    all-chubu)    echo "chubu    34.40 135.70 38.70 140.00" ;;
    all-kansai)   echo "kansai   33.30 133.90 36.50 136.60" ;;
    all-chugoku)  echo "chugoku  33.70 130.70 36.10 134.60" ;;
    all-shikoku)  echo "shikoku  32.60 131.90 34.60 134.90" ;;
    all-kyushu)   echo "kyushu   23.90 122.80 34.90 132.20" ;;   # 오키나와 포함
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
