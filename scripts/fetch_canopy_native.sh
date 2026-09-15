#!/usr/bin/env bash
# Meta/WRI 수관고 v2 — **원본 해상도(1.19 m)** 로 부산 도심 일부 크롭 (2026-09-15)
#
# 왜:
#   9/12 에 "위성은 도심 가로수를 못 본다"고 결론 냈다. 그런데 그 시험을 **9 m 화소**
#   (`-tr 0.0001`, 약 9 m)로 했다. 가로수 한 줄 폭이 5 m 다 — 못 보는 게 당연한 조건이었다.
#   **자료의 한계인지 우리 리샘플링의 한계인지 구분하지 않고 결론을 냈다.**
#   그 결론 위에 지금 설계가 다 서 있다(가로수=공공데이터, 큰녹지=위성). 다시 재야 한다.
#
#   뒤집히면 공공데이터가 없는 나라에서도 가로수 그늘이 된다 — 무영상 특허 범위가 넓어진다.
#   안 뒤집히면 9/12 결론이 제대로 확정되고 논문에 자신 있게 쓴다.
#
# 왜 전역이 아니라 일부인가:
#   0.7° x 0.5° 를 1.19 m 로 뜨면 약 65,000 x 46,000 화소 = 3 GB 다. 시험에는 필요 없다.
#   가로수 구간이 밀집한 도심 한 조각이면 수백 개 단면이 나온다.
#
# 라이선스: "Vantor satellite imagery (c) 2016, canopy height by Meta & WRI, CC-BY 4.0"
#
# 실행: [서버 호스트] bash ~/climax_mvp/scripts/fetch_canopy_native.sh
set -euo pipefail

STUB="https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/chm"
OUT=~/climax_mvp/data/canopy
mkdir -p "$OUT"

# 부산 도심 (서면~연산~부산진) — 가로수 구간이 많은 구역
W=129.00; S=35.14; E=129.12; N=35.24
# 0.00001 deg = 위도 약 1.11 m / 경도 약 0.91 m  -> 원본 1.19 m 보다 촘촘하므로 정보 손실 없음
TR=0.00001

for q in 1321121310 1321121311 1321121312 1321121313 1321130200; do
  echo "/vsicurl/${STUB}/${q}.tif"
done > /tmp/canopy_tiles.txt

if ! command -v gdalwarp >/dev/null 2>&1; then
  echo "GDAL 없음 → 호스트에만 설치합니다 (컨테이너 무영향)"
  sudo apt-get update -qq && sudo apt-get install -y -qq gdal-bin
fi
gdalwarp --version

echo "=== 원본 해상도 크롭 (필요한 블록만 받습니다) ==="
time gdalwarp -q -overwrite \
  -te "$W" "$S" "$E" "$N" -te_srs EPSG:4326 -t_srs EPSG:4326 \
  -tr "$TR" "$TR" -r max -of ENVI -ot Byte \
  --optfile /tmp/canopy_tiles.txt "$OUT/busan_native_max.img"

ls -la "$OUT/busan_native_max.img" "$OUT/busan_native_max.hdr"
echo
echo "다음:"
echo "  docker cp $OUT/busan_native_max.img climax-api:/tmp/"
echo "  docker cp $OUT/busan_native_max.hdr climax-api:/tmp/"
echo "  docker cp ~/climax_mvp/scripts/canopy_transect.py climax-api:/tmp/"
echo "  docker exec -i climax-api env CANOPY_IMG=/tmp/busan_native_max.img \\"
echo "      python3 /tmp/canopy_transect.py /tmp/garosu_std.csv"
