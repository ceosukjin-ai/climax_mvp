#!/usr/bin/env python3
"""건축물대장 표제부 → bldg_poly 태그 갱신 (구조명·주용도·층수·높이)  2026-09-13.

왜 이 스크립트인가:
  build_kr_bldg_tiles.py 는 GIS건물 geojsonl 을 원천으로 타일을 새로 만든다. 그런데 그 원천 파일은
  서버에 남아 있지 않고, 건물은 이미 `bldg_poly` 에 들어가 있다(태그에 pnu 보존).
  → 폴리곤을 다시 만들 이유가 없다. **pnu 로 조인해 태그만 덧씌운다.**

무엇이 바뀌나:
  building=yes → 주용도 기반 값(house/apartments/retail/...)   ← wall_material 의 1차 신호
  building:material 신규                                       ← wall_material 최우선 신호
  kr:purps / kr:strct_nm 신규 (판정 근거 보존)
  building:levels / height — **비어 있을 때만** 채운다(기존 값 우선).

안전장치:
  · 인덱스는 CONCURRENTLY — ACCESS EXCLUSIVE 대기줄을 만들지 않는다(9/12 락 사고 재발 방지).
  · lock_timeout 5s — 못 잡으면 그 구만 건너뛰고 계속한다.
  · 시군구(5자리) 단위로 트랜잭션을 쪼갠다. 중단해도 거기까지는 반영돼 있다.
  · --dry 는 아무것도 쓰지 않고 매칭률만 센다.

실행 [서버]:
  docker cp scripts/update_kr_bldg_tags.py climax-api:/tmp/
  docker cp ~/data/mart_djy_busan.txt climax-api:/tmp/     # 또는 -v 마운트 경로
  docker exec climax-api python3 /tmp/update_kr_bldg_tags.py /tmp/mart_djy_busan.txt --dry
  docker exec climax-api python3 /tmp/update_kr_bldg_tags.py /tmp/mart_djy_busan.txt
"""
from __future__ import annotations
import argparse, asyncio, collections, sys, time
sys.path.insert(0, "/app")

COL = dict(sgg=8, bjd=9, gb=10, bun=11, ji=12, dong=22, main=23,
           strct=31, strct_nm=32, purps=34, height=42, floors=43)
_GB_MAP = {"0": "1", "1": "2"}

# 구조명(텍스트) → 외벽 재질. 코드가 아니라 이름으로 판정한다 —
# 부산 실파일이 `51 = 일반목구조` 였고, 코드 체계는 개정으로 바뀌지만 이름은 안 바뀐다.
# 순서가 규칙: '철골철근콘크리트' 는 외벽이 콘크리트다 → 콘크리트를 먼저 본다.
#              '벽돌' 안에 '돌' 이 있다 → brick 을 stone 보다 먼저 본다.
STRCT_NAME_RULES = [
    ("concrete", ("콘크리트", "라멘", "프리캐스트", "피씨", "P.C", "PC조")),
    ("metal",    ("철골", "철파이프", "강구조", "경량철", "철재", "샌드위치판넬", "판넬")),
    ("wood",     ("목구조", "목조", "통나무", "목재")),
    ("brick",    ("벽돌", "블록", "블럭", "조적", "흙", "토담", "황토")),
    ("stone",    ("석조", "석구조", "자연석", "화강", "대리석")),
    ("glass",    ("유리",)),
]
PURPS_BUILDING = {
    "01": "house", "02": "apartments", "03": "retail", "04": "retail",
    "05": "civic", "06": "church", "07": "retail", "08": "transportation",
    "09": "hospital", "10": "school", "11": "civic", "12": "civic",
    "13": "sports_hall", "14": "office", "15": "hotel", "16": "commercial",
    "17": "industrial", "18": "warehouse", "19": "industrial", "20": "garage",
    "21": "farm_auxiliary", "22": "industrial", "23": "civic", "24": "industrial",
    "25": "industrial", "26": "civic", "27": "commercial", "28": "civic", "29": "hut",
}


def strct_material(name: str):
    t = (name or "").replace(" ", "")
    if not t:
        return None
    for mat, keys in STRCT_NAME_RULES:
        if any(k in t for k in keys):
            return mat
    return None


def _i(s):
    try:
        return int(float(str(s).strip() or 0))
    except ValueError:
        return 0


def _f(s):
    try:
        return float(str(s).strip() or 0)
    except ValueError:
        return 0.0


def load(path, sido):
    """pnu → (층수, 높이, 구조명, 주용도). 한 필지 여러 동이면 주건축물 최대층 > 전체 최대층."""
    best, n, bad = {}, 0, 0
    t0 = time.time()
    # 건축HUB 표제부는 UTF-8 이다. (2026-09-13 부산 실파일로 확인 — cp949 로 열면 첫 줄에서 깨진다.)
    enc = "utf-8"
    try:
        open(path, encoding="utf-8", errors="strict").readline()
    except UnicodeDecodeError:
        enc = "cp949"
    print(f"인코딩 {enc}")
    with open(path, encoding=enc, errors="replace") as f:
        for line in f:
            c = line.split("|")
            if len(c) <= COL["floors"]:
                bad += 1
                continue
            sgg = c[COL["sgg"]].strip()
            if sido and not sgg.startswith(sido):
                continue
            n += 1
            gb = _GB_MAP.get(c[COL["gb"]].strip(), c[COL["gb"]].strip())
            pnu = f"{sgg}{c[COL['bjd']].strip()}{gb}{c[COL['bun']].strip().zfill(4)}{c[COL['ji']].strip().zfill(4)}"
            if len(pnu) != 19:
                bad += 1
                continue
            fl, ht = _i(c[COL["floors"]]), _f(c[COL["height"]])
            is_main = c[COL["main"]].strip() == "0"
            rec = (fl, ht, c[COL["strct_nm"]].strip(), c[COL["purps"]].strip(), is_main)
            old = best.get(pnu)
            # 순위: 주건축물 우선 → 층수 큰 것 (보수적: 그늘 과소추정 방지)
            if old is None or (rec[4], rec[0]) > (old[4], old[0]):
                best[pnu] = rec
    print(f"표제부 {n:,}행 (건너뜀 {bad:,}) → 필지 {len(best):,}   {time.time()-t0:.0f}s")
    mats = collections.Counter(strct_material(v[2]) for v in best.values())
    print("  구조명→재질:", {str(k): v for k, v in mats.most_common()})
    return best


DDL = """
CREATE TABLE IF NOT EXISTS kr_bldg_attr (
  pnu TEXT PRIMARY KEY, floors INT, height REAL,
  strct_nm TEXT, purps TEXT, mat TEXT, bval TEXT);
"""
# 조인 열쇠: 건물관리번호(bd_mgt_sn, 25자리)의 **앞 19자리가 곧 PNU** 다.
#   2638010600 1 1551 0033 | 008908   ← 법정동10 + 대지구분1 + 본번4 + 부번4 + 일련번호6
# `pnu` 태그는 이 DB에 3동뿐이다(다른 로더의 흔적). 실제 19,032,625동은 전부 bd_mgt_sn 을 쓴다.
#
# 층수는 이미 `gro_flo_co` 로 들어가 있지만 이름이 OSM 규약과 달라 엔진이 못 읽는다 →
# `building:levels` 로 옮겨 준다(기존 값이 있으면 건드리지 않는다).
KEY = "left(b.tags->>'bd_mgt_sn', 19)"
UPD = f"""
UPDATE bldg_poly b
   SET tags = jsonb_strip_nulls(
         b.tags
         || jsonb_build_object('kr:strct_nm', nullif(a.strct_nm, ''),
                               'kr:purps',    nullif(a.purps, ''),
                               'building:material', a.mat,
                               'building', COALESCE(a.bval, b.tags->>'building'))
         || CASE WHEN (b.tags ? 'building:levels')
                       OR COALESCE(NULLIF(regexp_replace(COALESCE(b.tags->>'gro_flo_co',''),
                                                         '[^0-9]', '', 'g'), '')::int,
                                   a.floors) <= 0
                 THEN '{{}}'::jsonb
                 ELSE jsonb_build_object('building:levels',
                        COALESCE(NULLIF(regexp_replace(COALESCE(b.tags->>'gro_flo_co',''),
                                                       '[^0-9]', '', 'g'), '')::int,
                                 a.floors)::text) END
         || CASE WHEN (b.tags ? 'height') OR a.height <= 0 THEN '{{}}'::jsonb
                 ELSE jsonb_build_object('height', to_char(a.height, 'FM999990.0')) END)
  FROM kr_bldg_attr a
 WHERE {KEY} = a.pnu
   AND left(a.pnu, 5) = $1
"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pyojebu")
    ap.add_argument("--sido", default="26", help="시군구코드 앞 2자리. 부산 26")
    ap.add_argument("--dry", action="store_true", help="쓰지 않고 매칭률만 센다")
    a = ap.parse_args()

    best = load(a.pyojebu, a.sido)
    if not best:
        print("필지 0 — 파일이나 --sido 확인."); return

    from app.services.skyline import _get_pool
    pool = await _get_pool()

    async with pool.acquire() as c:
        await c.execute("SET lock_timeout = '5s'")
        await c.execute("SET statement_timeout = '0'")
        print("건물관리번호 인덱스 확인/생성 (CONCURRENTLY — 운영 차단 없음, 수 분 소요)…", flush=True)
        await c.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_bldg_poly_bdmgt19 "
                        "ON bldg_poly ((left(tags->>'bd_mgt_sn', 19)))")
        print("  인덱스 준비 완료", flush=True)

        await c.execute(DDL)
        await c.execute("TRUNCATE kr_bldg_attr")
        rows = [(p, v[0], v[1], v[2], v[3], strct_material(v[2]),
                 PURPS_BUILDING.get(v[3][:2]) if v[3] else None)
                for p, v in best.items()]
        await c.copy_records_to_table("kr_bldg_attr", records=rows,
                                      columns=["pnu", "floors", "height", "strct_nm",
                                               "purps", "mat", "bval"])
        print(f"스테이징 {len(rows):,}행 적재", flush=True)

        hit = await c.fetchval(
            f"SELECT count(*) FROM bldg_poly b JOIN kr_bldg_attr a ON {KEY} = a.pnu")
        print(f"건물관리번호→PNU 매칭 건물 {hit:,}동")
        if a.dry:
            print("--dry — 쓰지 않고 종료."); return
        if hit == 0:
            print("!! 매칭 0 — 대지구분(_GB_MAP)이나 열 위치를 의심해야 한다. 중단."); return

        sggs = [r[0] for r in await c.fetch(
            "SELECT DISTINCT left(pnu,5) s FROM kr_bldg_attr ORDER BY s")]
        done = 0
        for s in sggs:
            t = time.time()
            try:
                r = await c.execute(UPD, s)
            except Exception as e:                      # 락 못 잡으면 그 구만 건너뛴다
                print(f"  {s}  건너뜀 ({type(e).__name__}: {e})", flush=True); continue
            n = int(r.split()[-1])
            done += n
            print(f"  {s}  {n:,}동 갱신  {time.time()-t:.0f}s", flush=True)
        print(f"\n갱신 합계 {done:,}동")

        q = await c.fetchrow("""
            SELECT count(*) tot,
                   count(*) FILTER (WHERE tags ? 'building:material') mat,
                   count(*) FILTER (WHERE tags->>'building' <> 'yes') typed,
                   count(*) FILTER (WHERE tags ? 'building:levels')   lv
              FROM bldg_poly b JOIN kr_bldg_attr a ON {KEY} = a.pnu
        """.replace("{KEY}", KEY))
        t_, m_, y_, l_ = q["tot"], q["mat"], q["typed"], q["lv"]
        print(f"검증(표제부 매칭분): 건물 {t_:,}  용도반영 {y_:,}({100*y_/max(t_,1):.1f}%)  "
              f"구조→외벽재질 {m_:,}({100*m_/max(t_,1):.1f}%)  층수 {l_:,}({100*l_/max(t_,1):.1f}%)")
        for x in await c.fetch("SELECT tags FROM bldg_poly WHERE tags ? 'building:material' LIMIT 3"):
            print("  샘플:", x[0])


if __name__ == "__main__":
    asyncio.run(main())
