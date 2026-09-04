"""
개선 시뮬 배치도 — 실제 장소 항공(위성)사진 위에 추천 개입 위치를 표시할 바탕 (2026-09-05)

바탕: NCP Maps Static Map (maptype=satellite) — 서버에 키가 이미 있고, V-World 는 실서버에서
연결이 안 된다(2026-08-11 기록). 그리기는 화면(SVG)에서 한다 — 한글 폰트·수정 편의.
서버는 (이미지, 미터/픽셀, 중심 픽셀, 보행 축 방향)만 준다.

보행 축: 그 격자 주변 60m 안의 다른 측정 격자들이 늘어선 방향(주성분) — 가로수를 그 축으로 심는다.
근처 격자가 없으면 None(화면에서 남북 기본).
"""
from __future__ import annotations

import base64
import math

import httpx
from loguru import logger
from sqlalchemy import text

from app.config import get_settings

NCP_STATIC_URL = "https://maps.apigw.ntruss.com/map-static/v2/raster"


def meters_per_pixel(lat: float, level: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** level)


async def _axis_deg(archive, lat: float, lon: float, radius_m: float = 60.0) -> float | None:
    """주변 측정 격자의 주축 방향(도, 북=0 시계방향). PCA 1축."""
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
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)          # x축(동) 기준 반시계
    return (90.0 - math.degrees(ang)) % 180.0            # 북 기준 시계방향 0~180


async def siteplan(archive, lat: float, lon: float, level: int = 19, size: int = 800) -> dict:
    s = get_settings()
    if not s.ncp_maps_client_id or not s.ncp_maps_client_secret:
        return {"ok": False, "reason": "NCP_MAPS 키 미설정"}
    lat, lon = round(lat, 4), round(lon, 4)
    params = {"w": size, "h": size, "center": f"{lon},{lat}", "level": level,
              "maptype": "satellite_base", "format": "png", "scale": 1, "lang": "ko"}
    headers = {"x-ncp-apigw-api-key-id": s.ncp_maps_client_id,
               "x-ncp-apigw-api-key": s.ncp_maps_client_secret}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(NCP_STATIC_URL, params=params, headers=headers)
        if r.status_code != 200:
            return {"ok": False, "reason": f"NCP static map {r.status_code}: {r.text[:200]}"}
        img_b64 = base64.b64encode(r.content).decode()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    axis = await _axis_deg(archive, lat, lon)
    return {"ok": True, "lat": lat, "lon": lon, "size": size, "level": level,
            "m_per_px": round(meters_per_pixel(lat, level), 4),
            "axis_deg": None if axis is None else round(axis, 1),
            "image": "data:image/png;base64," + img_b64,
            "attribution": "항공사진 © NAVER Cloud (NCP Maps)"}
