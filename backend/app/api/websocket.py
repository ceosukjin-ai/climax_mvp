"""
실시간 위치 트래킹 WebSocket 엔드포인트.

사용자가 이동하는 동안 25m 간격으로 새 좌표를 보내면, 서버가 VPTI를
계산하여 밀어보냅니다.

특허 명세서: "사용자 위치 정보는 사용자의 이동에 따라 일정 거리 간격으로
갱신되며, 일 실시예로 약 25m 간격으로 주기적으로 획득될 수 있다"

프로토콜 (JSON 메시지):
    클라 → 서버: {"lat": float, "lon": float, "ts": ISO8601 | null}
    서버 → 클라: {"type": "vpti", "data": VPTIResponse, "telemetry": {...}}
                 {"type": "error", "code": str, "message": str}

25m 이하 이동은 클라이언트가 자체 throttle (서버가 아님) — 서버는 모든
요청을 정상 처리하되, 같은 panoId면 캐시 hit으로 즉시 응답.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from loguru import logger

from app.services.orchestrator import VPTIOrchestrator
from app.services.street_view import StreetViewNotFound

router = APIRouter()


async def _send_error(ws: WebSocket, code: str, message: str) -> None:
    try:
        await ws.send_json({"type": "error", "code": code, "message": message})
    except Exception:
        # WebSocket 이미 끊긴 경우 무시
        pass


def _parse_tracking_message(raw: str) -> tuple[float, float, datetime | None]:
    """클라이언트 메시지 파싱 및 유효성 검사."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if not isinstance(msg, dict):
        raise ValueError("Expected JSON object")

    lat = msg.get("lat")
    lon = msg.get("lon")
    ts_str = msg.get("ts")

    if not isinstance(lat, (int, float)) or not -90.0 <= lat <= 90.0:
        raise ValueError(f"Invalid lat: {lat}")
    if not isinstance(lon, (int, float)) or not -180.0 <= lon <= 180.0:
        raise ValueError(f"Invalid lon: {lon}")

    timestamp: datetime | None = None
    if ts_str is not None:
        try:
            timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise ValueError(f"Invalid timestamp: {ts_str}")

    return float(lat), float(lon), timestamp


@router.websocket("/api/v1/track")
async def track_websocket(ws: WebSocket) -> None:
    """실시간 위치 스트림 → VPTI 스트림.

    연결 후 클라이언트가 끊을 때까지 메시지를 계속 수신·처리합니다.
    각 메시지는 독립적인 VPTI 요청으로 처리됨.
    """
    await ws.accept()
    logger.info("WebSocket /track connected: {}", ws.client)

    orchestrator: VPTIOrchestrator | None = getattr(
        ws.app.state, "orchestrator", None
    )
    if orchestrator is None:
        logger.error("Orchestrator not initialized in app state")
        await _send_error(
            ws,
            "ORCHESTRATOR_UNAVAILABLE",
            "Server is not ready. Check backend startup logs.",
        )
        await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    try:
        while True:
            raw = await ws.receive_text()

            try:
                lat, lon, timestamp = _parse_tracking_message(raw)
            except ValueError as e:
                await _send_error(ws, "INVALID_MESSAGE", str(e))
                continue

            try:
                result, telemetry = await orchestrator.compute(
                    lat=lat, lon=lon, timestamp=timestamp
                )
            except StreetViewNotFound as e:
                await _send_error(ws, "NO_STREET_VIEW", str(e))
                continue
            except Exception as e:
                logger.exception("VPTI computation failed")
                await _send_error(ws, "COMPUTATION_ERROR", str(e))
                continue

            await ws.send_json(
                {
                    "type": "vpti",
                    "data": result.as_dict(),
                    "telemetry": {
                        "pano_cache_hit": telemetry.pano_cache_hit,
                        "weather_cache_hit": telemetry.weather_cache_hit,
                        "total_ms": round(telemetry.total_ms, 1),
                        "street_view_ms": round(telemetry.street_view_ms, 1),
                        "segmentation_ms": round(telemetry.segmentation_ms, 1),
                        "weather_ms": round(telemetry.weather_ms, 1),
                    },
                }
            )

    except WebSocketDisconnect:
        logger.info("WebSocket /track disconnected: {}", ws.client)
    except Exception as e:
        logger.exception("WebSocket error: {}", e)
        try:
            await ws.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass
