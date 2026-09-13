#!/usr/bin/env python3
"""한국 가로수 공공데이터 → PostGIS `tree_point` (2026-09-12).

왜: 한국 OSM 에는 가로수가 거의 없다(일본은 115,200그루, 한국은 0).
엔진의 tree_shade_factor 는 tree_point 만 읽으므로, 데이터가 없으면 가로수 그늘이 0 이다.

공공데이터는 개별 나무가 아니라 **구간(선) + 그루수** 다.
  · 전국가로수길정보표준데이터  : 시작위도/경도, 종료위도/경도, 가로수수량, 가로수길길이, 가로수종류
  · 부산광역시 구군 가로수현황  : 위도/경도(구간 대표점 1개), 식재거리, 총합계, 수종별 그루수
→ 구간을 따라 그루수만큼 점을 찍고, 좌우로 번갈아 배치한다.
  개별 나무의 실제 위치는 모르지만, "이 길에 가로수가 이 밀도로 있다"는 22m 격자에서 필요한 전부다.

수고(樹高)는 두 데이터 모두 없다 → 수종별 성목 기준 표준값을 쓴다(아래 SPECIES).
이 표는 추정치이며, 감도분석 대상이다. src 에 'kr-gov' 를 박아 두어 나중에 분리 평가할 수 있게 한다.

사용:
  # 표준데이터(전국) CSV
  python3 /repo/scripts/load_kr_trees.py std /data/전국가로수길정보표준데이터.csv
  # 부산 구군 현황 CSV (구간 대표점 1개 + 식재거리)
  python3 /repo/scripts/load_kr_trees.py busan /data/busan_garosu.csv
  # 지우고 다시
  python3 /repo/scripts/load_kr_trees.py std <csv> --purge
"""
from __future__ import annotations
import argparse, asyncio, csv, math, sys, time
sys.path.insert(0, "/app")

SRC = "kr-gov"
DDL = """
CREATE TABLE IF NOT EXISTS tree_point (
    id   TEXT PRIMARY KEY,
    geom geometry(Point, 4326) NOT NULL,
    h    REAL,
    r    REAL,
    src  TEXT
);
CREATE INDEX IF NOT EXISTS ix_tree_point_geom ON tree_point USING GIST (geom);
ALTER TABLE tree_point ADD COLUMN IF NOT EXISTS sp  TEXT;
ALTER TABLE tree_point ADD COLUMN IF NOT EXISTS evg BOOLEAN;
"""
SQL = ("INSERT INTO tree_point (id, geom, h, r, src, sp, evg) "
       "VALUES ($1, ST_SetSRID(ST_MakePoint($2,$3),4326),$4,$5,$6,$7,$8) "
       "ON CONFLICT (id) DO UPDATE SET geom=EXCLUDED.geom, h=EXCLUDED.h, r=EXCLUDED.r, "
       "src=EXCLUDED.src, sp=EXCLUDED.sp, evg=EXCLUDED.evg")

# 수종 → (수고 m, 수관반경 m, 상록여부). 국내 가로수 성목 기준 표준값 — 추정치다.
# 상록/낙엽 구분이 핵심이다. 낙엽수는 겨울에 볕을 통과시켜 이롭고, 상록수는 겨울 볕까지 막는다.
# 부산은 남부라 상록활엽수(후박·녹나무·먼나무) 가로수가 많다 — 서울과 수종 구성이 다르다.
# 메타세쿼이아·낙우송은 침엽수지만 **낙엽**이다. 주의.
SPECIES = {
    # ── 낙엽 ──
    "양버즘": (15.0, 5.0, False), "버즘": (15.0, 5.0, False), "플라타너스": (15.0, 5.0, False),
    "메타세": (18.0, 3.5, False), "메타쉐": (18.0, 3.5, False), "낙우송": (17.0, 3.5, False),
    "느티": (12.0, 5.5, False), "은행": (11.0, 3.5, False),
    "왕벚": (8.0, 4.5, False), "벚": (8.0, 4.5, False),
    "이팝": (8.0, 3.5, False), "칠엽": (10.0, 4.0, False),
    "튤립": (14.0, 4.0, False), "백합": (14.0, 4.0, False),
    "벽오동": (10.0, 3.5, False),
    "회화": (12.0, 5.0, False), "느릅": (11.0, 4.5, False), "팽나무": (12.0, 5.0, False),
    "단풍": (7.0, 3.5, False), "배롱": (4.0, 2.0, False),
    "무궁화": (3.0, 1.5, False), "산딸": (6.0, 3.0, False), "목련": (7.0, 3.5, False),
    "감나무": (6.0, 3.0, False), "대왕참나무": (14.0, 5.0, False), "참나무": (13.0, 5.0, False),
    "상수리": (13.0, 5.0, False), "층층": (10.0, 4.0, False), "모과": (6.0, 3.0, False),
    "자귀": (7.0, 4.0, False), "산수유": (5.0, 2.5, False), "복자기": (8.0, 3.5, False),
    "마가목": (6.0, 2.5, False), "쉬나무": (9.0, 3.5, False), "물푸레": (11.0, 4.0, False),
    # ── 상록 ── (겨울에도 볕을 막는다)
    "후박": (9.0, 4.0, True), "먼나무": (7.0, 3.0, True), "가시나무": (9.0, 4.0, True),
    "종가시": (9.0, 4.0, True), "녹나무": (10.0, 5.0, True), "아왜": (8.0, 3.5, True),
    "동백": (5.0, 2.5, True), "사철": (4.0, 2.0, True), "광나무": (5.0, 2.5, True),
    "소나무": (9.0, 3.5, True), "곰솔": (9.0, 3.5, True), "해송": (9.0, 3.5, True),
    "잣나무": (12.0, 3.0, True), "전나무": (13.0, 3.0, True), "구상": (9.0, 3.0, True),
    "개잎갈": (14.0, 4.0, True), "히말라야": (14.0, 4.0, True), "향나무": (7.0, 2.5, True),
    "편백": (12.0, 2.5, True), "측백": (8.0, 2.0, True), "가이즈카": (7.0, 2.5, True),
}
DEFAULT_HR = (8.0, 3.0, False)   # geo.py 의 TREE_H_M / TREE_CROWN_R_M, 미상은 낙엽 가정
OFFSET_M = 5.0              # 도로 중심선에서 가로수까지 — 편도 2차로 기준 근사. 한계로 기록.
MIN_SPACING_M = 3.0         # 데이터 오류 방어(그루수가 과대하면 간격이 0 이 된다)
MAX_SPACING_M = 25.0
DEFAULT_SPACING_M = 7.0


def hr_for(species: str):
    """수종명 → (수고, 수관반경, 상록여부). 부분일치, 표기 흔들림 흡수."""
    s = (species or "").replace(" ", "")
    for k, v in SPECIES.items():
        if k in s:
            return v
    return DEFAULT_HR


def fnum(v):
    """'1,234' '12.5m' '' → float | None."""
    if v is None:
        return None
    t = str(v).strip().replace(",", "")
    if not t:
        return None
    out = []
    for ch in t:
        if ch.isdigit() or ch in ".-":
            out.append(ch)
        elif out:
            break
    try:
        return float("".join(out))
    except ValueError:
        return None


def open_csv(path):
    """공공데이터포털 CSV 는 CP949 가 대부분이고 UTF-8 도 섞인다. 헤더에 한글이 살아나는 쪽을 쓴다."""
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            with open(path, encoding=enc, newline="") as f:
                head = f.readline()
        except (UnicodeDecodeError, LookupError):
            continue
        if any("가" <= ch <= "힣" for ch in head):
            print(f"인코딩 {enc}")
            return open(path, encoding=enc, newline="")
    print("인코딩 판별 실패 — utf-8 강행")
    return open(path, encoding="utf-8", errors="replace", newline="")


def pick(row: dict, *keys):
    """컬럼명이 기관마다 조금씩 달라서 부분일치로 찾는다."""
    for k in keys:
        for col, val in row.items():
            if col and k in col.replace(" ", ""):
                return val
    return None


def m_per_deg(lat: float) -> tuple[float, float]:
    return 111320.0, 111320.0 * math.cos(math.radians(lat))


def lay_points(la1, lo1, la2, lo2, n, h, r, prefix, sp="", evg=False):
    """(la1,lo1)~(la2,lo2) 선분을 따라 n 그루를 좌우 번갈아 배치."""
    mlat, mlon = m_per_deg((la1 + la2) / 2.0)
    dx = (lo2 - lo1) * mlon
    dy = (la2 - la1) * mlat
    L = math.hypot(dx, dy)
    if L < 1.0 or n < 1:
        return []
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux                      # 법선(좌측)
    out = []
    for i in range(n):
        t = (i + 0.5) / n * L
        side = 1.0 if i % 2 == 0 else -1.0
        px = ux * t + nx * OFFSET_M * side
        py = uy * t + ny * OFFSET_M * side
        out.append((f"{prefix}:{i}", lo1 + px / mlon, la1 + py / mlat, h, r, sp, evg))
    return out


# 한국 육지 대략 경계. 이 밖의 좌표는 오기(誤記)로 본다.
KR_LAT = (33.0, 38.7)
KR_LON = (124.5, 132.0)
MAX_SEG_M = 20000.0     # 가로수길 한 구간이 20km 를 넘을 수는 없다
MAX_TREES_PER_ROW = 5000


def in_kr(la, lo):
    return la is not None and lo is not None and \
        KR_LAT[0] < la < KR_LAT[1] and KR_LON[0] < lo < KR_LON[1]


def from_std(path):
    """전국가로수길정보표준데이터 — 시작/종료 좌표가 둘 다 있다.

    공공데이터는 좌표 오기가 섞인다(종료좌표 누락, 위경도 뒤바뀜, 0). 걸러내지 않으면
    구간이 수백 km 로 늘어나 태평양에 나무를 심는다 → 양끝을 모두 검사하고 구간장을 제한한다.
    """
    rows = []
    rej = {"좌표없음": 0, "국외": 0, "구간과대": 0, "수량없음": 0, "길이0": 0}
    kept = 0
    with open_csv(path) as f:
        for i, row in enumerate(csv.DictReader(f)):
            la1 = fnum(pick(row, "시작위도")); lo1 = fnum(pick(row, "시작경도"))
            la2 = fnum(pick(row, "종료위도")); lo2 = fnum(pick(row, "종료경도"))
            if None in (la1, lo1, la2, lo2):
                rej["좌표없음"] += 1; continue
            if not (in_kr(la1, lo1) and in_kr(la2, lo2)):
                rej["국외"] += 1; continue
            mlat, mlon = m_per_deg(la1)
            L = math.hypot((lo2 - lo1) * mlon, (la2 - la1) * mlat)
            if L < 5.0:
                rej["길이0"] += 1; continue
            if L > MAX_SEG_M:
                rej["구간과대"] += 1; continue
            n = fnum(pick(row, "가로수수량", "수량", "그루"))
            sp = pick(row, "가로수종류", "수종") or ""
            n = int(n) if n and n >= 1 else int(L / DEFAULT_SPACING_M)
            if n < 1:
                rej["수량없음"] += 1; continue
            sp_m = L / n
            if sp_m < MIN_SPACING_M:
                n = max(1, int(L / MIN_SPACING_M))
            elif sp_m > MAX_SPACING_M:
                n = max(1, int(L / MAX_SPACING_M))
            n = min(n, MAX_TREES_PER_ROW)
            h, r, evg = hr_for(sp)
            rows += lay_points(la1, lo1, la2, lo2, n, h, r, f"kr-std:{i}",
                               (sp or "").strip()[:60], evg)
            kept += 1
    print(f"구간 채택 {kept:,}  제외 " + "  ".join(f"{k} {v:,}" for k, v in rej.items() if v))
    return rows


def from_busan(path):
    """부산 구군 가로수현황 — 대표점 1개 + 식재거리. 구간 방향을 모르므로 동서로 편다.

    방향을 모르는 것이 이 데이터의 한계다. 22m 격자에서 '이 일대에 가로수가 있다'는
    유지되지만, 특정 좌표의 그늘 판정에는 오차가 생긴다 → src 로 분리 평가할 것.
    """
    rows = []
    with open_csv(path) as f:
        for i, row in enumerate(csv.DictReader(f)):
            la = fnum(pick(row, "위도")); lo = fnum(pick(row, "경도"))
            if la is None or lo is None or not (34.5 < la < 35.6 and 128.5 < lo < 129.5):
                continue
            total = fnum(pick(row, "총합계", "합계", "total"))
            dist = fnum(pick(row, "식재거리", "거리"))
            if dist and dist < 30:                 # km 로 들어온다
                dist *= 1000.0
            # 가장 많은 수종을 대표 수종으로
            best_sp, best_n = "", 0.0
            for col, val in row.items():
                if not col or col.replace(" ", "") in ("총합계", "합계", "식재거리", "위도", "경도"):
                    continue
                v = fnum(val)
                if v and v > best_n and any(k in col for k in ("나무", "수", "동", "솔", "벚")):
                    best_sp, best_n = col, v
            n = int(total) if total and total >= 1 else 0
            if n < 1:
                continue
            L = dist if dist and dist > 10 else n * DEFAULT_SPACING_M
            L = min(L, 3000.0)
            mlat, mlon = m_per_deg(la)
            half = L / 2.0
            la1, lo1 = la, lo - half / mlon
            la2, lo2 = la, lo + half / mlon
            sp_m = L / n
            if sp_m < MIN_SPACING_M:
                n = max(1, int(L / MIN_SPACING_M))
            h, r, evg = hr_for(best_sp)
            rows += lay_points(la1, lo1, la2, lo2, n, h, r, f"kr-bs:{i}",
                               (best_sp or "").strip()[:60], evg)
    return rows


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["std", "busan"])
    ap.add_argument("csv")
    ap.add_argument("--purge", action="store_true", help="기존 kr-gov 행을 지우고 다시 적재")
    ap.add_argument("--dry", action="store_true", help="DB 에 쓰지 않고 집계만")
    ap.add_argument("--batch", type=int, default=5000)
    a = ap.parse_args()

    t0 = time.time()
    pts = from_std(a.csv) if a.mode == "std" else from_busan(a.csv)
    print(f"전개 {len(pts):,}그루  ({time.time()-t0:.1f}s)")
    if not pts:
        print("0그루 — 컬럼명이 예상과 다를 수 있다. 아래 헤더 확인:")
        with open_csv(a.csv) as f:
            import csv as _c
            print("  " + " | ".join((next(_c.reader(f), []) or [])[:30]))
        return
    lats = [p[2] for p in pts]; lons = [p[1] for p in pts]
    print(f"범위 lat {min(lats):.4f}~{max(lats):.4f}  lon {min(lons):.4f}~{max(lons):.4f}")
    hs = sorted(p[3] for p in pts)
    print(f"수고 중앙값 {hs[len(hs)//2]:.1f}m")
    # 부산(35.0~35.4N, 128.8~129.3E) 안에 몇 그루인지 — 실측 80지점 검증용
    bs = sum(1 for p in pts if 35.0 < p[2] < 35.4 and 128.8 < p[1] < 129.3)
    print(f"부산권 {bs:,}그루")
    ev = sum(1 for p in pts if p[6])
    print(f"상록 {ev:,}그루 ({ev/len(pts)*100:.0f}%)  낙엽 {len(pts)-ev:,}그루")
    from collections import Counter
    top = Counter(p[5] or "(미상)" for p in pts).most_common(12)
    print("수종 상위:", ", ".join(f"{k} {v:,}" for k, v in top))
    unk = sum(v for k, v in Counter(p[5] or "" for p in pts).items() if hr_for(k) == DEFAULT_HR)
    print(f"수종표 미매칭 {unk:,}그루 ({unk/len(pts)*100:.0f}%) — 기본값 8m/낙엽 적용")
    if a.dry:
        print("--dry: DB 미기록")
        return

    from app.services.skyline import _get_pool
    pool = await _get_pool()
    async with pool.acquire() as c:
        await c.execute(DDL)
        if a.purge:
            d = await c.execute("DELETE FROM tree_point WHERE src = $1", SRC)
            print(f"기존 {SRC} 삭제: {d}")
        for i in range(0, len(pts), a.batch):
            await c.executemany(SQL, [(p[0], p[1], p[2], p[3], p[4], SRC, p[5], p[6])
                                      for p in pts[i:i + a.batch]])
            print(f"  {min(i+a.batch, len(pts)):,}/{len(pts):,}", flush=True)
        tot = await c.fetchval("SELECT COUNT(*) FROM tree_point")
        bysrc = await c.fetch("SELECT src, COUNT(*) n FROM tree_point GROUP BY src ORDER BY n DESC")
        await c.execute("ANALYZE tree_point")
    print(f"완료 {time.time()-t0:.0f}s — tree_point 총 {tot:,}행")
    for r in bysrc:
        print(f"   {r['src']}: {r['n']:,}")


if __name__ == "__main__":
    asyncio.run(main())
