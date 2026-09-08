"""
Mapillary(Meta) 거리영상 클라이언트 — GSV 대체/병행 원천.

왜 있나
------
Google Street View 는 약관상 파생물 저장·ML 학습 금지(data_policy.py 참조)라
전국 확장·위성 학생모델 학습에 못 쓴다. Mapillary(CC BY-SA)는 **저장·학습 합법**이라
Mapillary 커버리지가 있는 곳은 여기서 받고(무료), 없는 곳만 GSV 폴백한다.

GSV 와의 차이 & 대응
------------------
GSV: panoId 하나로 5방향(F/R/B/L/up) 원하는 heading/pitch 이미지를 서버가 렌더해 준다.
Mapillary: 기여자가 올린 원본 이미지(대개 전방 perspective, 일부 360 spherical).
  → SVF/GVI/BVI(전방위 시야율)를 GSV와 같게 뽑으려면 **360 구면(spherical) 이미지**가 필요.
  → spherical equirectangular 썸네일을 받아 GSV VIEW_CONFIG(F/R/B/L @pitch0, up @pitch90,
     FOV90)와 동일한 5개 perspective 뷰로 **재투영**한다. 그러면 하류 SegFormer 분석·캐시가
     GSV와 완전히 같은 인터페이스로 동작(StreetViewFetchResult 반환).
  → spherical 이미지가 인근에 없으면 ZERO_RESULTS → orchestrator가 GSV로 폴백.

pano_id 규약: Mapillary 결과는 "mly:{image_id}" 로 접두어를 붙여 캐시·라우팅에서 출처를 구분.

⚠️ 재투영 좌표 규약은 실측 이미지로 검증 후 프로덕션 활성화할 것(토큰 세팅 = 활성).
   Mapillary 의무(data_policy.MAPILLARY_OBLIGATIONS): 공식 API만, CC BY-NC-SA 제외,
   화면표시 시 로고·링크백, 모델 가중치 외부배포 금지.
"""
from __future__ import annotations

import asyncio
import io
import math
from datetime import datetime, timezone

import httpx
import numpy as np
from loguru import logger
from PIL import Image
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.street_view import (
    DEFAULT_FOV,
    PanoMetadata,
    StreetViewFetchResult,
    StreetViewNotFound,
    VIEW_CONFIG,
)

GRAPH_URL = "https://graph.mapillary.com"
PANO_PREFIX = "mly:"
SPHERICAL_TYPES = ("spherical", "equirectangular")
_OUT_SIZE = 640  # 뷰당 출력 해상도(px) — GSV 640x640과 맞춤


def is_mapillary_pano(pano_id: str) -> bool:
    return pano_id.startswith(PANO_PREFIX)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _equirect_to_perspective(
    equi: np.ndarray, yaw_deg: float, pitch_deg: float, fov_deg: float, out_size: int
) -> np.ndarray:
    """등장방형(equirectangular) 360 → perspective 뷰 재투영.

    equi: (H, W, 3) uint8. 이미지 가로 중앙(x=W/2)=전방(yaw 0), 위=하늘.
    yaw_deg: 뷰 중심 방위(구면 이미지 로컬 기준, 전방=0, 우=+90).
    pitch_deg: 위+ / 아래-.
    """
    H, W = equi.shape[:2]
    f = (out_size / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    xs = np.arange(out_size, dtype=np.float64) - out_size / 2.0
    ys = np.arange(out_size, dtype=np.float64) - out_size / 2.0
    xx, yy = np.meshgrid(xs, ys)
    # 카메라 좌표계: x=우, y=상, z=전방
    vx = xx
    vy = -yy
    vz = np.full_like(xx, f)
    norm = np.sqrt(vx * vx + vy * vy + vz * vz)
    vx, vy, vz = vx / norm, vy / norm, vz / norm

    # pitch(위+) 회전 — x축 기준. +pitch = 위를 향함(전방 ray → +y).
    cp, sp = math.cos(math.radians(pitch_deg)), math.sin(math.radians(pitch_deg))
    vy2 = vy * cp + vz * sp
    vz2 = -vy * sp + vz * cp
    vy, vz = vy2, vz2
    # yaw(우+) 회전 — y축 기준
    cy, sy = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    vx2 = vx * cy + vz * sy
    vz2 = -vx * sy + vz * cy
    vx, vz = vx2, vz2

    # 구면좌표 → equirect 픽셀. 전방(+z)=lon0, 우(+x)=lon+.
    lon = np.arctan2(vx, vz)          # [-pi, pi]
    lat = np.arcsin(np.clip(vy, -1, 1))  # [-pi/2, pi/2]
    u = (lon / (2 * math.pi) + 0.5) * W
    v = (0.5 - lat / math.pi) * H
    u = np.clip(u, 0, W - 1).astype(np.int64)   # 최근접 샘플(속도·의존성 최소)
    v = np.clip(v, 0, H - 1).astype(np.int64)
    return equi[v, u]


class MapillaryClient:
    """Mapillary Graph API 비동기 클라이언트. GSV 클라이언트와 같은 인터페이스."""

    IMAGERY_SOURCE = "mapillary"

    def __init__(self, access_token: str, fov: int = DEFAULT_FOV, timeout_sec: float = 20.0) -> None:
        if not access_token:
            raise ValueError("Mapillary access token is required")
        self.token = access_token
        self.fov = fov
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def __aenter__(self) -> "MapillaryClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"OAuth {self.token}"}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def get_pano_metadata(self, lat: float, lon: float, radius_m: int = 50) -> PanoMetadata:
        """반경 내 가장 가까운 **360 구면** Mapillary 이미지 조회.

        구면 이미지가 없으면 status="ZERO_RESULTS" → orchestrator가 GSV로 폴백.
        """
        dlat = radius_m / 111320.0
        dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 1e-6))
        bbox = f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}"
        params = {
            "fields": "id,geometry,compass_angle,camera_type,captured_at",
            "bbox": bbox,
            "limit": 100,
        }
        r = await self._client.get(f"{GRAPH_URL}/images", params=params, headers=self._headers())
        r.raise_for_status()
        data = r.json().get("data", [])

        best = None
        best_d = float("inf")
        for im in data:
            if im.get("camera_type") not in SPHERICAL_TYPES:
                continue
            coords = (im.get("geometry") or {}).get("coordinates")  # [lon, lat]
            if not coords:
                continue
            d = _haversine_m(lat, lon, coords[1], coords[0])
            if d < best_d:
                best_d, best = d, im

        if best is None:
            return PanoMetadata(pano_id="", lat=lat, lon=lon, date=None, status="ZERO_RESULTS")

        coords = best["geometry"]["coordinates"]
        date = None
        cap = best.get("captured_at")
        if cap:
            try:
                date = datetime.fromtimestamp(int(cap) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, OSError, TypeError):
                date = None
        return PanoMetadata(
            pano_id=f"{PANO_PREFIX}{best['id']}",
            lat=coords[1],
            lon=coords[0],
            date=date,
            status="OK",
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def _image_detail(self, image_id: str) -> dict:
        params = {"fields": "thumb_2048_url,compass_angle,camera_type"}
        r = await self._client.get(f"{GRAPH_URL}/{image_id}", params=params, headers=self._headers())
        r.raise_for_status()
        return r.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def _download(self, url: str) -> bytes:
        r = await self._client.get(url)
        r.raise_for_status()
        return r.content

    async def fetch_five_views(self, metadata: PanoMetadata) -> StreetViewFetchResult:
        """Mapillary 360 이미지 → GSV와 동일한 5-view(F/R/B/L/up) 재투영.

        pano_id "mly:{id}" 에서 id 를 떼어 상세(썸네일 URL·compass_angle) 조회 →
        equirect 다운로드 → VIEW_CONFIG heading/pitch 대로 재투영해 JPEG bytes 5장.
        """
        if metadata.status != "OK" or not metadata.pano_id:
            raise StreetViewNotFound(f"No Mapillary pano at ({metadata.lat}, {metadata.lon})")
        image_id = metadata.pano_id[len(PANO_PREFIX):] if is_mapillary_pano(metadata.pano_id) else metadata.pano_id

        detail = await self._image_detail(image_id)
        if detail.get("camera_type") not in SPHERICAL_TYPES:
            raise StreetViewNotFound(f"Mapillary image {image_id} is not spherical")
        thumb_url = detail.get("thumb_2048_url")
        if not thumb_url:
            raise StreetViewNotFound(f"Mapillary image {image_id} has no thumbnail")
        compass = float(detail.get("compass_angle") or 0.0)

        raw = await self._download(thumb_url)
        equi = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))

        def _render(direction: str) -> bytes:
            cfg = VIEW_CONFIG[direction]
            # GSV heading은 절대방위(0=북). Mapillary 로컬 yaw = 절대heading - compass_angle.
            yaw = (cfg["heading"] - compass) % 360.0
            view = _equirect_to_perspective(equi, yaw, cfg["pitch"], float(self.fov), _OUT_SIZE)
            buf = io.BytesIO()
            Image.fromarray(view).save(buf, format="JPEG", quality=85)
            return buf.getvalue()

        images = await asyncio.gather(
            *[asyncio.to_thread(_render, d) for d in VIEW_CONFIG]
        )
        return StreetViewFetchResult(
            pano_id=metadata.pano_id,
            lat=metadata.lat,
            lon=metadata.lon,
            images={d: img for d, img in zip(VIEW_CONFIG, images)},
            capture_date=metadata.date,
        )
