#!/usr/bin/env python3
"""뇌 v1 — 새벽 자동 학습 루프, Day 1: 채점판 + 약점 리포트 (2026-09-10).

매일 새벽 현재 활성 뇌(물리 엔진 + 잔차 AI)를 가진 채점 재료 전부로 채점하고,
어디가 약한지·어디를 재면 가장 많이 배우는지 리포트를 남긴다. Day 1 은 dry-run —
재학습·승격은 하지 않고 brain_version 에 'scorecard' 행만 남긴다(엔진 영향 0).

재료
  ① data/tier3_engine_output_80_v7.csv (+ tier3_width_80.csv 폭등급)  실측 80점, 시드
  ② field_check 테이블                                                현장실측 vs 엔진
  ③ engine_check 테이블(최근 90일)                                    ASOS 지면온도 vs 엔진
  ④ measurement 테이블 + data/momssi_geo_form.csv                    능동학습 후보(어디를 잴까)

실행(서버 컨테이너):
  cd ~/climax_mvp && docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml \
    run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/brain_nightly.py
옵션: --no-db (DB 없이 CSV만), --out DIR (기본 /repo/docs/brain)
출력: docs/brain/YYYY-MM-DD.md, brain_version 행 1개(kind='scorecard', promoted=false)
"""
from __future__ import annotations
import argparse, asyncio, csv, json, math, os, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

ROOT = os.environ.get("CLIMAX_REPO", "/repo")
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, "/app")
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime("%Y-%m-%d")

try:
    from vpti_core.pet_residual import apply_pet_residual, FEATURES, TRAINED_ON
except Exception as e:  # noqa: BLE001
    print(f"pet_residual 불러오기 실패: {e}"); sys.exit(1)

DDL = """
CREATE TABLE IF NOT EXISTS brain_version (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind        TEXT NOT NULL,            -- scorecard | ground | residual
    params      JSONB,                    -- 계수/모델 (scorecard 는 NULL)
    metrics     JSONB,                    -- 채점 결과
    n_train     INTEGER,
    promoted    BOOLEAN NOT NULL DEFAULT FALSE,
    reason      TEXT
);
CREATE INDEX IF NOT EXISTS ix_brain_version_time ON brain_version (created_at DESC);
"""


# ── 통계 도우미 ─────────────────────────────────────────────
def fnum(x, default=None):
    try:
        v = float(x); return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def stats(pairs):
    """pairs = [(obs, est)] → dict(n, mae, bias, r, hot_hit, hot_n)"""
    p = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(p)
    if n == 0:
        return {"n": 0}
    e = [b - a for a, b in p]
    mae = sum(map(abs, e)) / n; bias = sum(e) / n
    r = float("nan")
    if n >= 3:
        mo = sum(a for a, _ in p) / n; mr = sum(b for _, b in p) / n
        cov = sum((a - mo) * (b - mr) for a, b in p)
        so = math.sqrt(sum((a - mo) ** 2 for a, _ in p)); sr = math.sqrt(sum((b - mr) ** 2 for _, b in p))
        r = cov / (so * sr) if so > 0 and sr > 0 else float("nan")
    hot = [(a, b) for a, b in p if a >= 41]
    return {"n": n, "mae": round(mae, 2), "bias": round(bias, 2),
            "r": None if math.isnan(r) else round(r, 3),
            "hot_hit": sum(1 for a, b in hot if b >= 41), "hot_n": len(hot)}


def fmt(s):
    if s.get("n", 0) == 0:
        return "n=0"
    r = "—" if s["r"] is None else f"{s['r']:+.2f}"
    return f"n={s['n']:3d}  MAE {s['mae']:5.2f}  bias {s['bias']:+6.2f}  r {r}  극심 {s['hot_hit']}/{s['hot_n']}"


def hour_band(h):
    return "오전(6-11)" if 6 <= h < 11 else "정오(11-14)" if 11 <= h < 14 else "오후(14-18)" if 14 <= h < 18 else "야간"


# ── ① 실측 80점 채점 ────────────────────────────────────────
def score_tier3():
    path = os.path.join(ROOT, "data", "tier3_engine_output_80_v7.csv")
    if not os.path.exists(path):
        return None, ["tier3 CSV 없음"]
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    width = {}
    wp = os.path.join(ROOT, "data", "tier3_width_80.csv")
    if os.path.exists(wp):
        width = {r["측정ID"]: r.get("폭등급", "") for r in csv.DictReader(open(wp, encoding="utf-8-sig"))}
    recs = []
    for r in rows:
        feats = {k: fnum(r.get(k)) for k in FEATURES}
        phys = fnum(r["run_C_PET"]); obs = fnum(r["PET"])
        ai, applied, conf = apply_pet_residual(phys, feats)
        h = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S").hour
        recs.append({"id": r["측정ID"], "권역": r["권역"], "obs": obs, "phys": phys, "ai": ai, "conf": conf,
                     "볕": "볕" if r["볕"].strip() == "1" else "그늘", "시간대": hour_band(h),
                     "폭등급": width.get(r["측정ID"], "?"),
                     "conf구간": "c=1" if conf >= 0.999 else "0<c<1" if conf > 0 else "c=0(물리만)"})
    out = {"전체": {"물리": stats([(x["obs"], x["phys"]) for x in recs]),
                   "물리+AI": stats([(x["obs"], x["ai"]) for x in recs])},
           "조각": {}}
    for key in ("권역", "폭등급", "시간대", "볕", "conf구간"):
        groups = defaultdict(list)
        for x in recs:
            groups[x[key]].append(x)
        out["조각"][key] = {g: {"물리": stats([(x["obs"], x["phys"]) for x in v]),
                              "물리+AI": stats([(x["obs"], x["ai"]) for x in v])}
                           for g, v in sorted(groups.items())}
    # 주의: 잔차 모델이 이 80점으로 학습됐으므로 '물리+AI'는 재대입(낙관) — LOSO 값은 train 스크립트 참조
    return out, []


# ── ②③④ DB 재료 ───────────────────────────────────────────
async def db_materials(conn):
    notes = []
    await conn.execute(DDL)
    # ② field_check
    fc = await conn.fetch("SELECT observed_at, lat, lon, meas, est, note FROM field_check ORDER BY observed_at")
    fc_pairs, fc_by = [], defaultdict(lambda: defaultdict(list))
    for r in fc:
        meas = r["meas"] if isinstance(r["meas"], dict) else json.loads(r["meas"] or "{}")
        est = r["est"] if isinstance(r["est"], dict) else json.loads(r["est"] or "{}")
        o, e = fnum(meas.get("pet")), fnum(est.get("pvpti"))
        if o is None or e is None:
            continue
        fc_pairs.append((o, e))
        t = r["observed_at"].astimezone(KST)
        fc_by["월"][t.strftime("%Y-%m")].append((o, e))
        fc_by["시간대"][hour_band(t.hour)].append((o, e))
        fc_by["출처"][str(meas.get("source", "field"))].append((o, e))
    field = {"전체": stats(fc_pairs), "조각": {k: {g: stats(v) for g, v in sorted(d.items())} for k, d in fc_by.items()}}
    # ③ engine_check (90일)
    ec = await conn.fetch("SELECT observed_at, obs_ground_c, est_ground_c, obs_cloud, est_cloud "
                          "FROM engine_check WHERE observed_at > NOW() - INTERVAL '90 days' "
                          "AND obs_ground_c IS NOT NULL AND est_ground_c IS NOT NULL")
    g_pairs, g_by = [], defaultdict(list)
    c_pairs = []
    for r in ec:
        g_pairs.append((float(r["obs_ground_c"]), float(r["est_ground_c"])))
        t = r["observed_at"].astimezone(KST)
        g_by[hour_band(t.hour)].append(g_pairs[-1])
        if r["obs_cloud"] is not None and r["est_cloud"] is not None:
            c_pairs.append((float(r["obs_cloud"]), float(r["est_cloud"])))
    ground = {"전체": stats(g_pairs), "시간대": {g: stats(v) for g, v in sorted(g_by.items())}, "운량": stats(c_pairs)}
    # ④ 능동학습 후보: 측정 많은 격자 중 실측 없는 곳
    ms = await conn.fetch("SELECT ROUND(lat::numeric,3) la, ROUND(lon::numeric,3) lo, COUNT(*) n, AVG(pvpti) p "
                          "FROM measurement WHERE indoor=FALSE AND pvpti IS NOT NULL "
                          "GROUP BY 1,2 HAVING COUNT(*) >= 5 ORDER BY n DESC LIMIT 400")
    fpts = [(float(r["lat"]), float(r["lon"])) for r in fc]
    form = {}
    fp = os.path.join(ROOT, "data", "momssi_geo_form.csv")
    if os.path.exists(fp):
        for r in csv.DictReader(open(fp, encoding="utf-8-sig")):
            form[(round(fnum(r["lat"]), 3), round(fnum(r["lon"]), 3))] = r.get("폭등급", "")
    cands = []
    for r in ms:
        la, lo = float(r["la"]), float(r["lo"])
        near = any(abs(la - a) < 0.0015 and abs(lo - b) < 0.0018 for a, b in fpts)  # ≈150m
        if near:
            continue
        w = form.get((la, lo), "?")
        rare = 3 if "6m" in w and "<" in w else 2 if "6" in w and "12" in w else 1 if "개방" in w else 0  # 골목이 학습셋에 적음
        cands.append({"lat": la, "lon": lo, "n": int(r["n"]), "pvpti": round(float(r["p"] or 0), 1), "폭등급": w,
                      "score": int(r["n"]) * (1 + rare)})
    cands.sort(key=lambda x: -x["score"])
    counts = {t: (await conn.fetchval(f"SELECT COUNT(*) FROM {t}")) for t in ("measurement", "field_check", "engine_check")}
    return field, ground, cands[:10], counts, notes


# ── 리포트 ─────────────────────────────────────────────────
def render(tier3, field, ground, cands, counts, notes):
    L = [f"# 뇌 일일 리포트 {TODAY} (v1 · Day1 dry-run)", "",
         f"활성 잔차 모델: {TRAINED_ON}", f"DB 건수: {counts}" if counts else "DB: 미접속 또는 조회 실패(메모 참조)", ""]
    if tier3:
        L += ["## ① 실측 80점 (시드) — 물리 vs 물리+AI  ※AI는 재대입값(낙관), 전이 성능은 LOSO 2.69",
              f"  물리      {fmt(tier3['전체']['물리'])}", f"  물리+AI   {fmt(tier3['전체']['물리+AI'])}", ""]
        for key, d in tier3["조각"].items():
            L.append(f"### {key}별")
            for g, s in d.items():
                L.append(f"  {g:14s} 물리 {fmt(s['물리'])}")
                L.append(f"  {'':14s} +AI  {fmt(s['물리+AI'])}")
            L.append("")
        # 약점 자동 추출: 조각별 +AI MAE 상위
        weak = []
        for key, d in tier3["조각"].items():
            for g, s in d.items():
                if 5 <= s["물리+AI"].get("n", 0) < tier3["전체"]["물리+AI"]["n"]:
                    weak.append((s["물리+AI"]["mae"], f"{key}={g}", s["물리+AI"]["n"], s["물리+AI"]["bias"]))
        weak.sort(reverse=True)
        L += ["## 약점 (조각별 +AI MAE 상위 5, n≥5)"] + [f"  MAE {m:.2f}  {g}  (n={n}, bias {b:+.2f})" for m, g, n, b in weak[:5]] + [""]
    if field is not None:
        L += ["## ② field_check (현장실측 vs 엔진 pvpti)", f"  전체 {fmt(field['전체'])}"]
        for k, d in field["조각"].items():
            for g, s in d.items():
                L.append(f"  {k}={g:12s} {fmt(s)}")
        L.append("")
    if ground is not None:
        L += ["## ③ engine_check 90일 (ASOS 지면온도 vs 엔진)", f"  전체 {fmt(ground['전체'])}"]
        for g, s in ground["시간대"].items():
            L.append(f"  {g:12s} {fmt(s)}")
        L += [f"  운량(실측 vs 엔진) {fmt(ground['운량'])}", ""]
    if cands:
        L += ["## ④ 여기를 재면 가장 많이 배운다 (측정 많고 실측 없는 격자, 골목 우선)"]
        for c in cands:
            L.append(f"  {c['lat']:.3f},{c['lon']:.3f}  측정 {c['n']:4d}건  평균 PET {c['pvpti']}  {c['폭등급']}")
        L.append("")
    if notes:
        L += ["## 메모"] + [f"  - {n}" for n in notes]
    L += ["", "승격: 없음 (Day1 dry-run — 재학습·승격은 Day2)"]
    return "\n".join(L) + "\n"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-db", action="store_true"); ap.add_argument("--out", default=os.path.join(ROOT, "docs", "brain"))
    a = ap.parse_args()
    tier3, notes = score_tier3()
    field = ground = None; cands = []; counts = {}
    conn = None
    if not a.no_db:
        try:
            import asyncpg
            from app.config import get_settings
            url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(url)
            field, ground, cands, counts, n2 = await db_materials(conn); notes += n2
        except Exception as e:  # noqa: BLE001
            notes.append(f"DB 접속/조회 실패: {type(e).__name__}: {e}")
    report = render(tier3, field, ground, cands, counts, notes)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{TODAY}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report); print(f"저장: {path}")
    if conn is not None:
        try:
            metrics = {"tier3": tier3["전체"] if tier3 else None, "field": field["전체"] if field else None,
                       "ground": ground["전체"] if ground else None, "candidates": cands, "counts": counts}
            await conn.execute("INSERT INTO brain_version (kind, metrics, n_train, promoted, reason) VALUES ($1,$2,$3,FALSE,$4)",
                               "scorecard", json.dumps(metrics, ensure_ascii=False), counts.get("field_check", 0), "day1 dry-run")
            print("brain_version 에 scorecard 행 추가")
        except Exception as e:  # noqa: BLE001
            print(f"brain_version 기록 실패: {e}")
        finally:
            await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
