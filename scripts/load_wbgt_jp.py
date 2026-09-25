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
# 경보(熱中症特別警戒情報・熱中症警戒情報) 파일 (2026-09-25 확정).
# 실제 경로는 **연도 폴더**가 붙는다: alert/dl/<YYYY>/alert_<YYYYMMDD>_<HH>.csv
# (data_service.php 표본 + 검색으로 확인. 9/18 판은 연도 폴더를 빼먹어 한여름에도 0건이었다.)
# 하루 4번 발표: 05·10·14·17시(JST). 늦은 발표가 앞 발표를 덮는다.
ALERT_BASE = "https://www.wbgt.env.go.jp/alert/dl/"
ALERT_HOURS = ("05", "10", "14", "17")
# 파일의 플래그 정의(FlagExplanation 원문):
#   0 발표 없음, 1 熱中症警戒情報 발표, 2 特別警戒情報 판정, 3 特別警戒情報 발표, 9 발표시간 외
# 2(판정)는 아직 **발표가 아니다** — 우리가 발표로 격상하지 않는다(기상업무법·전재 원칙).
# 9 는 그 파일에 정보가 없다는 뜻이라 앞 발표를 덮지 않는다.
ALERT_FLAG = {"0": "none", "1": "alert", "2": "judged", "3": "special"}


def alert_url(base: str, day: datetime, hh: str) -> str:
    return f"{base}{day:%Y}/alert_{day:%Y%m%d}_{hh}.csv"

DDL = """
CREATE TABLE IF NOT EXISTS wbgt_point (
    point_id TEXT PRIMARY KEY,
    name     TEXT, name_en TEXT, region TEXT, addr TEXT,
    lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wbgt_point_ll ON wbgt_point (lat, lon);
-- 환경성 예측을 **그대로** 보관한다. 우리가 가공하지 않는다(전재).
-- 熱中症警戒情報 — 부현(府県予報区) 단위. 예측 WBGT 33 이상이면 발표.
-- ⚠️ 우리가 판정하지 않는다. 환경성 발표를 **그대로** 보관한다(기상업무법).
CREATE TABLE IF NOT EXISTS wbgt_alert (
    area      TEXT NOT NULL,          -- 府県予報区 코드 또는 이름(파일 그대로)
    target_date DATE NOT NULL,
    level     TEXT NOT NULL,          -- alert | special | none
    issued_at TIMESTAMPTZ,
    raw       TEXT,
    PRIMARY KEY (area, target_date)
);
-- 2026-09-25: 지역 매칭을 **지점 이름**으로 한다. 경보 파일의 각 행(예보구역)에
-- 그 구역 지점 목록(「宗谷岬:12/稚内:12/…」)이 들어 있다 → points 에 "/이름/이름/" 로 둔다.
-- 지점 마스터의 region(「北海道」)은 경보 구역(「宗谷地方」)과 단위가 달라 글자 맞추기가 안 된다.
ALTER TABLE wbgt_alert ADD COLUMN IF NOT EXISTS pref TEXT;
ALTER TABLE wbgt_alert ADD COLUMN IF NOT EXISTS points TEXT;
ALTER TABLE wbgt_alert ADD COLUMN IF NOT EXISTS flag TEXT;
-- 都府県・振興局表示番号(도쿄 44, 오사카 62 …) = 지점번호 앞 두 자리. 같은 이름 지점이
-- 여러 곳에 있을 때(지점 865 / 고유 이름 831) 어느 구역인지 가르는 데 쓴다.
ALTER TABLE wbgt_alert ADD COLUMN IF NOT EXISTS disp TEXT;
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


async def cmd_probe_alert(days: list[str] | None = None) -> None:
    """경보 파일 주소 확인. 오늘·어제 + 한여름 대조일(8월엔 거의 매일 어딘가 경보가 있다)."""
    now = datetime.now(JST)
    ds = [now - timedelta(days=d) for d in (0, 1)] + \
        [datetime(2026, 8, 5, tzinfo=JST), datetime(2026, 8, 12, tzinfo=JST)]
    print("\n경보 파일 주소 확인 (오늘·어제 + 한여름 대조)")
    found = False
    for day in ds:
        for hh in ALERT_HOURS:
            url = alert_url(ALERT_BASE, day, hh)
            b = await _get(url)
            if not b:
                print(f"     {url}")
                continue
            found = True
            meta, rows = parse_alert(b)
            n1 = sum(1 for r in rows if r["f1"] in ("1", "3"))
            n2 = sum(1 for r in rows if r["f2"] in ("1", "3"))
            print(f"  ✅ {url}  Status={meta.get('Status')}  구역 {len(rows)}  "
                  f"경보 {meta.get('TargetDate1')}:{n1}  {meta.get('TargetDate2')}:{n2}")
    if not found:
        print("  하나도 없다 → 주소가 또 바뀐 것. https://www.wbgt.env.go.jp/alert_record.php 확인.")


def parse_alert(body: bytes) -> tuple[dict, list[dict]]:
    """경보 CSV → (머리 정보, 구역 행들).

    형식(표본 alert_20240411_17.csv 로 확인): 위쪽에 「키,값」 머리 줄들, 그 다음
    「府県予報区,…,TargetDate1フラグ,TargetDate2フラグ,日最高WBGT…」 표.
    열: 0 구역명, 3 구역코드, 4 도도부현, 6·7 플래그, 8~ 지점별 일최고 WBGT 「이름:값/…」.
    """
    txt = body.decode("utf-8-sig", "replace")
    meta: dict = {}
    rows: list[dict] = []
    in_table = False
    for r in csv.reader(io.StringIO(txt)):
        if not r or not r[0].strip():
            continue
        k = r[0].strip()
        if not in_table:
            if k == "府県予報区":
                in_table = True
            elif len(r) > 1:
                meta[k] = r[1].strip()
            continue
        if len(r) < 8:
            continue
        names = set()
        for cell in r[8:]:
            for tok in cell.split("/"):
                nm = tok.split(":")[0].strip()
                if nm:
                    names.add(nm)
        rows.append({"area": k, "disp": r[1].strip(), "code": r[3].strip(), "pref": r[4].strip(),
                     "f1": r[6].strip(), "f2": r[7].strip(),
                     "points": "/" + "/".join(sorted(names)) + "/" if names else None,
                     "raw": ",".join(r[:8])})
    return meta, rows


def _d(s: str | None):
    try:
        return datetime.strptime((s or "").strip(), "%Y/%m/%d").date()
    except ValueError:
        return None


async def cmd_alert(conn, base: str) -> None:
    """경보 적재 — 환경성 플래그를 **그대로** 옮긴다(판정하지 않는다).

    어제·오늘 파일을 발표 순서대로 읽는다. 같은 (구역, 대상일)은 늦은 발표가 덮는다.
    플래그 9(발표시간 외)·모르는 값은 건너뛴다 — 앞 발표를 지우지 않게.
    """
    base = base or ALERT_BASE
    now = datetime.now(JST)
    n_file = n_row = 0
    for d in (1, 0):
        day = now - timedelta(days=d)
        for hh in ALERT_HOURS:
            b = await _get(alert_url(base, day, hh))
            if not b:
                continue
            meta, rows = parse_alert(b)
            if meta.get("Status") and meta["Status"] not in ("通常", "本番", "正式"):
                # 표본 파일은 「試験」이다. 운용 파일 값이 뭔지 한 번 보여주고 계속 간다.
                print(f"  (Status={meta['Status']} — {day:%m/%d} {hh}시 파일)")
            try:
                issued = datetime.strptime(f"{meta.get('ReportDate')} {meta.get('ReportTime')}",
                                           "%Y/%m/%d %H:%M:%S").replace(tzinfo=JST)
            except ValueError:
                issued = day.replace(hour=int(hh), minute=0, second=0, microsecond=0)
            n_file += 1
            for td_key, fk in (("TargetDate1", "f1"), ("TargetDate2", "f2")):
                td = _d(meta.get(td_key))
                if td is None:
                    continue
                for r in rows:
                    lv = ALERT_FLAG.get(r[fk])
                    if lv is None:
                        continue
                    await conn.execute(
                        "INSERT INTO wbgt_alert (area,target_date,level,issued_at,raw,pref,points,flag,disp) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT (area,target_date) DO UPDATE SET "
                        "level=EXCLUDED.level, issued_at=EXCLUDED.issued_at, raw=EXCLUDED.raw, "
                        "pref=EXCLUDED.pref, points=EXCLUDED.points, flag=EXCLUDED.flag, disp=EXCLUDED.disp "
                        "WHERE wbgt_alert.issued_at IS NULL OR EXCLUDED.issued_at >= wbgt_alert.issued_at",
                        r["area"], td, lv, issued, r["raw"][:500], r["pref"], r["points"], r[fk], r["disp"])
                    n_row += 1
    await conn.execute("DELETE FROM wbgt_alert WHERE target_date < (NOW() AT TIME ZONE 'Asia/Tokyo')::date - 7")
    on = await conn.fetch(
        "SELECT target_date, level, count(*) n FROM wbgt_alert "
        "WHERE target_date >= (NOW() AT TIME ZONE 'Asia/Tokyo')::date AND level <> 'none' "
        "GROUP BY 1,2 ORDER BY 1,2")
    print(f"✅ 경보 파일 {n_file}개, 구역·날짜 {n_row}행 반영")
    for r in on:
        print(f"   {r['target_date']}  {r['level']:8s} {r['n']}구역")
    if not on:
        print("   오늘·내일 발표 중인 경보 없음")


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
        "name=EXCLUDED.name, name_en=EXCLUDED.name_en, region=EXCLUDED.region, "
        "addr=EXCLUDED.addr, lat=EXCLUDED.lat, lon=EXCLUDED.lon", out)
    # 2026-09-25: 예전엔 name·lat·lon 만 덮어써서, 9/18 cp932 오독으로 깨진 region(「髢｢譚ｱ」=関東)이
    # 인코딩을 고친 뒤에도 남아 있었다. 전부 덮어쓴다.
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
    ap.add_argument("--probe-alert", action="store_true")
    ap.add_argument("--alert", action="store_true")
    ap.add_argument("--points", action="store_true")
    ap.add_argument("--forecast", action="store_true")
    ap.add_argument("--base", default="")
    ap.add_argument("--alert-base", default="", help="기본값 ALERT_BASE")
    ap.add_argument("--tokyo", action="store_true", help="도쿄 23구 지점만")
    a = ap.parse_args()

    if a.probe:
        await cmd_probe(); return
    if a.probe_alert:
        await cmd_probe_alert(); return

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
    if a.alert:
        await cmd_alert(conn, a.alert_base or ALERT_BASE)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
