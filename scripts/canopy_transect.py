#!/usr/bin/env python3
"""가로수길 수직 단면 — 위성이 가로수를 보는가, A 의 offset 은 맞는가 (2026-09-12).

배경:
  A(가로수길 표준데이터) 좌표의 66% 에서 위성 수관고가 0 이었다. 두 가지 설명이 가능하다.
    (가) 위성이 도심 가로수를 못 본다 (Meta 모델은 산림용)
    (나) A 적재 시 중심선에서 ±5m 에 나무를 놓았는데, 한국 도로는 편도 2차로만 돼도
         중심선에서 5m 는 아직 차도다 → 나무를 아스팔트 위에 심었다
  점 대 점 비교로는 둘을 구분할 수 없다.

방법:
  표준데이터는 구간의 시작·종료 좌표를 준다 → **도로 방향을 안다.**
  구간 중점에서 도로에 직각으로 -25m ~ +25m 를 1m 간격으로 훑으며 수관고를 읽는다.
  · 가로수가 있고 위성이 본다면 → 중심선(0m)은 낮고 양쪽 ±(도로반폭+2m)에 봉우리 두 개
  · 어디서도 0 이면 → (가)
  · 봉우리가 ±5m 가 아닌 다른 거리에 있으면 → (나), 그 거리가 옳은 OFFSET_M

대조군: 같은 방식으로 무작위 방향의 가짜 구간을 훑는다. 도심 배경 수준을 알아야 봉우리가 봉우리다.

  docker exec climax-api python3 /tmp/canopy_transect.py /tmp/garosu_std.csv
"""
from __future__ import annotations
import csv, math, os, random, sys
import numpy as np

# 2026-09-15 — 원본 해상도 재시험용으로 경로를 환경변수로 뺀다.
#
# 왜 다시 하나:
#   9/12 에 "위성은 도심 가로수를 못 본다"고 결론 냈다. 그런데 그 시험을 **9 m 화소**
#   (`-tr 0.0001`)로 했다. 가로수 한 줄의 폭이 5 m 다. **못 보는 게 당연한 조건이었다.**
#   원본(Meta/WRI v2)은 화소 1.19 m 다. 자료의 한계인지 우리 리샘플링의 한계인지
#   구분하지 않고 결론을 내렸다. 그 결론 위에 지금 설계가 다 서 있다 — 다시 재야 한다.
#
#   뒤집히면: 공공데이터 없는 나라에서도 가로수 그늘이 된다(무영상 특허 범위).
#   안 뒤집히면: 9/12 결론이 제대로 확정되고 논문에 자신 있게 쓴다.
#
# 판정 기준 (미리 정한다):
#   9/12 결과 — 가로수길 변동폭 0.16 m(7%), 대조군 0.17 m. 형태 동일, 신호 없음.
#   → **가로수길 변동폭이 대조군의 2배를 넘고, 양쪽 봉우리 또는 중심선 함몰이 보이면 뒤집힌 것.**
#
# BBOX 는 래스터 헤더에서 직접 읽는다(크롭 범위를 바꿔도 코드를 안 고치게).
IMG = os.environ.get("CANOPY_IMG", "/tmp/canopy/busan_canopy.img")
HDR = os.environ.get("CANOPY_HDR") or (IMG[:-4] + ".hdr" if IMG.endswith(".img") else IMG + ".hdr")
OFFS = list(range(-25, 26))                 # -25 ~ +25 m


def open_csv(path):
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            with open(path, encoding=enc, newline="") as f:
                head = f.readline()
        except (UnicodeDecodeError, LookupError):
            continue
        if any("가" <= ch <= "힣" for ch in head):
            return open(path, encoding=enc, newline="")
    return open(path, encoding="utf-8", errors="replace", newline="")


def pick(row, *keys):
    for k in keys:
        for c, v in row.items():
            if c and k in c.replace(" ", ""):
                return v
    return None


def fnum(v):
    try:
        return float(str(v).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


class Ras:
    def __init__(self):
        h = {}
        for l in open(HDR, encoding="utf-8", errors="replace"):
            if "=" in l:
                k, v = l.split("=", 1); h[k.strip().lower()] = v.strip()
        self.ns, self.nl = int(h["samples"]), int(h["lines"])
        mi = [p.strip() for p in h["map info"].strip("{} ").split(",")]
        self.ulx, self.uly = float(mi[3]), float(mi[4])
        self.xr, self.yr = float(mi[5]), float(mi[6])
        self.a = np.memmap(IMG, dtype=np.uint8, mode="r", shape=(self.nl, self.ns))
        # 래스터가 실제로 덮는 범위 — 구간 걸러내기에 쓴다.
        self.bbox = (self.ulx, self.uly - self.nl * self.yr,
                     self.ulx + self.ns * self.xr, self.uly)
        print(f"래스터 {IMG}\n  {self.ns} x {self.nl} 화소  화소크기 {self.xr:.7f} x {self.yr:.7f} deg"
              f"  (약 {self.xr * 111320 * math.cos(math.radians(self.uly)):.2f} x "
              f"{self.yr * 111320:.2f} m)")
        print(f"  범위 {self.bbox[0]:.4f},{self.bbox[1]:.4f} ~ {self.bbox[2]:.4f},{self.bbox[3]:.4f}")

    def at(self, lat, lon):
        c = int((lon - self.ulx) / self.xr)
        r = int((self.uly - lat) / self.yr)
        if 0 <= r < self.nl and 0 <= c < self.ns:
            return float(self.a[r, c])
        return None


def transect(ras, lat, lon, ux, uy):
    """(ux,uy) 진행방향에 직각으로 OFFS 만큼 떨어진 점들의 수관고."""
    nx, ny = -uy, ux
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(lat))
    out = []
    for d in OFFS:
        la = lat + (ny * d) / mlat
        lo = lon + (nx * d) / mlon
        out.append(ras.at(la, lo))
    return out


def show(title, prof, n):
    print(f"\n{title}  (구간 {n:,}개)")
    vals = [p for p in prof if p is not None]
    if not vals:
        print("  값 없음"); return
    lo, hi = min(vals), max(vals)
    rng = max(hi - lo, 1e-6)
    for d, v in zip(OFFS, prof):
        if d % 2 and abs(d) > 12:
            continue
        bar = "#" * int((v - lo) / rng * 46)
        mark = " <= 중심선" if d == 0 else ""
        print(f"  {d:+3d}m {v:5.2f}m |{bar}{mark}")
    peak = OFFS[int(np.argmax(prof))]
    print(f"  최고점 {peak:+d}m ({max(prof):.2f}m),  중심선 {prof[OFFS.index(0)]:.2f}m")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/garosu_std.csv"
    ras = Ras()
    W, S, E, N = ras.bbox
    segs = []
    with open_csv(path) as f:
        for row in csv.DictReader(f):
            la1, lo1 = fnum(pick(row, "시작위도")), fnum(pick(row, "시작경도"))
            la2, lo2 = fnum(pick(row, "종료위도")), fnum(pick(row, "종료경도"))
            if None in (la1, lo1, la2, lo2):
                continue
            mla, mlo = (la1 + la2) / 2, (lo1 + lo2) / 2
            if not (S < mla < N and W < mlo < E):
                continue
            mlat = 111320.0
            mlon = 111320.0 * math.cos(math.radians(mla))
            dx, dy = (lo2 - lo1) * mlon, (la2 - la1) * mlat
            L = math.hypot(dx, dy)
            if L < 30 or L > 20000:
                continue
            segs.append((mla, mlo, dx / L, dy / L))
    print(f"래스터 범위 안 가로수길 구간 {len(segs):,}개")
    if not segs:
        print("구간 없음 — CSV 경로나 bbox 확인."); return

    acc = np.zeros(len(OFFS)); cnt = np.zeros(len(OFFS))
    for mla, mlo, ux, uy in segs:
        for i, v in enumerate(transect(ras, mla, mlo, ux, uy)):
            if v is not None:
                acc[i] += v; cnt[i] += 1
    prof = acc / np.maximum(cnt, 1)
    show("가로수길 — 도로 직각 단면 평균 수관고", prof, len(segs))

    # 대조군: 같은 중점, 방향만 무작위 90도 회전 — 지형 배경을 같게 두고 방향만 흐트러뜨린다
    random.seed(7)
    acc2 = np.zeros(len(OFFS)); cnt2 = np.zeros(len(OFFS))
    for mla, mlo, ux, uy in segs:
        th = random.uniform(0, 2 * math.pi)
        for i, v in enumerate(transect(ras, mla, mlo, math.cos(th), math.sin(th))):
            if v is not None:
                acc2[i] += v; cnt2[i] += 1
    show("대조군 — 같은 지점, 방향만 무작위", acc2 / np.maximum(cnt2, 1), len(segs))

    print("\n판정:")
    print("  · 양쪽에 봉우리 + 중심선 함몰  → 위성이 가로수를 본다. 봉우리 거리 = 옳은 OFFSET_M")
    print("  · 대조군과 모양이 같음        → 위성이 도심 가로수를 못 본다 (설계 변경 유지)")


if __name__ == "__main__":
    main()
