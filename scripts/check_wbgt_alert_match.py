# 경보 구역 매칭 점검 (2026-09-25) — 한여름 파일(8/5 17시)을 잠깐 넣고 도시별로 API 와 같은 질의를 돌린다.
# 실행(서버): cat scripts/load_wbgt_jp.py scripts/check_wbgt_alert_match.py | docker exec -i climax-api python3 -
# load_wbgt_jp.py 뒤에 이어 붙여 그 함수들을 쓴다. 끝나면 8/5 행은 지운다(오늘 화면에는 영향 없음).
async def _check():
    import asyncpg
    from app.config import get_settings
    c = await asyncpg.connect(get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute(DDL)
    day = datetime(2026, 8, 5, tzinfo=JST)
    meta, rows = parse_alert(await _get(alert_url(ALERT_BASE, day, "17")))
    td = _d(meta["TargetDate1"])
    for r in rows:
        await c.execute(
            "INSERT INTO wbgt_alert (area,target_date,level,points,flag,disp) VALUES ($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT (area,target_date) DO UPDATE SET level=EXCLUDED.level, points=EXCLUDED.points, "
            "flag=EXCLUDED.flag, disp=EXCLUDED.disp",
            r["area"], td, ALERT_FLAG.get(r["f1"], "none"), r["points"], r["f1"], r["disp"])
    on = sorted(r["area"] for r in rows if r["f1"] in ("1", "3"))
    print(f"8/5 경보 구역 {len(on)}: {' '.join(on)}\n")
    cities = [("東京駅", 35.681, 139.767), ("新宿", 35.690, 139.700), ("横浜", 35.466, 139.622),
              ("江ノ島", 35.300, 139.480), ("大阪", 34.702, 135.496), ("京都", 35.011, 135.768),
              ("名古屋", 35.170, 136.882), ("福岡", 33.590, 130.420), ("那覇", 26.212, 127.679),
              ("札幌", 43.068, 141.351), ("仙台", 38.260, 140.882), ("日光", 36.750, 139.600)]
    for nm, la, lo in cities:
        a = await c.fetchrow(
            "SELECT p.name, a.area, a.level FROM wbgt_point p "
            "JOIN wbgt_alert a ON a.target_date = $3 AND a.points LIKE '%/' || p.name || '/%' "
            "ORDER BY (p.lat-$1)^2 + (p.lon-$2)^2, (a.disp = left(p.point_id, 2)) DESC LIMIT 1",
            la, lo, td)
        print(f"  {nm:6s} → 지점 {a['name']:6s} 구역 {a['area']:12s} {a['level']}" if a else f"  {nm} → 없음")
    await c.execute("DELETE FROM wbgt_alert WHERE target_date = $1", td)
    await c.close()

asyncio.run(_check())
