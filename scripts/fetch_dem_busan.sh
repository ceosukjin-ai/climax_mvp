#!/usr/bin/env bash
# Copernicus GLO-30 DEM — 부산 크롭 (2026-09-15)
#
# 왜:
#   `_svf_from_rings` 는 `h = 건물높이 - 눈높이` 만 쓴다. **지면 표고가 들어갈 자리가 없다.**
#   모든 건물이 같은 평지에 있다고 본다. 부산은 산지이고 부산대는 경사 캠퍼스다.
#   경사면 위쪽 건물은 옥상 절대고도가 그만큼 높아 하늘을 더 막는다.
#   **이게 얼마짜리인지 아무도 안 재봤다.** 자료 신청 전에 크기부터 재는 게 순서다.
#
# 자료 선택:
#   · Copernicus GLO-30 (30 m) — AWS 공개버킷, 인증 불필요, 상업 이용 가능.
#   · FABDEM(건물·나무 제거판)은 CC BY-NC-SA 라 상업 서비스에 못 쓴다. GlobalBuildingAtlas 와 같은 이유.
#
#   ⚠️ GLO-30 은 **DSM** 이다 — 건물·나무 높이가 섞여 있다.
#      그래서 분석 스크립트에서 반경 90 m **국소 최소값**을 지면 근사로 쓴다.
#      30 m 격자라 40 m 반경 안 이웃을 낱낱이 분해하진 못하지만,
#      "이 골목이 저 골목보다 몇 m 높은가" 라는 **대세 경사**는 담는다. 크기를 재는 데는 충분하다.
#
# 실행: [서버 호스트] bash ~/climax_mvp/scripts/fetch_dem_busan.sh
set -euo pipefail

OUT=~/climax_mvp/data/dem
mkdir -p "$OUT"
W=128.70; S=34.95; E=129.40; N=35.45     # 수관고 래스터와 같은 범위

STUB="https://copernicus-dem-30m.s3.amazonaws.com"
for t in N35_00_E128_00 N35_00_E129_00 N34_00_E128_00 N34_00_E129_00; do
  echo "/vsicurl/${STUB}/Copernicus_DSM_COG_10_${t}_DEM/Copernicus_DSM_COG_10_${t}_DEM.tif"
done > /tmp/dem_tiles.txt

if ! command -v gdalwarp >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo apt-get install -y -qq gdal-bin
fi
gdalwarp --version

echo "=== 크롭 (필요한 블록만) ==="
# 0.0001 deg(약 9 m) 로 맞춘다 — 수관고 래스터와 같은 격자라 코드가 단순해진다.
# 원자료가 30 m 이므로 실해상도는 30 m 다. 리샘플만 촘촘하게 한다.
time gdalwarp -q -overwrite \
  -te "$W" "$S" "$E" "$N" -te_srs EPSG:4326 -t_srs EPSG:4326 \
  -tr 0.0001 0.0001 -r bilinear -of ENVI -ot Float32 \
  --optfile /tmp/dem_tiles.txt "$OUT/busan_dem.img"

ls -la "$OUT/busan_dem.img" "$OUT/busan_dem.hdr"
gdalinfo -stats "$OUT/busan_dem.img" 2>/dev/null | grep -E "Minimum|Maximum|Mean" | head -3
