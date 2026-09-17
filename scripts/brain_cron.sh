#!/usr/bin/env bash
# ⚠️ crontab 에 등록할 때는 반드시 `bash` 로 부를 것 (2026-09-17).
#    이 파일이 git 에 100644(실행 불가)로 들어가 있었고, 서버가 pull 받은 뒤
#    9/15·9/16·9/17 새벽 학습이 전부 'Permission denied' 로 조용히 죽었다.
#    마지막으로 제대로 돈 것은 2026-09-14 04:08 이다. 사흘을 날렸다.
#    crontab 예:  0 4 * * * bash /home/ubuntu/climax_mvp/scripts/brain_cron.sh >> ~/brain.log 2>&1
#    (watch_health 는 `bash <경로>` 로 불러서 멀쩡했다 — 그 방식을 따른다)
# 야간 학습 루프 실행기 (2026-09-13 개정)
#
# 왜 고쳤나:
#   기존 cron 은 brain_nightly.py 하나만 불렀다. 그런데 그 스크립트는 **설계상 Day1 dry-run** —
#   채점표만 쓰고 재학습은 안 한다. 그 결과 measurement 가 9,246 → 10,394 로 느는 동안
#   brain_version 지표는 3일 내내 소수 셋째 자리까지 동일했다(field r 0.445 / 물리+AI r 0.732).
#   데이터만 쌓이고 모델은 9/10 이후 멈춰 있었다.
#
# 이번 개정:
#   ① 채점(brain_nightly)        — 기존과 동일. 오늘 엔진이 어디서 틀리는지.
#   ② 재학습 후보(fit_ground_lag) — 지면온도 열관성 계수를 다시 맞춰 `승격 후보` 판정만 남긴다.
#
# 승격은 자동화하지 않는다. ②는 brain_version(promoted=false) 에 기록만 하고 엔진은 안 건드린다.
# 밤사이 모델이 혼자 바뀌면 앱 수치가 왜 변했는지 추적이 안 되기 때문이다 — 9/10 열관성 승격도
# 사람이 리포트를 보고 반영했다. 그 방식을 유지한다.
#
# compose run 대신 docker run 으로 메모리 상한을 건다 (2026-09-12, 서버다운 사고 대응).
set -u
cd /home/ubuntu/climax_mvp
E=$(mktemp)
docker exec climax-api printenv | grep -vE '^(PATH|HOSTNAME|HOME|PWD|LANG|PYTHON|_)' > "$E"

# run <라벨> <타임아웃초> <스크립트경로>
run() {
  echo "--- $1  시작 $(date '+%F %T')"
  timeout "$2" docker run --rm --memory 2500m --cpus 2 --env-file "$E" \
    -e CLIMAX_REPO=/repo -v /home/ubuntu/climax_mvp:/repo -w /app \
    climax-backend:latest python3 "$3"
  local r=$?
  [ "$r" -eq 124 ] && echo "!! $1 타임아웃 (${2}s) — 다음 단계로 넘어간다"
  echo "--- $1  종료코드 $r"
  return "$r"
}

run "① 채점 brain_nightly" 3600 /repo/scripts/brain_nightly.py
rc=$?

# ②가 실패하거나 오래 걸려도 ①의 채점 결과는 지킨다 → 종료코드에 반영하지 않는다.
run "② 재학습 후보 brain_fit_ground_lag" 5400 /repo/scripts/brain_fit_ground_lag.py || true

rm -f "$E"
echo "=== brain_cron 종료코드 $rc  $(date '+%F %T')"
exit "$rc"
