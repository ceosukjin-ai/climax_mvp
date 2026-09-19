#!/usr/bin/env bash
# 스카이라인 격자 배치 드라이버 (2026-09-11). 위도 0.1° 띠로 잘라 병렬 실행, 각 띠는 --resume 이라 중단 후 재실행 가능.
#
# ⚠️ 2026-09-11 사고: 한도 없이 8개를 돌려 16GB 를 소진 → 서버 다운(운영 앱 4시간 정지, 재부팅으로 복구).
#    그래서 배치는 반드시 **메모리 한도 + 낮은 우선순위 + 소수 병렬**로 돈다.
#    `docker compose run` 에는 --memory 옵션이 없어서 `docker run` 을 직접 쓴다(이미지: climax-backend:latest).
#    DB 는 2026-09-11 부터 별도 서버(lbs-climax-db)라 compose 네트워크가 필요 없다.
#
# ⚠️ 반드시 `setsid` + `< /dev/null` 로 띄울 것 (2026-09-16 에 두 번 날렸다).
#    nohup 은 SIGHUP 만 무시한다. 배치가 ssh 의 프로세스 그룹 안에 있으면 세션이 끊기거나
#    Ctrl-C 를 누를 때 **SIGINT 가 그룹 전체에 간다** — 로그에 남은 증거:
#      grid_kr_35_15.log  got 3 SIGTERM/SIGINTs, forcefully exiting
#    이렇게 도쿄 격자가 12시간(밤새) 안 돌았다. setsid 로 새 세션을 만들어 떼어내고,
#    표준입력을 끊어 ssh 가 기다리지 않게 한다.
#    (범인은 watch_health 도 deploy.sh 도 아니었다. 그쪽을 두 번 의심했다가 틀렸다.)
#
#   타일:  setsid nohup bash scripts/run_grid_kr.sh tiles < /dev/null > ~/tiles_kr.log 2>&1 &
#   격자:  setsid nohup bash scripts/run_grid_kr.sh grid  < /dev/null > ~/grid_kr.log  2>&1 &
#   부산만: S=35.05 N=35.30 W=128.95 E=129.25 nohup bash scripts/run_grid_kr.sh grid > ~/grid_busan.log 2>&1 &
#   진행:  tail -n 2 ~/grid_kr_*.log      중단: pkill -f build_skyline_grid; docker ps -q --filter ancestor=climax-backend | xargs -r docker kill
set -u
cd "$(dirname "$0")/.."
S=${S:-33.10}; N=${N:-38.65}; W=${W:-125.90}; E=${E:-129.60}; STEP=${STEP:-0.1}
# 메모리 한도 2500m (2026-09-17). 1500m 로는 **파이썬이 1,530 MB 에서 커널에 죽었다.**
#   dmesg: "Memory cgroup out of memory: Killed process (python3) anon-rss:1530976kB"
#   9/16 15:26, 16:45, 9/17 03:13/03:14/03:18 — 도쿄 격자가 세 번 죽은 원인이 전부 이것이다.
#   (ssh 신호도, watch_health 도, 배포도 아니었다. 세 번 다 엉뚱한 곳을 의심했다.)
#   호스트는 15 GB 다. 3 x 2500m = 7.5 GB 면 절반이 남는다. 그래도 9/11 사고를 생각해
#   PAR 을 올릴 때는 MEM x PAR 이 8 GB 를 넘지 않게 할 것.
PAR=${PAR:-4}; MEM=${MEM:-2500m}; CPUS=${CPUS:-1.0}; IMG=${IMG:-climax-backend:latest}
TRIES=${TRIES:-5}       # 띠가 안 끝나면 --resume 으로 몇 번까지 다시 걸까
# 출처 표시 (2026-09-16). 한국은 V-World 타일 + 표제부라 vwtile+reg 가 맞지만,
# 일본은 OSM 이다. 고정해 두면 DB 에 거짓 출처가 남는다.  SRC=osm 로 넘길 것.
SRC=${SRC:-vwtile+reg}; RUN=${RUN:-kr}   # RUN 은 띠별 로그 이름 (grid_<RUN>_<위도>.log)
ENVF=infra/ncp/.env.prod
val() { grep -m1 "^$1=" "$ENVF" | cut -d= -f2-; }
DBURL="postgresql+asyncpg://climax:$(val DB_PASSWORD)@$(val DB_HOST):5432/climax"
DC="nice -n 19 docker run --rm --memory $MEM --memory-swap $MEM --cpus $CPUS \
 -e DATABASE_URL=$DBURL -e VWORLD_API_KEY=$(val VWORLD_API_KEY) \
 -e LOCAL_TILE_CACHE_MAX=${TILE_CACHE:-24} -e RINGS_CACHE_MAX=${RING_CACHE:-1500} \
 -e BUILDING_SOURCE=${BUILDING_SOURCE:-db} \
 -v $HOME/climax_mvp:/repo -v $HOME/climax_mvp/backend/data/buildings:/app/data/buildings:ro \
 -v $HOME/climax_mvp/data/canopy:/app/data/canopy:ro $IMG"

case "${1:-}" in
  tiles)
    $DC python3 /repo/scripts/fetch_vworld_tiles.py --bbox $S $W $N $E --threads 4 --to-db ;;
  grid)
    python3 -c "
s,n,st=$S,$N,$STEP
x=s
while x<n:
    print(f'{x:.2f} {min(x+st,n):.2f}'); x=round(x+st,2)
" | xargs -P $PAR -L 1 bash -c '
      s=$0; n=$1; tag=${s/./_}
      # 죽으면 다시 건다 (2026-09-17). OOM 으로 한 띠가 죽으면 드라이버가 그걸 "끝"으로 보고
      # 전체를 종료했다. --resume 이라 이어받으니, 끝났다는 표시가 나올 때까지 다시 건다.
      for t in $(seq 1 '"$TRIES"'); do
        '"$DC"' python3 /repo/scripts/build_skyline_grid.py --bbox $s '"$W"' $n '"$E"' \
          --step 0.0002 --threads 2 --resume --near-roads 25 --tiles-only --src-hint "'"$SRC"'" \
          >> ~/grid_'"$RUN"'_$tag.log 2>&1
        if tail -n 3 ~/grid_'"$RUN"'_$tag.log | grep -q "완료:"; then break; fi
        echo "띠 $s~$n $t회차 중단 — 다시 건다: $(tail -n 1 ~/grid_'"$RUN"'_$tag.log)"
        sleep 10
      done
      echo "띠 $s~$n 끝: $(tail -n 1 ~/grid_'"$RUN"'_$tag.log)"' ;;
  *) echo "usage: $0 tiles|grid"; exit 1 ;;
esac
