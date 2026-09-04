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
    source      TEXT DEFAULT 'app',          -- app / test / batch
    imagery_src TEXT                         -- 공간지표(svf/gvi/bvi)의 영상 출처
                                             -- gsv / mapillary / own — app/data_policy.py
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

# 이미 만들어진 테이블에 컬럼을 덧붙이는 변경분. DDL 과 분리해 둔다 —
# CREATE TABLE IF NOT EXISTS 는 기존 테이블의 스키마를 갱신하지 않기 때문.
# ALTER … IF NOT EXISTS 이므로 몇 번 실행돼도 안전하다.
MIGRATIONS = """
ALTER TABLE measurement ADD COLUMN IF NOT EXISTS imagery_src TEXT;
-- 연령대(10년 구간, '60s' 등) — 개인 나이가 아니라 구간. 취약군 분석용 (2026-09-05).
-- 개인 식별 불가(구간+11m 격자+시각으로는 특정 불가). 방침 제2조 "익명 측정 기록" 항목에 반영할 것.
ALTER TABLE measurement ADD COLUMN IF NOT EXISTS age_band TEXT;
CREATE INDEX IF NOT EXISTS ix_measurement_imagery ON measurement (imagery_src);
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
                for stmt in filter(None, (x.strip() for x in MIGRATIONS.split(";"))):
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
                "cloud", "cloud_src", "indoor", "source", "imagery_src", "age_band")
        kw.setdefault("imagery_src", "gsv")   # 미지정이면 보수적으로 GSV 취급
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

    # ── 조회 (2026-08-16, 대표 전용 '뇌 상태' 화면용) ──────────
    async def brain_stats(self) -> dict | None:
        """쌓인 학습 데이터 현황 — 눈에 보이는 뇌.

        적재는 fire-and-forget 이지만 조회는 응답이 필요하므로 동기 대기한다.
        DB가 없으면 None (화면에서 '아직 연결 안 됨' 표시).
        """
        if not self._ready:
            return None
        try:
            async with self._session() as s:
                counts = {}
                for t in ("measurement", "engine_check", "field_check"):
                    r = await s.execute(text(f"SELECT COUNT(*) FROM {t}"))
                    counts[t] = int(r.scalar() or 0)

                # Tsurf 잔차 — 최근 48h, 실측·추정 둘 다 있는 것만
                r = await s.execute(text(
                    "SELECT observed_at, obs_ground_c, est_ground_c "
                    "FROM engine_check "
                    "WHERE obs_ground_c IS NOT NULL AND est_ground_c IS NOT NULL "
                    "AND observed_at > NOW() - INTERVAL '48 hours' "
                    "ORDER BY observed_at DESC LIMIT 48"
                ))
                tsurf = [
                    {"t": row[0].isoformat(), "obs": round(float(row[1]), 1),
                     "est": round(float(row[2]), 1),   # REAL(float4) 변환 꼬리 제거
                     "resid": round(float(row[2]) - float(row[1]), 1)}
                    for row in r
                ]

                r = await s.execute(text(
                    "SELECT observed_at, lat, lon, meas, est, note "
                    "FROM field_check ORDER BY observed_at DESC LIMIT 5"
                ))
                field = [
                    {"t": row[0].isoformat(), "lat": row[1], "lon": row[2],
                     "meas": row[3], "est": row[4], "note": row[5]}
                    for row in r
                ]
                return {"counts": counts, "tsurf": tsurf, "field": field}
        except Exception as e:  # noqa: BLE001
            logger.warning("[archive] brain_stats 실패: {}: {}", type(e).__name__, e)
            return None

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

    # ── 학습 데이터셋 조회 (약관 필터 적용, 2026-08-21) ──────
    async def ml_dataset(self, hours: int | None = None,
                         limit: int = 100_000) -> list[dict]:
        """머신러닝 학습에 **써도 되는** 측정 행만 반환한다.

        위성 학생 모델(교사·학생 지식증류)은 반드시 이 메서드로 데이터를 받아야 한다.
        Google Street View 유래 공간지표는 약관 3.2.3(c)(vii)에 의해 모델 학습·
        테스트·검증·파인튜닝에 사용할 수 없으므로 SQL 단계에서 배제한다.

        원천을 Mapillary·자체 촬영으로 교체하기 전까지는 **빈 목록이 정상**이다.
        (그래서 조용히 0건을 돌려주지 않고 로그로 남긴다.)
        """
        if not self._ready:
            return []
        from app.data_policy import ML_TRAINABLE_SOURCES

        allowed = sorted(ML_TRAINABLE_SOURCES)
        sql = """
        SELECT observed_at, lat, lon, pvpti, air_temp, humidity, wind_ms, mrt,
               svf, gvi, bvi, cloud, cloud_src, imagery_src
        FROM measurement
        WHERE pvpti IS NOT NULL AND indoor = FALSE
          AND imagery_src = ANY(:allowed)
        """
        params: dict = {"allowed": allowed, "limit": limit}
        if hours is not None:
            sql += " AND observed_at > NOW() - make_interval(hours => :hours)"
            params["hours"] = hours
        sql += " ORDER BY observed_at DESC LIMIT :limit"
        try:
            async with self._session() as s:
                rows = [dict(r) for r in
                        (await s.execute(text(sql), params)).mappings()]
            total = await self._count_measurements()
            logger.info(
                "[archive] ML 학습 가능 측정 {}건 / 전체 {}건 "
                "(허용 출처 {} — GSV 유래는 약관상 학습 불가로 제외)",
                len(rows), total, allowed,
            )
            return rows
        except Exception as e:  # noqa: BLE001
            logger.warning("[archive] ml_dataset 조회 실패: {}: {}", type(e).__name__, e)
            return []

    # ── 관리자 대시보드 집계 (2026-09-04, 대표 전용) ────────
    async def dashboard(self, hours: int = 24 * 7, min_samples: int = 1) -> dict:
        """대시보드 한 화면에 필요한 집계를 한 번에.

        시간 축은 모두 **한국시간(Asia/Seoul)** 으로 잘라 준다 — 폭염은 낮에 오고
        운영자는 한국에 있다. 개인 식별자는 원천에 없으므로 여기서도 없다.
        """
        if not self._ready:
            return {"enabled": False}
        out: dict = {"enabled": True, "hours": hours}
        try:
            async with self._session() as s:
                # 총계
                m = (await s.execute(text(
                    "SELECT COUNT(*) n, MIN(observed_at) first, MAX(observed_at) last,"
                    " COUNT(DISTINCT (lat, lon)) cells,"
                    " COUNT(*) FILTER (WHERE observed_at >= date_trunc('day', NOW() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul') today,"
                    " COUNT(*) FILTER (WHERE indoor) indoor_n"
                    " FROM measurement"))).mappings().first()
                e = (await s.execute(text(
                    "SELECT COUNT(*) n, MAX(observed_at) last FROM engine_check"))).mappings().first()
                f = (await s.execute(text(
                    "SELECT COUNT(*) n FROM field_check"))).mappings().first()
                out["totals"] = {
                    "measurement": int(m["n"] or 0), "today": int(m["today"] or 0),
                    "cells": int(m["cells"] or 0), "indoor": int(m["indoor_n"] or 0),
                    "first": m["first"].isoformat() if m["first"] else None,
                    "last": m["last"].isoformat() if m["last"] else None,
                    "engine_check": int(e["n"] or 0),
                    "engine_check_last": e["last"].isoformat() if e["last"] else None,
                    "field_check": int(f["n"] or 0),
                }
                # 일별 적재 (최근 30일, KST)
                r = await s.execute(text(
                    "SELECT to_char(observed_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD') d,"
                    " COUNT(*) n, ROUND(AVG(pvpti)::numeric,1) avg_pvpti, ROUND(MAX(pvpti)::numeric,1) max_pvpti"
                    " FROM measurement WHERE observed_at > NOW() - INTERVAL '30 days'"
                    " GROUP BY d ORDER BY d"))
                out["daily"] = [dict(x) for x in r.mappings()]
                # 시간대별 평균 체감 (창 내, 실외만, KST 시)
                r = await s.execute(text(
                    "SELECT EXTRACT(HOUR FROM observed_at AT TIME ZONE 'Asia/Seoul')::int h,"
                    " COUNT(*) n, ROUND(AVG(pvpti)::numeric,1) avg_pvpti, ROUND(MAX(pvpti)::numeric,1) max_pvpti"
                    " FROM measurement WHERE observed_at > NOW() - make_interval(hours => :hours)"
                    " AND indoor = FALSE AND pvpti IS NOT NULL GROUP BY h ORDER BY h"),
                    {"hours": hours})
                out["hourly"] = [dict(x) for x in r.mappings()]
                # 위험등급 분포 (창 내)
                r = await s.execute(text(
                    "SELECT COALESCE(risk_level,'unknown') level, COUNT(*) n"
                    " FROM measurement WHERE observed_at > NOW() - make_interval(hours => :hours)"
                    " GROUP BY level"), {"hours": hours})
                out["risk"] = {x["level"]: int(x["n"]) for x in r.mappings()}
                # 연령대 분포 (창 내) — 구버전 앱은 NULL → '미상'
                r = await s.execute(text(
                    "SELECT COALESCE(age_band,'unknown') band, COUNT(*) n,"
                    " ROUND(AVG(pvpti)::numeric,1) avg_pvpti"
                    " FROM measurement WHERE observed_at > NOW() - make_interval(hours => :hours)"
                    " GROUP BY band ORDER BY band"), {"hours": hours})
                out["age"] = [dict(x) for x in r.mappings()]
                # 지면온도 실측↔추정 (최근 7일)
                r = await s.execute(text(
                    "SELECT observed_at, obs_ground_c, est_ground_c, air_temp FROM engine_check"
                    " WHERE obs_ground_c IS NOT NULL AND est_ground_c IS NOT NULL"
                    " AND observed_at > NOW() - INTERVAL '7 days' ORDER BY observed_at"))
                out["tsurf"] = [
                    {"t": row[0].isoformat(), "obs": round(float(row[1]), 1),
                     "est": round(float(row[2]), 1),
                     "air": (round(float(row[3]), 1) if row[3] is not None else None)}
                    for row in r]
            out["hotspots"] = await self.hotspots(hours=hours, min_samples=min_samples, limit=1500)
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("[archive] dashboard 집계 실패: {}: {}", type(e).__name__, e)
            out["error"] = f"{type(e).__name__}: {e}"
            return out

    async def _count_measurements(self) -> int:
        try:
            async with self._session() as s:
                r = await s.execute(text("SELECT COUNT(*) FROM measurement"))
                return int(r.scalar() or 0)
        except Exception:  # noqa: BLE001
            return -1

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
