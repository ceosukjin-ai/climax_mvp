#!/usr/bin/env bash
# =========================================================================
# ClimaX — 백업 암호화 열쇠 셋업 (맥에서 한 번만 실행)
#
#   bash ~/Desktop/climax_mvp/scripts/setup_backup_key.sh
#
# 하는 일
#   1) age 설치 (Homebrew 없이 공식 릴리스 바이너리 → ~/.local/bin)
#   2) 개인키 생성 (이미 있으면 절대 건드리지 않음)
#   3) 공개키만 서버에 심기
#   4) 서버 백업 1회 실행해서 실제로 암호화되는지 확인
#
# 개인키는 이 맥에만 남습니다. 서버에는 공개키만 갑니다.
# =========================================================================
set -uo pipefail

KEY_DIR="$HOME/.config/climax"
KEY="$KEY_DIR/backup_key.txt"
BIN_DIR="$HOME/.local/bin"
SSH_KEY="${CLIMAX_SSH_KEY:-$HOME/.ssh/climax-was-key.pem}"
SSH_PORT="${CLIMAX_SSH_PORT:-30022}"
SSH_HOST="${CLIMAX_SSH_HOST:-ubuntu@180.210.77.87}"

say() { echo; echo "▶ $*"; }
ok()  { echo "  ✅ $*"; }
die() { echo "  ❌ $*"; exit 1; }

# -------------------------------------------------------------------------
say "1/4 age 설치 확인"

export PATH="$BIN_DIR:$PATH"
if command -v age >/dev/null && command -v age-keygen >/dev/null; then
  ok "이미 설치됨 ($(age --version 2>/dev/null))"
else
  case "$(uname -m)" in
    arm64|aarch64) ARCH=arm64 ;;
    x86_64)        ARCH=amd64 ;;
    *) die "지원하지 않는 CPU: $(uname -m)" ;;
  esac
  echo "  · CPU: $ARCH — 공식 릴리스를 내려받습니다"

  URL="$(curl -fsSL https://api.github.com/repos/FiloSottile/age/releases/latest \
        | grep -o "https://[^\"]*age-v[0-9.]*-darwin-${ARCH}\.tar\.gz" | head -1)"
  [ -n "$URL" ] || URL="https://github.com/FiloSottile/age/releases/download/v1.3.1/age-v1.3.1-darwin-${ARCH}.tar.gz"
  echo "  · $URL"

  TMPD="$(mktemp -d)"
  curl -fsSL "$URL" -o "$TMPD/age.tar.gz" || die "다운로드 실패 (네트워크 확인)"
  tar xzf "$TMPD/age.tar.gz" -C "$TMPD" || die "압축 해제 실패"
  mkdir -p "$BIN_DIR"
  cp "$TMPD/age/age" "$TMPD/age/age-keygen" "$BIN_DIR/" || die "복사 실패"
  chmod +x "$BIN_DIR/age" "$BIN_DIR/age-keygen"
  xattr -d com.apple.quarantine "$BIN_DIR/age" "$BIN_DIR/age-keygen" 2>/dev/null || true
  rm -rf "$TMPD"

  command -v age >/dev/null || die "설치했는데 실행이 안 됩니다 ($BIN_DIR 확인)"
  ok "설치 완료 — $(age --version)"

  # 다음에 터미널을 새로 열어도 쓸 수 있게 PATH 등록
  if ! grep -q '.local/bin' "$HOME/.zshrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
    ok "~/.zshrc 에 PATH 추가 (새 터미널부터 적용)"
  fi
fi

# -------------------------------------------------------------------------
say "2/4 개인키 준비"

if [ -f "$KEY" ]; then
  ok "이미 있는 열쇠를 그대로 씁니다 ($KEY)"
  echo "     (새로 만들면 기존 백업을 영영 못 열기 때문에 덮어쓰지 않습니다)"
else
  mkdir -p "$KEY_DIR"; chmod 700 "$KEY_DIR"
  age-keygen -o "$KEY" 2>/dev/null || die "열쇠 생성 실패"
  chmod 600 "$KEY"
  ok "새 열쇠 생성: $KEY"
fi

PUB="$(grep -o 'age1[0-9a-z]*' "$KEY" | head -1)"
[ ${#PUB} -ge 50 ] || die "공개키를 못 찾았습니다 ($KEY 내용 확인)"
ok "공개키: ${PUB:0:20}… (${#PUB}자)"

# -------------------------------------------------------------------------
say "3/4 서버에 공개키 심기"

[ -f "$SSH_KEY" ] || die "ssh 키가 없습니다: $SSH_KEY (CLIMAX_SSH_KEY 로 경로 지정 가능)"

echo "$PUB" | ssh -i "$SSH_KEY" -p "$SSH_PORT" "$SSH_HOST" \
  'cat > ~/.climax_backup_recipient && chmod 600 ~/.climax_backup_recipient && echo "  서버 저장값: $(cat ~/.climax_backup_recipient)"' \
  || die "서버 전송 실패 (ssh 접속 확인)"
ok "전송 완료"

# -------------------------------------------------------------------------
say "4/4 서버에서 백업 1회 실행"

ssh -i "$SSH_KEY" -p "$SSH_PORT" "$SSH_HOST" 'bash ~/climax_mvp/infra/ncp/backup_db.sh'

cat <<EOF

============================================================
 ⚠️ 지금 바로 하실 일 — 열쇠 사본 만들기

   cat $KEY

 화면에 나오는 AGE-SECRET-KEY-1... 줄을
   · 1Password 등 비밀번호 관리자에 저장
   · 종이로 1부 인쇄해 보관

 이 줄이 사라지면 앞으로 쌓일 백업을 아무도 열 수 없습니다.
 (공개키 age1... 은 공개돼도 무해합니다)
============================================================
EOF
