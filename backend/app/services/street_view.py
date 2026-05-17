"""
Google Street View Static API 클라이언트.

특허 청구항 2: "다방향 시야 영상은 상향 시야 영상 1개와 수평 방향 시야
영상 4개로 구성되는 것을 특징으로 하는 방법"

이 모듈은 위경도 + 시각으로 요청받아, panoId를 해석하고 5방향 파노라마를
수집합니다. 같은 panoId는 영구 캐시되므로 두 번 긁지 않습니다.

비용 통제:
- Metadata 호출은 무료 (panoId 확인용)
- 이미지 호출만 유료 (약 $0.007/장, 5장/포인트 = $0.035/포인트)
- Redis에 panoId별로 영구 저장 → 재방문 시 0원
"""
from __future__ import annotations

import hashlib
import hmac
import base64
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode, urlparse

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"

# 특허 기준 5-view 정의: 전·후·좌·우·상
# heading: 북=0, 동=90, 남=180, 서=270
# pitch: 지평선=0, 위쪽=+, 아래쪽=-
VIEW_CONFIG: dict[str, dict[str, int]] = {
    "front": {"heading": 0, "pitch": 0},
    "right": {"heading": 90, "pitch": 0},
    "back": {"heading": 180, "pitch": 0},
    "left": {"heading": 270, "pitch": 0},
    "up": {"heading": 0, "pitch": 90},
}

DEFAULT_IMAGE_SIZE = "640x640"  # Google 무료 최대 사이즈
DEFAULT_FOV = 90  # 수평 FOV


@dataclass(frozen=True, slots=True)
class PanoMetadata:
    """Google Street View metadata 응답의 필요 필드만 추출."""

    pano_id: str
    lat: float
    lon: float
    date: str | None  # YYYY-MM-DD (파노라마 촬영일, 신선도 평가용)
    status: Literal["OK", "ZERO_RESULTS", "NOT_FOUND", "INVALID_REQUEST"]


@dataclass(frozen=True, slots=True)
class StreetViewFetchResult:
    """5-view 이미지 수집 결과."""

    pano_id: str
    lat: float
    lon: float
    images: dict[str, bytes]  # direction -> PNG/JPEG bytes
    capture_date: str | None


class StreetViewError(Exception):
    """Street View 요청 실패."""


class StreetViewNotFound(StreetViewError):
    """해당 좌표에 Street View가 없음 (바다, 산중턱 등)."""


def _sign_url(url: str, secret: str) -> str:
    """Google URL signing (선택적 보안 강화).

    signing secret이 설정된 프로젝트만 사용. 보안 권장사항이지만
    개발 단계에선 생략 가능.
    """
    parsed = urlparse(url)
    url_to_sign = parsed.path + "?" + parsed.query
    decoded_key = base64.urlsafe_b64decode(secret)
    signature = hmac.new(decoded_key, url_to_sign.encode(), hashlib.sha1)
    encoded_signature = base64.urlsafe_b64encode(signature.digest()).decode()
    return url + f"&signature={encoded_signature}"


class GoogleStreetViewClient:
    """Google Street View Static API 비동기 클라이언트.

    사용 예:
        async with GoogleStreetViewClient(api_key="...") as client:
            meta = await client.get_pano_metadata(37.5665, 126.9780)
            if meta.status == "OK":
                result = await client.fetch_five_views(meta)
    """

    def __init__(
        self,
        api_key: str,
        signing_secret: str = "",
        image_size: str = DEFAULT_IMAGE_SIZE,
        fov: int = DEFAULT_FOV,
        timeout_sec: float = 15.0,
    ) -> None:
        if not api_key:
            raise ValueError("Google Street View API key is required")
        self.api_key = api_key
        self.signing_secret = self._sanitize_signing_secret(signing_secret)
        self.image_size = image_size
        self.fov = fov
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    @staticmethod
    def _sanitize_signing_secret(secret: str) -> str:
        """signing_secret 유효성 검증.

        Google URL signing secret은 base64-URL-safe 문자열이다.
        .env에 placeholder 주석이 그대로 남거나 비ASCII가 섞이는 실수가 흔해서,
        invalid 값이면 경고 후 빈 값으로 폴백한다. 서명은 선택적 보안 강화이므로
        invalid라고 앱을 죽일 가치가 없다.
        """
        candidate = (secret or "").strip()
        if not candidate:
            return ""
        try:
            base64.urlsafe_b64decode(candidate)
        except (ValueError, Exception) as e:
            logger.warning(
                "GOOGLE_STREETVIEW_SIGNING_SECRET is not valid base64-URL-safe; "
                "skipping URL signing. ({}: {})",
                type(e).__name__, e,
            )
            return ""
        return candidate

    async def __aenter__(self) -> "GoogleStreetViewClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def close(self) -> None:
        await self._client.aclose()

    def _build_image_url(
        self,
        pano_id: str,
        heading: int,
        pitch: int,
    ) -> str:
        """panoId 기반 이미지 URL. 좌표보다 안정적 (같은 panoId 고정)."""
        params = {
            "size": self.image_size,
            "pano": pano_id,
            "heading": heading,
            "pitch": pitch,
            "fov": self.fov,
            "key": self.api_key,
        }
        url = f"{IMAGE_URL}?{urlencode(params)}"
        if self.signing_secret:
            url = _sign_url(url, self.signing_secret)
        return url

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def get_pano_metadata(
        self,
        lat: float,
        lon: float,
        radius_m: int = 50,
    ) -> PanoMetadata:
        """주어진 위경도 인근 가장 가까운 panoId 조회.

        Metadata API는 무료이므로 먼저 호출하여 유효성과 panoId를
        확인한 뒤에만 이미지 API를 호출합니다.

        Args:
            lat, lon: 조회 지점.
            radius_m: 허용 반경 [m]. 해당 반경 내 Street View 없으면 ZERO_RESULTS.

        Returns:
            PanoMetadata. status="OK"이면 pano_id 유효.
        """
        params = {
            "location": f"{lat},{lon}",
            "radius": radius_m,
            "source": "outdoor",  # 실외만 — 실내 뷰 제외
            "key": self.api_key,
        }
        response = await self._client.get(METADATA_URL, params=params)
        response.raise_for_status()
        data = response.json()

        status = data.get("status", "INVALID_REQUEST")
        if status != "OK":
            logger.debug(
                "Street View metadata {}: lat={}, lon={}", status, lat, lon
            )
            return PanoMetadata(
                pano_id="",
                lat=lat,
                lon=lon,
                date=None,
                status=status,
            )

        return PanoMetadata(
            pano_id=data["pano_id"],
            lat=data["location"]["lat"],
            lon=data["location"]["lng"],
            date=data.get("date"),
            status="OK",
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def _fetch_one_image(
        self,
        pano_id: str,
        direction: str,
    ) -> bytes:
        """단일 방향 이미지 fetch."""
        config = VIEW_CONFIG[direction]
        url = self._build_image_url(
            pano_id=pano_id,
            heading=config["heading"],
            pitch=config["pitch"],
        )
        response = await self._client.get(url)
        response.raise_for_status()

        # Google은 성공 시 image/jpeg 반환
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type:
            raise StreetViewError(
                f"Expected image, got {content_type}: {response.text[:200]}"
            )
        return response.content

    async def fetch_five_views(
        self,
        metadata: PanoMetadata,
    ) -> StreetViewFetchResult:
        """panoId로 5방향 이미지 전부 수집.

        5개 요청을 병렬 실행하여 총 지연시간 최소화.
        """
        if metadata.status != "OK" or not metadata.pano_id:
            raise StreetViewNotFound(
                f"No valid panorama at ({metadata.lat}, {metadata.lon})"
            )

        import asyncio

        directions = list(VIEW_CONFIG.keys())
        tasks = [
            self._fetch_one_image(metadata.pano_id, d) for d in directions
        ]
        images_list = await asyncio.gather(*tasks)

        return StreetViewFetchResult(
            pano_id=metadata.pano_id,
            lat=metadata.lat,
            lon=metadata.lon,
            images={d: img for d, img in zip(directions, images_list)},
            capture_date=metadata.date,
        )

    async def fetch_by_location(
        self,
        lat: float,
        lon: float,
    ) -> StreetViewFetchResult:
        """편의 메서드: 위경도 → metadata → 5-view 한 번에.

        Raises:
            StreetViewNotFound: 해당 좌표 근처에 Street View 없음.
        """
        meta = await self.get_pano_metadata(lat, lon)
        if meta.status != "OK":
            raise StreetViewNotFound(
                f"Street View {meta.status} at ({lat}, {lon})"
            )
        return await self.fetch_five_views(meta)
