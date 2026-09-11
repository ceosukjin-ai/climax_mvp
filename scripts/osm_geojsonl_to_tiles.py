#!/usr/bin/env python3
"""OSM 건물(geojsonseq, osmium export) → 로컬 건물 타일 (2026-09-11, 일본 전국용. 호스트 python3, 의존성 없음).

서버 절차 (일본 전국 — Geofabrik japan 1.8GB):
  wget -O ~/data/japan-latest.osm.pbf https://download.geofabrik.de/asia/japan-latest.osm.pbf
  osmium tags-filter -o ~/data/jp_bldg.osm.pbf ~/data/japan-latest.osm.pbf w/building a/building
  osmium export -f geojsonseq --add-unique-id=type_id -o ~/data/jp_bldg.geojsonl ~/data/jp_bldg.osm.pbf
  python3 ~/climax_mvp/scripts/osm_geojsonl_to_tiles.py ~/data/jp_bldg.geojsonl --out ~/climax_mvp/backend/data/buildings
기존 타일(도쿄 23구·PLATEAU 재질 주입분)은 id 기준 병합 — 기존 element 우선(재질 태그 보존).
형식은 extract_tokyo_tiles.py 와 동일(KEEP 태그, 좌표 6자리, 압축 JSON). 타일 파일 존재 = 권위 원천이므로
건물 없는 타일도 빈 파일로 만들어 둔다(--empty-bbox S W N E 로 범위 지정 시) → 실시간 V-World/OSM 호출 방지.
"""
from __future__ import annotations
import argparse, json, math, os, sys, time
from collections import defaultdict

KEEP = ("building", "building:material", "building:levels", "height", "name", "building:part")


def tkey(lat: float, lon: float) -> str:
    return f"{int(math.floor(lat * 100))}_{int(math.floor(lon * 100))}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojsonl"); ap.add_argument("--out", required=True)
    ap.add_argument("--empty-bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"), help="이 범위의 건물 없는 타일도 빈 파일 생성")
    ap.add_argument("--flush-every", type=int, default=400000, help="메모리 상한: 이만큼 모이면 디스크로 병합")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time(); n = skip = 0; written_tiles = set()
    tiles: dict[str, dict] = defaultdict(dict)

    def flush():
        nonlocal tiles
        for k, byid in tiles.items():
            fpath = os.path.join(a.out, k + ".json")
            if os.path.isfile(fpath):
                try:
                    for el in (json.load(open(fpath, encoding="utf-8")) or {}).get("elements") or []:
                        byid[el.get("id")] = el          # 기존 우선(PLATEAU 재질 등 보존)
                except Exception:  # noqa: BLE001
                    pass
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump({"elements": list(byid.values())}, f, ensure_ascii=False, separators=(",", ":"))
            written_tiles.add(k)
        tiles = defaultdict(dict)

    pending = 0
    with open(a.geojsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line.startswith("{"):
                continue
            ft = json.loads(line)
            p = ft.get("properties") or {}
            if "building" not in p:
                skip += 1; continue
            oid_s = str(ft.get("id") or p.get("@id") or "")
            # 기존 도쿄 타일(extract_tokyo_tiles.py)은 id=way 정수 → 같은 형식으로 맞춰 병합 시 중복 제거. relation 은 음수.
            if oid_s.startswith("w") and oid_s[1:].isdigit():
                oid = int(oid_s[1:])
            elif oid_s.startswith("r") and oid_s[1:].isdigit():
                oid = -int(oid_s[1:])
            else:
                skip += 1; continue
            g = ft.get("geometry") or {}; t = g.get("type"); c = g.get("coordinates") or []
            if t == "Polygon":
                rings = [c[0]] if c else []
            elif t == "MultiPolygon":
                rings = [pp[0] for pp in c if pp]
            else:
                skip += 1; continue
            tags = {k: p[k] for k in KEEP if k in p}
            for k, ring in enumerate(rings):
                if len(ring) < 4:
                    continue
                geom = [{"lat": round(y, 6), "lon": round(x, 6)} for x, y in ring]
                eid = oid if k == 0 else f"{oid}:{k}"
                tiles[tkey(geom[0]["lat"], geom[0]["lon"])][eid] = {"id": eid, "geometry": geom, "tags": tags}
                n += 1; pending += 1
            if pending >= a.flush_every:
                flush(); pending = 0
                print(f"  {n:,} 건물 ({time.time()-t0:.0f}s)", flush=True)
    flush()
    empty = 0
    if a.empty_bbox:
        S, W, N, E = a.empty_bbox
        for la in range(int(math.floor(S * 100)), int(math.floor(N * 100)) + 1):
            for lo in range(int(math.floor(W * 100)), int(math.floor(E * 100)) + 1):
                fpath = os.path.join(a.out, f"{la}_{lo}.json")
                if not os.path.isfile(fpath):
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write('{"elements":[]}')
                    empty += 1
    print(f"건물 {n:,} (건너뜀 {skip:,}) → 타일 {len(written_tiles):,}개 갱신, 빈 타일 {empty:,}개 생성  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
