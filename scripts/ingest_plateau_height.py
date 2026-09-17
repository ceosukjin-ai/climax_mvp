#!/usr/bin/env python3
"""PLATEAU 실측 높이 → DB 의 OSM 건물에 `height` 태그 주입 (2026-09-17).

왜: 도쿄 23구 OSM 건물의 **76.4%** 에 height/levels 태그가 없다 (긴자 90.7%, 신주쿠 86.6%).
그 건물들은 엔진이 기본 2층(6.94 m)으로 깔아, 도쿄 SVF 가 통째로 과대평가됐다
(23구 평균 0.749, 긴자 0.841). 격자 224만점이 그 위에 세워져 있다.

무엇을: PLATEAU LOD1 의 `bldg:measuredHeight`(실측)를 읽어 **22 m 안 최근접 OSM 건물**에
`height` 로 넣는다. 원칙:
  · OSM 에 height 나 levels 가 **이미 있으면 건드리지 않는다.** 어제 검증에서 OSM height 는
    실측 대비 +0.4 m 로 정확했다. 있는 걸 덮어쓸 이유가 없다.
  · 여러 PLATEAU 동이 한 OSM 건물에 붙으면 **최대 높이**를 쓴다. SVF 는 가장 높은 부분이 정한다.
  · 출처를 남긴다: `height:source = plateau`. 나중에 "이 값이 어디서 왔나"를 물을 수 있어야 한다.

디스크: 23구 zip 이 5.5 GB 고 서버 여유가 13 GB 라 통째로 풀지 않는다.
  zip 안에서 `udx/bldg/*.gml` 을 **한 파일씩 임시로 꺼내 파싱하고 지운다.**

적재 후 반드시:
  1. API 컨테이너 재시작 — 건물 타일이 메모리에 캐시돼 있다 (docker restart climax-api)
  2. 도쿄 격자 재계산 — skyline_grid 는 예전 높이로 계산된 값이다

  docker cp scripts/ingest_plateau_height.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/ingest_plateau_height.py /plateau/13100_tokyo23-ku_2022_citygml.zip --dry
  docker exec -i climax-api python3 /tmp/ingest_plateau_height.py /plateau/13100_tokyo23-ku_2022_citygml.zip
  (컨테이너에 /plateau 가 없으면 -v $HOME/plateau:/plateau 로 run --rm 할 것)
"""
from __future__ import annotations
import argparse, asyncio, json, math, os, sys, tempfile, time, zipfile

sys.path.insert(0, "/app")

MATCH_M = 22.0
GS = 0.0005          # 격자 색인 칸(약 50 m). 이웃 9칸만 뒤진다.


def _ln(t: str) -> str:
    return t.rsplit("}", 1)[-1] if "}" in t else t


def parse_gml(path: str) -> list[tuple[float, float, float]]:
    """GML 하나 → [(lat, lon, height_m)]. lod0 footprint 중심 + measuredHeight."""
    # lxml 은 API 이미지에 없다. 표준 라이브러리로 간다 — 느리지만 밤새 돌리는 일이라 괜찮다.
    try:
        from lxml import etree
    except ImportError:
        import xml.etree.ElementTree as etree
    out = []
    try:
        ctx = etree.iterparse(path, events=("end",))
    except Exception:  # noqa: BLE001
        return out
    for _ev, el in ctx:
        if _ln(el.tag) != "Building":
            continue
        h = None
        pos = None
        for sub in el.iter():
            n = _ln(sub.tag)
            if n == "measuredHeight" and sub.text:
                try:
                    h = float(sub.text.strip())
                except ValueError:
                    pass
            elif n == "posList" and pos is None and sub.text:
                pos = sub.text.split()
        if h is not None and h > 0 and pos and len(pos) >= 6:
            try:
                las = [float(pos[i]) for i in range(0, len(pos) - 2, 3)]
                los = [float(pos[i + 1]) for i in range(0, len(pos) - 2, 3)]
                out.append((sum(las) / len(las), sum(los) / len(los), h))
            except ValueError:
                pass
        el.clear()
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--dry", action="store_true", help="DB 를 바꾸지 않고 집계만")
    ap.add_argument("--limit-files", type=int, default=0, help="GML 몇 개만 (시험용)")
    a = ap.parse_args()

    import asyncpg
    from app.config import get_settings
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)

    zf = zipfile.ZipFile(a.zip)
    names = [n for n in zf.namelist() if "/udx/bldg/" in n and n.endswith(".gml")]
    if a.limit_files:
        names = names[: a.limit_files]
    print(f"\nPLATEAU 건물 GML {len(names)}개 (zip 안 udx/bldg)")

    t0 = time.time()
    n_pl = n_match = n_skip_has = 0
    updates: dict[str, float] = {}         # bldg_poly.id(문자열, '타일키/osm id') -> max height
    tmpdir = tempfile.mkdtemp()

    for i, name in enumerate(names, 1):
        tmp = os.path.join(tmpdir, "cur.gml")
        with zf.open(name) as src, open(tmp, "wb") as dst:
            while True:
                b = src.read(1 << 20)
                if not b:
                    break
                dst.write(b)
        items = parse_gml(tmp)
        os.remove(tmp)
        if not items:
            print(f"  [{i}/{len(names)}] {os.path.basename(name)}: measuredHeight 없음")
            continue
        n_pl += len(items)
        las = [x[0] for x in items]; los = [x[1] for x in items]
        pad = 0.001
        rows = await conn.fetch(
            "SELECT id, ST_Y(ST_Centroid(geom)) la, ST_X(ST_Centroid(geom)) lo, tags "
            "FROM bldg_poly WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)",
            min(los) - pad, min(las) - pad, max(los) + pad, max(las) + pad)
        idx: dict = {}
        for r in rows:
            idx.setdefault((int(r["la"] / GS), int(r["lo"] / GS)), []).append(r)
        m_here = 0
        for la, lo, h in items:
            best, bd = None, 1e18
            gi, gj = int(la / GS), int(lo / GS)
            for ii in (gi - 1, gi, gi + 1):
                for jj in (gj - 1, gj, gj + 1):
                    for r in idx.get((ii, jj), ()):
                        dy = (r["la"] - la) * 111320.0
                        dx = (r["lo"] - lo) * 111320.0 * math.cos(math.radians(la))
                        dd = dy * dy + dx * dx
                        if dd < bd:
                            bd, best = dd, r
            if best is None or math.sqrt(bd) > MATCH_M:
                continue
            tg = best["tags"] if isinstance(best["tags"], dict) else json.loads(best["tags"] or "{}")
            if tg.get("height") or tg.get("building:levels") or tg.get("gro_flo_co"):
                n_skip_has += 1          # OSM 이 이미 안다 — 건드리지 않는다
                continue
            oid = str(best["id"])
            if h > updates.get(oid, 0.0):
                updates[oid] = h
            m_here += 1
        n_match += m_here
        if i % 10 == 0 or i == len(names):
            print(f"  [{i}/{len(names)}] PLATEAU {n_pl:,}동  → 태그없는 OSM 에 붙음 {len(updates):,}동  "
                  f"(OSM 이 이미 아는 건물 {n_skip_has:,} 건너뜀)  {time.time()-t0:.0f}s")

    print(f"\n합계: PLATEAU {n_pl:,}동, 주입 대상 OSM {len(updates):,}동, "
          f"이미 태그 있어 건너뜀 {n_skip_has:,}")
    if updates:
        hs = sorted(updates.values())
        print(f"주입 높이 중앙 {hs[len(hs)//2]:.1f} m, 90% {hs[int(len(hs)*.9)]:.1f} m, 최대 {hs[-1]:.1f} m")

    if a.dry:
        print("\n--dry: DB 를 바꾸지 않았다.")
        await conn.close()
        return

    # 주입. tags || jsonb 라 다른 태그는 보존된다.
    batch = [(json.dumps({"height": f"{h:.1f}", "height:source": "plateau"}), oid)
             for oid, h in updates.items()]
    B = 5000
    for k in range(0, len(batch), B):
        await conn.executemany(
            "UPDATE bldg_poly SET tags = tags || $1::jsonb WHERE id = $2", batch[k:k + B])
        print(f"  UPDATE {min(k+B, len(batch)):,}/{len(batch):,}")
    await conn.close()
    print(f"\n✅ 완료 {len(batch):,}동 주입  {time.time()-t0:.0f}s")
    print("⚠️ 다음: docker restart climax-api (건물 타일 캐시) → 도쿄 격자 재계산")


if __name__ == "__main__":
    asyncio.run(main())
