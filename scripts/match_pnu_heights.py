#!/usr/bin/env python3
"""부산대 건물 대장(층수) → 캠퍼스 OSM 폴리곤에 붙인다 (2026-09-16).

왜:
  캠퍼스 27지점의 `svf_bldg_only` 중앙값이 0.985 — 건물이 하늘을 거의 안 막는 것으로 나온다
  (8월 시내는 0.662). 5층 제2공학관이 바로 앞인데 엔진은 0.3%만 막는다고 본다.
  캠퍼스 건물 2,118 폴리곤 중 실제 높이가 붙은 건 37개(1.7%)뿐, 나머지는 기본 2층(6.9 m)이다.
  표제부로는 못 고친다(동명 매칭 불가). 그런데 **대학이 건물 대장을 갖고 있다** — 층수가 다 있다.

  그래서 캠퍼스에서는 건물이 너무 열리고 수관이 너무 닫혀 둘이 상쇄된다
  (point31: 건물만 0.997 → 수관 후 0.627, 사진 0.738). 건물부터 채워야 수관항을 제대로 맞춘다.

어떻게 맞추나 (사람이 한 동씩 짚지 않는다):
  1. **이름** — 폴리곤에 name 태그가 있으면 그것부터. 가장 확실하다.
  2. **건축면적** — 대장의 건축면적(m²)은 곧 폴리곤 바닥면적이다. 상대오차가 작은 쌍부터
     **일대일**로 묶는다(한 폴리곤이 두 건물에 붙지 않게). 면적이 비슷한 동이 여럿이면
     애매한 것으로 표시하고 사람이 본다.

  ⚠️ 기본은 **미리보기**다. 실제로 DB 를 고치려면 --apply 를 준다.
     붙이는 태그는 `building:levels` 하나뿐 — 엔진이 층수 → 높이를 자기 식으로 환산한다
     (층수 x 3.018 + 0.902). 여기서 높이를 계산해 넣으면 규칙이 두 군데로 갈린다.

  docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml \
    exec -T api python - < scripts/match_pnu_heights.py
"""
from __future__ import annotations
import asyncio, json, math, os, sys
sys.path.insert(0, "/app")

TSV = os.environ.get("TSV", "/repo/data/pnu_buildings.tsv")
# 캠퍼스 bbox — 넉넉히. 구외(총장공관·부설고)는 이 밖이라 자동으로 빠진다.
S, W, N, E = 35.2250, 129.0740, 35.2420, 129.0900
TOL = float(os.environ.get("TOL", "0.25"))      # 면적 상대오차 허용
APPLY = "--apply" in sys.argv


async def main():
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 연결 없음"); return

    reg = []
    with open(TSV, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("#") or not ln.strip():
                continue
            c = ln.rstrip("\n").split("\t")
            if len(c) < 9:
                continue
            try:
                reg.append({"code": c[0], "name": c[1], "in": c[2], "struct": c[3],
                            "ug": int(c[4]), "fl": int(c[5]), "year": int(c[6]),
                            "area": float(c[7]), "gfa": float(c[8])})
            except ValueError:
                continue
    reg = [r for r in reg if r["in"] == "구내"]
    print(f"\n대장 구내 {len(reg)}동")

    rows = await pool.fetch(
        "SELECT id, tags, ST_Area(geom::geography) a, ST_Y(ST_Centroid(geom)) la, "
        "ST_X(ST_Centroid(geom)) lo FROM bldg_poly "
        "WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)", W, S, E, N)
    poly = []
    for r in rows:
        t = r["tags"] if isinstance(r["tags"], dict) else json.loads(r["tags"] or "{}")
        if r["a"] < 30:                       # 30 m² 미만은 부속·차양. 하늘을 안 막는다.
            continue
        poly.append({"id": r["id"], "tags": t, "a": float(r["a"]),
                     "la": r["la"], "lo": r["lo"],
                     "has_h": bool(t.get("height") or t.get("building:levels")
                                   or t.get("gro_flo_co"))})
    print(f"캠퍼스 폴리곤 {len(poly)}개 (30 m² 이상)   그중 높이 있음 {sum(p['has_h'] for p in poly)}")

    # 1) 이름
    pairs, used_p, used_r = [], set(), set()
    for i, r in enumerate(reg):
        for p in poly:
            if p["id"] in used_p:
                continue
            nm = str(p["tags"].get("name") or "")
            if nm and (nm == r["name"] or (len(nm) >= 3 and nm in r["name"])):
                pairs.append((r, p, "이름", abs(p["a"] - r["area"]) / max(r["area"], 1)))
                used_p.add(p["id"]); used_r.add(i); break

    # 2) 면적 — 상대오차가 작은 쌍부터 일대일
    cand = []
    for i, r in enumerate(reg):
        if i in used_r:
            continue
        for p in poly:
            if p["id"] in used_p:
                continue
            d = abs(p["a"] - r["area"]) / max(r["area"], 1)
            if d <= TOL:
                cand.append((d, i, p["id"]))
    cand.sort()
    pid = {p["id"]: p for p in poly}
    for d, i, p_id in cand:
        if i in used_r or p_id in used_p:
            continue
        pairs.append((reg[i], pid[p_id], "면적", d))
        used_r.add(i); used_p.add(p_id)

    pairs.sort(key=lambda x: -x[0]["fl"])
    print(f"\n맞춘 {len(pairs)}동 (이름 {sum(1 for x in pairs if x[2]=='이름')} / "
          f"면적 {sum(1 for x in pairs if x[2]=='면적')})\n")
    print(f"{'코드':<8}{'층':>3}{'대장면적':>9}{'폴리곤':>9}{'오차':>7} {'근거':<5} 이름")
    for r, p, how, d in pairs[:45]:
        print(f"{r['code']:<8}{r['fl']:>3}{r['area']:>9.0f}{p['a']:>9.0f}{d:>6.0%} {how:<5} {r['name'][:26]}")

    miss = [reg[i] for i in range(len(reg)) if i not in used_r]
    miss.sort(key=lambda r: -r["fl"])
    print(f"\n못 맞춘 대장 {len(miss)}동 (높은 것부터 10개)")
    for r in miss[:10]:
        print(f"  {r['code']:<8}{r['fl']:>3}층{r['area']:>8.0f} m²  {r['name'][:26]}")

    tall = [(r, p) for r, p, _h, _d in pairs if r["fl"] >= 4 and not p["has_h"]]
    print(f"\n→ 4층 이상인데 엔진이 기본2층으로 보던 것: {len(tall)}동")

    if not APPLY:
        print("\n미리보기다. 실제로 넣으려면 --apply 를 붙여 다시 돌릴 것.")
        return
    n = 0
    for r, p, _h, _d in pairs:
        t = dict(p["tags"]); t["building:levels"] = str(r["fl"])
        t["pnu_code"] = r["code"]; t["name"] = t.get("name") or r["name"]
        await pool.execute("UPDATE bldg_poly SET tags=$1::jsonb WHERE id=$2",
                           json.dumps(t, ensure_ascii=False), p["id"])
        n += 1
    print(f"\n{n}동에 building:levels 를 넣었다. 격자를 다시 만들어야 반영된다.")


asyncio.run(main())
