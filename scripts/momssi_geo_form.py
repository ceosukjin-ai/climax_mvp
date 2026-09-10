#!/usr/bin/env python3
"""몸씨 측정 기록 좌표 → 공간 형태(기하 SVF·가로폭·H/W) 집계 (2026-09-10).

몸씨 기록(export CSV)의 고유 격자마다 /geo/form 을 호출해 골목/폭 분포를 만든다.
실측 없이 "몸씨 데이터가 어떤 공간(골목 vs 큰길)에서 왔는지"를 안다.
맥 터미널:  python3 scripts/momssi_geo_form.py [data/momssi_export_260909_slim.csv]
출력: data/momssi_geo_form.csv (격자별) + 화면 요약. 중단 후 재실행하면 이어서 함.
"""
import csv, json, os, sys, time, urllib.request
from collections import Counter, defaultdict

API = "https://api.climaxapp.kr/api/v1/geo/form"
IN = sys.argv[1] if len(sys.argv) > 1 else "data/momssi_export_260909_slim.csv"
OUT = "data/momssi_geo_form.csv"
GRID = 4  # 소수 4자리 ≈ 11m 격자


def cls(w):
    if w is None or w == "":
        return "개방"
    w = float(w)
    return "보행골목(<6m)" if w < 6 else ("혼합골목(6-12m)" if w < 12 else "큰길(12m+)")


def main():
    rows = [r for r in csv.DictReader(open(IN, encoding="utf-8-sig")) if r.get("lat") and r.get("lon")]
    rows = [r for r in rows if str(r.get("indoor", "")).lower() not in ("1", "true")]
    grids = defaultdict(list)
    for r in rows:
        grids[(round(float(r["lat"]), GRID), round(float(r["lon"]), GRID))].append(r)
    print(f"기록 {len(rows)}건 → 고유 격자 {len(grids)}개")

    done = {}
    if os.path.exists(OUT):
        for r in csv.DictReader(open(OUT, encoding="utf-8-sig")):
            done[(float(r["lat"]), float(r["lon"]))] = r
        print(f"이미 계산됨 {len(done)}개 (이어서)")

    fields = ["lat", "lon", "n_records", "svf", "n_buildings", "street_width_m", "hw_ratio", "snapped_m", "폭등급"]
    todo = [(k, rs) for k, rs in grids.items() if k not in done]

    def fetch(item):
        (la, lo), rs = item
        d = {}
        for attempt in range(2):
            try:
                d = json.load(urllib.request.urlopen(f"{API}?lat={la}&lon={lo}", timeout=90)); break
            except Exception as e:
                if attempt == 1: print(f"  실패 {la},{lo}: {e}")
        rec = {"lat": la, "lon": lo, "n_records": len(rs), "svf": d.get("svf", ""),
               "n_buildings": d.get("n_buildings", ""), "street_width_m": d.get("street_width_m", ""),
               "hw_ratio": d.get("hw_ratio", ""), "snapped_m": d.get("snapped_m", ""),
               "폭등급": cls(d.get("street_width_m"))}
        return (la, lo), {k: ("" if v is None else v) for k, v in rec.items()}

    from concurrent.futures import ThreadPoolExecutor
    new = 0
    with open(OUT, "a" if done else "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not done:
            w.writeheader()
        with ThreadPoolExecutor(max_workers=6) as ex:
            for key, rec in ex.map(fetch, todo):
                w.writerow(rec); f.flush(); done[key] = rec; new += 1
                if new % 100 == 0:
                    print(f"  {len(done)}/{len(grids)}", flush=True)

    # ── 요약 ──
    print("\n=== 몸씨 기록의 공간 형태 분포 ===")
    by = Counter(); byrec = Counter(); pv = defaultdict(list); sv = defaultdict(list)
    for (la, lo), rec in done.items():
        c = rec["폭등급"]; by[c] += 1; byrec[c] += int(rec["n_records"])
        if rec["svf"] not in ("", None): sv[c].append(float(rec["svf"]))
        for r in grids.get((la, lo), []):
            try: pv[c].append(float(r["pvpti"]))
            except (ValueError, KeyError, TypeError): pass
    tot_g = sum(by.values()); tot_r = sum(byrec.values())
    for c in ("보행골목(<6m)", "혼합골목(6-12m)", "큰길(12m+)", "개방"):
        if by[c] == 0: continue
        print(f"  {c:12s} 격자 {by[c]:5d} ({by[c]/tot_g:4.0%})  기록 {byrec[c]:5d} ({byrec[c]/tot_r:4.0%})"
              f"  기하SVF {sum(sv[c])/len(sv[c]) if sv[c] else 0:.2f}"
              f"  앱 pVPTI 평균 {sum(pv[c])/len(pv[c]) if pv[c] else 0:.1f}")
    ws = [float(r["street_width_m"]) for r in done.values() if r["street_width_m"] not in ("", None)]
    if ws:
        ws.sort(); print(f"  폭 중앙값 {ws[len(ws)//2]:.1f}m,  6m 미만 {sum(1 for x in ws if x < 6)/len(ws):.0%},  12m 미만 {sum(1 for x in ws if x < 12)/len(ws):.0%}")
    print(f"  GPS 스냅 격자 {sum(1 for r in done.values() if r['snapped_m'] not in ('', None, 0) and float(r['snapped_m']) > 0)}개")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())
