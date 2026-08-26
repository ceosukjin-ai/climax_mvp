#!/usr/bin/env python3
"""ASOS 관측소 표 검증 — 기상청에서 직접 받아 대조한다. **배포 전에** 돌린다.

왜 필요한가 (2026-08-26):
    kma.py 의 ASOS_STATIONS 는 사람이 적어 넣은 표다. **지점번호가 틀리면
    다른 도시의 관측값을 그 도시 것이라고 내보낸다.** 좌표가 몇백 m 어긋나는 건
    50km 반경 선택에서 무해하지만, 번호가 틀리면 조용히 거짓말을 하게 된다.

⚠️ 설계 원칙 — 이 도구는 **의존성이 없어야 한다.**
    배포 전에 돌리는 검사이므로, 아직 빌드되지 않은 이미지 안의 패키지
    (httpx·loguru·tenacity)에 기대면 안 된다. 그래서 kma.py 를 import 하지 않고
    ast 로 표만 읽어낸다. 파이썬 표준 라이브러리만 쓴다.
    (2026-08-26 첫 시도에서 컨테이너 안에 두었다가 바로 이 함정에 빠졌다 —
     이미지에 코드가 구워져 있어 git pull 로는 안 바뀌고, 재배포하면 이미
     늦는다.)

쓰는 법 (서버 호스트에서, 배포 **전에**):
    cd ~/climax_mvp
    set -a; . infra/ncp/.env.prod; set +a
    python3 scripts/verify_asos_stations.py

읽는 법:
    OK       표와 기상청이 일치 (좌표 오차 2km 이내)
    ⚠️ 좌표   번호는 맞는데 좌표가 2km 넘게 다름 → 표를 고칠 것
    ❌ 없음   기상청 목록에 없는 지점번호 → **반드시 빼거나 고칠 것**
"""
from __future__ import annotations

import ast
import math
import os
import re
import urllib.parse
import urllib.request

STN_INF_URL = "https://apihub.kma.go.kr/api/typ01/url/stn_inf.php"
TOLERANCE_KM = 2.0
KMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "backend", "app", "services", "kma.py",
)


def load_table() -> dict[int, tuple[float, float]]:
    """kma.py 에서 ASOS_STATIONS 만 읽어낸다. import 하지 않는다(의존성 회피)."""
    with open(KMA_PATH, encoding="utf-8") as f:
        src = f.read()
    m = re.search(
        r"ASOS_STATIONS:\s*dict\[int,\s*tuple\[float,\s*float\]\]\s*=\s*(\{.*?\n\})",
        src, re.S,
    )
    if not m:
        raise SystemExit(f"ASOS_STATIONS 를 못 찾았습니다: {KMA_PATH}")
    # 주석은 ast 가 알아서 무시한다
    return ast.literal_eval(m.group(1))


def fetch_official(auth_key: str) -> dict[int, tuple[str, float, float]]:
    """기상청 지상관측(SFC) 지점정보 전체."""
    q = urllib.parse.urlencode({"inf": "SFC", "stn": "", "authKey": auth_key})
    with urllib.request.urlopen(f"{STN_INF_URL}?{q}", timeout=20) as r:
        text = r.read().decode("euc-kr", errors="replace")

    out: dict[int, tuple[str, float, float]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        try:                                   # 고정 컬럼: STN LON LAT ...
            stn, lon, lat = int(f[0]), float(f[1]), float(f[2])
        except (ValueError, IndexError):
            continue
        name = next((t for t in f[3:] if any("가" <= c <= "힣" for c in t)), "?")
        out[stn] = (name, lat, lon)
    return out


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def main() -> int:
    auth_key = os.environ.get("KMA_APIHUB_KEY", "")
    if not auth_key:
        print("KMA_APIHUB_KEY 가 없습니다. 환경을 먼저 읽으세요:")
        print("    set -a; . infra/ncp/.env.prod; set +a")
        return 2

    table = load_table()
    try:
        official = fetch_official(auth_key)
    except Exception as e:                                    # noqa: BLE001
        print(f"지점정보를 받지 못했습니다: {e}")
        return 2
    if not official:
        print("지점정보 응답이 비었습니다 — 인증키나 활용신청을 확인하세요.")
        return 2

    bad = 0
    print(f"우리 표 {len(table)}곳 / 기상청 목록 {len(official)}곳\n")
    for stn, (lat, lon) in sorted(table.items()):
        if stn not in official:
            print(f"❌ 없음   {stn:>4}  기상청 목록에 없는 지점번호")
            bad += 1
            continue
        name, olat, olon = official[stn]
        d = km(lat, lon, olat, olon)
        if d > TOLERANCE_KM:
            print(f"⚠️ 좌표   {stn:>4} {name:<8} {d:5.1f}km 어긋남 "
                  f"→ ({olat:.5f}, {olon:.5f}) 로 고치세요")
            bad += 1
        else:
            print(f"OK       {stn:>4} {name:<8} {d:4.1f}km")

    print()
    if bad:
        print(f"문제 {bad}건 — 고치고 다시 돌리세요. 배포하지 마십시오.")
        return 1
    print("전부 일치합니다. 배포해도 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
