"""
개선 시뮬 배치도 — 실제 장소 항공사진 위에 추천 개입 위치 표시 (2026-09-05, 대표 전용)

바탕: V-World 위성 타일(공공 데이터, 편집·표기 허용). 서버가 타일을 받아오면 실서버에서
api.vworld.kr 타임아웃(8/11 기록)이 나므로, 서버는 키와 보행 축만 주고 **브라우저가 직접**
Leaflet 로 V-World 타일을 띄운다(그 위에 SVG 개입 마킹).

보행 축: 주변 60m 안 측정 격자들의 주축(PCA) — 가로수를 그 축으로 심는다. 없으면 None.
"""
from __future__ import annotations

import math

from loguru import logger
from sqlalchemy import text

from app.config import get_settings


async def _axis_deg(archive, lat: float, lon: float, radius_m: float = 60.0) -> float | None:
    if archive is None or not archive._ready:
        return None
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    sql = ("SELECT DISTINCT lat, lon FROM measurement WHERE indoor = FALSE"
           " AND lat BETWEEN :la1 AND :la2 AND lon BETWEEN :lo1 AND :lo2")
    try:
        async with archive._session() as s:
            rows = (await s.execute(text(sql), {"la1": lat - dlat, "la2": lat + dlat,
                                                "lo1": lon - dlon, "lo2": lon + dlon})).all()
    except Exception as e:  # noqa: BLE001
        logger.warning("[siteplan] 축 조회 실패 {}", e)
        return None
    pts = [((r[1] - lon) * 111_320.0 * math.cos(math.radians(lat)), (r[0] - lat) * 111_320.0) for r in rows]
    if len(pts) < 3:
        return None
    mx = sum(p[0] for p in pts) / len(pts); my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts); syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return (90.0 - math.degrees(ang)) % 180.0


async def siteplan(archive, lat: float, lon: float, level: int = 19, size: int = 800) -> dict:
    s = get_settings()
    if not getattr(s, "vworld_api_key", None):
        return {"ok": False, "reason": "VWORLD_API_KEY 미설정"}
    lat, lon = round(lat, 4), round(lon, 4)
    axis = await _axis_deg(archive, lat, lon)
    return {"ok": True, "lat": lat, "lon": lon,
            "vworld_key": s.vworld_api_key,
            "axis_deg": None if axis is None else round(axis, 1),
            "attribution": "항공사진 © 국토교통부 V-World"}
