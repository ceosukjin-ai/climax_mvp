#!/usr/bin/env bash
# 도쿄 광역 건물 다운로드 — ⚠️ 맥 터미널에서 실행 (Overpass 접근 필요, VM/서버 불가)
# 기본 대상: 시부야·하라주쿠·요요기공원 (약 4.4×2.7km). 다른 지역은 아래 셀 범위만 변경.
set -u
cd ~/Desktop/climax_mvp || exit 1
OUT="data/osm_raw"; mkdir -p "$OUT"
API="https://overpass-api.de/api/interpreter"

# 0.01° 정수 셀 범위 (= lat*100, lon*100)
LA1=3565; LA2=3568      # 35.65 ~ 35.69
LO1=13969; LO2=13971    # 139.69 ~ 139.72

TOTAL=0; N=0
for la in $(seq $LA1 $LA2); do
  for lo in $(seq $LO1 $LO2); do
    f="$OUT/${la}_${lo}.json"
    if [ -s "$f" ]; then
      c=$(python3 -c "import json;print(len(json.load(open('$f')).get('elements',[])))" 2>/dev/null||echo 0)
      echo "skip ${la}_${lo} (이미 있음, ${c}동)"; TOTAL=$((TOTAL+c)); continue
    fi
    s=$(awk "BEGIN{printf \"%.2f\",$la/100}");   w=$(awk "BEGIN{printf \"%.2f\",$lo/100}")
    nth=$(awk "BEGIN{printf \"%.2f\",($la+1)/100}"); e=$(awk "BEGIN{printf \"%.2f\",($lo+1)/100}")
    q="[out:json][timeout:180];way[\"building\"](${s},${w},${nth},${e});out geom tags;"
    code=$(curl -s -G "$API" --data-urlencode "data=$q" -o "$f" -w "%{http_code}")
    c=$(python3 -c "import json;print(len(json.load(open('$f')).get('elements',[])))" 2>/dev/null||echo "ERR")
    echo "cell ${la}_${lo}  http=${code}  건물=${c}"
    [ "$c" != "ERR" ] && TOTAL=$((TOTAL+c))
    N=$((N+1)); sleep 3
  done
done
echo "=== 완료: ${N}셀 요청, 누적 약 ${TOTAL}동 → ${OUT}/ ==="
echo "클로드에게 '다운로드 끝'이라고 알려주세요."
