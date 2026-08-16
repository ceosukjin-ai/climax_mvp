"""측정 이력 적재 — 핫스팟 자산 축적 (2026-08-11).

왜 만드나
---------
지금까지 서버는 체감기후를 계산해 응답만 하고 **아무것도 남기지 않았다**.
"언제 어디가 몇 도였는지"가 없으면 부산 폭염 지도도, B2G 제안의 근거도,
엔진 정확도 교정도 만들 수 없다. 지원사업으로 받은 PostGIS 도 비어 있었다.

무엇을 남기나
-------------
① measurement — 측정 1건: 좌표(격자 반올림)·시각·체감온도·위험등급·공간지표·기상값
② engine_check — 같은 시각의 실측(ASOS 지면온도 등) vs 엔진 추정 짝 → 계수 교정용

개인정보 원칙
-------------
- **개인 식별자를 저장하지 않는다.** 사용자 ID·기기 ID·나이·질환·생체값은 넣지 않는다.
- 좌표는 **약 25m 격자로 반올림**해 저장(원좌표 미보관) → 집을 특정하기 어렵게.
- 저장 여부는 설정으로 끌 수 있다(`ARCHIVE_ENABLED`). 일반 사용자 대상 수집은
  **동의 화면 + 개인정보 처리방침 개정 이후** 켠다. 그 전까지는 내부 테스트 데이터만 쌓인다.
- 적재 실패는 본 응답에 영향을 주지 않는다(조용히 무시).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

# 좌표 반올림 자릿수 — 4자리 ≈ 11m, 3자리 ≈ 110m.
# VSI 분석 단위(25m)와 개인정보 보호를 함께 고려해 4자리(≈11m) 사용.
COORD_PRECISION = 4

DDL = """
CREATE TABLE IF NOT EXISTS measurement (
    id          BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ  NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,   -- 격자 반올림 좌표
    lon         DOUBLE PRECISION NOT NULL,
    pvpti       REAL,                        -- 체감기후 (PET, °C)
    risk_level  TEXT,                        -- safe … severe (개인화 전 기준)
    air_temp    REAL,                        -- 기온
    humidity    REAL,
    wind_ms     REAL,
    mrt         REAL,                        -- 평균복사온도
    svf         REAL, gvi REAL, bvi REAL,    -- 공간 지표
    cloud       REAL,                        -- 전운량 [0,1]
    cloud_src   TEXT,                        -- 실측(일사)/실측(운량)/예보
    indoor      BOOLEAN DEFAULT FALSE,
    source      TEXT DEFAULT 'app'           -- app / test / batch
);
CREATE INDEX IF NOT EXISTS ix_measurement_time ON measurement (observed_at DESC);
CREATE INDEX IF NOT EXISTS ix_measurement_cell ON measurement (lat, lon);

CREATE TABLE IF NOT EXISTS engine_check (
    id            BIGSERIAL PRIMARY KEY,
    observed_at   TIMESTAMPTZ NOT NULL,
    station_id    INTEGER,
    obs_ground_c  REAL,     -- ASOS 실측 지면온도
    est_ground_c  REAL,     -- 엔진 추정 지표면온도
    obs_solar_mj  REAL,     -- ASOS 실측 일사
    obs_cloud     REAL,     -- ASOS 실측 전운량 [0,1]
    est_cloud     REAL,     -- 엔진이 사용한 운량
    air_temp      REAL,
    wind_ms       REAL,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS ix_engine_check_time ON engine_check (observed_at DESC);

-- 현장 실측 ↔ 엔진 산출 대조 (2026-08-16, 대표 전용 검증 도구)
-- 현장에서 잰 값(기온·습도·풍속·흑구·표면온도·PET)과, 같은 순간 같은 좌표의
-- 엔진 전체 산출을 짝으로 남긴다 → PWI 풍속 보정·MRT 검증·계수 교정의 원천 데이터.
CREATE TABLE IF NOT EXISTS field_check (
    id           BIGSERIAL PRIMARY KEY,
    observed_at  TIMESTAMPTZ NOT NULL,
    lat          DOUBLE PRECISION NOT NULL,
    lon          DOUBLE PRECISION NOT NULL,
    meas         JSONB,     -- 실측: {ta, rh, wind_ms, globe_c, surface_c, pet, ...}
    est          JSONB,     -- 엔진: {pvpti, mrt, tsurf, u_p, ta, rh, svf, gvi, cloud, ...}
    note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_field_check_time ON field_check (observed_at DESC);
"""


class Archive:
    """측정 이력 적재기. DB 가 없거나 꺼져 있으면 조용히 아무것도 하지 않는다."""

    def __init__(self, database_url: str, enabled: bool = True) -> None:
        self.enabled = enabled and bool(database_url)
        self._engine: AsyncEngine | None = None
        self._session = None
        self._url = database_url
        self._ready = False

    async def start(self) -> None:
        if not self.enabled:
            logger.info("[archive] 비활성 — 측정 이력을 저장하지 않습니다")
            return
        try:
            self._engine = create_async_engine(self._url, pool_size=3, max_overflow=2)
            self._session = async_sessionmaker(self._engine, expire_on_commit=False)
            # API 는 워커 여러 개로 뜬다. 워커들이 동시에 CREATE TABLE/INDEX 를 실행하면
            # IF NOT EXISTS 라도 pg_class 유니크 제약에서 충돌한다(2026-08-11 실서버 확인).
            # → 자문 잠금(advisory lock)으로 한 워커만 DDL 을 돌리고 나머지는 대기시킨다.
            async with self._engine.begin() as conn:
                await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                                   {"k": 8_150_811})     # 이 앱 전용 임의 상수
                for stmt in filter(None, (x.strip() for x in DDL.split(";"))):
                    await conn.execute(text(stmt))
            self._ready = True
            logger.info("[archive] 준비 완료 — 측정 이력 적재 시작")
        except Exception as e:  # noqa: BLE001
            logger.warning("[archive] 초기화 실패(측정은 정상 동작): {}: {}",
                           type(e).__name__, e or "(메시지 없음)")
            self._ready = False

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    # ── 적재 ────────────────────────────────────────────────
    def record_measurement(self, **kw) -> None:
        """백그라운드 적재 — 응답을 막지 않는다."""
        if not self._ready:
            return
        asyncio.create_task(self._insert_measurement(kw))

    def record_engine_check(self, **kw) -> None:
        if not self._ready:
            return
        asyncio.create_task(self._insert_engine_check(kw))

    def record_field_check(self, **kw) -> None:
        if not self._ready:
            return
        asyncio.create_task(self._insert_field_check(kw))

    async def _insert_measurement(self, kw: dict) -> None:
        kw.setdefault("observed_at", datetime.now(timezone.utc))
        kw["lat"] = round(float(kw["lat"]), COORD_PRECISION)
        kw["lon"] = round(float(kw["lon"]), COORD_PRECISION)
        cols = ("observed_at", "lat", "lon", "pvpti", "risk_level", "air_temp",
                "humidity", "wind_ms", "mrt", "svf", "gvi", "bvi",
                "cloud", "cloud_src", "indoor", "source")
        vals = {c: kw.get(c) for c in cols}
        sql = (f"INSERT INTO measurement ({', '.join(cols)}) "
               f"VALUES ({', '.join(':' + c for c in cols)})")
        await self._run(sql, vals, "measurement")

    async def _insert_engine_check(self, kw: dict) -> None:
        kw.setdefault("observed_at", datetime.now(timezone.utc))
        cols = ("observed_at", "station_id", "obs_ground_c", "est_ground_c",
                "obs_solar_mj", "obs_cloud", "est_cloud", "air_temp", "wind_ms", "note")
        vals = {c: kw.get(c) for c in cols}
        sql = (f"INSERT INTO engine_check ({', '.join(cols)}) "
               f"VALUES ({', '.join(':' + c for c in cols)})")
        await self._run(sql, vals, "engine_check")

    async def _insert_field_check(self, kw: dict) -> None:
        import json as _json

        kw.setdefault("observed_at", datetime.now(timezone.utc))
        vals = {
            "observed_at": kw["observed_at"],
            "lat": round(float(kw["lat"]), COORD_PRECISION),
            "lon": round(float(kw["lon"]), COORD_PRECISION),
            "meas": _json.dumps(kw.get("meas") or {}, ensure_ascii=False),
            "est": _json.dumps(kw.get("est") or {}, ensure_ascii=False),
            "note": kw.get("note"),
        }
        sql = ("INSERT INTO field_check (observed_at, lat, lon, meas, est, note) "
               "VALUES (:observed_at, :lat, :lon, CAST(:meas AS JSONB), "
               "CAST(:est AS JSONB), :note)")
        await self._run(sql, vals, "field_check")

    async def _run(self, sql: str, vals: dict, what: str) -> None:
        try:
            async with self._session() as s:
                await s.execute(text(sql), vals)
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[archive] {} 적재 실패: {}: {}", what, type(e).__name__, e)

    # ── 집계 조회 (B2G·연구용) ───────────────────────────────
    async def hotspots(self, hours: int = 24, min_samples: int = 1,
                       limit: int = 500) -> list[dict]:
        """격자별 평균·최고 체감온도. min_samples 로 소표본 격자를 걸러 재식별을 막는다."""
        if not self._ready:
            return []
        sql = """
        SELECT lat, lon,
               COUNT(*)          AS n,
               ROUND(AVG(pvpti)::numeric, 1) AS avg_pvpti,
               ROUND(MAX(pvpti)::numeric, 1) AS max_pvpti,
               ROUND(AVG(air_temp)::numeric, 1) AS avg_air,
               ROUND(AVG(svf)::numeric, 2)  AS avg_svf,
               ROUND(AVG(gvi)::numeric, 2)  AS avg_gvi
        FROM measurement
        WHERE observed_at > NOW() - make_interval(hours => :hours)
          AND indoor = FALSE AND pvpti IS NOT NULL
        GROUP BY lat, lon
        HAVING COUNT(*) >= :min_samples
        ORDER BY avg_pvpti DESC
        LIMIT :limit
        """
        try:
            async with self._session() as s:
                rows = (await s.execute(text(sql), {
                    "hours": hours, "min_samples": min_samples, "limit": limit})).mappings()
                return [dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.warning("[archive] 집계 조회 실패: {}: {}", type(e).__name__, e)
            return []

    async def stats(self) -> dict:
        """적재 현황 — 데이터가 실제로 쌓이는지 눈으로 확인하는 용도."""
        if not self._ready:
            return {"enabled": False}
        try:
            async with self._session() as s:
                m = (await s.execute(text(
                    "SELECT COUNT(*) n, MIN(observed_at) first, MAX(observed_at) last,"
                    " COUNT(DISTINCT (lat, lon)) cells FROM measurement"))).mappings().first()
                e = (await s.execute(text(
                    "SELECT COUNT(*) n, MAX(observed_at) last FROM engine_check"))).mappings().first()
                return {"enabled": True,
                        "measurement": dict(m) if m else {},
                        "engine_check": dict(e) if e else {}}
        except Exception as ex:  # noqa: BLE001
            return {"enabled": True, "error": f"{type(ex).__name__}: {ex}"}
