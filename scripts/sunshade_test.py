#!/usr/bin/env python3
"""거리영상 태양방향 픽셀로 볕/그늘 판정 — 실측 80점 검정 (인수인계 2026-09-10).

앱은 equirectangular 파노라마가 아니라 Static API 5-view(front/right/back/left = 절대 heading 0/90/180/270,
pitch 0, FOV 90, 640×640 + up = pitch 90)를 받아 SegFormer 로 분할한다. heading 이 절대값이라 정렬 문제 없음.
태양(방위 az, 고도 el) 방향을 핀홀 투영으로 해당 이미지 픽셀에 찍고, 반경 r° 원뿔 안 샘플의 '하늘'(ADE20K 2) 비율 f_sky.
  · el ≥ 45° → up 이미지(천정각 ≤45° 커버; 이번 80점은 고도 46~67° 라 전부 up 에 잡힘)
  · el < 45° → 태양방위에 가장 가까운 수평 이미지
파노라마: 지금 GSV 가 그 좌표에 주는 것(실측 당시와 같은지 pano 날짜·거리로 메모). 분할 모델·계수 변경 없음.

실행(서버 컨테이너, GSV 키·SegFormer 필요, 이미지 400장 ≈ $3):
  ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/sunshade_test.py
입력 data/sunshade_test_input_80.csv → 출력 data/sunshade_test_output_80.csv + _summary.md
"""
from __future__ import annotations
import asyncio, csv, io, math, os, sys
import numpy as np

sys.path.insert(0, "/app")
ROOT = os.environ.get("CLIMAX_REPO", "/repo")
IN = os.path.join(ROOT, "data", "sunshade_test_input_80.csv")
OUT = os.path.join(ROOT, "data", "sunshade_test_output_80.csv")
SUM = os.path.join(ROOT, "data", "sunshade_test_summary.md")
RADII = (3, 5, 8)
SKY_ID = 2
VIEWS = {"front": 0, "right": 90, "back": 180, "left": 270}


def unit(az_deg, el_deg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    return np.array([math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)])  # x동 y북 z상


def project(d, view, W, H):
    """핀홀 투영: FOV 90 → f = W/2. 반환 (x, y) 또는 None(이미지 뒤/밖)."""
    if view == "up":
        F, R, U = np.array([0, 0, 1.0]), np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
    else:
        h = math.radians(VIEWS[view])
        F = np.array([math.sin(h), math.cos(h), 0.0]); R = np.array([math.cos(h), -math.sin(h), 0.0]); U = np.array([0, 0, 1.0])
    depth = float(d @ F)
    if depth <= 1e-6:
        return None
    x = W / 2 + (W / 2) * float(d @ R) / depth
    y = H / 2 - (H / 2) * float(d @ U) / depth
    if 0 <= x < W and 0 <= y < H:
        return int(x), int(y)
    return None


def pick_view(az, el):
    if el >= 45:
        return "up"
    return min(VIEWS, key=lambda v: abs(((az - VIEWS[v] + 180) % 360) - 180))


def cone_samples(az, el, r_deg, n=241):
    """태양 방향 중심 반경 r° 원뿔 안 방향들 (중심 + 동심원)."""
    c = unit(az, el)
    # 직교 기저
    a = np.array([0, 0, 1.0]) if abs(c[2]) < 0.9 else np.array([1.0, 0, 0])
    e1 = np.cross(c, a); e1 /= np.linalg.norm(e1); e2 = np.cross(c, e1)
    out = [c]
    rings = 6
    for k in range(1, rings + 1):
        rho = math.radians(r_deg) * k / rings; m = int(round(8 * k))
        for j in range(m):
            th = 2 * math.pi * j / m
            v = math.cos(rho) * c + math.sin(rho) * (math.cos(th) * e1 + math.sin(th) * e2)
            out.append(v / np.linalg.norm(v))
    return out


def kappa_stats(y, p):
    tp = sum(1 for a, b in zip(y, p) if a == 1 and b == 1); tn = sum(1 for a, b in zip(y, p) if a == 0 and b == 0)
    fp = sum(1 for a, b in zip(y, p) if a == 0 and b == 1); fn = sum(1 for a, b in zip(y, p) if a == 1 and b == 0)
    n = len(y); acc = (tp + tn) / n if n else float("nan")
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n) if n else 0
    k = (acc - pe) / (1 - pe) if (1 - pe) > 1e-9 else float("nan")
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, acc=acc, rec_shade=tn / (tn + fp) if tn + fp else float("nan"),
                rec_sun=tp / (tp + fn) if tp + fn else float("nan"), kappa=k)


def auc(y, s):
    pos = [v for a, v in zip(y, s) if a == 1]; neg = [v for a, v in zip(y, s) if a == 0]
    if not pos or not neg:
        return float("nan")
    return sum((1.0 if p > q else 0.5 if p == q else 0.0) for p in pos for q in neg) / (len(pos) * len(neg))


async def main():
    from app.config import get_settings
    from app.services.street_view import GoogleStreetViewClient
    from app.ml.segformer import get_segformer_service
    import torch
    from PIL import Image
    s = get_settings()
    seg = get_segformer_service(); seg.load()

    def pred_map(img_bytes):
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        inputs = seg._processor(images=image, return_tensors="pt")
        inputs = {k: v.to(seg._device) for k, v in inputs.items()}
        with torch.no_grad():
            logits = seg._model(**inputs).logits
        up = torch.nn.functional.interpolate(logits, size=image.size[::-1], mode="bilinear", align_corners=False)
        return up.argmax(dim=1)[0].cpu().numpy()

    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    out = []
    async with GoogleStreetViewClient(api_key=s.google_streetview_api_key, signing_secret=s.google_streetview_signing_secret) as cli:
        for i, r in enumerate(rows, 1):
            lat, lon = float(r["위도"]), float(r["경도"]); az, el = float(r["태양방위각"]), float(r["태양고도"])
            rec = dict(r); rec.update({"heading": "0/90/180/270(절대)+up", "pano_dist_m": "", "pano_date": "", "view_used": ""})
            try:
                meta = await cli.get_pano_metadata(lat, lon)
                if meta.status != "OK":
                    raise RuntimeError(f"pano {meta.status}")
                dist = math.hypot((meta.lat - lat) * 111000, (meta.lon - lon) * 111000 * math.cos(math.radians(lat)))
                sv = await cli.fetch_five_views(meta)
                preds = {d: pred_map(b) for d, b in sv.images.items()}
                rec["pano_dist_m"] = round(dist, 1); rec["pano_date"] = meta.date or ""
                rec["view_used"] = pick_view(az, el)
                for rr in RADII:
                    hits = tot = 0
                    for d in cone_samples(az, el, rr):
                        el_d = math.degrees(math.asin(max(-1, min(1, d[2])))); az_d = math.degrees(math.atan2(d[0], d[1])) % 360
                        v = pick_view(az_d, el_d); pm = preds.get(v)
                        if pm is None:
                            continue
                        H, W = pm.shape; xy = project(d, v, W, H)
                        if xy is None:
                            continue
                        tot += 1; hits += int(pm[xy[1], xy[0]] == SKY_ID)
                    f = hits / tot if tot else float("nan")
                    rec[f"f_sky_r{rr}"] = round(f, 3) if tot else ""
                    rec[f"판정_r{rr}"] = "" if not tot else int(f >= 0.5)
                rec["흐림(cloud≥0.8)"] = int(float(r["cloud"] or 0) >= 0.8)
                print(f"[{i:2d}/80] {r['측정ID']} 실측볕={r['실측볕_광학']} f_sky r3/5/8 = {rec.get('f_sky_r3')}/{rec.get('f_sky_r5')}/{rec.get('f_sky_r8')} "
                      f"pano {rec['pano_dist_m']}m {rec['pano_date']}", flush=True)
            except Exception as e:  # noqa: BLE001
                rec["에러"] = f"{type(e).__name__}: {e}"
                for rr in RADII:
                    rec[f"f_sky_r{rr}"] = ""; rec[f"판정_r{rr}"] = ""
                rec["흐림(cloud≥0.8)"] = int(float(r["cloud"] or 0) >= 0.8)
                print(f"[{i:2d}/80] {r['측정ID']} 실패: {rec['에러']}", flush=True)
            out.append(rec)
    fields = list(out[0].keys())
    for rec in out:
        for k in rec:
            if k not in fields:
                fields.append(k)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)

    # ── 채점 ──
    L = ["# 거리영상 태양방향 볕/그늘 판정 검정 — 결과 (2026-09-10)", "",
         "방법: 앱 5-view(절대 heading, FOV 90) + SegFormer 분할, 태양 방향 원뿔 r° 안 하늘 픽셀 비율 f_sky ≥ 0.5 → 볕.",
         "파노라마: 현재 GSV 반환(실측 당시와 동일 보장 없음 — pano_date 열 참조).", ""]
    def table(name, sel):
        L.append(f"## {name} (n={len(sel)})")
        L.append("| 판정 | 볕→볕 | 그늘→볕(FP) | 볕→그늘(FN) | 그늘→그늘 | 정확도 | 그늘재현 | 볕재현 | κ | AUC(f_sky) |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for lab, pk, fk in [(f"영상 r={rr}°", f"판정_r{rr}", f"f_sky_r{rr}") for rr in RADII] + [("8월 엔진", "엔진볕판정_8월", None)]:
            pairs = [(int(x["실측볕_광학"]), int(x[pk])) for x in sel if str(x.get(pk, "")) != ""]
            if not pairs:
                continue
            y, p = zip(*pairs); st = kappa_stats(y, p)
            a = auc([int(x["실측볕_광학"]) for x in sel if str(x.get(fk, "")) != ""], [float(x[fk]) for x in sel if str(x.get(fk, "")) != ""]) if fk else float("nan")
            L.append(f"| {lab} | {st['tp']} | {st['fp']} | {st['fn']} | {st['tn']} | {st['acc']:.2f} | {st['rec_shade']:.2f} | {st['rec_sun']:.2f} | {st['kappa']:.2f} | {a:.2f} |")
        L.append("")
    valid = [x for x in out if x["앱_유효"] == "유효"]
    near = [x for x in out if x["앱_유효"].startswith("무효-인근")]
    table("① 앱_유효 55 (주 검정)", valid)
    table("② 인근대체 17 (참고)", near)
    table("③ 전체 80 (참고)", out)
    table("④ 유효 55 − 구름그늘(실측그늘 & 파노라마SVF>0.7) 제외", [x for x in valid if not (x["실측볕_광학"] == "0" and float(x["파노라마_SVF_실측"]) > 0.7)])
    L.append("## 실측 그늘인데 볕으로 판정(r=5°, 전체)")
    L.append("| 측정ID | 지점 | 유효 | f_sky r3/r5/r8 | 파노SVF실측 | cloud | pano_dist | 짐작 |"); L.append("|---|---|---|---|---|---|---|---|")
    for x in out:
        if x["실측볕_광학"] == "0" and str(x.get("판정_r5", "")) == "1":
            svf = float(x["파노라마_SVF_실측"]); cl = float(x["cloud"] or 0)
            guess = "구름 그늘(SVF 열림)" if svf > 0.7 else "차양/나무(파노 위치 차이)" if x["앱_유효"] != "유효" else "차양·나무 미분할 또는 파노 시점 차이"
            if cl >= 0.8:
                guess = "흐림 " + guess
            L.append(f"| {x['측정ID']} | {x['지점명']} | {x['앱_유효']} | {x.get('f_sky_r3')}/{x.get('f_sky_r5')}/{x.get('f_sky_r8')} | {svf:.2f} | {cl} | {x.get('pano_dist_m')} | {guess} |")
    L.append("")
    errs = [x for x in out if x.get("에러")]
    if errs:
        L.append(f"실패 {len(errs)}건: " + ", ".join(x["측정ID"] for x in errs))
    open(SUM, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L)); print(f"\n저장: {OUT}\n저장: {SUM}")


if __name__ == "__main__":
    asyncio.run(main())
