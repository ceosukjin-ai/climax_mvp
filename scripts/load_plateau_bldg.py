#!/usr/bin/env python3
"""PLATEAU LOD1 건물을 **직접** bldg_poly 에 넣는다 — OSM 을 거치지 않는다 (2026-09-18).

왜 바꾸나: 9/17 에는 OSM 건물에 PLATEAU 높이를 *붙였다*(22 m 최근접). 88만 동이 붙었지만
41%(외곽 소형)는 여전히 2층 추정이고, 최근접 매칭이 옆 건물에 붙는 경우도 있다.
스페인 지적을 직접 넣기로 하면서 원칙을 바로잡는다: **정부 자료가 있으면 그것이 1순위, OSM 은 폴백.**

PLATEAU 는 전국이 아니라 329개 도시다. 그래서 타일 단위로 배타 처리한다 —
PLATEAU 건물이 들어가는 타일(0.01°)에서는 **OSM 건물을 지운다.** 같은 타일에 둘이 섞이면
건물이 이중으로 잡혀 SVF 가 틀린다. 지운 OSM 은 Geofabrik 에서 20분이면 다시 받는다.

넣는 것 (건물마다):
  · 윤곽: lod0RoofEdge / lod0FootPrint 의 첫 posList (EPSG:6697 = 위도 경도 높이 순, WGS84 와 동일 취급)
  · height = bldg:measuredHeight (실측)   · building:levels = bldg:storeysAboveGround (있으면)
  · plateau:usage = bldg:usage 코드      · source = plateau
  · id = plateau/<gml:id>

  docker run --rm --env-file /tmp/api.env -v $HOME/plateau:/plateau -v $HOME/climax_mvp:/repo \
    climax-backend:latest python3 /repo/scripts/load_plateau_bldg.py /plateau/13100_tokyo23-ku_2022_citygml.zip --dry --limit-files 5
"""
from __future__ import annotations
import argparse, asyncio, json, math, os, sys, tempfile, time, zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, "/app")

INSERT = ("INSERT INTO bldg_poly (id, tags, geom, tkey) VALUES ($1,$2::jsonb,ST_GeomFromText($3,4326),$4) "
          "ON CONFLICT (id) DO UPDATE SET tags=EXCLUDED.tags, geom=EXCLUDED.geom, tkey=EXCLUDED.tkey")


def _ln(t: str) -> str:
    return t.rsplit("}", 1)[-1] if "}" in t else t


def tkey(lat: float, lon: float) -> str:
    return f"{int(math.floor(lat*100))}_{int(math.floor(lon*100))}"


def parse(path: str):
    """GML → [(gml_id, height, storeys, usage, ring[(lat,lon)...])]"""
    out = []
    for _ev, el in ET.iterparse(path, events=("end",)):
        if _ln(el.tag) != "Building":
            continue
        gid = None
        for k, v in el.attrib.items():
            if _ln(k) == "id":
                gid = v
        h = st = us = None; ring = None
        for sub in el.iter():
            n = _ln(sub.tag)
            if n == "measuredHeight" and sub.text:
                try: h = float(sub.text.strip())
                except ValueError: pass
            elif n == "storeysAboveGround" and sub.text:
                try: st = int(sub.text.strip())
                except ValueError: pass
            elif n == "usage" and sub.text and us is None:
                us = sub.text.strip()
            elif n in ("lod0RoofEdge", "lod0FootPrint") and ring is None:
                for pl in sub.iter():
                    if _ln(pl.tag) == "posList" and pl.text:
                        v = pl.text.split()
                        try:
                            ring = [(float(v[i]), float(v[i+1])) for i in range(0, len(v) - 2, 3)]
                        except ValueError:
                            ring = None
                        break
        el.clear()
        if gid and h and h > 0 and ring and len(ring) >= 3:
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            out.append((gid, h, st, us, ring))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip"); ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit-files", type=int, default=0)
    a = ap.parse_args()

    import asyncpg
    from app.config import get_settings
    conn = await asyncpg.connect(get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://"))

    zf = zipfile.ZipFile(a.zip)
    names = [n for n in zf.namelist() if "/udx/bldg/" in n and n.endswith(".gml")]
    if a.limit_files:
        names = names[: a.limit_files]
    print(f"\nPLATEAU 건물 GML {len(names)}개")
    tmpdir = tempfile.mkdtemp(); t0 = time.time()
    n_b = n_ins = n_del = 0; tiles_done: set = set(); hs = []; st_cnt = 0

    for i, name in enumerate(names, 1):
        tmp = os.path.join(tmpdir, "cur.gml")
        with zf.open(name) as src, open(tmp, "wb") as dst:
            while True:
                b = src.read(1 << 20)
                if not b: break
                dst.write(b)
        items = parse(tmp); os.remove(tmp)
        n_b += len(items)
        rows = []; tiles_here: dict[str, int] = {}
        for gid, h, st, us, ring in items:
            tk = tkey(ring[0][0], ring[0][1])
            tags = {"building": "yes", "source": "plateau", "height": f"{h:.1f}"}
            if st: tags["building:levels"] = str(st); st_cnt += 1
            if us: tags["plateau:usage"] = us
            wkt = "POLYGON((" + ",".join(f"{lo:.7f} {la:.7f}" for la, lo in ring) + "))"
            rows.append((f"plateau/{gid}", json.dumps(tags), wkt, tk))
            tiles_here[tk] = tiles_here.get(tk, 0) + 1
            hs.append(h)
        if not a.dry and rows:
            # 이 파일이 처음 건드리는 타일에서 OSM(비-plateau) 건물을 뺀다 — 이중 계산 방지
            new_tiles = [t for t in tiles_here if t not in tiles_done]
            if new_tiles:
                r = await conn.execute(
                    "DELETE FROM bldg_poly WHERE tkey = ANY($1::text[]) AND (tags->>'source') IS DISTINCT FROM 'plateau'",
                    new_tiles)
                n_del += int(r.split()[-1]); tiles_done.update(new_tiles)
            await conn.executemany(INSERT, rows)
            n_ins += len(rows)
        if i % 20 == 0 or i == len(names):
            print(f"  [{i}/{len(names)}] 건물 {n_b:,}  적재 {n_ins:,}  OSM 제거 {n_del:,}  타일 {len(tiles_done)}  {time.time()-t0:.0f}s", flush=True)

    hs.sort()
    print(f"\n합계: 건물 {n_b:,}  높이 중앙 {hs[len(hs)//2]:.1f} m  90% {hs[int(len(hs)*.9)]:.1f} m  층수 있음 {100*st_cnt/max(n_b,1):.0f}%")
    if a.dry:
        print("--dry: DB 를 바꾸지 않았다."); await conn.close(); return
    await conn.execute(
        "INSERT INTO bldg_tile (tkey, n, src, built_at) "
        "SELECT tkey, count(*), 'plateau', NOW() FROM bldg_poly WHERE tags->>'source'='plateau' GROUP BY tkey "
        "ON CONFLICT (tkey) DO UPDATE SET n=EXCLUDED.n, src=EXCLUDED.src, built_at=NOW()")
    print(f"✅ 완료 {n_ins:,}동 적재, OSM {n_del:,}동 제거, 타일 {len(tiles_done)}개 → docker restart climax-api")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
