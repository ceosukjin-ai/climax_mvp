#!/usr/bin/env python3
"""PLATEAU 실측 높이 vs 엔진이 쓰는 높이 — 2층 기본값이 얼마나 틀리나 (2026-09-16).

왜:
  일본 건물의 **65%가 기본 2층(약 6.9 m)** 으로 계산되고 있다(9/16 진단).
      일본  height보유 18%  levels보유 35%  둘다없음 61%  기본값비율 65%
      부산  height보유 61%  levels보유 95%  둘다없음  5%  기본값비율 16%
  도쿄 마루노우치는 2층→6층으로만 바꿔도 SVF 가 0.549 → 0.197 로 움직인다.
  즉 **일본 SVF 는 체계적으로 과대평가**(하늘이 실제보다 열림)돼 있을 가능성이 크다.

  PLATEAU LOD1 에는 `bldg:measuredHeight`(실측 높이)가 들어 있다. 構造種別은 이미 쓰고 있으니
  **같은 파일에서 높이만 더 읽으면** 된다. 새 자료를 구할 필요가 없다.

  다만 도쿄 전역 PLATEAU 는 수 GB 다. **시부야만 있는 지금 자료로 먼저 크기를 재고**
  전체를 받을 값어치를 정한다.

무엇을 재나:
  1. PLATEAU 실측 높이의 분포 — 시부야 건물이 실제로 몇 m 인가
  2. 그중 우리 타일에 매칭되는 건물에서, **엔진이 지금 쓰는 높이**와의 차이
  3. 기본 2층(6.9 m)으로 계산된 건물만 따로 — 거기서 실제 높이가 얼마인가

  python3 scripts/diag_plateau_height.py data/plateau_shibuya/udx/bldg
  (맥에서. lxml 필요: pip3 install lxml)
"""
from __future__ import annotations
import glob, json, math, os, statistics as st, sys

try:
    from lxml import etree
except ImportError:
    raise SystemExit("lxml 이 없다: pip3 install lxml")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TILE_DIR = os.path.join(ROOT, "backend", "data", "buildings")
MATCH_M = 22.0


def _ln(t: str) -> str:
    return t.rsplit("}", 1)[-1] if "}" in t else t


def parse(path: str):
    """GML 하나 → [(lat, lon, height_m)] — lod0RoofEdge 중심 + measuredHeight."""
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
            except ValueError:
                el.clear(); continue
            if las and los:
                out.append((sum(las) / len(las), sum(los) / len(los), h))
        el.clear()
    return out


def tkey(lat, lon):
    return f"{int(math.floor(lat*100))}_{int(math.floor(lon*100))}"


def eng_height(tags: dict) -> tuple[float, str]:
    """엔진이 쓰는 높이 [m] 와 그 출처. geo._height_m_from_props 와 같은 규칙."""
    FLOOR_PER_M, ROOF_ADD_M = 3.018, 0.902
    def _f(x):
        try:
            v = float(str(x).strip().split()[0]); return v if v > 0 else None
        except (TypeError, ValueError, IndexError):
            return None
    h = _f(tags.get("height"))
    fl = None
    for k in ("building:levels", "gro_flo_co", "levels"):
        fl = fl or _f(tags.get(k))
    if h is not None:
        if fl and h > fl * 8.0:
            pass                        # 모순 → 버림(런타임과 같은 규칙)
        else:
            return h, "height"
    if fl:
        return fl * FLOOR_PER_M + ROOF_ADD_M, "levels"
    return 2 * FLOOR_PER_M + ROOF_ADD_M, "default2"


# 구역별로 나눠 본다 (2026-09-16).
#   시부야 한 곳만 보고 "2층 기본값이 크게 안 틀린다"고 결론 내릴 뻔했다.
#   시부야는 저층 상업·주거가 섞인 곳이고, 마루노우치·니시신주쿠 같은 오피스가는 다르다.
#   같은 자료 안에서 구역을 갈라 재야 그 차이가 보인다.
AREAS = [
    ("니시신주쿠(오피스)", 35.6830, 35.6960, 139.6870, 139.7020),
    ("신주쿠역 주변",      35.6860, 35.6950, 139.6980, 139.7080),
    ("시부야역 주변",      35.6530, 35.6640, 139.6960, 139.7080),
    ("마루노우치",        35.6750, 35.6870, 139.7600, 139.7720),
]


def area_of(lat: float, lon: float) -> str | None:
    for nm, s0, n0, w0, e0 in AREAS:
        if s0 <= lat <= n0 and w0 <= lon <= e0:
            return nm
    return None


# ---------------------------------------------------------------- [5] SVF
# 건물 단위 '평균 높이차' 로는 답이 안 나온다 (2026-09-16).
#   SVF 는 평균이 아니라 **그 방위에서 가장 높은 건물**이 정한다.
#   니시신주쿠 평균차 -1.9 m 는 작아 보이지만, 26 m 짜리 하나를 6.9 m 로 보면
#   그 방위의 하늘이 통째로 열린다. 그래서 SVF 를 직접 두 번 계산해 뺀다.
#
# 근사: 건물을 반폭 6 m 의 기둥으로 보고, 방위 5도 칸마다 최대 고도각을 채운다.
#       SVF = 1 - mean(sin^2 beta)  (Steyn 1980) — geo._svf_from_rings 와 같은 식.
SPOTS = [
    ("니시신주쿠 도청앞",  35.6896, 139.6917),
    ("니시신주쿠 공원",    35.6852, 139.6905),
    ("신주쿠역 서쪽",      35.6896, 139.6995),
    ("신주쿠역 동쪽",      35.6917, 139.7036),
    ("시부야 스크램블",    35.6595, 139.7005),
    ("시부야 센터가이",    35.6605, 139.6985),
    ("요요기공원 옆",      35.6700, 139.6950),
    ("하라주쿠",          35.6702, 139.7027),
]
SVF_RAD_M = 150.0
HALF_W_M = 6.0
EYE_M = 1.5
AZ_STEP = 5


def _svf_point(items, eye=EYE_M):
    """items = [(dx_m, dy_m, height_m)] — 관측점 기준 상대좌표."""
    n = 360 // AZ_STEP
    bins = [0.0] * n
    for dx, dy, h in items:
        d = math.hypot(dx, dy)
        if d < 1.0:
            d = 1.0
        hh = h - eye
        if hh <= 0:
            continue
        beta = math.atan2(hh, d)
        az = math.degrees(math.atan2(dx, dy)) % 360.0
        half = math.degrees(math.atan2(HALF_W_M, d))
        i0 = int(math.floor((az - half) / AZ_STEP))
        i1 = int(math.ceil((az + half) / AZ_STEP))
        for i in range(i0, i1 + 1):
            k = i % n
            if beta > bins[k]:
                bins[k] = beta
    return 1.0 - sum(math.sin(b) ** 2 for b in bins) / n


def svf_section(matched):
    print("\n[5] SVF 로 비교 — 실제로 하늘이 얼마나 더 열려 보이나")
    if not matched:
        print("  매칭 자료 없음")
        return
    # 0.01도 격자 색인
    grid: dict = {}
    for la, lo, hp, he in matched:
        grid.setdefault((int(la * 100), int(lo * 100)), []).append((la, lo, hp, he))
    print(f"  {'지점':<18}{'건물수':>7}{'실측SVF':>9}{'엔진SVF':>9}{'차이':>8}{'실측최고':>9}{'엔진최고':>9}")
    rows = []
    for nm, la0, lo0 in SPOTS:
        near = []
        for gi in range(int(la0 * 100) - 1, int(la0 * 100) + 2):
            for gj in range(int(lo0 * 100) - 1, int(lo0 * 100) + 2):
                near.extend(grid.get((gi, gj), []))
        P = []
        E = []
        for la, lo, hp, he in near:
            dy = (la - la0) * 111320.0
            dx = (lo - lo0) * 111320.0 * math.cos(math.radians(la0))
            if math.hypot(dx, dy) > SVF_RAD_M:
                continue
            P.append((dx, dy, hp))
            E.append((dx, dy, he))
        if len(P) < 5:
            print(f"  {nm:<18}{len(P):>7}   (건물 부족 — 타일 미적재)")
            continue
        sp = _svf_point(P)
        se = _svf_point(E)
        rows.append(se - sp)
        print(f"  {nm:<18}{len(P):>7}{sp:>9.3f}{se:>9.3f}{se-sp:>+8.3f}"
              f"{max(x[2] for x in P):>9.1f}{max(x[2] for x in E):>9.1f}")
    if rows:
        print(f"  {'평균':<18}{'':>7}{'':>9}{'':>9}{st.mean(rows):>+8.3f}")


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data/plateau_shibuya/udx/bldg")
    files = sorted(glob.glob(os.path.join(d, "*.gml")))
    if not files:
        raise SystemExit(f"GML 이 없다: {d}")
    print(f"\nGML {len(files)}개 읽는 중 …")
    P = []
    for i, f in enumerate(files, 1):
        P.extend(parse(f))
        if i % 20 == 0:
            print(f"  {i}/{len(files)}  누적 {len(P):,}동", flush=True)
    print(f"  PLATEAU 건물 {len(P):,}동 (measuredHeight 있는 것만)\n")
    if not P:
        raise SystemExit("높이가 있는 건물이 없다 — 경로 확인")

    hs = sorted(h for _a, _b, h in P)
    def q(p): return hs[min(len(hs) - 1, int(len(hs) * p))]
    print("[1] PLATEAU 실측 높이 분포")
    print(f"  중앙 {q(.5):.1f} m   25% {q(.25):.1f}   75% {q(.75):.1f}   90% {q(.9):.1f}"
          f"   최대 {hs[-1]:.1f}")
    print(f"  6.9 m(기본2층) 이하 {100*sum(1 for h in hs if h <= 6.9)/len(hs):.0f}%"
          f"   10 m 초과 {100*sum(1 for h in hs if h > 10)/len(hs):.0f}%"
          f"   20 m 초과 {100*sum(1 for h in hs if h > 20)/len(hs):.0f}%")

    # 타일별로 묶어 최근접 매칭
    print("\n[2] 우리 타일과 매칭 — 엔진이 쓰는 높이와 비교")
    bytile: dict[str, list] = {}
    for la, lo, h in P:
        bytile.setdefault(tkey(la, lo), []).append((la, lo, h))
    pairs = []
    matched: list = []          # (lat, lon, h_plateau, h_engine) — [5] SVF 비교용
    src_cnt: dict[str, int] = {}
    for tk, items in sorted(bytile.items(), key=lambda kv: -len(kv[1])):
        path = os.path.join(TILE_DIR, tk + ".json")
        if not os.path.isfile(path):
            continue
        try:
            els = (json.load(open(path, encoding="utf-8")) or {}).get("elements") or []
        except Exception:  # noqa: BLE001
            continue
        osm = []
        for e in els:
            g = e.get("geometry") or []
            if len(g) < 4:
                continue
            la = sum(p["lat"] for p in g) / len(g)
            lo = sum(p["lon"] for p in g) / len(g)
            osm.append((la, lo, e.get("tags") or {}))
        if not osm:
            continue
        for la, lo, h in items:
            best, bd = None, 1e9
            for ola, olo, tg in osm:
                dy = (ola - la) * 111320.0
                dx = (olo - lo) * 111320.0 * math.cos(math.radians(la))
                dd = dy * dy + dx * dx
                if dd < bd:
                    bd, best = dd, tg
            if best is not None and math.sqrt(bd) <= MATCH_M:
                eh, srcs = eng_height(best)
                pairs.append((h, eh, srcs, area_of(la, lo)))
                matched.append((la, lo, h, eh))
                src_cnt[srcs] = src_cnt.get(srcs, 0) + 1

    if not pairs:
        print("  매칭 0 — 타일이 없거나 좌표계가 다르다")
        return
    print(f"  매칭 {len(pairs):,}동   출처별 {src_cnt}")
    diff = [e - p for p, e, _s, _a in pairs]
    ad = sorted(abs(x) for x in diff)
    print(f"  엔진 − 실측   평균 {st.mean(diff):+.1f} m   중앙 {st.median(diff):+.1f} m")
    print(f"  |차이|        중앙 {ad[len(ad)//2]:.1f} m   90% {ad[int(len(ad)*.9)]:.1f} m")

    print("\n[3] 출처별 — 어디가 문제인가")
    print(f"  {'출처':<12}{'동수':>8}{'실측중앙':>10}{'엔진중앙':>10}{'평균차':>9}")
    for s in ("height", "levels", "default2"):
        v = [(p, e) for p, e, ss, _a in pairs if ss == s]
        if not v:
            continue
        print(f"  {s:<12}{len(v):>8}{st.median(x[0] for x in v):>10.1f}"
              f"{st.median(x[1] for x in v):>10.1f}"
              f"{st.mean(x[1]-x[0] for x in v):>+9.1f}")

    print("\n[4] 구역별 — 오피스가와 저층가가 다른가")
    print(f"  {'구역':<20}{'동수':>8}{'실측중앙':>10}{'실측90%':>10}{'엔진중앙':>10}"
          f"{'평균차':>9}{'기본2층비율':>12}")
    for nm, *_r in AREAS:
        v = [(p, e, ss) for p, e, ss, a in pairs if a == nm]
        if not v:
            print(f"  {nm:<20}{'자료없음':>8}")
            continue
        hh = sorted(x[0] for x in v)
        nd = sum(1 for x in v if x[2] == "default2")
        print(f"  {nm:<20}{len(v):>8}{hh[len(hh)//2]:>10.1f}{hh[int(len(hh)*0.9)]:>10.1f}"
              f"{st.median(x[1] for x in v):>10.1f}"
              f"{st.mean(x[1]-x[0] for x in v):>+9.1f}{100*nd/len(v):>11.0f}%")

    svf_section(matched)

    print("\n읽는 법:")
    print("  · [4] 에서 오피스가의 '평균차' 가 크게 음수면 -> 그 구역에서 하늘을 과대평가하고 있다.")
    print("    저층가에서만 맞는다면 도시 전체에 같은 기본값을 쓰면 안 된다는 뜻이다.")
    print("  · default2 줄의 '실측중앙' 이 6.9 m 보다 훨씬 크면 -> 2층 기본값이 크게 틀린 것이다.")
    print("    그 차이가 곧 일본 SVF 과대평가의 크기다. PLATEAU 전체를 받을 값어치가 정해진다.")
    print("  · height/levels 줄의 평균차가 크면 -> OSM 태그 자체도 못 믿는다는 뜻이다.")
    print("  · [5] 의 '차이' 가 +0.05 이상이면 -> 엔진이 하늘을 그만큼 더 열려 있다고 본다.")
    print("    SVF 0.05 는 여름 낮 체감온도로 대략 0.3–0.5°C 이다. PLATEAU 도입 값어치의 기준.")
    print("  · '실측최고' 와 '엔진최고' 가 많이 벌어지면 -> 그 지점 근처 고층을 통째로 놓치고 있다.")


if __name__ == "__main__":
    main()
