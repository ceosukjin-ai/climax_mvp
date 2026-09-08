#!/usr/bin/env python3
"""건물 GeoJSON/Overpass-JSON을 0.01° 타일로 사전적재 (전세계 파일럿, 2026-09-08).

프로덕션 서버는 Overpass 아웃바운드가 막혀 있어 해외 건물을 실시간 조회할 수 없다.
타깃 도시 건물을 미리 받아 이 스크립트로 타일링해 backend/data/buildings/ 에 넣으면
geo.py `_rings_from_local` 이 로컬에서 조회한다. 타일은 건물 첫 꼭짓점 기준으로 배정하고,
기존 타일 파일과 id 기준으로 병합(중복 제거)한다.

사용:
  python scripts/ingest_buildings.py data/tokyo_shibuya.json
입력: Overpass out geom 결과({"elements":[{id,geometry:[{lat,lon}],tags}]}).
"""
import json
import math
import os
import sys

OUT_DIR = os.environ.get("LOCAL_BUILDING_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "buildings"
)


def tile_key(lat: float, lon: float) -> str:
    return f"{int(math.floor(lat * 100))}_{int(math.floor(lon * 100))}"


def main(paths):
    os.makedirs(OUT_DIR, exist_ok=True)
    tiles: dict[str, dict] = {}   # tkey -> {id: element}
    total = 0
    for path in paths:
        data = json.load(open(path, encoding="utf-8"))
        for el in data.get("elements") or []:
            geom = el.get("geometry") or []
            if len(geom) < 4:
                continue
            tkey = tile_key(geom[0]["lat"], geom[0]["lon"])
            tiles.setdefault(tkey, {})[el.get("id", id(el))] = {
                "id": el.get("id"), "geometry": geom, "tags": el.get("tags") or {},
            }
            total += 1
    written = 0
    for tkey, byid in tiles.items():
        fpath = os.path.join(OUT_DIR, tkey + ".json")
        if os.path.isfile(fpath):
            try:
                for el in (json.load(open(fpath, encoding="utf-8")) or {}).get("elements") or []:
                    byid.setdefault(el.get("id", id(el)), el)
            except Exception:
                pass
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump({"elements": list(byid.values())}, f, ensure_ascii=False)
        written += 1
    print(f"적재 완료: 건물 {total}동 → 타일 {written}개 @ {OUT_DIR}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용: python scripts/ingest_buildings.py <overpass.json> [...]"); sys.exit(1)
    main(sys.argv[1:])
