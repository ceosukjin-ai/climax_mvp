#!/usr/bin/env python3
"""실측 80점에 건물기하 가로폭(W)·협곡비(H/W)를 붙여 폭 등급별 잔차 확인 (2026-09-09).

배포된 API(/vpti/geo/at → street_width_m, hw_ratio)를 80번 호출. 맥 터미널에서:
  python3 scripts/street_width_80.py
출력: data/tier3_width_80.csv + 요약표 (그대로 붙여넣기)
"""
import csv, json, os, sys, time, urllib.request

API = "https://api.climaxapp.kr/api/v1/vpti/geo/at"
IN = "data/tier3_engine_output_80_v2.csv"
OUT = "data/tier3_width_80.csv"


def cls(w):
    if w is None:
        return "개방"
    if w < 6:
        return "보행골목(<6m)"
    if w < 12:
        return "혼합골목(6-12m)"
    return "큰길(12m+)"


def main():
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    out = []
    for i, r in enumerate(rows, 1):
        url = f"{API}?lat={r['위도']}&lon={r['경도']}"
        try:
            d = json.load(urllib.request.urlopen(url, timeout=60))
        except Exception as e:
            print(f"  {i} 실패 {e}"); d = {}
        w, hw = d.get("street_width_m"), d.get("hw_ratio")
        out.append({**r, "width_m": w if w is not None else "", "hw_ratio": hw if hw is not None else "",
                    "api_svf": d.get("svf", ""), "snapped_m": d.get("snapped_m", ""), "폭등급": cls(w)})
        if i % 10 == 0:
            print(f"  {i}/80", flush=True)
        time.sleep(0.2)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(out[0].keys())); wr.writeheader(); wr.writerows(out)
    print(f"저장: {OUT}")
    ns = sum(1 for o in out if o["snapped_m"] not in ("", None, 0, 0.0) and float(o["snapped_m"]) > 0)
    print(f"GPS 오차로 건물 안에 찍혀 골목으로 끌어낸 지점: {ns}/80")

    print("\n=== 폭등급별 (n, 폭, H/W, SVF, 실측PET, 엔진v2 bias, MAE, 그늘비율) ===")
    for c in ("보행골목(<6m)", "혼합골목(6-12m)", "큰길(12m+)", "개방"):
        g = [o for o in out if o["폭등급"] == c]
        if not g:
            print(f"  {c}: 0"); continue
        e = [float(o["run_C_PET"]) - float(o["PET"]) for o in g]
        ws = [float(o["width_m"]) for o in g if o["width_m"] != ""]
        hs = [float(o["hw_ratio"]) for o in g if o["hw_ratio"] != ""]
        print(f"  {c:12s} n={len(g):2d}  W {sum(ws)/len(ws) if ws else 0:5.1f}m  H/W {sum(hs)/len(hs) if hs else 0:.2f}"
              f"  SVF {sum(float(o['tier3_svf']) for o in g)/len(g):.2f}"
              f"  실측PET {sum(float(o['PET']) for o in g)/len(g):.1f}"
              f"  bias {sum(e)/len(e):+.2f}  MAE {sum(abs(x) for x in e)/len(e):.2f}"
              f"  그늘 {sum(1 for o in g if o['볕'].strip()!='1')/len(g):.0%}")

    try:
        import numpy as np
        from sklearn.linear_model import Ridge
    except ImportError:
        print("\n(sklearn 없음: python3 -m pip install scikit-learn 후 재실행하면 LOSO 비교 나옴)"); return
    F = ["볕", "tier3_svf", "tier3_gvi", "Ta", "RH", "v", "태양고도", "run_C_Tmrt", "run_C_PET", "ndvi30"]
    ok = [o for o in out if o["width_m"] != ""]
    X = np.array([[float(o[k]) for k in F] for o in ok])
    Xw = np.array([[float(o["width_m"]), float(o["hw_ratio"])] for o in ok])
    y = np.array([float(o["PET"]) for o in ok]); ye = np.array([float(o["run_C_PET"]) for o in ok])
    grp = np.array([o["권역"] for o in ok])
    print(f"\n폭 있는 지점 {len(ok)}/80,  r(폭, 잔차) = {np.corrcoef(Xw[:,0], y-ye)[0,1]:+.2f},"
          f"  r(H/W, 잔차) = {np.corrcoef(Xw[:,1], y-ye)[0,1]:+.2f},  r(H/W, SVF) = {np.corrcoef(Xw[:,1], X[:,1])[0,1]:+.2f}")

    def loso(Xf):
        p = np.zeros(len(y))
        for gname in set(grp):
            te = grp == gname; tr = ~te
            mu = Xf[tr].mean(0); sd = Xf[tr].std(0) + 1e-9
            m = Ridge(10.0).fit((Xf[tr] - mu) / sd, (y - ye)[tr])
            p[te] = ye[te] + m.predict((Xf[te] - mu) / sd)
        return float(np.mean(np.abs(p - y)))
    print(f"=== 잔차 AI LOSO MAE: 폭 없이 {loso(X):.2f}  /  폭+H/W 추가 {loso(np.hstack([X, Xw])):.2f}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main())
