#!/usr/bin/env python3
"""ASOS 관측소 표 검증 — 기상청에서 직접 받아 대조한다.

왜 필요한가 (2026-08-26):
    kma.py 의 ASOS_STATIONS 는 사람이 적어 넣은 표다. **지점번호가 틀리면
    다른 도시의 관측값을 그 도시 것이라고 내보낸다.** 좌표가 몇백 m 어긋나는 건
    50km 반경 선택에서 무해하지만, 번호가 틀리면 조용히 거짓말을 하게 된다.
    그래서 배포 전에 기상청 지점정보(stn_inf)를 받아 한 줄씩 대조한다.

⚠️ 이 파일이 scripts/ 가 아니라 app/tools/ 에 있는 이유:
    도커 이미지에는 backend/app 과 backend/vpti_core 만 복사된다(backend/Dockerfile).
    저장소 루트의 scripts/ 는 컨테이너 안에 아예 없다 — 거기 두면 서버에서 못 돌린다.

쓰는 법 (서버에서):
    cd ~/climax_mvp
    docker compose -f infra/ncp/docker-compose.prod.yml exec api \
        python3 -m app.tools.verify_asos_stations

    인증키는 컨테이너에 이미 들어 있는 KMA_APIHUB_KEY 를 그대로 쓴다.

읽는 법:
    OK       표와 기상청이 일치 (이름·좌표 오차 2km 이내)
    ⚠️ 좌표   번호는 맞는데 좌표가 2km 넘게 다름 → 표를 고칠 것
    ❌ 없음   기상청 목록에 없는 지점번호 → **반드시 빼거나 고칠 것**
"""
from __future__ import annotations

import math
import os
import urllib.parse
import urllib.request

STN_INF_URL = "https://apihub.kma.go.kr/api/typ01/url/stn_inf.php"
TOLERANCE_KM = 2.0


def load_table() -> dict[int, tuple[float, float]]:
    """kma.py 의 표를 그대로 읽어온다 — 두 곳에 베껴 쓰면 언젠가 어긋난다."""
    from app.services.kma import ASOS_STATIONS
    return ASOS_STATIONS


def fetch_official(auth_key: str) -> dict[int, tuple[str, float, float]]:
    """기상청 지상관측 지점정보 전체."""
    q = urllib.parse.urlencode({"inf": "SFC", "stn": "", "authKey": auth_key})
    with urllib.request.urlopen(f"{STN_INF_URL}?{q}", timeout=20) as r:
        text = r.read().decode("euc-kr", errors="replace")

    out: dict[int, tuple[str, float, float]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split()
        # 고정 컬럼: STN LON LAT ... (문서 기준). 이름은 뒤쪽 한글 토큰.
        try:
            stn = int(f[0])
            lon = float(f[1])
            lat = float(f[2])
        except (ValueError, IndexError):
            continue
        name = next((t for t in f[3:] if any("가" <= c <= "힣" for c in t)), "?")
        out[stn] = (name, lat, lon)
    return out


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def main() -> int:
    auth_key = ""
    try:
        from app.core.config import settings
        auth_key = settings.kma_apihub_key or ""
    except Exception:                                         # noqa: BLE001
        pass
    auth_key = auth_key or os.environ.get("KMA_APIHUB_KEY", "")
    if not auth_key:
        print("KMA_APIHUB_KEY 가 없습니다 — 컨테이너 안에서 실행하세요.")
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
