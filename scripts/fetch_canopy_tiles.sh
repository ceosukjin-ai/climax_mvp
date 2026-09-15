#!/usr/bin/env bash
# Meta/WRI 수관고 v2 → **1°x1° 타일**로 내려받기 (2026-09-15)
#
# 왜 타일인가:
#   처음엔 부산 크롭 한 장(7000x5000)을 코드에 박아 놓았다. 부산 밖에서는 수관이 통째로
#   안 잡히고, 전국·일본으로 넓힐 방법이 없었다. 건물 타일·도로망이 이미 타일 방식이므로
#   같은 구조로 맞췄다(geo._canopy_tile).
#
#   파일   data/canopy/c_{lat}_{lon}.img   1도 x 1도, 화소 0.0001도 -> 10000 x 10000, Byte
#   크기   타일당 100 MB (압축 안 함 — numpy.memmap 이 그대로 읽어야 한다)
#   격자   전 지구 공통. row = (90-lat)/0.0001, col = (lon+180)/0.0001
#
#   ⚠️ 디스크를 먼저 보라. 한국 육지 약 20타일 = 2 GB. WAS 디스크가 50 GB 다.
#      `df -h ~` 로 여유를 확인하고 시작할 것.
#
# -r max 를 쓰는 이유 (2026-09-12):
#   원본 1.19 m 를 9 m 칸으로 **평균**내면 도로변 한 줄짜리 가로수가 아스팔트와 섞여 뭉개진다.
#   차폐 판정에 필요한 값은 "이 칸에서 가장 높은 것"이다.
#   (다만 그래도 도심 가로수는 탐지되지 않는다 — 9/15 원본 해상도 단면 시험으로 확정.
#    위성 수관고는 **큰 녹지 전용**이고 가로수는 공공데이터가 담당한다.)
#
# 라이선스: "Vantor satellite imagery (c) 2016, canopy height by Meta & WRI, CC-BY 4.0"
#
# 사용 [서버 호스트]:
#   bash ~/climax_mvp/scripts/fetch_canopy_tiles.sh 35 129          # 한 타일
#   bash ~/climax_mvp/scripts/fetch_canopy_tiles.sh --kr            # 한국 주요 도시권
#   bash ~/climax_mvp/scripts/fetch_canopy_tiles.sh --jp            # 일본 관동
set -euo pipefail

OUT=~/climax_mvp/data/canopy
STUB="https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm"
PX=0.0001
mkdir -p "$OUT"

if ! command -v gdalwarp >/dev/null 2>&1; then
  echo "GDAL 없음 → 호스트에만 설치 (컨테이너 무영향)"
  sudo apt-get update -qq && sudo apt-get install -y -qq gdal-bin
fi

# 위경도 -> 줌10 쿼드키. Meta 타일은 웹메르카토르 줌10(타일당 32768 화소, 약 1.19 m).
quadkey() {  # $1=lat $2=lon
  python3 - "$1" "$2" <<'PY'
import math, sys
lat, lon = float(sys.argv[1]), float(sys.argv[2])
z = 10
n = 2 ** z
x = int((lon + 180.0) / 360.0 * n)
la = math.radians(max(min(lat, 85.05112878), -85.05112878))
y = int((1.0 - math.asinh(math.tan(la)) / math.pi) / 2.0 * n)
x = max(0, min(n - 1, x)); y = max(0, min(n - 1, y))
q = ""
for i in range(z, 0, -1):
    d = 0; m = 1 << (i - 1)
    if x & m: d += 1
    if y & m: d += 2
    q += str(d)
print(q)
PY
}

fetch_one() {  # $1=lat(정수) $2=lon(정수)
  local LA=$1 LO=$2
  local DST="$OUT/c_${LA}_${LO}.img"
  if [ -f "$DST" ]; then echo "  이미 있음 c_${LA}_${LO}"; return 0; fi

  # 이 1도 칸을 덮는 줌10 쿼드키를 모은다 (0.25도 간격으로 훑으면 빠짐이 없다)
  : > /tmp/qk.txt
  for la in $(seq $LA 0.25 $(echo "$LA + 1" | bc)); do
    for lo in $(seq $LO 0.25 $(echo "$LO + 1" | bc)); do
      quadkey "$la" "$lo" >> /tmp/qk.txt
    done
  done
  sort -u /tmp/qk.txt | sed "s|^|/vsicurl/${STUB}/|; s|$|.tif|" > /tmp/qk_urls.txt
  echo "  c_${LA}_${LO}  쿼드키 $(wc -l < /tmp/qk_urls.txt)장"

  # 없는 타일(404)은 걸러낸다 — gdalwarp 가 통째로 실패하지 않게
  : > /tmp/qk_ok.txt
  while read -r u; do
    if gdalinfo "$u" >/dev/null 2>&1; then echo "$u" >> /tmp/qk_ok.txt; fi
  done < /tmp/qk_urls.txt
  local N; N=$(wc -l < /tmp/qk_ok.txt)
  if [ "$N" -eq 0 ]; then echo "    자료 없음(바다?) — 건너뜀"; return 0; fi
  echo "    실제 존재 ${N}장 → 크롭"

  gdalwarp -q -overwrite \
    -te "$LO" "$LA" "$(echo "$LO + 1" | bc)" "$(echo "$LA + 1" | bc)" \
    -te_srs EPSG:4326 -t_srs EPSG:4326 \
    -tr $PX $PX -r max -of ENVI -ot Byte \
    --optfile /tmp/qk_ok.txt "$DST"
  ls -la "$DST"
}

case "${1:-}" in
  --kr)
    # 한국 주요 도시권 — 수도권·충청·영남·호남·제주
    for t in "37 126" "37 127" "36 126" "36 127" "35 126" "35 127" "35 128" "35 129" \
             "36 128" "36 129" "34 126" "34 127" "34 128" "33 126" "38 127" "38 128"; do
      fetch_one $t
    done ;;
  --jp)
    for t in "35 139" "35 140" "36 139" "34 135" "35 135" "35 136"; do fetch_one $t; done ;;
  "")
    echo "사용: $0 <lat> <lon>  |  $0 --kr  |  $0 --jp"; exit 1 ;;
  *)
    fetch_one "$1" "$2" ;;
esac

echo
echo "현재 타일:"
ls -la "$OUT"/c_*.img 2>/dev/null | awk '{print "  "$9"  "$5/1048576" MB"}'
df -h "$OUT" | tail -1
