#!/usr/bin/env bash
# 일본 전국 보행 도로망·녹지 → PostGIS osm_way (2026-09-16).
#
# 왜: 도쿄에 건물은 191만 동 있는데 osm_way 가 0 이었다. 그 결과 두 가지가 동시에 망가져 있었다.
#   1) `/route/shade` 의 건물 그늘이 통째로 꺼져 있었다 — build_graph 는 스카이라인 격자에서만
#      건물 그늘을 읽고, 그 격자는 osm_way 에서 도로 근처 칸을 뽑아 만든다.
#   2) 경로 응답이 6.4초였다 — 일본은 도로망이 DB 에 없어 매 요청마다 Overpass 를 때렸다.
#
# 디스크: WAS 여유가 10 GB 뿐이라 중간 geojsonl(수 GB)을 만들면 먼저 찬다.
#   그래서 osmium 출력을 **파이프로 바로** load_osm_ways.py 에 넣는다(`-` 인자).
#   피크 용량 = 내려받은 pbf 한 개(최대 약 1 GB). 각 단계가 끝나면 즉시 지운다.
#
# ⚠️ --replace 를 쓰지 않는다. 테이블이 하나라 한국 도로가 지워진다. 같은 id 는 UPSERT 된다.
#
#   nohup bash scripts/load_jp_ways.sh > ~/jp_ways.log 2>&1 &
#   tail -f ~/jp_ways.log
#   중단 후 다시 돌려도 안전하다(이미 넣은 way 는 UPSERT).
set -u
cd "$(dirname "$0")/.."
D=${D:-$HOME/data}; mkdir -p "$D"
# 인구 많은 곳부터. 중간에 멈춰도 쓸모 있는 순서여야 한다.
REGIONS=${REGIONS:-"kanto kansai chubu kyushu tohoku chugoku shikoku hokkaido"}
MIN_FREE_GB=${MIN_FREE_GB:-4}
COMPOSE="docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml"

free_gb() { df -BG --output=avail / | tail -1 | tr -dc '0-9'; }

for R in $REGIONS; do
  echo "=== $R  $(date '+%F %T')  여유 $(free_gb)GB"
  if [ "$(free_gb)" -lt "$MIN_FREE_GB" ]; then
    echo "!! 디스크 여유 $(free_gb)GB < ${MIN_FREE_GB}GB — 중단한다. 9/11 처럼 운영을 멈추지 않기 위해서다."
    exit 1
  fi
  wget -q --show-progress -O "$D/r.osm.pbf" \
    "https://download.geofabrik.de/asia/japan/$R-latest.osm.pbf" || { echo "  받기 실패 — 건너뜀"; rm -f "$D/r.osm.pbf"; continue; }
  ls -lh "$D/r.osm.pbf"

  osmium tags-filter -o "$D/rw.osm.pbf" "$D/r.osm.pbf" \
    w/highway=footway,path,pedestrian,steps,living_street,residential,service,unclassified,tertiary,tertiary_link,secondary,secondary_link,primary,primary_link,track,cycleway \
    w/leisure=park,garden,playground,recreation_ground,nature_reserve \
    w/landuse=grass,forest,meadow,recreation_ground,cemetery,orchard,village_green,reservoir \
    w/natural=water,wood,scrub,grassland,heath,wetland,beach || { echo "  거르기 실패"; rm -f "$D"/r.osm.pbf "$D"/rw.osm.pbf; continue; }
  rm -f "$D/r.osm.pbf"

  # 파일을 남기지 않고 바로 적재. 파이프가 끊기면 osmium 이 SIGPIPE 로 죽으므로 rc 를 확인한다.
  osmium export -f geojsonseq --add-unique-id=type_id -o - "$D/rw.osm.pbf" \
    | nice -n 19 $COMPOSE run --rm -i -v "$HOME/climax_mvp:/repo" api \
        python3 /repo/scripts/load_osm_ways.py -
  echo "  적재 rc=${PIPESTATUS[1]}"
  rm -f "$D/rw.osm.pbf"
  echo "=== $R 끝  $(date '+%F %T')  여유 $(free_gb)GB"
done
echo "전부 끝: $(date '+%F %T')"
