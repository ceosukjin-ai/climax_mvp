#!/usr/bin/env python3
"""일본 공식 쿨링 셸터 적재 — 지자체 오픈데이터 (2026-09-25).

환경성은 전국 셸터 좌표를 모아 주지 않는다(지자체 링크 모음만). 그래서 지자체가
좌표까지 공개한 목록을 모은다. 처음은 도쿄도 오픈데이터 카탈로그(CKAN) —
구·시가 「クーリングシェルター(指定暑熱避難施設)一覧」「クールシェアスポット」을 올린다.

  # 1) 무엇이 있는지 본다 (DB 안 씀)
  docker exec -i climax-api python3 - --probe < scripts/load_shelters_jp.py
  # 2) 적재
  docker exec -i climax-api python3 - --load  < scripts/load_shelters_jp.py

원칙
  · 좌표가 없는 행은 **버린다.** 주소로 지오코딩해 넣지 않는다(틀린 점이 공식으로 보이면 안 된다).
  · 행마다 출처 URL·라이선스를 보관한다. 화면·API 가 그걸 그대로 보여준다.
  · 다시 돌리면 같은 출처(src) 행을 지우고 새로 넣는다 — 지정 해제된 곳이 남지 않게.
"""
from __future__ import annotations
import argparse, asyncio, csv, io, re, sys

sys.path.insert(0, "/app")

CKAN = "https://catalog.data.metro.tokyo.lg.jp/api/3/action/package_search"
QUERIES = ["クーリングシェルター", "暑熱避難施設", "クールシェアスポット"]
# 도쿄도 오픈데이터 API (2026-09-25). CSV 파일 서버(opendata.metro.tokyo.lg.jp)는 기계 접속에 403 을
# 주지만, 같은 데이터가 API 로도 열려 있다. POST {} → {"total":…, "hits":[{열:값}]} 류.
# 카탈로그 검색으로 찾은 데이터셋의 CSV 가 막히면 여기 등록한 API 로 받는다.
TOKYO_API = "https://service.api.metro.tokyo.lg.jp/api/{}/json"
API_IDS = {
    # 패키지 name → API id (spec.api.metro.tokyo.lg.jp 에서 확인)
    "t131083d3100000016": "t131083d3100000016-262c81f06e226d2cb4f20ed5cbe817ff-0",   # 江東区 62곳
}
UA = {"User-Agent": "ClimaX/0.1 (heat-safety app; contact: ceosukjin@gmail.com)"}

DDL = """
CREATE TABLE IF NOT EXISTS cooling_shelter (
    id BIGSERIAL PRIMARY KEY,
    src TEXT NOT NULL,              -- 'tokyo:<패키지 name>'
    kind TEXT NOT NULL,             -- designated | coolshare
    name TEXT NOT NULL, addr TEXT, hours TEXT,
    lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL,
    license TEXT, source_url TEXT, loaded_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_cooling_shelter_ll ON cooling_shelter (lat, lon);
"""

LAT_KEYS = ("緯度", "latitude", "lat", "y座標", "y")
LON_KEYS = ("経度", "longitude", "lon", "lng", "x座標", "x")
NAME_KEYS = ("名称", "施設名", "施設名称", "スポット名", "name")
ADDR_KEYS = ("住所", "所在地", "所在地_連結表記", "address")
HOURS_KEYS = ("開設時間", "開放時間", "利用可能時間", "開館時間", "開所時間", "受入時間", "時間")


async def _get(client, url, **kw):
    try:
        r = await client.get(url, headers=UA, timeout=30.0, follow_redirects=True, **kw)
        return r if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001
        print(f"   ✗ {url[:90]} {type(e).__name__}")
        return None


def _decode(b: bytes) -> str:
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _col(head: list[str], keys) -> int | None:
    h = [re.sub(r"\s", "", x).lower() for x in head]
    for k in keys:                      # 완전 일치 먼저
        if k.lower() in h:
            return h.index(k.lower())
    for k in keys:                      # 그다음 포함 (단, 한 글자짜리 x/y 는 제외)
        if len(k) < 2:
            continue
        for i, x in enumerate(h):
            if k.lower() in x:
                return i
    return None


def _kind(title: str) -> str:
    return "coolshare" if "クールシェア" in title and "シェルター" not in title else "designated"


async def _packages(client):
    seen, out = set(), []
    for q in QUERIES:
        r = await _get(client, CKAN, params={"q": q, "rows": 200})
        if not r:
            print(f"  카탈로그 검색 실패: {q}"); continue
        for p in r.json()["result"]["results"]:
            if p["name"] in seen:
                continue
            t = p.get("title", "")
            if not any(k in t for k in ("クーリングシェルター", "暑熱避難", "クールシェア")):
                continue
            seen.add(p["name"])
            out.append(p)
    return out


def _records(js) -> list[list[str]]:
    """API JSON → CSV 와 같은 [머리행, 행…] 모양. 레코드 목록은 dict 들의 첫 배열로 찾는다."""
    def find(o):
        if isinstance(o, list) and o and isinstance(o[0], dict):
            return o
        if isinstance(o, dict):
            for k in ("hits", "records", "data", "items", "results"):
                if k in o and find(o[k]):
                    return find(o[k])
            for v in o.values():
                r = find(v)
                if r:
                    return r
        return None
    recs = find(js) or []
    if not recs:
        return []
    head = list(recs[0].keys())
    return [head] + [[("" if r.get(h) is None else str(r.get(h))) for h in head] for r in recs]


async def _api_rows(client, api_id):
    try:
        r = await client.post(TOKYO_API.format(api_id), json={}, headers=UA, timeout=30.0)
        if r.status_code != 200:
            print(f"   ✗ API HTTP {r.status_code}"); return None
        return _records(r.json())
    except Exception as e:  # noqa: BLE001
        print(f"   ✗ API {type(e).__name__}"); return None


def _parse(txt, rows=None):
    rows = rows if rows is not None else list(csv.reader(io.StringIO(txt)))
    if len(rows) < 2:
        return None, []
    head = rows[0]
    ci = dict(la=_col(head, LAT_KEYS), lo=_col(head, LON_KEYS), nm=_col(head, NAME_KEYS),
              ad=_col(head, ADDR_KEYS), hr=_col(head, HOURS_KEYS))
    out = []
    if ci["la"] is None or ci["lo"] is None or ci["nm"] is None:
        return (head, ci), []
    for r in rows[1:]:
        try:
            la, lo = float(r[ci["la"]]), float(r[ci["lo"]])
        except (ValueError, IndexError):
            continue
        if not (20 < la < 46 and 122 < lo < 154):
            continue
        nm = (r[ci["nm"]] if ci["nm"] < len(r) else "").strip()
        if not nm:
            continue
        g = lambda k: (r[ci[k]].strip() or None) if ci[k] is not None and ci[k] < len(r) else None
        out.append((nm, g("ad"), g("hr"), la, lo))
    return (head, ci), out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--load", action="store_true")
    a = ap.parse_args()
    import httpx
    async with httpx.AsyncClient() as client:
        pk = await _packages(client)
        print(f"\n도쿄도 카탈로그 — 셸터 관련 데이터셋 {len(pk)}개")
        plan = []
        for p in pk:
            org = (p.get("organization") or {}).get("title", "?")
            lic = p.get("license_title") or p.get("license_id") or "?"
            csvs = [r for r in p.get("resources", []) if (r.get("format") or "").upper() == "CSV"
                    or (r.get("url") or "").lower().endswith(".csv")]
            print(f"\n■ {org} · {p['title'][:40]}  [{lic}]  CSV {len(csvs)}개")
            srcs = [("csv", r["url"]) for r in csvs[:3]]
            if p["name"] in API_IDS:
                srcs.append(("api", API_IDS[p["name"]]))
            for how, url in srcs:
                if how == "csv":
                    rr = await _get(client, url)
                    if not rr:
                        print(f"   ✗ 못 받음 {url[:90]}"); continue
                    meta, rows = _parse(_decode(rr.content))
                else:
                    recs = await _api_rows(client, url)
                    if not recs:
                        continue
                    meta, rows = _parse(None, recs)
                    url = TOKYO_API.format(url)
                res = {"url": url}
                head, ci = meta if meta else ([], {})
                print(f"   {res['url'][:90]}")
                print(f"     [{how}] 열 {len(head)}: {', '.join(head[:14])[:200]}")
                print(f"     좌표열 {ci.get('la')},{ci.get('lo')} 이름열 {ci.get('nm')} → 좌표 있는 행 {len(rows)}")
                if rows:
                    print(f"     예: {rows[0]}")
                if rows:
                    plan.append((f"tokyo:{p['name']}", _kind(p["title"]), lic, res["url"], rows))
                    break                         # 같은 데이터셋의 다른 판(연도별)은 겹친다 — 첫 CSV 하나만
        tot = sum(len(x[4]) for x in plan)
        print(f"\n적재 가능: 데이터셋 {len(plan)}개, 시설 {tot}곳")
        if not a.load:
            return
        import asyncpg
        from app.config import get_settings
        conn = await asyncpg.connect(get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://"))
        await conn.execute(DDL)
        for src, kind, lic, url, rows in plan:
            async with conn.transaction():
                await conn.execute("DELETE FROM cooling_shelter WHERE src=$1", src)
                seen = set()
                vals = []
                for nm, ad, hr, la, lo in rows:
                    k = (nm, round(la, 5), round(lo, 5))
                    if k in seen:
                        continue
                    seen.add(k)
                    vals.append((src, kind, nm, ad, hr, la, lo, lic, url))
                await conn.executemany(
                    "INSERT INTO cooling_shelter (src,kind,name,addr,hours,lat,lon,license,source_url) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)", vals)
        n = await conn.fetch("SELECT kind, count(*) n FROM cooling_shelter GROUP BY 1 ORDER BY 1")
        print("✅ DB:", ", ".join(f"{r['kind']} {r['n']}" for r in n))
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
