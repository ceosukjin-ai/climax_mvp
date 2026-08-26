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

검증은 두 단계다:
    [정밀] 지점정보(stn_inf) — 번호 + **좌표**까지 대조. 이게 진짜 검증이다.
    [약식] 지상관측(kma_sfctm2, stn=0) — 번호가 살아 있는지만 확인.
           지점정보 API에 활용신청이 안 돼 있으면(403) 여기로 폴백한다.
           ⚠️ 약식은 좌표를 확인하지 못한다. 번호는 살아 있는데 우리가 엉뚱한
              도시 좌표를 붙여 놓았다면 못 잡는다 — 그러면 그 도시 사용자에게
              다른 도시의 하늘을 보여주게 된다.
           → apihub.kma.go.kr 에서 「지점정보」 활용신청(무료·즉시)을 하면
             정밀 검증이 돈다.

읽는 법:
    OK       일치
    ⚠️ 좌표   번호는 맞는데 좌표가 2km 넘게 다름 → 표를 고칠 것
    ❌ 없음   기상청에 없는(또는 관측이 없는) 지점번호 → **반드시 고칠 것**
"""
from __future__ import annotations

import ast
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

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


SFCTM2_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php"


def fetch_live_station_ids(auth_key: str) -> set[int]:
    """지상관측 전체 지점(stn=0)에서 살아 있는 지점번호만 뽑는다. 좌표는 없다."""
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    for back in (2, 3, 4):
        tm = (kst - timedelta(hours=back)).strftime("%Y%m%d%H") + "00"
        q = urllib.parse.urlencode(
            {"tm": tm, "stn": "0", "help": "0", "authKey": auth_key})
        try:
            with urllib.request.urlopen(f"{SFCTM2_URL}?{q}", timeout=20) as r:
                text = r.read().decode("euc-kr", errors="replace")
        except Exception:                                     # noqa: BLE001
            continue
        ids: set[int] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            f = line.split()
            try:
                ids.add(int(f[1]))                            # YYMMDDHHMI STN ...
            except (ValueError, IndexError):
                continue
        if ids:
            return ids
    return set()


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

    official: dict[int, tuple[str, float, float]] = {}
    precise = True
    try:
        official = fetch_official(auth_key)
    except Exception as e:                                    # noqa: BLE001
        print(f"[정밀] 지점정보를 못 받았습니다: {e}")
        precise = False
    if not official:
        precise = False

    if not precise:
        print("[약식] 지상관측(stn=0)으로 번호만 확인합니다.\n")
        live = fetch_live_station_ids(auth_key)
        if not live:
            print("지상관측도 받지 못했습니다 — 인증키를 확인하세요.")
            return 2
        bad = 0
        print(f"우리 표 {len(table)}곳 / 관측 중인 지점 {len(live)}곳\n")
        for stn in sorted(table):
            if stn in live:
                print(f"OK       {stn:>4}  관측 중")
            else:
                print(f"❌ 없음   {stn:>4}  이 시각 관측이 없습니다")
                bad += 1
        print()
        if bad:
            print(f"문제 {bad}건 — 고치고 다시 돌리세요. 배포하지 마십시오.")
            return 1
        print("번호는 모두 살아 있습니다.")
        print()
        print("⚠️ 다만 **좌표는 확인하지 못했습니다.** 번호가 살아 있어도 우리가")
        print("   엉뚱한 도시 좌표를 붙여 놓았다면 그 도시 사용자에게 다른 도시의")
        print("   하늘을 보여주게 됩니다. apihub.kma.go.kr 에서 「지점정보」")
        print("   활용신청(무료·즉시)을 하고 이 검사를 다시 돌리세요.")
        return 0

    print("[정밀] 지점정보로 번호와 좌표를 대조합니다.\n")
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
