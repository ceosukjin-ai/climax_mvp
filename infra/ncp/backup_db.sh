#!/usr/bin/env bash
# ClimaX — PostGIS 야간 백업 + 암호화 (서버에서 실행)
#
# 하는 일
#   1) climax-postgres 컨테이너에서 DB 전체를 custom 포맷으로 덤프
#   2) 덤프가 실제로 복원 가능한지 pg_restore -l 로 검증 (깨진 백업 방지)
#   3) age 공개키로 암호화 (.dump.age) — 평문 덤프는 즉시 삭제
#   4) 주요 테이블 행 수를 사이드카(.info)로 남김 — "언제부터 안 쌓였나" 추적용
#   5) 보관정책: 일 백업 14개 + 매월 1일 백업 12개 유지, 나머지 삭제
#
# 암호화 열쇠 (중요)
#   - 서버에는 **공개키만** 둔다: ~/.climax_backup_recipient  (age1... 한 줄)
#   - 개인키는 맥에만: ~/.config/climax/backup_key.txt  → 이게 없으면 복원 불가
#   - 즉 서버가 통째로 털려도 백업 파일은 열리지 않는다.
#
# 수동 실행:  bash infra/ncp/backup_db.sh
# 크론 등록:  crontab -e  →  10 4 * * * /home/ubuntu/climax_mvp/infra/ncp/backup_db.sh
#
# 복원(맥에서):  bash scripts/restore_backup.sh <파일>.dump.age

set -uo pipefail

CONTAINER="${CLIMAX_PG_CONTAINER:-climax-postgres}"
DB_USER="${CLIMAX_PG_USER:-climax}"
DB_NAME="${CLIMAX_PG_DB:-climax}"
BACKUP_DIR="${CLIMAX_BACKUP_DIR:-$HOME/climax_backups}"
RECIPIENT_FILE="${CLIMAX_AGE_RECIPIENT_FILE:-$HOME/.climax_backup_recipient}"
KEEP_DAILY="${CLIMAX_KEEP_DAILY:-14}"     # 최근 일 백업 보관 개수
KEEP_MONTHLY="${CLIMAX_KEEP_MONTHLY:-12}" # 매월 1일 백업 보관 개수
ALLOW_PLAINTEXT="${CLIMAX_ALLOW_PLAINTEXT:-0}"  # 1로 두면 암호화 없이 저장(권장 안 함)

STAMP="$(date +%Y%m%d_%H%M)"
TMP="$BACKUP_DIR/.climax_${STAMP}.dump.tmp"
OUT="$BACKUP_DIR/climax_${STAMP}.dump.age"
LOG="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
fail() { log "❌ 실패: $*"; echo "$(date '+%F %T') $*" > "$BACKUP_DIR/LAST_FAILURE"; rm -f "$TMP"; exit 1; }

log "── 백업 시작 ($CONTAINER → $(basename "$OUT"))"

# 0) 전제 확인 — 컨테이너와 암호화 열쇠
docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true \
  || fail "컨테이너 $CONTAINER 가 실행 중이 아님"

RECIPIENT=""
if [ "$ALLOW_PLAINTEXT" != "1" ]; then
  command -v age >/dev/null 2>&1 || fail "age 미설치 — sudo apt-get install -y age"
  [ -r "$RECIPIENT_FILE" ] || fail "공개키 파일 없음: $RECIPIENT_FILE (맥에서 age-keygen 후 공개키 한 줄만 넣을 것)"
  # 줄 앞에 '# public key:' 같은 게 붙어 있어도 공개키만 뽑아낸다
  RECIPIENT="$(grep -o 'age1[0-9a-z]\{50,\}' "$RECIPIENT_FILE" | head -1)"
  [ -n "$RECIPIENT" ] || fail "$RECIPIENT_FILE 에서 age1... 공개키를 찾지 못함 (현재 내용: $(head -c 80 "$RECIPIENT_FILE"))"
  # 형식 사전 검증 — 잘린 키/자리표시자를 여기서 잡는다
  echo "test" | age -r "$RECIPIENT" -o /dev/null 2>/tmp/.age_check \
    || fail "공개키 형식 오류: $(tr -d '\n' < /tmp/.age_check) — 키=${RECIPIENT:0:16}…(${#RECIPIENT}자, 정상은 62자)"
else
  OUT="$BACKUP_DIR/climax_${STAMP}.dump"
  log "⚠️  평문 저장 모드 (CLIMAX_ALLOW_PLAINTEXT=1) — 임시로만 쓸 것"
fi

# 1) 덤프 (custom 포맷 = 압축 포함, 부분 복원 가능)
if ! docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$TMP" 2>>"$LOG"; then
  fail "pg_dump 오류 (로그: $LOG)"
fi

SIZE=$(stat -c %s "$TMP" 2>/dev/null || stat -f %z "$TMP")
[ "$SIZE" -gt 1024 ] || fail "덤프 파일이 너무 작음 (${SIZE}B) — 빈 백업 방지"

# 2) 무결성 검증 — 목록을 읽을 수 있어야 복원 가능한 파일 (암호화 전에 검사)
docker exec -i "$CONTAINER" pg_restore -l > /dev/null 2>>"$LOG" < "$TMP" \
  || fail "덤프 무결성 검증 실패 (pg_restore -l)"

# 3) 암호화 — 공개키로만 잠그므로 서버에는 여는 열쇠가 없다
if [ -n "$RECIPIENT" ]; then
  ERR="$(age -r "$RECIPIENT" -o "$OUT" "$TMP" 2>&1)" \
    || { echo "$ERR" >> "$LOG"; rm -f "$OUT"; fail "age 암호화 실패: ${ERR:-원인 미상}"; }
  ENC_SIZE=$(stat -c %s "$OUT" 2>/dev/null || stat -f %z "$OUT")
  [ "$ENC_SIZE" -gt 1024 ] || { rm -f "$OUT"; fail "암호문이 비정상적으로 작음 (${ENC_SIZE}B)"; }
  # age 파일 헤더 확인 — 진짜 암호문인지
  head -c 16 "$OUT" | grep -q "age-encryption" || { rm -f "$OUT"; fail "age 헤더 없음 — 암호화 검증 실패"; }
else
  mv "$TMP" "$OUT"
fi
rm -f "$TMP"
chmod 600 "$OUT"

# 4) 행 수 기록 — 적재가 멈춘 날을 나중에 찾을 수 있게 (개인정보 없음: 테이블명·건수뿐)
{
  echo "# ClimaX backup $STAMP  plain=${SIZE}B  file=$(basename "$OUT")"
  docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -At -c "
    SELECT relname || '=' || n_live_tup
    FROM pg_stat_user_tables ORDER BY relname;" 2>/dev/null
} > "${OUT}.info"

rm -f "$BACKUP_DIR/LAST_FAILURE"
log "✅ 완료 $(du -h "$OUT" | cut -f1)$([ -n "$RECIPIENT" ] && echo ' (암호화됨)') — $(tr '\n' ' ' < "${OUT}.info" | cut -c1-200)"

# 5) 보관정책 — 매월 1일자는 월백업으로 따로 세고, 나머지는 일백업
prune_list() { # stdin: 오래된 순으로 지울 파일 목록
  while read -r f; do
    rm -f "$f" "${f}.info"
    log "🗑  오래된 백업 삭제: $(basename "$f")"
  done
}
# shellcheck disable=SC2012
ls -1t "$BACKUP_DIR"/climax_??????01_*.dump* 2>/dev/null | grep -v '\.info$' \
  | tail -n "+$((KEEP_MONTHLY + 1))" | prune_list
# shellcheck disable=SC2012
ls -1t "$BACKUP_DIR"/climax_*.dump* 2>/dev/null | grep -v '\.info$' | grep -v '_[0-9]\{6\}01_' \
  | tail -n "+$((KEEP_DAILY + 1))" | prune_list

USED=$(df -h "$BACKUP_DIR" | awk 'NR==2{print $5" 사용 / "$4" 남음"}')
COUNT=$(ls -1 "$BACKUP_DIR"/climax_*.dump* 2>/dev/null | grep -vc '\.info$')
log "── 종료. 보관 ${COUNT}개, 디스크 $USED"
