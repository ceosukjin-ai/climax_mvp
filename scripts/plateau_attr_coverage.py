#!/usr/bin/env python3
"""PLATEAU 건물 속성 채움률 — 일본 BTLI 입력으로 쓸 수 있나 (2026-09-25).

일본 BTLI 에 필요한 것: 건축연도(→ 단열 등급: 1980 전 무단열 / 1980~92 S55 / 1992~99 H4 / 이후),
구조(목조·S·RC → 열용량·U), 용도, 연면적. 한국은 건축물대장이 다 주지만 일본은 공개 대장이 없다.
PLATEAU(도시계획기초조사 속성)에 들어 있는 도시가 있다 — 도쿄 23구 2022 판에 얼마나 채워졌는지 센다.

  docker run --rm -v ~/plateau:/plateau -v ~/climax_mvp:/repo climax-backend:latest \
    python3 /repo/scripts/plateau_attr_coverage.py /plateau            # zip 이 있는 폴더 또는 zip 경로
표본: zip 안 udx/bldg/*.gml 중 고르게 40개(구 전체에 퍼지게). 한 파일씩 꺼내 세고 지운다.

zip 을 지웠으면 URL 을 그대로 줘도 된다 — **5.5 GB 를 받지 않고** HTTP Range 로 zip 목차와
표본 파일만 읽는다(수백 MB). 디스크를 쓰지 않는다.
  docker run --rm -v ~/climax_mvp:/repo climax-backend:latest python3 /repo/scripts/plateau_attr_coverage.py \
    https://assets.cms.plateau.reearth.io/assets/74/b317b5-a7b0-426f-ba8b-af5f777c76c5/13100_tokyo23-ku_2022_citygml_1_2_op.zip
"""
from __future__ import annotations
import glob, os, sys, tempfile, zipfile
from collections import Counter

FIELDS = ["yearOfConstruction", "storeysAboveGround", "measuredHeight", "usage",
          "buildingStructureType", "fireproofStructureType", "totalFloorArea",
          "buildingFootprintArea", "detailedUsage", "surveyYear"]


class HttpRangeFile:
    """zipfile 이 읽을 수 있는 원격 파일 — 필요한 바이트만 Range 로 가져온다(1 MB 블록 캐시)."""
    BLK = 1 << 20

    def __init__(self, url):
        import httpx
        self.c = httpx.Client(timeout=60.0, follow_redirects=True)
        self.url = url
        r = self.c.head(url)
        self.size = int(r.headers["content-length"])
        self.pos = 0
        self.cache = {}
        self.fetched = 0

    def _blk(self, i):
        if i not in self.cache:
            a = i * self.BLK
            b = min(self.size, a + self.BLK) - 1
            r = self.c.get(self.url, headers={"Range": f"bytes={a}-{b}"})
            if r.status_code == 200 and self.size <= 200_000_000:
                # Range 를 무시하는 서버(작은 파일) — 통째로 받아 잘라 쓴다
                full = r.content
                self.fetched += len(full)
                for j in range(0, (self.size + self.BLK - 1) // self.BLK):
                    self.cache[j] = full[j * self.BLK:(j + 1) * self.BLK]
                return self.cache[i]
            if r.status_code != 206:
                raise RuntimeError(f"Range 미지원 HTTP {r.status_code} — zip 을 내려받아 경로로 줄 것")
            self.cache[i] = r.content
            self.fetched += len(r.content)
            if len(self.cache) > 64:
                self.cache.pop(next(iter(self.cache)))
        return self.cache[i]

    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        out = bytearray()
        while n > 0:
            i, o = divmod(self.pos, self.BLK)
            d = self._blk(i)[o:o + n]
            out += d; self.pos += len(d); n -= len(d)
        return bytes(out)


def _ln(t):
    return t.rsplit("}", 1)[-1] if "}" in t else t


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "/plateau"
    n_files = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    import xml.etree.ElementTree as ET
    remote = None
    if arg.startswith("http"):
        remote = HttpRangeFile(arg)
        zp = arg.rsplit("/", 1)[-1]
        print(f"원격 zip {remote.size / 1e9:.1f} GB — Range 로 목차·표본만 읽는다")
        zf = zipfile.ZipFile(remote)
    else:
        zp = arg if arg.endswith(".zip") else (sorted(glob.glob(os.path.join(arg, "*.zip"))) or [None])[0]
        if not zp or not os.path.exists(zp):
            print("PLATEAU zip 없음:", arg, "— URL 을 넣어도 된다(파일 머리말 참고)"); return
        zf = zipfile.ZipFile(zp)
    names = sorted(n for n in zf.namelist() if "/udx/bldg/" in n and n.endswith(".gml"))
    step = max(1, len(names) // n_files)
    pick = names[::step][:n_files]
    print(f"{os.path.basename(zp)} — bldg gml {len(names)}개 중 {len(pick)}개 표본")
    have, n_b = Counter(), 0
    struct, fire, usage, yb = Counter(), Counter(), Counter(), Counter()
    tmpd = tempfile.mkdtemp()
    for i, nm in enumerate(pick, 1):
        p = zf.extract(nm, tmpd)
        try:
            for _ev, el in ET.iterparse(p, events=("end",)):
                if _ln(el.tag) != "Building":
                    continue
                n_b += 1
                seen = {}
                for sub in el.iter():
                    k = _ln(sub.tag)
                    if k in FIELDS and (sub.text or "").strip() and k not in seen:
                        seen[k] = sub.text.strip()
                for k in seen:
                    have[k] += 1
                if "buildingStructureType" in seen: struct[seen["buildingStructureType"]] += 1
                if "fireproofStructureType" in seen: fire[seen["fireproofStructureType"]] += 1
                if "usage" in seen: usage[seen["usage"]] += 1
                y = seen.get("yearOfConstruction", "")[:4]
                if y.isdigit():
                    y = int(y)
                    yb["~1979 무단열기" if y < 1980 else "1980~91 S55" if y < 1992 else
                       "1992~98 H4" if y < 1999 else "1999~2024 H11+" if y < 2025 else "2025~ 의무화"] += 1
                el.clear()
        finally:
            os.remove(p)
        if i % 10 == 0:
            print(f"  {i}/{len(pick)} 건물 {n_b:,}", flush=True)
    print(f"\n건물 {n_b:,}동 — 속성 채움률")
    for k in FIELDS:
        print(f"  {k:24s} {have[k] / max(1, n_b) * 100:5.1f}%")
    print("\n구조(buildingStructureType 코드):", struct.most_common(10))
    print("내화구조(fireproofStructureType 코드):", fire.most_common(6))
    print("용도(usage 코드):", usage.most_common(8))
    print("건축연도 → 단열 기준 시기:", dict(yb))
    if remote:
        print(f"\n(받은 양 {remote.fetched / 1e6:.0f} MB)")
    print("\n코드 의미는 PLATEAU 코드리스트(Building_buildingStructureType.xml 등)로 확인할 것 — 여기서 짐작하지 않는다.")


if __name__ == "__main__":
    main()
