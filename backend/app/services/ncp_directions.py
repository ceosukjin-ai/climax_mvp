"""
NCP Maps Directions 5 클라이언트 — 자동차 경로 탐색.

두 지점(출발/도착) 사이의 도로 경로 polyline 을 받아온다. 도보 전용 길찾기는
NCP Maps 가 제공하지 않으므로 자동차 경로(도로 기준)를 사용한다 — 보행 경로와
대체로 겹치고, Street View 커버리지가 도로변에 있어 VPTI 산출에 유리하다.

  · Endpoint : https://maps.apigw.ntruss.com/map-direction/v1/driving
  · Headers  : x-ncp-apigw-api-key-id, x-ncp-apigw-api-key
  · Params   : start=lon,lat  goal=lon,lat  option=trafast|traoptimal|tracomfort
  · Response : route.<option>[0].path = [[lon, lat], ...]
"""
from __future__ import annotations

import math

import httpx
from loguru import logger

DIRECTIONS_URL = "https://maps.apigw.ntruss.com/map-direction/v1/driving"

LatLon = tuple[float, float]


class NCPDirectionsError(Exception):
    pass


def haversine_m(a: LatLon, b: LatLon) -> float:
    """두 (lat, lon) 사이 대권거리 [m]."""
    R = 6371000.0
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def sample_path(path: list[LatLon], max_points: int = 10) -> list[LatLon]:
    """경로 polyline 을 거리 기준으로 균등하게 max_points 개로 축약.

    출발·도착점은 항상 포함한다.
    """
    n = len(path)
    if n == 0:
        return []
    if n <= max_points:
        return path

    cum = [0.0]
    for i in range(1, n):
        cum.append(cum[-1] + haversine_m(path[i - 1], path[i]))
    total = cum[-1]
    if total == 0:
        return [path[0]]

    targets = [total * i / (max_points - 1) for i in range(max_points)]
    out: list[LatLon] = []
    j = 0
    for t in targets:
        while j < n - 1 and cum[j] < t:
            j += 1
        out.append(path[j])
    # 중복 제거(연속 동일점)
    dedup = [out[0]]
    for p in out[1:]:
        if p != dedup[-1]:
            dedup.append(p)
    return dedup


class NCPDirectionsClient:
    """NCP Maps Directions 5 비동기 클라이언트."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        timeout_sec: float = 10.0,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("NCP Maps client id/secret required")
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_path(
        self,
        olat: float,
        olon: float,
        dlat: float,
        dlon: float,
        option: str = "trafast",
    ) -> list[LatLon]:
        """출발→도착 도로 경로 polyline [(lat, lon), ...] 반환."""
        params = {
            "start": f"{olon},{olat}",
            "goal": f"{dlon},{dlat}",
            "option": option,
        }
        headers = {
            "x-ncp-apigw-api-key-id": self.client_id,
            "x-ncp-apigw-api-key": self.client_secret,
        }
        resp = await self._client.get(DIRECTIONS_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        code = data.get("code")
        if code not in (0, None):
            raise NCPDirectionsError(
                f"NCP directions code={code} message={data.get('message')}"
            )

        route = data.get("route", {}) or {}
        for key in (option, "trafast", "traoptimal", "tracomfort"):
            seg = route.get(key)
            if seg:
                path = seg[0].get("path") or []
                if path:
                    # NCP path 는 [lon, lat] → (lat, lon) 로 변환
                    return [(pt[1], pt[0]) for pt in path]
        raise NCPDirectionsError("NCP directions: 응답에 path 없음")
