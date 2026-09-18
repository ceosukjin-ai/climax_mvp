#!/usr/bin/env python3
"""환경성 暑さ指数(WBGT) 적재 — 일본 앱의 공식 언어 (2026-09-18).

왜: 일본인은 「暑さ指数 33」을 안다. PET·VPTI 는 모른다. 뉴스·학교·직장이 그 숫자로 말한다.
환경성이 실황·예측·경보를 무료 CSV 오픈데이터로 준다(출처 명기 조건).

그리고 이게 **법적으로도 필요하다.** 일본 기상업무법은 자체 예보를 일반에 공표하려면
기상청 허가가 필요하다(제17조, 원칙 금지). 2026년 5월 개정으로 외국 사업자 규제가 강화됐다.
→ "지금 이 자리"는 우리 엔진이 산출(실황 해석, 허가 불요),
  "앞으로"는 **환경성 예측을 그대로 전재**한다(전재는 허가 불요).

제품에서의 자리: 환경성은 841지점·부현 단위, 우리는 11 m 격자다.
  「도쿄도 경계 발령(33) — 지금 선 이 자리는 31, 50 m 앞 횡단보도는 36」

⚠️ 운용 CSV 주소가 안내 페이지에 안 적혀 있다. 추측하지 않는다 —
   `--probe` 로 후보를 실제로 찔러 보고 되는 것을 쓴다.

  # 1) 주소 찾기
  docker run --rm --env-file /tmp/api.env -v $HOME/climax_mvp:/repo climax-backend:latest \
    python3 /repo/scripts/load_wbgt_jp.py --probe
  # 2) 지점 마스터 적재 (한 번)
  ... load_wbgt_jp.py --points
  # 3) 예측 적재 (크론으로 3시간마다)
  ... load_wbgt_jp.py --forecast --base <되는 주소 접두사>
"""
from __future__ import annotations
import argparse, asyncio, csv, io, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

JST = timezone(timedelta(hours=9))
MASTER = "https://www.wbgt.env.go.jp/man15NH/wbgt_point_master-20260515.csv"

# 운용 주소 후보. 안내 페이지에 없어서 찔러 본다. 되는 것이 나오면 --base 로 고정한다.
CANDIDATES = [
    "https://www.wbgt.env.go.jp/prev15WG/dl/",
    "https://www.wbgt.env.go.jp/est15WG/dl/",
    "https://www.wbgt.env.go.jp/man15NH/",
    "https://www.wbgt.env.go.jp/data_service_sample/",
]
PROBE_POINT = "44132"          # 東京(気象庁)

DDL = """
CREATE TABLE IF NOT EXISTS wbgt_point (
    point_id TEXT PRIMARY KEY,
    name     TEXT, name_en TEXT, region TEXT, addr TEXT,
    lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wbgt_point_ll ON wbgt_point (lat, lon);
-- 환경성 예측을 **그대로** 보관한다. 우리가 가공하지 않는다(전재).
CREATE TABLE IF NOT EXISTS wbgt_forecast (
    point_id  TEXT NOT NULL,
    target_at TIMESTAMPTZ NOT NULL,
    wbgt      REAL NOT NULL,
    issued_at TIMESTAMPTZ,
    PRIMARY KEY (point_id, target_at)
);
"""


async def _get(url: str) -> bytes | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(url)
            return r.content if r.status_code == 200 and r.content else None
    except Exception:  # noqa: BLE001
        return None


def _dm(deg: str, minute: str) -> float | None:
    """도 + 분 → 십진도. 마스터 파일이 위경도를 두 칸으로 나눠 준다."""
    try:
        return float(deg.strip()) + float(minute.strip()) / 60.0
    except (TypeError, ValueError):
        return None


def parse_yohou(body: bytes) -> tuple[str, datetime | None, list[tuple[datetime, float]]]:
    """예측 CSV → (지점번호, 작성시각, [(대상시각, WBGT)]). 값은 ×10 으로 들어온다."""
    rows = list(csv.reader(io.StringIO(body.decode("utf-8", "replace"))))
    if len(rows) < 2:
        return "", None, []
    head, data = rows[0], rows[1]
    pid = (data[0] or "").strip()
    issued = None
    try:
        issued = datetime.strptime(data[1].strip(), "%Y/%m/%d %H:%M").replace(tzinfo=JST)
    except (IndexError, ValueError):
        pass
    out = []
    for i in range(2, min(len(head), len(data))):
        t, v = head[i].strip(), data[i].strip()
        if len(t) != 10 or not v:
            continue
        try:
            # 시각 "24" 는 다음날 00 시다 — 그대로 strptime 하면 터진다.
            hh = int(t[8:10])
            base = datetime.strptime(t[:8], "%Y%m%d").replace(tzinfo=JST)
            when = base + timedelta(hours=hh)
            out.append((when, float(v) / 10.0))
        except ValueError:
            continue
    return pid, issued, out


async def cmd_probe() -> None:
    print(f"\n운용 주소 찾기 — 지점 {PROBE_POINT} 예측 파일")
    ym = datetime.now(JST).strftime("%Y%m")
    for base in CANDIDATES:
        for name in (f"yohou_{PROBE_POINT}.csv", f"wbgt_{PROBE_POINT}_{ym}.csv"):
            url = base + name
            b = await _get(url)
            mark = "✅" if b else "  "
            print(f"  {mark} {url}" + (f"   ({len(b)}바이트)" if b else ""))
            if b and name.startswith("yohou"):
                pid, issued, vals = parse_yohou(b)
                if vals:
                    print(f"       지점 {pid} 작성 {issued}  예측 {len(vals)}개  "
                          f"첫 값 {vals[0][0]:%m/%d %H시} {vals[0][1]:.1f}°C")
    print("\n✅ 표시된 접두사를 --base 로 넘긴다. 하나도 없으면 운용 주소가 바뀐 것 —")
    print("   https://www.wbgt.env.go.jp/data_service.php 의 이용안내를 다시 볼 것.")


async def cmd_points(conn) -> None:
    b = await _get(MASTER)
    if not b:
        print("지점 마스터를 못 받았다"); return
    # 인코딩 (2026-09-18 고침): 처음에 cp932 로 읽어 지점 이름이 전부 깨졌다(縺輔＞…).
    # 실제로는 UTF-8 이다. 관공서 CSV 라 cp932 로 넘겨짚었던 것 — 확인하고 쓴다.
    txt = None
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            txt = b.decode(enc)
            if "\ufffd" not in txt and "縺" not in txt:
                break
        except UnicodeDecodeError:
            continue
    rows = list(csv.reader(io.StringIO(txt or b.decode("utf-8", "replace"))))
    out = []
    for r in rows[1:]:
        if len(r) < 11:
            continue
        pid = (r[2] or "").strip()
        la, lo = _dm(r[7], r[8]), _dm(r[9], r[10])
        if not pid or la is None or lo is None:
            continue
        out.append((pid, r[3].strip(), r[5].strip(), r[0].strip(), r[6].strip(), la, lo))
    await conn.executemany(
        "INSERT INTO wbgt_point (point_id,name,name_en,region,addr,lat,lon) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (point_id) DO UPDATE SET "
        "name=EXCLUDED.name, lat=EXCLUDED.lat, lon=EXCLUDED.lon", out)
    n = await conn.fetchval("SELECT count(*) FROM wbgt_point")
    tk = await conn.fetchval("SELECT count(*) FROM wbgt_point WHERE lat BETWEEN 35.5 AND 35.9 "
                             "AND lon BETWEEN 139.55 AND 139.92")
    print(f"✅ 지점 {len(out)}건 적재 (DB 총 {n})  — 도쿄 23구 안 {tk}개")


async def cmd_forecast(conn, base: str, bbox: tuple | None) -> None:
    q = "SELECT point_id FROM wbgt_point"
    args: list = []
    if bbox:
        q += " WHERE lat BETWEEN $1 AND $2 AND lon BETWEEN $3 AND $4"
        args = [bbox[0], bbox[2], bbox[1], bbox[3]]
    pids = [r["point_id"] for r in await conn.fetch(q, *args)]
    print(f"예측 내려받기 — 지점 {len(pids)}개")
    n_ok = n_val = n_noforecast = 0
    for i, pid in enumerate(pids, 1):
        b = await _get(f"{base}yohou_{pid}.csv")
        if not b:
            n_noforecast += 1      # 실측 전용 지점(전국 47곳)은 예측을 안 준다 — 실패가 아니다
            continue
        _p, issued, vals = parse_yohou(b)
        if not vals:
            continue
        await conn.executemany(
            "INSERT INTO wbgt_forecast (point_id,target_at,wbgt,issued_at) VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (point_id,target_at) DO UPDATE SET wbgt=EXCLUDED.wbgt, issued_at=EXCLUDED.issued_at",
            [(pid, t, v, issued) for t, v in vals])
        n_ok += 1; n_val += len(vals)
        if i % 50 == 0:
            print(f"  {i}/{len(pids)}  성공 {n_ok}", flush=True)
        await asyncio.sleep(0.1)          # 공공 서버다 — 몰아치지 않는다
    await conn.execute("DELETE FROM wbgt_forecast WHERE target_at < NOW() - INTERVAL '2 days'")
    print(f"✅ 지점 {n_ok}개, 예측값 {n_val}건 적재"
          + (f"  (예측 미제공 {n_noforecast}개 — 실측 전용 지점)" if n_noforecast else ""))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--points", action="store_true")
    ap.add_argument("--forecast", action="store_true")
    ap.add_argument("--base", default="")
    ap.add_argument("--tokyo", action="store_true", help="도쿄 23구 지점만")
    a = ap.parse_args()

    if a.probe:
        await cmd_probe(); return

    import asyncpg
    from app.config import get_settings
    conn = await asyncpg.connect(
        get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://"))
    await conn.execute(DDL)
    if a.points:
        await cmd_points(conn)
    if a.forecast:
        if not a.base:
            print("--base 가 필요하다 (--probe 로 찾은 접두사)"); await conn.close(); return
        await cmd_forecast(conn, a.base, (35.5, 139.55, 35.9, 139.92) if a.tokyo else None)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
