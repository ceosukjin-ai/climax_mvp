#!/usr/bin/env python3
"""기하 SVF 진단 — 80점에서 "왜 닫혀 보이나"를 변형(what-if)으로 가른다 (2026-09-10).

변형: 현재(기본 2층·스냅) / 기본 1층 / 층수결측 제외 / 스냅 없음 / 50m 이내만 / 기본1층+50m
각 변형의 80점 r·MAE·bias 를 비교 → 어느 가설(기본층수 과대 / 스냅 / 원거리 건물)이 맞는지.
최악 10점은 이웃 건물 상세(거리·높이·층수 실제/기본값·차폐각) 출력.

실행(서버 컨테이너): ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/diag_geo_svf.py
"""
import asyncio, math, re, sys, os
sys.path.insert(0, "/app")
ROOT = os.environ.get("CLIMAX_REPO", "/repo")
from app.services import geo as G  # noqa: E402

src = open(os.path.join(ROOT, "backend", "scripts", "validate_geo_svf.py"), encoding="utf-8").read()
PTS = eval(re.search(r"PTS = json\.loads\('''(.*?)'''\)", src, re.S).group(1))
WORST = {5, 7, 8, 21, 23, 24, 30, 56, 60, 72}


def svf_variant(rings, default_floors, snap, max_dist, eye=1.5, step=2, detail=False):
    if snap:
        rings, moved = G._snap_outside(rings)
    else:
        moved = 0.0
    blds = []
    n_def = 0
    for ring, props in rings:
        if len(ring) < 4 or G._point_in_ring(0.0, 0.0, ring):
            continue
        real = G._height_m_from_props(props, default_floors=None)
        H = real if real is not None else (default_floors * G.FLOOR_HEIGHT_M if default_floors else None)
        if H is None:
            continue
        if real is None:
            n_def += 1
        h = H - eye
        if h <= 0:
            continue
        d = G._dist_to_ring(0.0, 0.0, ring)
        if max_dist and d > max_dist:
            continue
        blds.append((ring, h, real is None, d))
    if not blds:
        return 1.0, moved, 0, 0, []
    n = int(360 / step); s = 0.0; blockers = {}
    for i in range(n):
        az = math.radians(i * step); dx, dy = math.sin(az), math.cos(az)
        bmax, who = 0.0, None
        for j, (ring, h, isdef, d) in enumerate(blds):
            t = G._ray_ring_hit(dx, dy, ring)
            if t is None:
                continue
            b = math.atan2(h, t)
            if b > bmax:
                bmax, who = b, (j, t)
        s += math.sin(bmax) ** 2
        if who and detail:
            j, t = who
            blockers.setdefault(j, [0, 0.0, t])
            blockers[j][0] += 1; blockers[j][1] = max(blockers[j][1], bmax)
    top = []
    if detail:
        for j, (cnt, bmax, t) in sorted(blockers.items(), key=lambda kv: -kv[1][0] * math.sin(kv[1][1]) ** 2)[:4]:
            ring, h, isdef, d = blds[j]
            top.append(f"{'기본2층' if isdef else f'{h + eye:.0f}m'} 거리{d:.0f}m 각{math.degrees(bmax):.0f}° 폭{cnt * step}°")
    return 1.0 - s / n, moved, len(blds), n_def, top


VARS = [("현재(기본2층·스냅)", 2, True, None), ("기본1층", 1, True, None), ("결측제외", None, True, None),
        ("스냅없음", 2, False, None), ("50m이내", 2, True, 50.0), ("기본1층+50m", 1, True, 50.0), ("결측제외+50m", None, True, 50.0)]


def stats(pairs):
    n = len(pairs); e = [p - o for o, p in pairs]
    mo = sum(o for o, _ in pairs) / n; mp = sum(p for _, p in pairs) / n
    so = math.sqrt(sum((o - mo) ** 2 for o, _ in pairs)); sp = math.sqrt(sum((p - mp) ** 2 for _, p in pairs))
    r = sum((o - mo) * (p - mp) for o, p in pairs) / (so * sp) if so and sp else float("nan")
    return sum(map(abs, e)) / n, sum(e) / n, r


async def main():
    res = {v[0]: [] for v in VARS}
    per_place = {}
    for i, (place, lat, lon, obs) in enumerate(PTS, 1):
        rings, srcname = await G._rings_cached(lat, lon)
        line = f"[{i:2d}] {place} 실측 {obs:.2f}"
        for name, df, snap, md in VARS:
            svf, moved, nb, nd, top = svf_variant(rings, df, snap, md, detail=(i in WORST and name == VARS[0][0]))
            res[name].append((obs, svf)); per_place.setdefault(place, {}).setdefault(name, []).append((obs, svf))
            if name == VARS[0][0]:
                line += f" | 기하 {svf:.2f} (건물 {nb}동, 결측 {nd}, 스냅 {moved:.1f}m)"
                if i in WORST:
                    line += "\n      차폐 주범: " + " / ".join(top)
            else:
                line += f" | {name} {svf:.2f}"
        print(line, flush=True)
    print("\n===== 변형별 80점 =====")
    for name, *_ in VARS:
        mae, bias, r = stats(res[name]); print(f"  {name:14s} MAE {mae:.3f}  bias {bias:+.3f}  r {r:+.2f}")
    print("\n===== 변형별 지역 r =====")
    for pl, d in per_place.items():
        print("  " + pl + "  " + "  ".join(f"{n[:6]} {stats(d[n])[2]:+.2f}" for n in res))


asyncio.run(main())
