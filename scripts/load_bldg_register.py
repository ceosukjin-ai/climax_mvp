#!/usr/bin/env python3
"""건축물대장 표제부(전국 mart_djy_03.txt) → PostGIS `bldg_register` (2026-09-11).

부산은 JSON(_pyojebu_floors_kr.json 10.8MB)로 충분했지만 전국 700만 동은 API 메모리를 1GB 넘게 먹는다 →
테이블로 넣고 geo._fill_floors_from_register_many 가 PNU 배열 한 쿼리로 조회한다.
주건축물(주부속코드 0)·지상층수>0 만. PNU19 = 시군구5+법정동5+대지구분1(0대지/1산/2블록)+번4+지4 — V-World bd_mgt_sn 앞 19자리와 동일 체계.

서버:
  docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml run --rm \
    -v $HOME/climax_mvp:/repo -v $HOME/data:/data api python3 /repo/scripts/load_bldg_register.py /data/mart_djy_03.txt [--replace]
전국 약 5~8분(COPY). 이후 API 재시작 불필요(조회는 요청 시).
"""
from __future__ import annotations
import argparse, asyncio, os, sys, time
sys.path.insert(0, "/app"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_kr_bldg_tiles import COL, _i, _f  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS bldg_register (
    pnu     TEXT NOT NULL,          -- CHAR(19) 이면 text[] 비교가 인덱스를 못 탄다(2026-09-11 5~14s/쿼리 사고)
    dong    TEXT,
    floors  SMALLINT NOT NULL,
    height  REAL,
    struct  TEXT,
    usage   TEXT
);
CREATE INDEX IF NOT EXISTS ix_bldg_register_pnu ON bldg_register (pnu);
"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pyojebu"); ap.add_argument("--replace", action="store_true"); ap.add_argument("--sido", default=None)
    a = ap.parse_args()
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 접속 실패"); return
    async with pool.acquire() as c:
        for stmt in filter(None, (x.strip() for x in DDL.split(";"))):
            await c.execute(stmt)
        if a.replace:
            await c.execute("TRUNCATE bldg_register")
    enc = "utf-8"
    try:
        open(a.pyojebu, "rb").read(4096).decode("utf-8")
    except UnicodeDecodeError:
        enc = "cp949"
    t0 = time.time(); n = 0; buf = []

    async def flush():
        nonlocal buf
        if buf:
            async with pool.acquire() as c:
                await c.copy_records_to_table("bldg_register", records=buf,
                                              columns=["pnu", "dong", "floors", "height", "struct", "usage"])
            buf = []

    with open(a.pyojebu, encoding=enc, errors="replace") as f:
        for line in f:
            c = line.rstrip("\r\n").split("|")
            if len(c) < 45:
                continue
            sgg = c[COL["sgg"]].strip()
            if a.sido and not sgg.startswith(a.sido):
                continue
            if c[COL["main"]].strip() != "0":
                continue
            fl = _i(c[COL["floors"]])
            if fl <= 0:
                continue
            pnu = f"{sgg}{c[COL['bjd']].strip()}{c[COL['gb']].strip()}{c[COL['bun']].strip().zfill(4)}{c[COL['ji']].strip().zfill(4)}"
            if len(pnu) != 19:
                continue
            h = _f(c[COL["height"]])
            buf.append((pnu, c[COL["dong"]].strip() or None, fl, h if h > 0 else None,
                        c[32].strip() or None, c[35].strip() or None))
            n += 1
            if len(buf) >= 20000:
                await flush()
                if n % 500000 == 0:
                    print(f"  {n:,} ({time.time()-t0:.0f}s)", flush=True)
    await flush()
    async with pool.acquire() as c:
        tot = await c.fetchval("SELECT COUNT(*) FROM bldg_register")
        await c.execute("ANALYZE bldg_register")
    print(f"적재 {n:,}동 → bldg_register 총 {tot:,}행  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
