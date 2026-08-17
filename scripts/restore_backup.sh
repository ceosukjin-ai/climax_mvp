#!/usr/bin/env bash
# ClimaX — 암호화 백업 복원 (맥에서 실행)
#
# 사용법:
#   bash scripts/restore_backup.sh                      # 최신 백업을 복호화만 (내용 확인)
#   bash scripts/restore_backup.sh <파일>.dump.age      # 특정 파일 복호화
#   bash scripts/restore_backup.sh --to-server <파일>   # 복호화 후 서버 DB에 실제 복원 ⚠️ 덮어씀
#
# 전제: 개인키 ~/.config/climax/backup_key.txt (age-keygen 으로 만든 것)
#
# ⚠️ --to-server 는 운영 DB를 덮어쓴다. 사고 복구 때만 쓸 것.

set -uo pipefail

KEY="${CLIMAX_AGE_KEY:-$HOME/.config/climax/backup_key.txt}"
LOCAL_DIR="${CLIMAX_LOCAL_BACKUP_DIR:-$HOME/ClimaX_backups}"
SSH_PORT="${CLIMAX_SSH_PORT:-30022}"
SSH_HOST="${CLIMAX_SSH_HOST:-ubuntu@180.210.77.87}"
SSH_KEY="${CLIMAX_SSH_KEY:-$HOME/.ssh/climax-was-key.pem}"
SSH="ssh -p $SSH_PORT"; SCP="scp -P $SSH_PORT"
[ -f "$SSH_KEY" ] && { SSH="$SSH -i $SSH_KEY"; SCP="$SCP -i $SSH_KEY"; }
CONTAINER="${CLIMAX_PG_CONTAINER:-climax-postgres}"

TO_SERVER=0
[ "${1:-}" = "--to-server" ] && { TO_SERVER=1; shift; }

SRC="${1:-$(ls -1t "$LOCAL_DIR"/climax_*.dump.age 2>/dev/null | head -1)}"
[ -n "$SRC" ] && [ -f "$SRC" ] || { echo "❌ 복원할 파일이 없습니다: ${1:-$LOCAL_DIR}"; exit 1; }
[ -r "$KEY" ] || { echo "❌ 개인키가 없습니다: $KEY  — 이 열쇠 없이는 백업을 열 수 없습니다"; exit 1; }
command -v age >/dev/null 2>&1 || { echo "❌ age 미설치 — brew install age"; exit 1; }

OUT="${SRC%.age}"
echo "── 복호화: $(basename "$SRC") → $(basename "$OUT")"
age -d -i "$KEY" -o "$OUT" "$SRC" || { echo "❌ 복호화 실패 (열쇠가 맞는지 확인)"; exit 1; }
echo "✅ 복호화 완료 — $(du -h "$OUT" | cut -f1)"

if command -v pg_restore >/dev/null 2>&1; then
  echo "── 내용 미리보기 (상위 10줄)"
  pg_restore -l "$OUT" 2>/dev/null | grep -E "TABLE DATA" | head -10
fi

if [ "$TO_SERVER" = "1" ]; then
  echo
  echo "⚠️  운영 DB($SSH_HOST)를 이 백업으로 덮어씁니다. 되돌릴 수 없습니다."
  read -r -p "정말 진행하려면 'RESTORE' 를 입력하세요: " ANS
  [ "$ANS" = "RESTORE" ] || { echo "취소했습니다."; exit 0; }

  $SCP "$OUT" "$SSH_HOST:/tmp/climax_restore.dump" || exit 1
  $SSH "$SSH_HOST" "
    docker cp /tmp/climax_restore.dump $CONTAINER:/tmp/r.dump &&
    docker exec $CONTAINER pg_restore -U climax -d climax --clean --if-exists /tmp/r.dump &&
    docker exec $CONTAINER rm -f /tmp/r.dump && rm -f /tmp/climax_restore.dump &&
    echo '✅ 서버 복원 완료'
  "
else
  echo
  echo "복호화된 파일: $OUT"
  echo "서버에 실제로 되돌리려면:  bash scripts/restore_backup.sh --to-server \"$SRC\""
fi
