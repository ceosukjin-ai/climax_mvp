"""스카이라인 격자 — 공간지표 사전계산 (2026-09-10, 대표 결정: 정확·즉시·경로).

왜
---
건물은 안 움직인다. 요청마다 V-World 호출 + 광선투사(0.3~0.8s)를 하던 것을, 11m 격자마다
**지평선 상승각 72개(방위 5°)** 를 한 번 계산해 PostGIS 에 넣어 두면
  · SVF      = 1 − mean(sin²β)                        즉시
  · 볕/그늘  = horizon[태양방위] > 태양고도            즉시 (건물 폴리곤 재조회 없음)
  · 가로폭·H/W·도로축                                  저장값
쾌적경로(경로당 수백 점)는 `WHERE cell_id = ANY(...)` 한 번으로 수십 ms.
격자 계산은 배치(`scripts/build_skyline_grid.py`)가 하고, 실시간 경로는 조회 → 미스면 기존 계산.

정확도 원칙은 오늘 검증된 그대로: 가로 중심선 스냅 + 도로축 ±4m 중앙값(실측 80점 r .31→.40, bias 0).
층수 결측 건물은 보수적 2층(개활 과대 방지). 원천 교체(GIS건물통합+건축물대장)는 재계산으로 흡수.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

AZ_STEP = 5                      # 방위 간격 [deg] → 72칸
N_AZ = 360 // AZ_STEP
SVF_AZ_STEP = 2                  # SVF 는 2° 로 계산(svf_geometric 과 동일)
EYE_M = 1.5

DDL = """
CREATE TABLE IF NOT EXISTS skyline_grid (
    cell_id     TEXT PRIMARY KEY,           -- 'lat4:lon4' (11m 격자)
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    horizon     SMALLINT[] NOT NULL,        -- 72개, 상승각 ×10 (0.1° 단위), 방위 0=북 시계방향
    svf         REAL NOT NULL,
    bvi         REAL,
    width_m     REAL,
    hw_ratio    REAL,
    axis_deg    REAL,
    n_bld       INTEGER,
    centered_m  REAL,
    src         TEXT,                       -- vworld | local | gis-bldg …
    built_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_skyline_latlon ON skyline_grid (lat, lon);
"""


def cell_id(lat: float, lon: float) -> str:
    return f"{round(lat, 4):.4f}:{round(lon, 4):.4f}"


@dataclass
class Skyline:
    cell_id: str
    lat: float
    lon: float
    horizon: list[float]           # deg, 길이 N_AZ
    svf: float
    bvi: float | None
    width_m: float | None
    hw_ratio: float | None
    axis_deg: float | None
    n_bld: int
    centered_m: float
    src: str

    def is_sun_blocked(self, sun_az_deg: float, sun_el_deg: float) -> bool:
        if sun_el_deg <= 0.0:
            return False
        i = int(round((sun_az_deg % 360) / AZ_STEP)) % N_AZ
        # 이웃 칸과 보간(태양 원반·격자 오차 완화)
        h = max(self.horizon[i], 0.5 * (self.horizon[(i - 1) % N_AZ] + self.horizon[(i + 1) % N_AZ]))
        return sun_el_deg < h


# ───────────────────────── 계산 (순수 함수, 건물 rings 입력) ─────────────────────────

def _horizon_from_rings(rings: list, az_step: int, default_floors: int = 2) -> tuple[list[float], int]:
    """원점(0,0)에서 방위별 최대 상승각 [deg]. rings = [(ring_xy, props)] (geo._rings_cached 형식)."""
    from app.services.geo import _height_m_from_props, _point_in_ring, _ray_ring_hit
    blds = []
    for ring, props in rings:
        if len(ring) < 4 or _point_in_ring(0.0, 0.0, ring):
            continue
        H = _height_m_from_props(props, default_floors=default_floors)
        if H is None or H - EYE_M <= 0:
            continue
        blds.append((ring, H - EYE_M))
    n = 360 // az_step
    out = [0.0] * n
    for i in range(n):
        az = math.radians(i * az_step)
        dx, dy = math.sin(az), math.cos(az)
        bmax = 0.0
        for ring, h in blds:
            t = _ray_ring_hit(dx, dy, ring)
            if t is not None:
                b = math.degrees(math.atan2(h, t))
                if b > bmax:
                    bmax = b
        out[i] = bmax
    return out, len(blds)


def _svf_from_horizon(h: list[float]) -> float:
    return max(0.0, min(1.0, 1.0 - sum(math.sin(math.radians(b)) ** 2 for b in h) / len(h)))


def compute_skyline_from_rings(lat: float, lon: float, rings: list, src: str) -> Skyline:
    """오늘 검증된 절차 그대로: 건물 밖으로 스냅 → 가로 중심선 스냅 → 도로축 ±4m 3점 중앙값(SVF).
    horizon 은 중심점에서 5°, SVF 는 2° 로 계산(svf_geometric 과 동일 값)."""
    from app.services.geo import _snap_outside, _snap_to_street_center, _shift_rings, street_width_geometric  # noqa: F401
    if not rings:
        return Skyline(cell_id(lat, lon), lat, lon, [0.0] * N_AZ, 1.0, 0.0, None, None, None, 0, 0.0, src)
    rings, _snapped = _snap_outside(rings)
    rings, centered, axis = _snap_to_street_center(rings)
    horizon5, n_bld = _horizon_from_rings(rings, AZ_STEP)
    if axis is not None:
        vals = []
        ax = math.radians(axis)
        for off in (-4.0, 0.0, 4.0):
            rr, _ = _snap_outside(_shift_rings(rings, math.sin(ax) * off, math.cos(ax) * off))
            vals.append(_svf_from_horizon(_horizon_from_rings(rr, SVF_AZ_STEP)[0]))
        vals.sort()
        svf = vals[1]
    else:
        svf = _svf_from_horizon(_horizon_from_rings(rings, SVF_AZ_STEP)[0])
    # 가로폭·H/W — 중심점에서 마주보는 광선쌍 최소합 (street_width_geometric 과 같은 정의)
    width, hw = _width_from_rings(rings)
    bvi = max(0.0, 1.0 - svf)      # 위성 GVI 가 나중에 빼 간다(orchestrator._analyze_geometry 와 동일)
    return Skyline(cell_id(lat, lon), lat, lon, horizon5, round(svf, 3), round(bvi, 3),
                   width, hw, axis, n_bld, round(centered, 1), src)


def _width_from_rings(rings: list, az_step: int = 5, max_m: float = 60.0) -> tuple[float | None, float | None]:
    from app.services.geo import _height_m_from_props, _ray_ring_hit
    n = 360 // az_step
    d = [None] * n
    h = [0.0] * n
    for i in range(n):
        az = math.radians(i * az_step)
        dx, dy = math.sin(az), math.cos(az)
        for ring, props in rings:
            if len(ring) < 4:
                continue
            t = _ray_ring_hit(dx, dy, ring)
            if t is not None and t <= max_m and (d[i] is None or t < d[i]):
                d[i] = t
                h[i] = _height_m_from_props(props, default_floors=2) or 0.0
    best = None
    for i in range(n // 2):
        j = i + n // 2
        if d[i] is None or d[j] is None:
            continue
        w = d[i] + d[j]
        if best is None or w < best[0]:
            best = (w, (h[i] + h[j]) / 2.0)
    if best is None:
        return None, None
    w, hm = best
    return round(w, 1), (round(hm / w, 2) if w > 0 else None)


# ───────────────────────── 저장·조회 (asyncpg) ─────────────────────────

_pool = None
_pool_lock = asyncio.Lock()
_DISABLED = False


async def _get_pool():
    global _pool, _DISABLED
    if _pool is not None or _DISABLED:
        return _pool
    async with _pool_lock:
        if _pool is not None or _DISABLED:
            return _pool
        try:
            import asyncpg
            from app.config import get_settings
            url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
            _pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
            async with _pool.acquire() as c:
                for stmt in filter(None, (x.strip() for x in DDL.split(";"))):
                    await c.execute(stmt)
            logger.info("[skyline] 격자 테이블 준비")
        except Exception as e:  # noqa: BLE001
            logger.warning("[skyline] DB 미사용(실시간 계산으로 폴백): {}: {}", type(e).__name__, e)
            _DISABLED = True
            _pool = None
    return _pool


def _row_to_skyline(r) -> Skyline:
    return Skyline(r["cell_id"], float(r["lat"]), float(r["lon"]), [v / 10.0 for v in r["horizon"]],
                   float(r["svf"]), r["bvi"], r["width_m"], r["hw_ratio"], r["axis_deg"],
                   int(r["n_bld"] or 0), float(r["centered_m"] or 0.0), r["src"] or "")


async def get_cell(lat: float, lon: float) -> Skyline | None:
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            r = await c.fetchrow("SELECT * FROM skyline_grid WHERE cell_id=$1", cell_id(lat, lon))
        return _row_to_skyline(r) if r else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[skyline] 조회 실패: {}", e)
        return None


async def get_cells(points: list[tuple[float, float]]) -> dict[str, Skyline]:
    """경로용 일괄 조회."""
    pool = await _get_pool()
    if pool is None or not points:
        return {}
    ids = list({cell_id(a, b) for a, b in points})
    try:
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM skyline_grid WHERE cell_id = ANY($1::text[])", ids)
        return {r["cell_id"]: _row_to_skyline(r) for r in rows}
    except Exception as e:  # noqa: BLE001
        logger.warning("[skyline] 일괄 조회 실패: {}", e)
        return {}


async def upsert(sk: Skyline) -> bool:
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            await c.execute(
                "INSERT INTO skyline_grid (cell_id, lat, lon, horizon, svf, bvi, width_m, hw_ratio, axis_deg, n_bld, centered_m, src, built_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) "
                "ON CONFLICT (cell_id) DO UPDATE SET horizon=EXCLUDED.horizon, svf=EXCLUDED.svf, bvi=EXCLUDED.bvi, "
                "width_m=EXCLUDED.width_m, hw_ratio=EXCLUDED.hw_ratio, axis_deg=EXCLUDED.axis_deg, n_bld=EXCLUDED.n_bld, "
                "centered_m=EXCLUDED.centered_m, src=EXCLUDED.src, built_at=EXCLUDED.built_at",
                sk.cell_id, sk.lat, sk.lon, [int(round(h * 10)) for h in sk.horizon], sk.svf, sk.bvi,
                sk.width_m, sk.hw_ratio, sk.axis_deg, sk.n_bld, sk.centered_m, sk.src, datetime.now(timezone.utc))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[skyline] 저장 실패: {}", e)
        return False


async def compute_and_store(lat: float, lon: float) -> Skyline | None:
    """실시간 미스 → 건물 조회(기존 캐시 경로) → 계산 → 저장. 배치와 같은 함수."""
    from app.services.geo import _rings_cached
    rings, src = await _rings_cached(lat, lon)
    sk = compute_skyline_from_rings(lat, lon, rings, src or "none")
    await upsert(sk)
    return sk
