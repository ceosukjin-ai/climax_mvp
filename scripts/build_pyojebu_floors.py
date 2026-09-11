#!/usr/bin/env python3
"""건축물대장 표제부(mart_djy_03.txt) → PNU별 층수·높이 표 (2026-09-11).

geo._fill_floors_from_register 가 V-World 건물 폴리곤(bd_mgt_sn 앞 19자리 = 법정동10+대지구분1+본번4+부번4)의
층수 결측을 채우는 데 쓴다. 대지구분은 표제부·도로명주소 모두 0=대지 1=san 2=블록이라 변환 없음.
출력: {pnu19: [[동명, 지상층수, 높이m], ...]}  (주건축물만, 층수>0)

  python3 scripts/build_pyojebu_floors.py --pyojebu ~/data/mart_djy_03.txt --sido 26 \
      --out ~/climax_mvp/backend/data/buildings/_pyojebu_floors_kr.json
전국이면 --sido 생략(약 700만 동 → 수백 MB, 메모리 주의; 시도별로 나눠 --merge 로 누적 가능).
"""
import argparse, json, os, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_kr_bldg_tiles import COL, _i, _f  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pyojebu", required=True); ap.add_argument("--sido", default=None)
    ap.add_argument("--out", required=True); ap.add_argument("--merge", action="store_true", help="기존 out 파일에 누적")
    a = ap.parse_args()
    reg: dict[str, list] = defaultdict(list)
    if a.merge and os.path.isfile(a.out):
        reg.update(json.load(open(a.out, encoding="utf-8")))
    enc = "utf-8"
    try:
        open(a.pyojebu, "rb").read(4096).decode("utf-8")
    except UnicodeDecodeError:
        enc = "cp949"
    n = 0; t0 = time.time()
    with open(a.pyojebu, encoding=enc, errors="replace") as f:
        for line in f:
            c = line.rstrip("\r\n").split("|")
            if len(c) < 45:
                continue
            sgg = c[COL["sgg"]].strip()
            if a.sido and not sgg.startswith(a.sido):
                continue
            if c[COL["main"]].strip() != "0":          # 부속건축물(창고·경비실 등)은 제외 — 과차폐 방지
                continue
            fl = _i(c[COL["floors"]])
            if fl <= 0:
                continue
            pnu = f"{sgg}{c[COL['bjd']].strip()}{c[COL['gb']].strip()}{c[COL['bun']].strip().zfill(4)}{c[COL['ji']].strip().zfill(4)}"
            if len(pnu) != 19:
                continue
            reg[pnu].append([c[COL["dong"]].strip(), fl, round(_f(c[COL["height"]]), 1)])
            n += 1
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, separators=(",", ":"))
    print(f"표제부 주건축물 {n:,}동 → 필지 {len(reg):,} → {a.out} ({os.path.getsize(a.out)/1e6:.1f}MB, {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
