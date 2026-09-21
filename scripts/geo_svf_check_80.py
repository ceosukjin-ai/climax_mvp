#!/usr/bin/env python3
"""기하(무영상) SVF 를 80지점 어안 SVF(일관 정의)와 대조 — 서버 climax-api 안에서 실행 (2026-09-21).

  docker cp data/scs_master_80.csv climax-api:/tmp/
  docker cp scripts/geo_svf_check_80.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/geo_svf_check_80.py
  docker cp climax-api:/tmp/geo_svf_80.csv data/

출력 data/geo_svf_80.csv (측정ID, geo_svf, n_bld, n_canopy, source) + 화면에 MAE/bias/r.
"""
import asyncio, csv, math, statistics as st, sys
sys.path.insert(0, "/app")


async def main():
    from app.services import geo
    rows = list(csv.DictReader(open("/tmp/scs_master_80.csv", encoding="utf-8-sig")))
    out = []
    for r in rows:
        d = await geo.svf_geometric(float(r["위도"]), float(r["경도"]))
        g = d.get("svf")
        out.append(dict(측정ID=r["측정ID"], geo_svf="" if g is None else round(g, 4),
                        n_bld=d.get("n_bld", ""), n_canopy=d.get("n_canopy", 0),
                        source=str(d.get("source", ""))[:40]))
        if len(out) % 20 == 0:
            print(f"  {len(out)}/80", flush=True)
    with open("/tmp/geo_svf_80.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    obs = {r["측정ID"]: r["SVF"] for r in rows}
    p = [(float(o["geo_svf"]), float(obs[o["측정ID"]])) for o in out
         if o["geo_svf"] != "" and obs[o["측정ID"]] != ""]
    e = [a - b for a, b in p]; n = len(p)
    ma = sum(a for a, _ in p) / n; mb = sum(b for _, b in p) / n
    cov = sum((a - ma) * (b - mb) for a, b in p)
    r = cov / math.sqrt(sum((a - ma) ** 2 for a, _ in p) * sum((b - mb) ** 2 for _, b in p))
    print(f"\n기하 SVF vs 어안 SVF   n {n}   MAE {st.mean(map(abs, e)):.3f}   "
          f"bias {st.mean(e):+.3f}   r {r:.2f}")
    print("저장 /tmp/geo_svf_80.csv")


asyncio.run(main())
