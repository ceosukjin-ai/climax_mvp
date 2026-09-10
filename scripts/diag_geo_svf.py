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


def _shift(rings, ox, oy):
    return [([(x - ox, y - oy) for x, y in r], p) for r, p in rings]


def center_snap(rings, step=10, max_m=30.0):
    """벽 1m 밖 → 가로 중심선으로: 마주보는 광선쌍 중 (d1+d2) 최소 = 가로 단면, 그 중점으로 이동."""
    n = int(360 / step); d = [None] * n
    for i in range(n):
        az = math.radians(i * step); dx, dy = math.sin(az), math.cos(az)
        for ring, props in rings:
            if len(ring) < 4:
                continue
            t = G._ray_ring_hit(dx, dy, ring)
            if t is not None and t <= max_m and (d[i] is None or t < d[i]):
                d[i] = t
    best = None
    for i in range(n // 2):
        j = i + n // 2
        if d[i] is None or d[j] is None:
            continue
        if best is None or d[i] + d[j] < best[0]:
            best = (d[i] + d[j], i, d[i], d[j])
    if best is None:
        return rings, 0.0, None
    w, i, d1, d2 = best
    az = math.radians(i * step); off = (d1 - d2) / 2.0      # d1>d2 면 i 방향으로 이동
    ox, oy = math.sin(az) * off, math.cos(az) * off
    return _shift(rings, ox, oy), abs(off), (i * step + 90) % 180   # 도로축


def neighbor_default(rings):
    fl = []
    for ring, props in rings:
        try:
            f = int(props.get("gro_flo_co") or props.get("building:levels") or 0)
        except (TypeError, ValueError):
            f = 0
        if f > 0:
            fl.append(f)
    if len(fl) >= 5:
        fl.sort(); return fl[len(fl) // 2]
    return 1


VARS = [("현재(기본2층·스냅)", 2, True, None), ("기본1층", 1, True, None), ("결측제외", None, True, None),
        ("스냅없음", 2, False, None), ("50m이내", 2, True, 50.0), ("기본1층+50m", 1, True, 50.0), ("결측제외+50m", None, True, 50.0)]


def svf_new(rings, mode):
    """mode: 'center' 중심선 스냅 / 'center_med' + 도로축 ±4m 3점 중앙값 / 'center_med_nd' + 이웃중앙값 기본층수"""
    rings, _ = G._snap_outside(rings)
    rings, moved, axis = center_snap(rings)
    df = neighbor_default(rings) if mode.endswith("_nd") else 2
    if mode == "center":
        return svf_variant(rings, df, False, None)[0], moved
    vals = []
    offs = [0.0] if axis is None else [-4.0, 0.0, 4.0]
    for o in offs:
        ax = math.radians(axis or 0); rr = _shift(rings, math.sin(ax) * o, math.cos(ax) * o)
        vals.append(svf_variant(rr, df, True, None)[0])
    vals.sort(); return vals[len(vals) // 2], moved


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
        for mode in ("center", "center_med", "center_med_nd"):
            v, mv = svf_new(rings, mode)
            res.setdefault(mode, []).append((obs, v)); per_place[place].setdefault(mode, []).append((obs, v))
            line += f" | {mode} {v:.2f}" + (f"(이동{mv:.1f}m)" if mode == "center" else "")
        print(line, flush=True)
    print("\n===== 변형별 80점 =====")
    for name in res:
        mae, bias, r = stats(res[name]); print(f"  {name:14s} MAE {mae:.3f}  bias {bias:+.3f}  r {r:+.2f}")
    print("\n===== 변형별 지역 r =====")
    for pl, d in per_place.items():
        print("  " + pl + "  " + "  ".join(f"{n[:12]} {stats(d[n])[2]:+.2f}" for n in res))


asyncio.run(main())
