#!/usr/bin/env bash
# ETH Global Canopy Height 10m (2020) — 부산 크롭 (2026-09-12)
#
# 왜: 가로수 공공데이터(A)는 지자체가 등록한 '가로수길'만 담는다. 공원·단지·산자락·미등록 수목은 빠지고,
#     한국 밖은 비어 있다. 위성 수관고 래스터는 그 공백을 메우고, ③ 위성 학생모델의 입력도 된다
#     (NDVI 는 평면 녹지율이라 잔디밭과 20m 플라타너스를 구분하지 못한다).
#
# 설계 결정:
#   · 컨테이너에 의존성을 넣지 않는다 — 운영 중이다. 호스트에서만 GDAL 을 쓴다.
#   · 출력은 ENVI(원시 바이너리 .img + 텍스트 .hdr) — numpy.memmap 이 추가 패키지 없이 읽는다.
#   · COG 이므로 /vsicurl/ 로 필요한 블록만 받는다. 3° 타일 통째(1GB급)를 받지 않는다.
#
# 라이선스: CC-BY 4.0. 출처표기 필요 —
#   Lang, N. et al. "A high-resolution canopy height model of the Earth." (ETH Zurich)
#
# 실행: [서버] bash ~/climax_mvp/scripts/fetch_canopy_busan.sh
set -euo pipefail

STUB="https://share.phys.ethz.ch/~pf/nlangdata/ETH_GlobalCanopyHeight_10m_2020_version1/3deg_cogs"
OUT=~/climax_mvp/data/canopy
mkdir -p "$OUT"

# 부산 광역 bbox (여유 포함). 3° 격자 기준 N33E126(126~129E) 과 N33E129(129~132E) 에 걸친다.
W=128.70; S=34.95; E=129.40; N=35.45

if ! command -v gdalwarp >/dev/null 2>&1; then
  echo "GDAL 없음 → 설치합니다 (호스트에만, 컨테이너 무영향)"
  sudo apt-get update -qq
  sudo apt-get install -y -qq gdal-bin
fi
gdalwarp --version

A="/vsicurl/${STUB}/ETH_GlobalCanopyHeight_10m_2020_N33E126_Map.tif"
B="/vsicurl/${STUB}/ETH_GlobalCanopyHeight_10m_2020_N33E129_Map.tif"

echo "=== 크롭 시작 (필요한 블록만 내려받습니다) ==="
time gdalwarp -q -overwrite \
  -te "$W" "$S" "$E" "$N" -te_srs EPSG:4326 -t_srs EPSG:4326 \
  -r near -of ENVI -ot Byte \
  "$A" "$B" "$OUT/busan_canopy.img"

echo
echo "=== 결과 ==="
ls -lh "$OUT"/busan_canopy.*
echo "--- hdr ---"
cat "$OUT/busan_canopy.hdr"
echo
gdalinfo -stats "$OUT/busan_canopy.img" 2>/dev/null | grep -E "Size is|Pixel Size|Origin|Minimum=|STATISTICS_MEAN" || true
