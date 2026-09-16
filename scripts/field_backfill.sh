#!/usr/bin/env bash
# 캡처로 남긴 현장 실측을 뒤늦게 올린다 (2026-09-16).
#
# 왜: 폰에 접근키(FIELD_KEY)가 없어 "엔진과 대조 + 저장"이 막혔고, 그동안 잰 지점을
#     화면 캡처로만 남겼다. 다시 타이핑하지 않도록 여기서 올린다.
#     키는 **서버가 자기 .env.prod 에서 직접 읽는다** — 어디에도 적어 두지 않는다.
#
# 시각: `when` 으로 **잰 시각의 태양**을 쓰게 한다(2026-09-16 에 엔드포인트를 열었다).
#   안 그러면 그늘이었던 지점이 양지로 계산된다.
#   ⚠️ 날씨(기온·습도·풍속)는 여전히 지금 관측이다 — 같은 날 몇 시간 안의 복원에만 쓸 것.
#      실측 기상은 meas 로 따로 들어가므로 잔차에는 영향이 없다.
DAY=${DAY:-$(date +%F)}    # TSV 의 시각이 어느 날짜인지. 다른 날이면 DAY=2026-09-16 처럼 넘길 것.
#
#   bash scripts/field_backfill.sh scripts/field_backfill_20260916.tsv
set -u
cd "$(dirname "$0")/.."
F=${1:?"TSV 경로를 달라"}
KEY=$(grep -m1 '^FIELD_KEY=' infra/ncp/.env.prod | cut -d= -f2-)
[ -z "$KEY" ] && { echo "!! .env.prod 에 FIELD_KEY 가 없다"; exit 1; }
API=${API:-https://api.climaxapp.kr}

n=0
while IFS=$'\t' read -r t lat lon ta rh wind tg ts note; do
  case "$t" in ''|'#'*) continue ;; esac
  n=$((n+1))
  printf "%-6s %-10s " "$t" "$note"
  curl -s -X POST "$API/api/v1/field/check" \
    -H "Content-Type: application/json" -H "X-Field-Key: $KEY" \
    -d "{\"lat\":$lat,\"lon\":$lon,\"when\":\"${DAY}T${t}:00+09:00\",\"meas\":{\"ta\":$ta,\"rh\":$rh,\"wind_ms\":$wind,\"globe_c\":$tg,\"surface_c\":$ts},\"note\":\"$note (실측 $t, 캡처 복원)\"}" \
  | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('  응답 파싱 실패'); raise SystemExit
print('저장' if d.get('saved') else '저장안됨', ' 엔진PET', d.get('pet_engine') or d.get('pet'), ' MRT', d.get('mrt_c'), ' SVF', d.get('svf'))
"
done < "$F"
echo "보낸 줄 $n"
