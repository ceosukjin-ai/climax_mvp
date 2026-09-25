#!/usr/bin/env python3
"""BTLI 검증 진단 — 단지별 월 전기 **원 시계열** 저장 (2026-09-25).

3차(기상 민감도)에서 전기-냉방도일 기울기가 R² 0.02·음수로 나왔다. 4~10월에 더위가 올라가도
전기가 안 오른다는 뜻인데, 1차의 여름 증가율(+71%)과 맞지 않는다. 적합식이 아니라 **달별 원값**을
봐야 원인을 안다(사용월=청구월 지연? 난방·조명 계절성? 공용부 비중?).
→ 앞쪽 N개 단지의 2024-01~2026-05 전 달(29개월)을 그대로 저장한다. 판단은 Mac 에서.

  docker run --rm --env-file /tmp/api.env -v ~/climax_mvp:/repo -v ~/climax_mvp/backend/app:/app/app:ro \
    climax-backend:latest python3 -u /repo/scripts/btli_energy_series.py 40
출력: /repo/data/btli_energy_series_busan_2026-09-25.csv  (단지 × 월, kWh)
호출량: N × 29 (40곳이면 1,160건 — 개발계정 일 한도 안)
"""
import asyncio, csv, os, sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/repo/scripts")
IN = "/repo/data/btli_energy_busan_2026-09-25.csv"
OUT = "/repo/data/btli_energy_series_busan_2026-09-25.csv"
URL = "https://apis.data.go.kr/1613000/BldEngyHubService/getBeElctyUsgInfo"
MONTHS = [f"{y}{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 5)]


async def main():
    import httpx
    from app.config import get_settings
    from app.services.building import _reverse_vworld
    from btli_energy_validate import SEEDS, _items, _f
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    key = os.environ.get("ENERGY_API_KEY") or get_settings().building_api_key
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    # 동마다 고르게 뽑는다(앞에서부터 N 개면 한두 동에 몰린다)
    by = {}
    for r in rows:
        by.setdefault(r["동"], []).append(r)
    pick, i = [], 0
    while len(pick) < n and any(by.values()):
        for d in list(by):
            if by[d] and len(pick) < n:
                pick.append(by[d].pop(0))
    async with httpx.AsyncClient(timeout=20.0) as client:
        codes = {}
        for nm, (la, lo) in SEEDS.items():
            pc = await _reverse_vworld(client, la, lo)
            if pc:
                codes[nm] = (pc[0][:5], pc[0][5:10])
        out = []
        for k, r in enumerate(pick, 1):
            sgg, bjd = codes[r["동"]]
            bun, ji = r["지번"].split("-")
            rec = {"동": r["동"], "지번": r["지번"], "단지": r["단지"], "세대": r["세대"],
                   "btli_env_w_m2": r["btli_env_w_m2"]}
            for ym in MONTHS:
                v = None
                for t in range(3):
                    try:
                        resp = await client.get(URL, params={"serviceKey": key, "sigunguCd": sgg, "bjdongCd": bjd,
                                                             "platGbCd": "0", "bun": bun, "ji": ji, "useYm": ym,
                                                             "numOfRows": "10", "pageNo": "1", "_type": "json"})
                        items, _ = _items(resp.json())
                        v = sum(_f(x.get("useQty")) for x in items) if items else None
                        break
                    except Exception:  # noqa: BLE001
                        await asyncio.sleep(1 + t)
                rec[ym] = "" if v is None else int(v)
                await asyncio.sleep(0.12)
            out.append(rec)
            print(f"  {k}/{len(pick)} {r['단지'][:12]}", flush=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    print(f"저장 {OUT}  ({len(out)}단지 × {len(MONTHS)}개월)")


if __name__ == "__main__":
    asyncio.run(main())
