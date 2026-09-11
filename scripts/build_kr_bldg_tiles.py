#!/usr/bin/env python3
"""GIS건물통합정보(SHP) + 건축물대장 표제부(mart_djy_03.txt) → 로컬 건물 타일 (2026-09-11).

왜: V-World 건물 폴리곤은 층수 결측이 많다(부암 241/245). 국토부 GIS건물통합정보(폴리곤 47만/부산)는
층수 61%·높이 32%만 있고, 건축물대장 표제부는 동별 층수·높이가 거의 다 있다.
둘을 PNU(19자리)로 조인해 층수·높이를 채운 뒤 기존 로컬 타일 형식(0.01°, Overpass elements)으로 내보낸다.
→ geo._rings_from_local 이 그대로 읽고, build_skyline_grid --force --src-hint gis-bldg 로 격자 재계산.

1단계(서버 셸, gdal 컨테이너 — SHP 5186 → GeoJSONSeq 4326, 필드 A2 PNU·A16 높이·A24 건물명·A25 동명·A26 지상층·A27 지하층):
  docker run --rm -v $HOME/data:/data ghcr.io/osgeo/gdal:alpine-small-latest ogr2ogr -f GeoJSONSeq -t_srs EPSG:4326 \
    -select A0,A2,A16,A24,A25,A26,A27 -lco COORDINATE_PRECISION=6 --config SHAPE_ENCODING CP949 \
    /data/bldg_26.geojsonl /data/AL_D010_26_20260909/AL_D010_26_20260909.shp
2단계(서버 호스트 python3, 의존성 없음):
  python3 ~/climax_mvp/scripts/build_kr_bldg_tiles.py --geojsonl ~/data/bldg_26.geojsonl \
    --pyojebu ~/data/mart_djy_03.txt --sido 26 --out ~/climax_mvp/backend/data/buildings
3단계: docker compose ... restart api  →  build_skyline_grid.py --bbox ... --force --src-hint gis-bldg

표제부 열(0-based, 파이프 구분, UTF-8, 헤더 없음 — 건축HUB 2026-08 대용량 형식): 0 관리번호, 5 대지위치, 7 건물명,
8 시군구코드, 9 법정동코드, 10 대지구분, 11 번, 12 지, 22 동명, 23 주부속구분코드(0=주건축물), 31 구조코드,
34 주용도코드, 42 높이, 43 지상층수, 44 지하층수. 실행 시 샘플 출력 + 층수있음 비율(<80%면 중단)로 자가검증.
"""
from __future__ import annotations
import argparse, json, math, os, sys, time
from collections import defaultdict

COL = dict(sgg=8, bjd=9, gb=10, bun=11, ji=12, dong=22, main=23, height=42, floors=43, under=44)   # 건축HUB 2026-08 형식(서버 head 로 확인)
SRC = "gis-bldg-2026-09"


def tile_key(lat: float, lon: float) -> str:
    return f"{int(math.floor(lat * 100))}_{int(math.floor(lon * 100))}"


def _i(s) -> int:
    try:
        return int(float(str(s).strip() or 0))
    except ValueError:
        return 0


def _f(s) -> float:
    try:
        return float(str(s).strip() or 0)
    except ValueError:
        return 0.0


def load_pyojebu(path: str, sido: str | None):
    """PNU → [(동명, 주건축물여부, 지상층수, 높이)]. sido 지정 시 그 시도만(메모리 절약)."""
    by_pnu: dict[str, list] = defaultdict(list)
    n = bad = 0
    t0 = time.time()
    enc = "utf-8"
    try:
        with open(path, "rb") as fb:
            fb.read(4096).decode("utf-8")
    except UnicodeDecodeError:
        enc = "cp949"
    print(f"표제부 인코딩: {enc}")
    with open(path, encoding=enc, errors="replace") as f:
        for line in f:
            c = line.rstrip("\r\n").split("|")
            if len(c) < 45:
                bad += 1
                continue
            sgg = c[COL["sgg"]].strip()
            if sido and not sgg.startswith(sido):
                continue
            n += 1
            pnu = f"{sgg}{c[COL['bjd']].strip()}{c[COL['gb']].strip()}{c[COL['bun']].strip().zfill(4)}{c[COL['ji']].strip().zfill(4)}"
            if len(pnu) != 19:
                bad += 1
                continue
            by_pnu[pnu].append((c[COL["dong"]].strip(), c[COL["main"]].strip() == "0",
                                _i(c[COL["floors"]]), _f(c[COL["height"]])))
            if n <= 3:
                print(f"  표제부 샘플: pnu={pnu} 동={c[COL['dong']].strip()!r} 주부속={c[COL['main']].strip()} "
                      f"층={c[COL['floors']].strip()} 높이={c[COL['height']].strip()} 구조={c[32].strip()!r} 용도={c[35].strip()!r}")
    with_floor = sum(1 for L in by_pnu.values() for x in L if x[2] > 0)
    tot = sum(len(L) for L in by_pnu.values())
    print(f"표제부: {n:,}행 (건너뜀 {bad}) → 필지 {len(by_pnu):,}, 동 {tot:,}, 층수있음 {with_floor:,} "
          f"({100*with_floor/max(tot,1):.1f}%)  {time.time()-t0:.0f}s")
    if tot and with_floor / tot < 0.8:
        print("!! 층수 열 위치가 의심스럽다(층수 있음 <80%). COL 확인 후 재실행."); sys.exit(2)
    return by_pnu


def pick(rows: list, dong: str):
    """조인 규칙: 동명 일치 > 주건축물 중 최대층 > 전체 최대층 (보수적: 그늘 과소 방지)."""
    if dong:
        m = [r for r in rows if r[0] and (r[0] == dong or dong in r[0] or r[0] in dong)]
        if m:
            return max(m, key=lambda r: r[2])
    main = [r for r in rows if r[1] and r[2] > 0]
    if main:
        return max(main, key=lambda r: r[2])
    return max(rows, key=lambda r: r[2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojsonl", required=True); ap.add_argument("--pyojebu", required=True)
    ap.add_argument("--sido", default=None, help="시군구코드 앞 2자리(부산 26). 생략=전국")
    ap.add_argument("--out", required=True); ap.add_argument("--replace", action="store_true", help="같은 타일의 기존 kr: 건물을 교체")
    a = ap.parse_args()
    by_pnu = load_pyojebu(a.pyojebu, a.sido)
    tiles: dict[str, dict] = defaultdict(dict)
    st = dict(n=0, own=0, joined=0, joined_dong=0, still0=0, multi=0)
    t0 = time.time()
    with open(a.geojsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line.startswith("{"):
                continue
            ft = json.loads(line)
            p = ft.get("properties") or {}
            g = ft.get("geometry") or {}
            polys = [g["coordinates"]] if g.get("type") == "Polygon" else (g.get("coordinates") or [])
            pnu = str(p.get("A2") or "").strip()
            floors, height = _i(p.get("A26")), _f(p.get("A16"))
            name, dong = (p.get("A24") or "").strip(), (p.get("A25") or "").strip()
            st["n"] += 1
            if floors > 0:
                st["own"] += 1
            elif pnu in by_pnu:
                r = pick(by_pnu[pnu], dong)
                if r[2] > 0:
                    floors = r[2]; st["joined"] += 1
                    if dong and r[0] and (r[0] == dong or dong in r[0] or r[0] in dong):
                        st["joined_dong"] += 1
                    if height <= 0 and r[3] > 0:
                        height = r[3]
                    if len(by_pnu[pnu]) > 1:
                        st["multi"] += 1
            tags = {"building": "yes", "src": SRC, "pnu": pnu}
            if floors > 0:
                tags["building:levels"] = str(floors)
            else:
                st["still0"] += 1             # 층수 미상 폴리곤도 넣는다: 격자(skyline)는 2층 기본, 실시간 폴리곤 경로는 제외(기존 규칙 그대로)
            if height > 0:
                tags["height"] = f"{height:.1f}"
            if name:
                tags["name"] = name
            for k, poly in enumerate(polys):
                ring = poly[0] if poly else []
                if len(ring) < 4:
                    continue
                geom = [{"lat": round(y, 6), "lon": round(x, 6)} for x, y in ring]
                eid = f"kr:{p.get('A0')}" + (f":{k}" if k else "")
                tiles[tile_key(geom[0]["lat"], geom[0]["lon"])][eid] = {"id": eid, "geometry": geom, "tags": tags}
    print(f"폴리곤 {st['n']:,}: 자체층수 {st['own']:,}, 표제부조인 {st['joined']:,}(동명일치 {st['joined_dong']:,}, 다동필지 {st['multi']:,}), "
          f"여전히 결측 {st['still0']:,} ({100*st['still0']/max(st['n'],1):.1f}%)  {time.time()-t0:.0f}s")
    os.makedirs(a.out, exist_ok=True)
    written = 0
    for tkey, byid in tiles.items():
        fpath = os.path.join(a.out, tkey + ".json")
        if os.path.isfile(fpath):
            try:
                for el in (json.load(open(fpath, encoding="utf-8")) or {}).get("elements") or []:
                    eid = el.get("id")
                    if a.replace and isinstance(eid, str) and eid.startswith("kr:"):
                        continue
                    byid.setdefault(eid, el)
            except Exception:  # noqa: BLE001
                pass
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump({"elements": list(byid.values())}, f, ensure_ascii=False, separators=(",", ":"))
        written += 1
    print(f"타일 {written}개 @ {a.out}  (건물 {sum(len(v) for v in tiles.values()):,})")


if __name__ == "__main__":
    main()
