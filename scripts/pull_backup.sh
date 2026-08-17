#!/usr/bin/env bash
# ClimaX — 서버 백업을 맥으로 회수 (맥에서 실행)
#
# 왜 필요한가: 서버 안에만 있는 백업은 서버 디스크가 죽으면 같이 죽는다.
#              한 벌은 반드시 서버 밖(맥)에 있어야 한다.
#              받아오는 파일은 age로 암호화돼 있어, 전송 중에도 맥에서도 평문이 아니다.
#
# 수동 실행:  bash scripts/pull_backup.sh
# 크론 등록:  crontab -e  →  30 5 * * * /Users/sukjinjung/Desktop/climax_mvp/scripts/pull_backup.sh
#             (서버 백업 04:10 → 맥 회수 05:30 순서)
#
# 전제: ssh 키 로그인이 되어 있어야 한다(비밀번호를 물으면 크론이 멈춘다).
#       확인:  ssh -p 30022 -o BatchMode=yes ubuntu@180.210.77.87 true && echo OK

set -uo pipefail

SSH_PORT="${CLIMAX_SSH_PORT:-30022}"
SSH_HOST="${CLIMAX_SSH_HOST:-ubuntu@180.210.77.87}"
SSH_KEY="${CLIMAX_SSH_KEY:-$HOME/.ssh/climax-was-key.pem}"
SSH="ssh -p $SSH_PORT"
[ -f "$SSH_KEY" ] && SSH="$SSH -i $SSH_KEY"
REMOTE_DIR="${CLIMAX_REMOTE_BACKUP_DIR:-/home/ubuntu/climax_backups}"
LOCAL_DIR="${CLIMAX_LOCAL_BACKUP_DIR:-$HOME/ClimaX_backups}"
KEY="${CLIMAX_AGE_KEY:-$HOME/.config/climax/backup_key.txt}"
KEEP="${CLIMAX_LOCAL_KEEP:-30}"   # 맥에 보관할 개수

mkdir -p "$LOCAL_DIR"; chmod 700 "$LOCAL_DIR"
LOG="$LOCAL_DIR/pull.log"
log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

log "── 회수 시작 ($SSH_HOST:$REMOTE_DIR → $LOCAL_DIR)"

$SSH -o BatchMode=yes -o ConnectTimeout=15 "$SSH_HOST" true 2>>"$LOG" \
  || { log "❌ ssh 접속 실패 — 키 로그인 설정 확인 (ssh-copy-id -i $SSH_KEY -p $SSH_PORT $SSH_HOST)"; exit 1; }

# 서버가 백업에 실패한 흔적이 있으면 먼저 알린다
if $SSH "$SSH_HOST" "test -f $REMOTE_DIR/LAST_FAILURE" 2>/dev/null; then
  log "⚠️  서버 쪽 백업이 실패한 기록이 있음:"
  $SSH "$SSH_HOST" "cat $REMOTE_DIR/LAST_FAILURE" | tee -a "$LOG"
fi

# 새 파일만 받아온다(이미 받은 건 건너뜀)
rsync -avz --ignore-existing -e "$SSH" \
  "$SSH_HOST:$REMOTE_DIR/climax_*" "$LOCAL_DIR/" >>"$LOG" 2>&1 \
  || { log "❌ rsync 실패 (로그: $LOG)"; exit 1; }

LATEST=$(ls -1t "$LOCAL_DIR"/climax_*.dump.age "$LOCAL_DIR"/climax_*.dump 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
  log "❌ 받아온 백업이 없음 — 서버 크론이 도는지 확인"
  exit 1
fi

# 암호화 여부·복원 열쇠 점검 — 열쇠 없는 암호문은 백업이 아니라 벽돌이다
case "$LATEST" in
  *.age) [ -r "$KEY" ] || log "🚨 개인키가 없습니다($KEY) — 지금 백업은 복원 불가 상태. age-keygen 으로 만든 열쇠를 이 경로에 두세요." ;;
  *)     log "⚠️  최신 백업이 평문입니다 — 서버의 암호화 설정(~/.climax_backup_recipient)을 확인하세요." ;;
esac

# 최신 백업이 36시간보다 오래됐으면 경고 (백업이 조용히 멈춘 상태 감지)
AGE_H=$(( ( $(date +%s) - $(stat -f %m "$LATEST") ) / 3600 ))
if [ "$AGE_H" -gt 36 ]; then
  log "⚠️  최신 백업이 ${AGE_H}시간 전 것임 — 서버 백업이 멈췄을 수 있음"
fi

# 로컬 보관정책
# shellcheck disable=SC2012
ls -1t "$LOCAL_DIR"/climax_*.dump.age "$LOCAL_DIR"/climax_*.dump 2>/dev/null | tail -n "+$((KEEP + 1))" \
  | while read -r f; do rm -f "$f" "${f}.info"; log "🗑  로컬 오래된 백업 삭제: $(basename "$f")"; done

log "✅ 완료 — 최신 $(basename "$LATEST") ($(du -h "$LATEST" | cut -f1)), 로컬 보관 $(ls -1 "$LOCAL_DIR"/climax_*.dump* 2>/dev/null | grep -vc '\.info$')개"
