"""
FastAPI 라우트 통합 테스트 — HTTP 레벨 동작 검증.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


class TestHealth:
    def test_health_check(self, client: TestClient) -> None:
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_root(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "ClimaX" in r.json()["name"]


class TestVSIEndpoints:
    def test_vsi_components(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/vsi/components",
            json={"svf": 0.5, "gvi": 0.2, "bvi": 0.35},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["vsi"] == pytest.approx(0.56, abs=0.01)
        assert data["category"] in ("Low", "Moderate", "High")

    def test_vsi_components_out_of_range(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/vsi/components",
            json={"svf": 1.5, "gvi": 0.2, "bvi": 0.3},
        )
        assert r.status_code == 422  # Pydantic validation

    def test_vsi_from_views(self, client: TestClient) -> None:
        views = [
            {
                "direction": "up",
                "sky_ratio": 0.6,
                "vegetation_ratio": 0.0,
                "building_ratio": 0.0,
            }
        ] + [
            {
                "direction": d,
                "sky_ratio": 0.0,
                "vegetation_ratio": 0.2,
                "building_ratio": 0.3,
            }
            for d in ("front", "back", "left", "right")
        ]
        r = client.post("/api/v1/vsi", json=views)
        assert r.status_code == 200
        data = r.json()
        assert data["svf"] == pytest.approx(0.6)
        assert data["gvi"] == pytest.approx(0.2)
        assert data["bvi"] == pytest.approx(0.3)


class TestVPTIEndpoint:
    def test_vpti_full_request(self, client: TestClient) -> None:
        views = [
            {
                "direction": "up",
                "sky_ratio": 0.85,
                "vegetation_ratio": 0.0,
                "building_ratio": 0.0,
            }
        ] + [
            {
                "direction": d,
                "sky_ratio": 0.0,
                "vegetation_ratio": 0.05,
                "building_ratio": 0.25,
            }
            for d in ("front", "back", "left", "right")
        ]
        payload = {
            "location": {"lat": 35.2338, "lon": 129.0820},
            "views": views,
            "materials": [
                {"material": "asphalt", "fraction": 0.7},
                {"material": "concrete", "fraction": 0.3},
            ],
            "weather": {
                "temperature_c": 33.0,
                "humidity_pct": 70.0,
                "wind_speed_ms": 2.0,
                "wind_direction_deg": 180.0,
                "precipitation_mm": 0.0,
            },
            "timestamp": "2026-08-01T14:00:00",
        }
        r = client.post("/api/v1/vpti", json=payload)
        assert r.status_code == 200
        data = r.json()

        assert "vpti" in data
        assert "risk_level" in data
        assert "action_guide" in data
        assert data["season"] == "summer"

    def test_vpti_without_weather_returns_501(
        self, client: TestClient
    ) -> None:
        """기상 자동조회는 Step 2에서 구현 예정."""
        views = [
            {
                "direction": "up",
                "sky_ratio": 0.5,
                "vegetation_ratio": 0.0,
                "building_ratio": 0.0,
            }
        ] + [
            {
                "direction": d,
                "sky_ratio": 0.0,
                "vegetation_ratio": 0.2,
                "building_ratio": 0.3,
            }
            for d in ("front", "back", "left", "right")
        ]
        payload = {
            "location": {"lat": 37.5665, "lon": 126.9780},
            "views": views,
            "materials": [{"material": "concrete", "fraction": 1.0}],
        }
        r = client.post("/api/v1/vpti", json=payload)
        assert r.status_code == 501
