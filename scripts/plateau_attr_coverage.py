#!/usr/bin/env python3
"""PLATEAU 건물 속성 채움률 — 일본 BTLI 입력으로 쓸 수 있나 (2026-09-25).

일본 BTLI 에 필요한 것: 건축연도(→ 단열 등급: 1980 전 무단열 / 1980~92 S55 / 1992~99 H4 / 이후),
구조(목조·S·RC → 열용량·U), 용도, 연면적. 한국은 건축물대장이 다 주지만 일본은 공개 대장이 없다.
PLATEAU(도시계획기초조사 속성)에 들어 있는 도시가 있다 — 도쿄 23구 2022 판에 얼마나 채워졌는지 센다.

  docker run --rm -v ~/plateau:/plateau -v ~/climax_mvp:/repo climax-backend:latest \
    python3 /repo/scripts/plateau_attr_coverage.py /plateau            # zip 이 있는 폴더 또는 zip 경로
표본: zip 안 udx/bldg/*.gml 중 고르게 40개(구 전체에 퍼지게). 한 파일씩 꺼내 세고 지운다.
"""
from __future__ import annotations
import glob, os, sys, tempfile, zipfile
from collections import Counter

FIELDS = ["yearOfConstruction", "storeysAboveGround", "measuredHeight", "usage",
          "buildingStructureType", "fireproofStructureType", "totalFloorArea",
          "buildingFootprintArea", "detailedUsage", "surveyYear"]


def _ln(t):
    return t.rsplit("}", 1)[-1] if "}" in t else t


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "/plateau"
    n_files = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    zp = arg if arg.endswith(".zip") else (sorted(glob.glob(os.path.join(arg, "*.zip"))) or [None])[0]
    if not zp or not os.path.exists(zp):
        print("PLATEAU zip 없음:", arg); return
    import xml.etree.ElementTree as ET
    zf = zipfile.ZipFile(zp)
    names = sorted(n for n in zf.namelist() if "/udx/bldg/" in n and n.endswith(".gml"))
    step = max(1, len(names) // n_files)
    pick = names[::step][:n_files]
    print(f"{os.path.basename(zp)} — bldg gml {len(names)}개 중 {len(pick)}개 표본")
    have, n_b = Counter(), 0
    struct, fire, usage, yb = Counter(), Counter(), Counter(), Counter()
    tmpd = tempfile.mkdtemp()
    for i, nm in enumerate(pick, 1):
        p = zf.extract(nm, tmpd)
        try:
            for _ev, el in ET.iterparse(p, events=("end",)):
                if _ln(el.tag) != "Building":
                    continue
                n_b += 1
                seen = {}
                for sub in el.iter():
                    k = _ln(sub.tag)
                    if k in FIELDS and (sub.text or "").strip() and k not in seen:
                        seen[k] = sub.text.strip()
                for k in seen:
                    have[k] += 1
                if "buildingStructureType" in seen: struct[seen["buildingStructureType"]] += 1
                if "fireproofStructureType" in seen: fire[seen["fireproofStructureType"]] += 1
                if "usage" in seen: usage[seen["usage"]] += 1
                y = seen.get("yearOfConstruction", "")[:4]
                if y.isdigit():
                    y = int(y)
                    yb["~1979 무단열기" if y < 1980 else "1980~91 S55" if y < 1992 else
                       "1992~98 H4" if y < 1999 else "1999~2024 H11+" if y < 2025 else "2025~ 의무화"] += 1
                el.clear()
        finally:
            os.remove(p)
        if i % 10 == 0:
            print(f"  {i}/{len(pick)} 건물 {n_b:,}", flush=True)
    print(f"\n건물 {n_b:,}동 — 속성 채움률")
    for k in FIELDS:
        print(f"  {k:24s} {have[k] / max(1, n_b) * 100:5.1f}%")
    print("\n구조(buildingStructureType 코드):", struct.most_common(10))
    print("내화구조(fireproofStructureType 코드):", fire.most_common(6))
    print("용도(usage 코드):", usage.most_common(8))
    print("건축연도 → 단열 기준 시기:", dict(yb))
    print("\n코드 의미는 PLATEAU 코드리스트(Building_buildingStructureType.xml 등)로 확인할 것 — 여기서 짐작하지 않는다.")


if __name__ == "__main__":
    main()
