#!/usr/bin/env python3
"""SCS 논문 정본 자료 한 장 (2026-09-21) — data/scs_master_80.csv

논문의 모든 그림·표·본문 숫자는 이 파일 하나에서만 나온다.
  볕/그늘 62/18 (2026-09-25 정정, 아래 SUN_OVERRIDE)
열마다 출처가 하나다. 폐기된 열은 가져오지 않는다.

  실측 (현장 계측, 바뀌지 않음)       tier3_engine_output_80_v7_photo.csv
      측정ID 시각 권역 위도 경도 Ta RH v Tmrt PET Ts 태양고도
  볕/그늘 (v7 사진검증 라벨, 61/19)   같은 파일의 `볕`
  뷰팩터 (일관 정의, 9/21)            aug80_viewfactors.csv  SVF TVF BVF (위쪽 반구·Steyn·보정 없음)
                                      158번 사진(서제2동_07)은 자세복원 실패 → 결측
  가로 기하                           tier3_width_80.csv     width_m hw_ratio (7곳 결측)
  표고                                elev80.json            Copernicus 90 m DEM (Open-Meteo)
  엔진 무영상 입력(비교용)            v7_photo 의 tier3_svf tier3_gvi tier3_bvi

  ✗ 가져오지 않는 것
    v7_photo 의 SVF/TVF/BVF  — 9/15 폐기된 옛 파노라마 값
    aug80_indices.csv        — 9/14 판, 세 지표 정의가 섞임
    cloud 열                 — 논문에서 쓰지 않음
"""
import csv, json

D = "data"
# 볕/그늘 판독 정정 (2026-09-25, 파노라마 이음새의 관측자 그림자로 재판독; 두 판독자 합의)
#   부암제1동_05: 관측자·전신주·전기함 그림자 선명, 흰 벽 전면 볕 → 볕 (v7 사진판정 SHADE-B mid 를 뒤집음)
#   명장제4동_01: 흐린 하늘, 그림자 전무 → 그늘 유지 (확산광 = 직달광 없음)
SUN_OVERRIDE = {"20260826_부암제1동_05": "1"}

LCZ = {"부암제1동": 1, "보수동": 2, "서제2동": 3, "용호제1동": 4, "명장동": 5}
EN = {"부암제1동": "Buam 1", "보수동": "Bosu", "서제2동": "Seo 2",
      "용호제1동": "Yongho 1", "명장동": "Myeongjang"}

meas = list(csv.DictReader(open(f"{D}/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig")))
vf = {r["측정ID"]: r for r in csv.DictReader(open(f"{D}/aug80_viewfactors.csv", encoding="utf-8-sig"))}
wd = {r["측정ID"]: r for r in csv.DictReader(open(f"{D}/tier3_width_80.csv", encoding="utf-8-sig"))}
ev = json.load(open(f"{D}/elev80.json"))
assert len(meas) == 80 and len(ev) == 80

rows = []
for r, e in zip(meas, ev):
    k = r["측정ID"]; v = vf[k]; w = wd.get(k, {})
    bad = "자세복원" in (v.get("비고") or "")
    f = lambda x: "" if bad else x
    rows.append(dict(
        측정ID=k, 시각=r["시각"], 권역=r["권역"], LCZ=LCZ[r["권역"]], name=EN[r["권역"]],
        위도=r["위도"], 경도=r["경도"], 표고_m=e,
        Ta=r["Ta"], RH=r["RH"], v=r["v"], Tmrt=r["Tmrt"], PET=r["PET"], Ts=r["Ts"],
        태양고도=r["태양고도"], sun=SUN_OVERRIDE.get(k, r["볕"]),
        SVF=f(v["SVF"]), TVF=f(v["TVF"]), BVF=f(v["BVF"]), 사진=v["사진"],
        width_m=w.get("width_m", ""), hw_ratio=w.get("hw_ratio", ""),
        geo_SVF=r["tier3_svf"], geo_GVI=r["tier3_gvi"], geo_BVI=r["tier3_bvi"]))

with open(f"{D}/scs_master_80.csv", "w", newline="", encoding="utf-8-sig") as fh:
    wr = csv.DictWriter(fh, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
print("wrote data/scs_master_80.csv", len(rows),
      "| SVF 결측", sum(1 for x in rows if x["SVF"] == ""),
      "| 폭 결측", sum(1 for x in rows if x["width_m"] == ""),
      "| 볕", sum(1 for x in rows if x["sun"] == "1"))
