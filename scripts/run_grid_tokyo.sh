#!/usr/bin/env bash
# 도쿄 23구 스카이라인 격자 — 4띠 병렬, 띠마다 5회 재시도 (2026-09-18, v3: PLATEAU 직접)
#
# 왜 v2 인가: 첫 격자(9/16~17, 14시간)는 OSM 건물 76.4% 가 높이 없이 기본 2층(6.94 m)으로
#   깔린 위에 계산됐다 (긴자 90.7%, 신주쿠 86.6%). 23구 SVF 평균 0.749 — 도쿄가 부산보다
#   열려 있다고 나왔다. PLATEAU 실측 높이 884,038동을 주입한 뒤(ingest_plateau_height.py)
#   긴자 SVF 0.841 → 0.635. 격자를 전부 다시 깐다.
#
# --force 가 아니라 **먼저 지우고 --resume** 인 이유: 띠가 OOM 으로 죽으면 재시도가 --resume 에
#   기대 이어받는다. --force 면 재시도마다 처음부터라 하루가 이틀이 된다.
#   → 실행 전에 도쿄 bbox 의 skyline_grid 를 DELETE 할 것 (아래 실행 순서).
#
# DB 주소는 살아 있는 climax-api 컨테이너에서 빌린다 — .env.prod 의 DATABASE_URL 은 localhost 고
#   compose run 은 WAS 로컬 postgres 에 붙는다(9/17 두 번 헛짚음). 비밀번호를 명령줄에 안 쓴다.
#
# 실행 (서버):
#   1) 지우기:  docker exec -i climax-api python3 -c "…DELETE FROM skyline_grid WHERE 도쿄 bbox…"
#   2) 띄우기:  setsid nohup bash scripts/run_grid_tokyo.sh < /dev/null > ~/grid_tokyo_v2_driver.log 2>&1 &
#   진행:       for f in ~/grid_tokyo_v2_*.log; do echo "$(basename $f): $(tail -n 1 $f)"; done
#   중단:       pkill -f 'run_grid_tokyo[.]sh'; docker ps -q --filter ancestor=climax-backend:latest | xargs -r docker kill
#
# ⚠️ 반드시 setsid + < /dev/null 로 띄울 것. nohup 은 SIGHUP 만 막는다 — ssh 가 끊기면 SIGINT 가
#    프로세스 그룹에 가서 배치가 죽는다 (9/16 에 두 번 날렸다).
# 수관 보정 (2026-09-19): v2 는 data/canopy 미마운트로 건물만 계산됐다. 수관 있는 칸만 다시:
#   GRID_EXTRA="--force --canopy-only" RUN=canopy setsid nohup bash scripts/run_grid_tokyo.sh < /dev/null > ~/grid_tokyo_canopy_driver.log 2>&1 &
set -u
RUN=${RUN:-v2}
cd "$HOME/climax_mvp"
docker exec climax-api printenv | grep -E '^(DATABASE_URL|BUILDING_SOURCE|REDIS_URL)=' > /tmp/grid.env
chmod 600 /tmp/grid.env
W=139.55; E=139.92

band() {   # $1=남위도 $2=북위도
  local s=$1 n=$2 tag=${1/./_}
  local log="$HOME/grid_tokyo_${RUN}_$tag.log"
  for t in $(seq 1 5); do
    nice -n 19 docker run --rm --memory 2500m --memory-swap 2500m --cpus 1.0 \
      --env-file /tmp/grid.env -e LOCAL_TILE_CACHE_MAX=24 -e RINGS_CACHE_MAX=1500 -e BUILDING_SOURCE=db \
      -v "$HOME/climax_mvp:/repo" -v "$HOME/climax_mvp/backend/data/buildings:/app/data/buildings:ro" \
      -v "$HOME/climax_mvp/data/canopy:/app/data/canopy:ro" \
      climax-backend:latest python3 /repo/scripts/build_skyline_grid.py \
      --bbox "$s" "$W" "$n" "$E" --step 0.0002 --threads 2 --resume --near-roads 25 --tiles-only \
      --src-hint "plateau" ${GRID_EXTRA:-} >> "$log" 2>&1
    if tail -n 3 "$log" | grep -q "완료:"; then break; fi
    echo "띠 $s~$n $t회차 중단 — 다시 건다: $(tail -n 1 "$log")"; sleep 10
  done
  echo "띠 $s~$n 끝: $(tail -n 1 "$log")"
}

echo "시작 $(date)"
# 4띠 동시 (2026-09-18). 병목이 DB 라 컨테이너 수를 늘려도 합산 29/s 는 그대로지만,
# 큰 띠(35.60·35.70)가 마지막에 둘만 남아 꼬리가 길어지는 건 막는다. 메모리 4×2.5 = 10 GB.
band 35.50 35.60 & band 35.60 35.70 & band 35.70 35.80 & band 35.80 35.90 & wait
rm -f /tmp/grid.env
echo "도쿄 전체 끝 $(date)"
