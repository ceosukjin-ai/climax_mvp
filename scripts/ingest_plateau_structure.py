#!/usr/bin/env python3
"""PLATEAU 건축물 構造種別(RC/S/W…) → 기존 OSM 타일에 벽재질 정밀 오버라이드 (2026-09-09).

무영상·전지구 파이프라인의 벽재질 스테이지 정밀화(일본). PLATEAU CityGML(建築物)에서
건물별 구조종별과 footprint 중심을 뽑아, backend/data/buildings 의 OSM 건물 중 가장 가까운
것에 매칭해 `building:material` 을 채운다(런타임 dominant_wall_material 이 최우선 사용).

설계 요점:
- 구조→재질은 **열질량** 구분(목조/콘크리트/철골(ALC≈콘크리트)/조적/석재)만. **유리는 구조가
  아니라 외피** 신호라 OSM 용도(office/commercial…)로만 판정 → 매칭 시 OSM이 glass면 glass 유지.
- 코드→라벨은 데이터셋 자체 코드리스트(BuildingDetailAttribute_buildingStructureType.xml)로
  해석해 버전 변동에 견고. 라벨 해석 실패 시 코드표 폴백.
- 네임스페이스 버전차를 피하려고 XML은 **local-name**으로 매칭. footprint는 lod0RoofEdge
  (없으면 lod0FootPrint, 그것도 없으면 첫 posList), EPSG:6697 → posList = "lat lon alt …".

사용(맥 터미널):
  pip3 install lxml
  # PLATEAU 도쿄23구 CityGML zip 풀어서 udx/bldg/*.gml 이 있는 루트를 지정
  python3 scripts/ingest_plateau_structure.py /path/to/13100_tokyo23-ku_2020_citygml/udx
  #   (선택) 검증용 bbox 한정:  --bbox 139.685,35.655,139.710,35.680
출력: backend/data/buildings/<타일>.json 의 건물 tags 에 building:material 추가(제자리 갱신).
"""
from __future__ import annotations

import glob
import math
import os
import sys

from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TILE_DIR = os.path.join(ROOT, "backend", "data", "buildings")
# wall_material 재사용(용도→재질, 라벨→재질)
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.services import wall_material as WM  # noqa: E402

MATCH_M = 22.0          # OSM↔PLATEAU 중심 최근접 허용거리(m)
CELL_DEG = 0.0003       # 근접검색 서브그리드(~30m)


def _lname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def tile_key(lat: float, lon: float) -> str:
    return f"{int(math.floor(lat * 100))}_{int(math.floor(lon * 100))}"


def load_codelist(udx_root: str) -> dict:
    """데이터셋 코드리스트 → {코드: 재질클래스}. 못 찾으면 빈 dict(코드표 폴백 사용)."""
    cands = glob.glob(os.path.join(udx_root, "..", "codelists",
                                   "BuildingDetailAttribute_buildingStructureType.xml"))
    cands += glob.glob(os.path.join(udx_root, "**",
                                    "BuildingDetailAttribute_buildingStructureType.xml"),
                       recursive=True)
    code2mat: dict[str, str] = {}
    for path in cands[:1]:
        try:
            tree = etree.parse(path)
        except Exception:
            continue
        name = desc = None
        for el in tree.iter():
            ln = _lname(el.tag)
            if ln == "name":
                name = (el.text or "").strip()
            elif ln == "description":
                desc = (el.text or "").strip()
            if name and desc is not None:      # 한 엔트리의 코드+라벨이 모이면 매핑
                mat = WM.struct_label_to_material(desc)
                if mat:
                    code2mat[name] = mat
                name = desc = None
    return code2mat


def _centroid_from_poslist(text: str) -> tuple[float, float] | None:
    """EPSG:6697 posList("lat lon alt lat lon alt …") → 링 중심(lat, lon)."""
    v = text.split()
    if len(v) < 9:                      # 최소 3점×3좌표
        return None
    try:
        lat = [float(v[i]) for i in range(0, len(v) - 2, 3)]
        lon = [float(v[i]) for i in range(1, len(v) - 1, 3)]
    except ValueError:
        return None
    if not lat:
        return None
    return sum(lat) / len(lat), sum(lon) / len(lon)


def parse_gml(path: str, code2mat: dict, bbox, buckets: dict) -> int:
    """한 GML → (중심, 재질)들을 buckets[tile][cell] 에 적재. 반환: 구조 있는 건물 수."""
    W, S, E, N = bbox if bbox else (-999, -999, 999, 999)
    n = 0
    ctx = etree.iterparse(path, events=("end",), tag="{*}Building", huge_tree=True)
    for _, bldg in ctx:
        code = None
        roof = foot = anyp = None
        for el in bldg.iter():
            ln = _lname(el.tag)
            if ln == "buildingStructureType" and el.text:
                code = el.text.strip()
            elif ln in ("lod0RoofEdge", "lod0FootPrint", "lod0Geometry"):
                pls = [e.text for e in el.iter()
                       if _lname(e.tag) == "posList" and e.text]
                if pls:
                    if ln == "lod0RoofEdge" and roof is None:
                        roof = pls[0]
                    elif foot is None:
                        foot = pls[0]
            elif ln == "posList" and el.text and anyp is None:
                anyp = el.text
        src = roof or foot or anyp
        cen = _centroid_from_poslist(src) if src else None
        if code and cen:
            la, lo = cen
            if W <= lo <= E and S <= la <= N:
                mat = code2mat.get(code) or WM.material_from_plateau(code)
                if mat:
                    tk = tile_key(la, lo)
                    ck = (int(math.floor(la / CELL_DEG)), int(math.floor(lo / CELL_DEG)))
                    buckets.setdefault(tk, {}).setdefault(ck, []).append((la, lo, mat))
                    n += 1
        bldg.clear()
        while bldg.getprevious() is not None:
            del bldg.getparent()[0]
    return n


def _osm_centroid(geom: list) -> tuple[float, float] | None:
    if not geom:
        return None
    la = sum(g["lat"] for g in geom) / len(geom)
    lo = sum(g["lon"] for g in geom) / len(geom)
    return la, lo


def _nearest(la: float, lo: float, cells: dict) -> str | None:
    """서브그리드에서 (la,lo) 최근접 PLATEAU 재질. MATCH_M 이내만."""
    best = None
    bestd = MATCH_M
    c0 = int(math.floor(la / CELL_DEG))
    c1 = int(math.floor(lo / CELL_DEG))
    for dc0 in (-1, 0, 1):
        for dc1 in (-1, 0, 1):
            for (pla, plo, mat) in cells.get((c0 + dc0, c1 + dc1), ()):  # noqa: E741
                d = math.hypot((pla - la) * 111320.0,
                               (plo - lo) * 111320.0 * math.cos(math.radians(la)))
                if d < bestd:
                    bestd = d
                    best = mat
    return best


def apply_to_tiles(buckets: dict) -> tuple[int, int]:
    """buckets 를 기존 타일에 매칭·주입. 반환: (매칭된 건물, 처리 타일)."""
    import json
    matched = tiles_done = 0
    for tk, cells in buckets.items():
        fp = os.path.join(TILE_DIR, tk + ".json")
        if not os.path.isfile(fp):
            continue
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        changed = False
        for el in data.get("elements") or []:
            cen = _osm_centroid(el.get("geometry") or [])
            if not cen:
                continue
            struct_mat = _nearest(cen[0], cen[1], cells)
            if not struct_mat:
                continue
            tags = el.setdefault("tags", {})
            # 유리(외피)는 OSM 용도로만 → OSM이 glass면 유지, 아니면 구조재질 채택
            osm_mat = WM.material_from_osm(tags)
            final = "glass" if osm_mat == "glass" else struct_mat
            if tags.get("building:material") != final:
                tags["building:material"] = final
                changed = True
            matched += 1
        if changed:
            json.dump(data, open(fp, "w", encoding="utf-8"),
                      ensure_ascii=False, separators=(",", ":"))
        tiles_done += 1
    return matched, tiles_done


def main(argv):
    import time
    if not argv:
        print("사용: python3 scripts/ingest_plateau_structure.py <udx_root> [--bbox W,S,E,N]")
        return 1
    udx_root = argv[0]
    bbox = None
    if "--bbox" in argv:
        W, S, E, N = (float(x) for x in argv[argv.index("--bbox") + 1].split(","))
        bbox = (W, S, E, N)
        print(f"bbox 한정: {bbox}")
    gmls = sorted(glob.glob(os.path.join(udx_root, "bldg", "*.gml")))
    if not gmls:
        gmls = sorted(glob.glob(os.path.join(udx_root, "**", "*_bldg_*.gml"), recursive=True))
    print(f"건축물 GML {len(gmls)}개")
    code2mat = load_codelist(udx_root)
    print(f"코드리스트 해석: {len(code2mat)}개 코드 → 재질  {code2mat}")
    t0 = time.time()
    buckets: dict = {}
    total = 0
    for i, g in enumerate(gmls, 1):
        try:
            total += parse_gml(g, code2mat, bbox, buckets)
        except Exception as e:
            print(f"  건너뜀 {os.path.basename(g)}: {type(e).__name__} {e}")
        if i % 50 == 0:
            print(f"  {i}/{len(gmls)} GML, 구조건물 {total:,}, {time.time()-t0:.0f}s", flush=True)
    print(f"파싱 완료: 구조 건물 {total:,} / {time.time()-t0:.0f}s")
    matched, td = apply_to_tiles(buckets)
    print(f"타일 주입 완료: 매칭 {matched:,}동 → 타일 {td}개 / {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
