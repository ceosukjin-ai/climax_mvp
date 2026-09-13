#!/usr/bin/env python3
"""열화상 원본에서 **벽면 온도**를 뽑는다 (2026-09-13).

왜:
  벽 스테이지를 켤 수 없었던 이유가 "계산 벽온도 44℃ vs 폴백(벽=노면) 55℃" 중 어느 쪽이 참인지
  몰라서였다. 80점 PET 로는 판별이 안 된다 — 그건 순환논증이다.
  열화상 원본에는 답이 직접 들어 있다. 한 장 안에 하늘·벽·노면이 다 찍혀 있다.

온도 복원:
  HIKMICRO 컬러맵은 좌측 세로 컬러바(x 16~22, y 184~379)로 드러나 있고, 위=Max 아래=Min 이다.
  화소 색을 컬러바에서 최근접 탐색해 온도로 되돌린다. 각 장의 Min/Max 는 thermal_minmax.json
  (기존 OCR 결과)에서 읽는다.
  검증: HM20260826143150 재현값 중앙 37.6 / 평균 38.6 ↔ 기존 시트 37.64 / 38.31.

벽/노면 분리:
  가로경관 컷이라는 기하를 쓴다 — 좌우 25% 띠(하단 22% 제외)는 파사드, 하단중앙 쐐기는 노면.
  **자기검증**: 하단중앙 값이 시트의 노면 Ts 와 맞아야 이 가정이 성립한 것이다. 안 맞으면 그 장은 버린다.
  하늘은 차가운 화소(Min+15% 이내, 상반부)로 따로 센다.

  [맥] python3 scripts/thermal_wall_extract.py <DCIM/202504 경로> <thermal_minmax.json> [출력csv]
"""
from __future__ import annotations
import csv, json, os, sys
import numpy as np
from PIL import Image

BAR_Y0, BAR_Y1, BAR_X0, BAR_X1 = 184, 380, 16, 23
PAL_MAX_DIST = 70.0        # 팔레트 최근접 거리 상한 — 넘으면 오버레이·글자로 보고 버린다


def temp_map(path: str, tmin: float, tmax: float):
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    if a.shape[0] < BAR_Y1 or a.shape[1] < 60:
        return None, None
    bar = np.median(a[BAR_Y0:BAR_Y1, BAR_X0:BAR_X1, :], axis=1)
    tb = tmax - (np.arange(len(bar)) / (len(bar) - 1)) * (tmax - tmin)
    h, w, _ = a.shape
    flat = a.reshape(-1, 3)
    d = ((flat[:, None, :] - bar[None, :, :]) ** 2).sum(2)
    T = tb[d.argmin(1)].reshape(h, w)
    E = np.sqrt(d.min(1)).reshape(h, w)
    m = np.ones((h, w), bool)
    m[:178, :105] = False          # 좌상단 Cen/Max/Min 박스
    m[170:435, :42] = False        # 컬러바 + 라벨
    m[:48, 405:] = False           # HIKMICRO 로고
    m[408:, 525:] = False          # MENU
    m[418:, :] = False             # 하단 정보줄
    m[E > PAL_MAX_DIST] = False
    return T, m


def med(T, m):
    return float(np.median(T[m])) if m.sum() >= 200 else None


def main():
    src = sys.argv[1]
    mm = json.load(open(sys.argv[2], encoding="utf-8"))
    out = sys.argv[3] if len(sys.argv) > 3 else "thermal_wall.csv"
    files = sorted(f for f in os.listdir(src)
                   if f.startswith("HM") and ".VIS." not in f and f.lower().endswith((".jpg", ".jpeg")))
    print(f"열화상 {len(files)}장, 보정표 {len(mm)}건")
    rows = []
    for i, f in enumerate(files, 1):
        e = mm.get(f)
        if not e:
            continue
        tmin, tmax, ts_med, ts_avg = e[0], e[1], e[2], e[3]
        try:
            T, m = temp_map(os.path.join(src, f), float(tmin), float(tmax))
        except Exception as ex:                      # noqa: BLE001
            print(f"  {f} 실패 {type(ex).__name__}"); continue
        if T is None:
            continue
        h, w = T.shape
        wall = m.copy(); wall[:, int(w * .25):int(w * .75)] = False; wall[int(h * .78):, :] = False
        grd = np.zeros((h, w), bool); grd[int(h * .78):, int(w * .25):int(w * .75)] = True; grd &= m
        sky = m & (T < tmin + 0.15 * (tmax - tmin)); sky[int(h * .5):, :] = False
        rec = dict(파일=f, min=tmin, max=tmax, 전체중앙=med(T, m),
                   벽중앙=med(T, wall), 벽평균=(float(T[wall].mean()) if wall.sum() >= 200 else None),
                   벽p90=(float(np.percentile(T[wall], 90)) if wall.sum() >= 200 else None),
                   노면중앙=med(T, grd), 하늘중앙=med(T, sky),
                   시트중앙=ts_med, 시트평균=ts_avg, 벽화소=int(wall.sum()), 노면화소=int(grd.sum()))
        rows.append(rec)
        if i % 20 == 0:
            print(f"  {i}/{len(files)}", flush=True)
    if not rows:
        print("결과 없음"); return
    with open(out, "w", encoding="utf-8-sig", newline="") as fp:
        wr = csv.DictWriter(fp, fieldnames=list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)

    def arr(k, sub=None):
        return np.array([r[k] for r in (sub or rows) if r[k] is not None], dtype=float)

    # 재현 검증 — 기존 시트와 전체중앙이 맞아야 팔레트 복원이 옳다
    a1, a2 = [], []
    for r in rows:
        if r["전체중앙"] is not None and r["시트중앙"] is not None:
            a1.append(r["전체중앙"]); a2.append(r["시트중앙"])
    if a1:
        d = np.array(a1) - np.array(a2)
        print(f"\n[재현 검증] 전체중앙 vs 기존 시트  n={len(d)}  bias {d.mean():+.2f}  MAE {np.abs(d).mean():.2f}℃")

    print(f"\n[분리 결과] n={len(rows)}")
    for k in ("하늘중앙", "벽중앙", "벽평균", "벽p90", "노면중앙"):
        v = arr(k)
        if len(v):
            print(f"  {k:8} n={len(v):3}  중앙 {np.median(v):5.1f}  평균 {v.mean():5.1f}  "
                  f"p10 {np.percentile(v,10):5.1f}  p90 {np.percentile(v,90):5.1f}")
    w_, g_ = [], []
    for r in rows:
        if r["벽중앙"] is not None and r["노면중앙"] is not None:
            w_.append(r["벽중앙"]); g_.append(r["노면중앙"])
    if w_:
        d = np.array(g_) - np.array(w_)
        print(f"\n  노면 − 벽  평균 {d.mean():+.1f}℃  중앙 {np.median(d):+.1f}℃  "
              f"(벽이 노면보다 낮은 장 {100*(d>0).mean():.0f}%)")
    print(f"\n저장: {out}")
    print("판정: '벽=노면온도' 폴백이 옳으려면 노면−벽 이 0 근처여야 한다.")


if __name__ == "__main__":
    main()
