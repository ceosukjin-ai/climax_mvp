"""측정 이력 적재 — 좌표 익명화·비활성 동작·SQL 형태 검증(DB 없이)."""
from __future__ import annotations

import asyncio
import pytest

from app.services.archive import Archive, COORD_PRECISION


class TestDisabled:
    """DB가 없거나 꺼져 있어도 서비스는 정상 동작해야 한다."""

    def test_비활성이면_조용히_무시(self):
        a = Archive("", enabled=True)
        assert a.enabled is False
        a.record_measurement(lat=35.1, lon=129.1, pvpti=30.0)   # 예외 없이 통과
        a.record_engine_check(station_id=159)

    def test_설정으로_끄기(self):
        a = Archive("postgresql+asyncpg://x/y", enabled=False)
        assert a.enabled is False

    def test_준비_전에는_적재하지_않음(self):
        a = Archive("postgresql+asyncpg://x/y", enabled=True)
        assert a._ready is False
        a.record_measurement(lat=35.1, lon=129.1)               # start() 전 → 무시


class TestAnonymization:
    """좌표는 격자로 반올림해 저장한다(원좌표 미보관)."""

    @pytest.mark.asyncio
    async def test_좌표_반올림(self):
        captured = {}

        a = Archive("postgresql+asyncpg://x/y", enabled=True)
        a._ready = True

        async def fake_run(sql, vals, what):
            captured.update(vals)

        a._run = fake_run
        await a._insert_measurement({"lat": 35.182456789, "lon": 129.103281234,
                                     "pvpti": 31.2, "risk_level": "warning"})
        assert captured["lat"] == round(35.182456789, COORD_PRECISION)
        assert captured["lon"] == round(129.103281234, COORD_PRECISION)
        # 원좌표가 그대로 남지 않는다
        assert captured["lat"] != 35.182456789

    @pytest.mark.asyncio
    async def test_개인정보_컬럼_없음(self):
        """나이·질환·심박 등 개인 식별·민감 항목은 스키마에 존재하지 않는다."""
        import re as _re
        from app.services.archive import DDL
        # 부분문자열로 보면 imagery_src 안의 'age' 같은 것에 걸린다.
        # 컬럼 이름 단위(단어 경계)로 본다 — 검사의 원래 뜻은 그것이었다.
        lowered = DDL.lower()
        for banned in ("age", "user_id", "device_id", "condition",
                       "heart_rate", "core_temp", "phone", "name"):
            hit = _re.search(rf"(?<![a-z0-9_]){_re.escape(banned)}(?![a-z0-9_])", lowered)
            assert hit is None, f"{banned} 컬럼이 스키마에 있으면 안 됨"

    @pytest.mark.asyncio
    async def test_관측시각_자동설정(self):
        captured = {}
        a = Archive("postgresql+asyncpg://x/y", enabled=True)
        a._ready = True

        async def fake_run(sql, vals, what):
            captured.update(vals)

        a._run = fake_run
        await a._insert_measurement({"lat": 35.1, "lon": 129.1})
        assert captured["observed_at"] is not None


class TestSchema:
    def test_필수_테이블_정의(self):
        from app.services.archive import DDL
        assert "CREATE TABLE IF NOT EXISTS measurement" in DDL
        assert "CREATE TABLE IF NOT EXISTS engine_check" in DDL

    def test_인덱스_정의(self):
        from app.services.archive import DDL
        assert "ix_measurement_time" in DDL      # 기간 조회
        assert "ix_measurement_cell" in DDL      # 격자 집계

    def test_재실행_안전(self):
        """배포 때마다 DDL이 다시 실행돼도 안전해야 한다."""
        from app.services.archive import DDL
        assert DDL.count("IF NOT EXISTS") >= 4
