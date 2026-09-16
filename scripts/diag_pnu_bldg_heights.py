#!/usr/bin/env python3
"""부산대 27지점에서 **하늘을 실제로 막는 건물**만 추려 높이 채울 목록을 만든다 (2026-09-16).

왜:
  캠퍼스 27지점의 `svf_bldg_only` 가 0.985(중앙값) — 건물이 하늘을 거의 안 막는 것으로 나온다.
  8월 시내는 0.662 다. 사진에는 5층 제2공학관이 바로 앞에 서 있는데 엔진은 0.3%만 막는다고 본다.
  원인은 캠퍼스 건물 높이 오염 — 2,118 폴리곤 중 실제 높이가 붙은 건 37개(1.7%)뿐이고
  나머지는 기본 2층(6.9 m)이다. 20 m 떨어진 5층 건물을 6.9 m 로 보면 거의 안 막힌다.

  그래서 캠퍼스에서는 **건물이 너무 열리고 수관이 너무 닫혀** 둘이 상쇄된다
  (point31: 건물만 0.997 → 수관 후 0.627, 사진 0.738). 잔차가 작아 보이는 것이 상쇄의 결과다.
  수관항만 고쳐도, 건물만 고쳐도 더 나빠진다. **건물 높이를 먼저 채워야 한다.**

  표제부로는 못 고친다(동명 매칭 불가). 사람이 층수를 채워야 하는데, 2,118동을 다 할 필요는 없다.
  SVF 는 **방위별 가장 높은 건물**이 정하므로 지점마다 3~5동이면 된다. 그 목록을 뽑는다.

무엇을 내나:
  건물마다 (몇 개 지점에서 보이나, 최대 고도각, 지금 쓰는 높이와 그 출처).
  **많은 지점에 걸리고 각이 큰 것부터** 채우면 된다. 위에서부터 채우다 충분해지면 멈춘다.

  docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml \
    exec -T api python - < scripts/diag_pnu_bldg_heights.py
"""
from __future__ import annotations
import asyncio, csv, json, math, os, sys
sys.path.insert(0, "/app")

RAD_M = float(os.environ.get("RAD_M", "70"))      # 이 반경 안 건물만
EYE = 1.5
MIN_DEG = float(os.environ.get("MIN_DEG", "4"))   # 이 고도각 미만은 하늘을 사실상 안 막는다
CSV = os.environ.get("PTS", "/repo/data/pnu_28_final.csv")


def _f(x):
    try:
        v = float(str(x).strip().split()[0])
        return v if v > 0 else None
    except (TypeError, ValueError, IndexError):
        return None


def eng_height(tags: dict) -> tuple[float, str]:
    """엔진이 지금 쓰는 높이와 출처 (geo._height_m_from_props 와 같은 규칙)."""
    h = _f(tags.get("height"))
    fl = None
    for k in ("building:levels", "gro_flo_co", "levels"):
        fl = fl or _f(tags.get(k))
    if h is not None and not (fl and h > fl * 8.0):
        return h, "height"
    if fl:
        return fl * 3.018 + 0.902, "levels"
    return 2 * 3.018 + 0.902, "기본2층"


async def main():
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 연결 없음"); return

    pts = []
    with open(CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            la = _f(r.get("위도") or r.get("lat"))
            lo = _f(r.get("경도") or r.get("lon"))
            if la and lo:
                pts.append((r.get("측정ID") or r.get("point_id") or "", la, lo))
    print(f"\n지점 {len(pts)}개, 반경 {RAD_M:.0f} m, 고도각 {MIN_DEG:.0f}도 이상만\n")

    hit: dict = {}     # id -> dict
    for pid, la0, lo0 in pts:
        d = RAD_M / 111320.0
        rows = await pool.fetch(
            "SELECT id, tags, ST_AsGeoJSON(geom) g FROM bldg_poly "
            "WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)",
            lo0 - d / math.cos(math.radians(la0)), la0 - d,
            lo0 + d / math.cos(math.radians(la0)), la0 + d)
        for r in rows:
            try:
                coords = json.loads(r["g"])["coordinates"][0]
            except Exception:  # noqa: BLE001
                continue
            best = 1e9
            for lo, la in coords:
                dy = (la - la0) * 111320.0
                dx = (lo - lo0) * 111320.0 * math.cos(math.radians(la0))
                best = min(best, math.hypot(dx, dy))
            if best > RAD_M:
                continue
            tags = r["tags"] if isinstance(r["tags"], dict) else json.loads(r["tags"] or "{}")
            h, src = eng_height(tags)
            ang = math.degrees(math.atan2(max(h - EYE, 0.1), max(best, 1.0)))
            e = hit.setdefault(r["id"], {"pts": set(), "ang": 0.0, "d": best,
                                         "h": h, "src": src,
                                         "name": tags.get("name") or tags.get("bld_nm") or ""})
            e["pts"].add(pid)
            if ang > e["ang"]:
                e["ang"], e["d"] = ang, best

    rows = [v | {"id": k} for k, v in hit.items()]
    big = [r for r in rows if r["ang"] >= MIN_DEG or r["src"] == "기본2층"]
    big.sort(key=lambda r: (-len(r["pts"]), -r["ang"]))

    print(f"{'건물id':<12}{'지점수':>6}{'최대각':>7}{'거리m':>7}{'현재높이':>9}{'출처':>9}  이름")
    for r in big[:40]:
        print(f"{str(r['id'])[-10:]:<12}{len(r['pts']):>6}{r['ang']:>7.1f}{r['d']:>7.0f}"
              f"{r['h']:>9.1f}{r['src']:>9}  {r['name'][:24]}")

    n_def = sum(1 for r in big if r["src"] == "기본2층")
    print(f"\n반경 안 건물 {len(rows)}동 / 추려낸 {len(big)}동 / 그중 기본2층 {n_def}동")
    print("\n읽는 법:")
    print("  · '지점수' 가 큰 것부터 채우면 한 동으로 여러 지점이 고쳐진다.")
    print("  · '현재높이 6.9 / 출처 기본2층' 이 채워야 할 것이다.")
    print("  · 층수만 알면 된다: 높이 = 층수 x 3.018 + 0.902.")
    print("  · 이름이 비어 있으면 360 사진에서 위치로 찾아야 한다.")


asyncio.run(main())
