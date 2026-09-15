#!/usr/bin/env python3
"""부산대 주변 가로수 노선에서 현장 확인 지점을 뽑는다 (2026-09-15).

왜:
  가로수 그늘이 실질적으로 작동하지 않는다.
    · 위성 수관고는 도심 가로수를 못 본다 — 9 m 와 **원본 1.1 m** 두 해상도에서 확정(오늘).
    · 공공데이터(A)는 DB 에 266만 그루가 있지만 **엉뚱한 자리에 놓여 있다.**
      서면 교차로 30 m 안 0그루, PNU point01 최근접 121 m, 해운대 해변로 145 m.
  수치를 보면 이유가 보인다 — 수고 18.0/14.0/12.0 m, 수관반경 5.0/5.5/4.0 m 로 딱 떨어진다.
  개별 나무 실측이 아니라 **노선 단위 대표값**이고, 구간 선에서 `OFFSET_M=5 m` 로 뿌린 것이다.
  편도 2차로만 돼도 중심선에서 5 m 는 아직 차도다 → 나무를 도로 위에 심어 둔 셈.

  9/12 에 "OFFSET_M 이 옳은지는 별도 수단으로 검증해야 한다"고 남겨 뒀다. 그 별도 수단이
  **현장에서 나무 밑동 GPS 를 찍어 역산하는 것**이다.

무엇을 뽑나:
  부산대 반경 2 km 안에서 tree_point 를 노선처럼 묶고, 노선마다 대표 지점 한 곳씩.
  같은 노선에서 여러 그루를 재야 GPS 오차가 평균되므로, 대표 지점 주변에 나무가 여러 그루
  있는 노선을 고른다. 도로 폭이 다른 노선을 섞는다 — 어긋남이 폭에 따라 달라질 수 있다.

  docker cp scripts/pick_garosu_targets.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/pick_garosu_targets.py > /tmp/garosu_targets.json
  (진단 출력은 stderr 로 나가고, JSON 만 stdout 으로 나간다)
"""
from __future__ import annotations
import asyncio, json, math, sys
sys.path.insert(0, "/app")

PNU = (35.2340, 129.0800)      # 부산대 캠퍼스 중앙
RAD_KM = 2.0
N_PICK = 6                      # 뽑을 노선 수


def log(*a):
    print(*a, file=sys.stderr)


async def main():
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        log("DB 풀 없음"); return

    async with pool.acquire() as c:
        cols = await c.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='tree_point' ORDER BY ordinal_position")
        log("tree_point 열: " + ", ".join(f"{r['column_name']}({r['data_type']})" for r in cols))
        names = {r["column_name"] for r in cols}

        dlat = RAD_KM / 111.32
        dlon = RAD_KM / (111.32 * math.cos(math.radians(PNU[0])))
        extra = ""
        for cand in ("road_name", "route", "name", "line_id", "src", "species", "kind"):
            if cand in names:
                extra = f", {cand} AS grp"
                break
        rows = await c.fetch(
            f"SELECT ST_Y(geom) la, ST_X(geom) lo, h, r{extra} FROM tree_point "
            f"WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)",
            PNU[1] - dlon, PNU[0] - dlat, PNU[1] + dlon, PNU[0] + dlat)
        log(f"부산대 반경 {RAD_KM} km 안 나무 {len(rows):,}그루"
            f"  (노선 열: {'있음' if extra else '없음 — (수고,수관반경)+위치로 묶는다'})")

    if not rows:
        log("나무가 없다 — 반경을 넓히거나 적재를 확인할 것")
        return

    # 노선 묶기: 같은 (수고, 수관반경) 이면 같은 노선일 가능성이 크다(대표값이므로).
    # 거기에 공간적으로 끊어 붙인다 — 30 m 안에 이어지는 것을 한 덩어리로.
    buckets: dict = {}
    for x in rows:
        key = (round(float(x["h"] or 0), 1), round(float(x["r"] or 0), 1),
               (x["grp"] if "grp" in x.keys() else None))
        buckets.setdefault(key, []).append((float(x["la"]), float(x["lo"])))

    def dm(a, b, c2, d2):
        return math.hypot((c2 - a) * 111320.0,
                          (d2 - b) * 111320.0 * math.cos(math.radians(a)))

    lines = []
    for key, pts in buckets.items():
        pts.sort()
        cur = [pts[0]]
        for p in pts[1:]:
            if dm(cur[-1][0], cur[-1][1], p[0], p[1]) <= 40.0:
                cur.append(p)
            else:
                if len(cur) >= 5:
                    lines.append((key, cur))
                cur = [p]
        if len(cur) >= 5:
            lines.append((key, cur))

    lines.sort(key=lambda kv: -len(kv[1]))
    log(f"\n묶인 노선 후보 {len(lines)}개 (5그루 이상)")

    picked, used = [], []
    for (h, r, grp), pts in lines:
        mid = pts[len(pts) // 2]
        if any(dm(mid[0], mid[1], u[0], u[1]) < 400.0 for u in used):
            continue            # 같은 동네에서 여러 개 뽑지 않는다
        used.append(mid)
        near = sum(1 for p in pts if dm(mid[0], mid[1], p[0], p[1]) <= 30.0)
        d_pnu = dm(PNU[0], PNU[1], mid[0], mid[1])
        picked.append({
            "id": f"가로수{len(picked)+1:02d}",
            "lat": round(mid[0], 6), "lon": round(mid[1], 6),
            "note": (f"표준데이터 수고 {h:.0f} m · 수관반경 {r:.1f} m · "
                     f"30 m 안 {near}그루 · 캠퍼스에서 {d_pnu/1000:.1f} km"),
        })
        log(f"  {picked[-1]['id']}  {mid[0]:.6f},{mid[1]:.6f}  "
            f"그루 {len(pts):>4}  30m내 {near:>3}  수고 {h:.0f}m  캠퍼스 {d_pnu/1000:.1f}km")
        if len(picked) >= N_PICK:
            break

    out = {
        "id": "garosu",
        "title": "가로수 위치 검증 (밑동 GPS)",
        "why": ("공공데이터 가로수가 실제 위치와 얼마나 어긋나는지 재려는 것입니다. "
                "지금은 도로 중심선에서 5 m 로 박아 놓았는데, 편도 2차로면 5 m 는 아직 차도라 "
                "나무가 도로 위에 놓입니다. 실제로 서면 교차로 30 m 안에 나무가 0그루입니다. "
                "밑동 좌표 몇 개면 이 값을 역산해 266만 그루를 한 번에 고칠 수 있습니다."),
        "do": ("한 노선에서 3~4그루. **나무 밑동에 서서** 현재 위치를 찍고, "
               "차도 끝에서 몇 m쯤인지 메모에 적어 주세요(눈대중 OK). "
               "나무와 도로가 같이 나오게 사진도 한 장 — 나중에 위성영상으로 보정합니다."),
        "points": picked,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    log(f"\n지점 {len(picked)}개를 stdout 으로 냈다.")


if __name__ == "__main__":
    asyncio.run(main())
