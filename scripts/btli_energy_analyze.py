#!/usr/bin/env python3
"""BTLI 검증 2차 분석 — 이미 받은 CSV 만으로 (API 호출 없음, 몇 초) (2026-09-25).

1차 결과: BTLI ρ≈−0.02, 봄가을 기저전기 ρ=+0.58.
→ 연면적당 냉방전기는 **그 단지 사람들이 전기를 얼마나 쓰나**(세대원 수·생활방식)가 좌우한다.
   건물 외피 차이는 그 밑에 묻힌다. 그래서 사람 요인을 나눠 없앤 지표로 다시 본다:
   · 여름 증가율 = 냉방전기 / 봄가을 월평균  (같은 사람들이 여름에 얼마나 더 쓰나)
   · 세대당 냉방전기
  python3 scripts/btli_energy_analyze.py data/btli_energy_busan_2026-09-25.csv
"""
import csv, math, statistics as st, sys


def rank(v):
    o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2
        i = j + 1
    return r


def rho(x, y):
    p = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(p) < 8:
        return float("nan"), len(p)
    rx, ry = rank([a for a, _ in p]), rank([b for _, b in p])
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return (num / den if den else float("nan")), len(p)


rows = list(csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "data/btli_energy_busan_2026-09-25.csv", encoding="utf-8-sig")))
f = lambda r, k: float(r[k]) if r.get(k) not in (None, "") else None
for r in rows:
    sh, cool = f(r, "봄가을월"), f(r, "냉방전기")
    r["_uplift"] = cool / sh if sh else None
    r["_per_hh"] = cool / f(r, "세대") if f(r, "세대") else None
    r["_env_share"] = f(r, "btli_env_w_m2") / f(r, "btli_w_m2") if f(r, "btli_w_m2") else None
print(f"단지 {len(rows)}곳 · 여름 증가율 중앙값 {st.median([r['_uplift'] for r in rows if r['_uplift'] is not None]):.2f} "
      f"(냉방전기 ÷ 봄가을 월평균) · 세대당 냉방 중앙값 {st.median([r['_per_hh'] for r in rows if r['_per_hh']]):.0f} kWh")
print(f"냉방전기 0 으로 잡힌 단지(여름<봄가을): {sum(1 for r in rows if f(r,'냉방전기')==0)}곳\n")
tests = [("BTLI 외피부하/연면적", "btli_env_w_m2"), ("BTLI 외피 비중", "_env_share"),
         ("평균층수(낮을수록↑)", "-평균층"), ("준공연도(오래될수록↑)", "-준공"), ("동수", "동수")]
for tgt, lab in (("_uplift", "여름 증가율"), ("_per_hh", "세대당 냉방전기"), ("obs_kwh_m2", "연면적당 냉방전기")):
    print(f"[{lab}]")
    y = [r[tgt] if tgt.startswith("_") else f(r, tgt) for r in rows]
    for name, k in tests:
        neg = k.startswith("-"); kk = k.lstrip("-")
        x = [(r[kk] if kk.startswith("_") else f(r, kk)) for r in rows]
        x = [(-v if (neg and v is not None) else v) for v in x]
        p, n = rho(x, y)
        print(f"   {name:22s} ρ = {p:+.2f}  (n={n})")
# 동(지역)별 기후·생활권 차이를 빼고: 동 안에서 순위만 본 평균 ρ
print("\n[동 안에서만 비교한 ρ 평균 — 지역 차이 제거, 여름 증가율 기준]")
for name, k in tests[:4]:
    neg = k.startswith("-"); kk = k.lstrip("-"); ps = []
    for d in sorted(set(r["동"] for r in rows)):
        g = [r for r in rows if r["동"] == d]
        x = [(r[kk] if kk.startswith("_") else f(r, kk)) for r in g]
        x = [(-v if (neg and v is not None) else v) for v in x]
        p, n = rho(x, [r["_uplift"] for r in g])
        if not math.isnan(p):
            ps.append(p)
    print(f"   {name:22s} 평균 ρ = {st.mean(ps):+.2f}  (동 {len(ps)}개)" if ps else f"   {name}: 표본 부족")
