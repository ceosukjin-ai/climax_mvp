#!/usr/bin/env python3
"""천정면 오분류를 고치는 세 가지 방식 A/B/C 비교 (2026-09-14).

왜:
  큐브맵 6면 중 **천정면 한 면이 SVF 적분 가중의 55.7%** 를 차지하는데,
  그 면에서 하늘을 1% 밖에 못 찾는다(사진 4장 오버레이로 확인).
  지평선을 보는 네 면은 같은 하얀 하늘을 98~99% 하늘로 제대로 본다.
  천정만 90도로 잘라내면 지평선도 땅도 없어 모델이 기댈 맥락이 없다는 뜻.

무엇을 비교하나:
  A  현행 — 큐브맵 6면(화각 90도), 천정/바닥 별도
  B  기울임 — 지평선 4면(90도) + 고도 45도로 기울인 4면(화각 120도) + 바닥
     기울인 면에는 지평선과 하늘이 한 장에 같이 들어간다
  C  등장방형 한 장 통째로 분할 (1024x512)

기준:
  정숙진이 직접 산출한 3월 28지점 표. 이것을 정답으로 놓고 bias/MAE/r 을 본다.
  동시에 각 방식이 '하늘로 본 영역'을 원본 위에 칠한 그림을 뽑아 눈으로 확인한다.
  표와 그림 둘 다 좋아져야 채택한다.
"""
from __future__ import annotations
import glob, json, math, os, sys

import numpy as np
import torch
from PIL import Image
from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation

IMGDIR = os.environ.get("PANO_DIR", "/mnt/user-data/uploads/segdbg/mar4096")
OUTDIR = os.environ.get("PANO_OUT", "/home/claude/ab_out")
MODEL = os.environ.get("SEG_MODEL", "nvidia/segformer-b0-finetuned-ade-512-512")
FACE = 512
NTH, NPH = 90, 360
Z = np.array([0.0, 0.0, 1.0])


def basis(f):
    f = np.array(f, float); f /= np.linalg.norm(f)
    c = np.cross(f, Z)
    r = np.array([1.0, 0.0, 0.0]) if np.linalg.norm(c) < 1e-9 else c / np.linalg.norm(c)
    return f, r, np.cross(r, f)


def horiz(az_deg, el_deg=0.0):
    a, e = math.radians(az_deg), math.radians(el_deg)
    return (math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e))


# (이름, 전방벡터, 화각도)
VARIANTS = {
    "A_현행6면": [("front", horiz(0), 90), ("right", horiz(90), 90),
                  ("back", horiz(180), 90), ("left", horiz(270), 90),
                  ("up", (0, 0, 1), 90), ("down", (0, 0, -1), 90)],
    "B_기울임": [("front", horiz(0), 90), ("right", horiz(90), 90),
                 ("back", horiz(180), 90), ("left", horiz(270), 90),
                 ("t45", horiz(45, 45), 120), ("t135", horiz(135, 45), 120),
                 ("t225", horiz(225, 45), 120), ("t315", horiz(315, 45), 120),
                 ("down", (0, 0, -1), 90)],
}


def sample_equirect(img, dirs):
    H, W, _ = img.shape
    x, y, z = dirs[..., 0], dirs[..., 1], dirs[..., 2]
    az = np.arctan2(x, y) % (2 * math.pi)
    th = np.arccos(np.clip(z, -1, 1))
    return img[np.clip((th / math.pi * H).astype(int), 0, H - 1),
               np.clip((az / (2 * math.pi) * W).astype(int), 0, W - 1)]


def face_image(img, f, r, u, fov, n=FACE):
    t = math.tan(math.radians(fov) / 2)
    a = ((np.arange(n) + 0.5) / n * 2 - 1) * t
    xx, yy = np.meshgrid(a, a)
    d = f + xx[..., None] * r - yy[..., None] * u
    d /= np.linalg.norm(d, axis=2, keepdims=True)
    return sample_equirect(img, d)


def grid_dirs():
    th = (np.arange(NTH) + 0.5) / NTH * math.pi
    ph = (np.arange(NPH) + 0.5) / NPH * 2 * math.pi
    T, P = np.meshgrid(th, ph, indexing="ij")
    D = np.stack([np.sin(T) * np.sin(P), np.sin(T) * np.cos(P), np.cos(T)], -1)
    return th, D


def gather(labels, spec, D):
    """각 방향을 중심이 가장 가까운 면에서 읽는다."""
    bas = [basis(f) for _, f, _ in spec]
    dots = np.stack([D @ b[0] for b in bas], -1)
    pick = dots.argmax(-1)
    G = np.full(D.shape[:2], -1, np.int16)
    cov = np.zeros(D.shape[:2], bool)
    for i, (nm, _, fov) in enumerate(spec):
        sel = pick == i
        if not sel.any():
            continue
        f, r, u = bas[i]
        df = D[sel] @ f
        ok = df > 1e-9
        t = math.tan(math.radians(fov) / 2)
        a = (D[sel] @ r) / np.where(ok, df, 1) / t
        b = (D[sel] @ u) / np.where(ok, df, 1) / t
        ok &= (np.abs(a) <= 1) & (np.abs(b) <= 1)
        px = np.clip(((a + 1) / 2 * FACE).astype(int), 0, FACE - 1)
        py = np.clip(((1 - b) / 2 * FACE).astype(int), 0, FACE - 1)
        idx = np.where(sel)
        G[idx[0][ok], idx[1][ok]] = labels[nm][py[ok], px[ok]]
        cov[idx[0][ok], idx[1][ok]] = True
    return G, cov


def integrate(G, th, SKY, VEG, BLD):
    up = th < math.pi / 2
    w = np.sin(2 * th[up]); w = w / w.sum()
    out = {}
    # 세 가지 가중 — 선생님 표가 어느 정의인지 판별하기 위해 모두 낸다
    wc = np.cos(th[up]) * np.sin(th[up]); wc = wc / wc.sum()   # cos 가중(어안 투영)
    ws = np.sin(th[up]); ws = ws / ws.sum()                    # 입체각 단순비
    for k, ids in (("svf", SKY), ("gvi", VEG), ("bvi", BLD)):
        m = np.isin(G, list(ids))
        f = m[up].mean(axis=1)
        out[k] = float((f * w).sum())            # Steyn sin(2θ)
        out[k + "_up"] = float(m[up].mean())     # 상반구 단순 면적비(등장방형 행 평균)
        out[k + "_cos"] = float((f * wc).sum())  # cos 가중
        out[k + "_sa"] = float((f * ws).sum())   # 입체각 비율
        out[k + "_all"] = float(m.mean())
    return out


def seg(proc, model, arr):
    with torch.no_grad():
        o = model(**proc(images=Image.fromarray(arr), return_tensors="pt")).logits
    o = torch.nn.functional.interpolate(o, size=arr.shape[:2], mode="bilinear",
                                        align_corners=False)
    return o.argmax(1)[0].numpy().astype(np.int16)


TABLE = {
 "point01_260327":(0.892889,0.054755,0.210122),"point02_260327":(0.833673,0.229510,0.037356),
 "point03_260327":(0.253556,0.362008,0.048395),"point04_260327":(0.662297,0.174575,0.259727),
 "point05_260327":(0.183651,0.205799,0.162763),"point06_260327":(0.892873,0.070181,0.176374),
 "point07_260327":(0.899674,0.095309,0.315590),"point08_260327":(0.430605,0.035499,0.408963),
 "point09_260327":(0.557007,0.199037,0.205196),"point10_260327":(0.246464,0.323485,0.095648),
 "point11_260327":(0.506384,0.266866,0.086395),"point12_260327":(0.622424,0.053858,0.311889),
 "point13_260327":(0.761845,0.050025,0.308531),"point14_260327":(0.588564,0.236137,0.180140),
 "point15_260327":(0.684051,0.057673,0.232789),"point16_260327":(0.564128,0.218206,0.198151),
 "point17_260327":(0.109916,0.346417,0.035074),"point18_260327":(0.102611,0.389978,0.068766),
 "point19_260327":(0.532996,0.128505,0.334030),"point20_260325":(0.464088,0.325977,0.045492),
 "point21_260325":(0.757243,0.102880,0.333336),"point22_260325":(0.482852,0.041284,0.463476),
 "point23_260325":(0.777276,0.048121,0.281989),"point24_260325":(0.348247,0.166014,0.234769),
 "point25_260325":(0.666215,0.042691,0.285035),"point26_260325":(0.921452,0.110865,0.311352),
 "point27_260325":(0.767962,0.019611,0.299978),"point30_260325":(0.848936,0.089466,0.267617),
 "point31_260325":(0.896935,0.028338,0.316501)}
PAIR = {"135":"point01_260327","131":"point02_260327","130":"point03_260327",
 "127":"point04_260327","126":"point05_260327","123":"point06_260327","125":"point07_260327",
 "129":"point08_260327","128":"point09_260327","134":"point10_260327","133":"point11_260327",
 "119":"point12_260327","124":"point13_260327","122":"point14_260327","121":"point15_260327",
 "120":"point16_260327","118":"point17_260327","117":"point18_260327","116":"point19_260327",
 "112":"point20_260325","111":"point21_260325","110":"point22_260325","108":"point23_260325",
 "109":"point24_260325","107":"point25_260325","105":"point26_260325","106":"point27_260325",
 "114":"point30_260325","115":"point31_260325"}


def stats(pred, obs, label):
    import statistics as st
    d = [p - o for p, o in zip(pred, obs)]
    mp, mo = st.mean(pred), st.mean(obs)
    num = sum((pred[i] - mp) * (obs[i] - mo) for i in range(len(d)))
    den = (sum((p - mp) ** 2 for p in pred) * sum((o - mo) ** 2 for o in obs)) ** 0.5
    r = num / den if den > 0 else float("nan")
    mae = st.mean([abs(x) for x in d])
    print(f"    {label:<6} bias {st.mean(d):+.3f}  MAE {mae:.3f}  r {r:.3f}")
    return mae


def verify_geometry():
    th, D = grid_dirs()
    print("기하 사전검증")
    for vn, spec in VARIANTS.items():
        dummy = {nm: np.zeros((FACE, FACE), np.int16) for nm, _, _ in spec}
        _, cov = gather(dummy, spec, D)
        # 전천 SVF = 1 검산
        dummy2 = {nm: np.full((FACE, FACE), 2, np.int16) for nm, _, _ in spec}  # 2=sky(ADE)
        G, _ = gather(dummy2, spec, D)
        up = th < math.pi / 2
        w = np.sin(2 * th[up]); w = w / w.sum()
        s = float((np.isin(G, [2])[up].mean(axis=1) * w).sum())
        print(f"  {vn:<10} 방향 덮개 {cov.mean()*100:6.2f}%   전천 SVF {s:.3f}")
    print()


def main():
    files = sorted(glob.glob(os.path.join(IMGDIR, "*.jpg")))
    os.makedirs(OUTDIR, exist_ok=True)
    verify_geometry()
    print(f"사진 {len(files)}장  {IMGDIR}\n")

    proc = SegformerImageProcessor.from_pretrained(MODEL)
    model = SegformerForSemanticSegmentation.from_pretrained(MODEL).eval()
    id2l = {int(k): v.strip() for k, v in model.config.id2label.items()}
    SKY = {k for k, v in id2l.items() if v == "sky"}
    VEG = {k for k, v in id2l.items() if v in ("tree", "plant", "grass", "palm", "flower")}
    BLD = {k for k, v in id2l.items() if v in ("building", "house", "skyscraper", "wall",
                                               "hovel", "tower", "fence", "bridge", "column")}
    th, D = grid_dirs()

    res = {v: {} for v in list(VARIANTS) + ["C_등장방형"]}
    for fp in files:
        b = os.path.basename(fp)
        img = np.asarray(Image.open(fp).convert("RGB"), np.uint8)
        for vn, spec in VARIANTS.items():
            labels = {}
            for nm, f, fov in spec:
                fb, r, u = basis(f)
                labels[nm] = seg(proc, model, face_image(img, fb, r, u, fov))
            G, _ = gather(labels, spec, D)
            res[vn][b] = integrate(G, th, SKY, VEG, BLD)
            res[vn][b]["_G"] = G
        # C: 등장방형 통째
        eq = np.asarray(Image.fromarray(img).resize((1024, 512)), np.uint8)
        lab = seg(proc, model, eq)
        yy = np.clip((th[:, None] / math.pi * 512).astype(int), 0, 511)
        xx = np.clip(((np.arange(NPH)[None, :] + 0.5) / NPH * 1024).astype(int), 0, 1023)
        G = lab[yy, xx]
        res["C_등장방형"][b] = integrate(G, th, SKY, VEG, BLD)
        res["C_등장방형"][b]["_G"] = G
        line = f"  {b[-7:-4]}"
        for vn in res:
            line += f"   {vn.split('_')[0]} SVF {res[vn][b]['svf']:.3f}"
        print(line, flush=True)

    print("\n=== 3월 28지점 표(직접 산출) 대비 ===")
    best = None
    for vn in res:
        print(f"  {vn}")
        cols = {0: ("SVF", "svf"), 1: ("GVI", "gvi"), 2: ("BVI", "bvi")}
        maes = {}
        for i, (nm, key) in cols.items():
            p, o = [], []
            for b, d in res[vn].items():
                n = b[-7:-4]
                if n in PAIR and PAIR[n] in TABLE:
                    p.append(d[key]); o.append(TABLE[PAIR[n]][i])
            maes[nm] = stats(p, o, nm)
        if best is None or maes["SVF"] < best[1]:
            best = (vn, maes["SVF"])
    print(f"\n  SVF 기준 최선: {best[0]}  MAE {best[1]:.3f}")
    print("  (현행 배포 = A. 1차 등장방형 4조각은 MAE 0.131 이었다)")

    # 그림: 각 방식의 하늘 판정을 원본 위에 칠한다
    for fp in files:
        b = os.path.basename(fp)
        if b[-7:-4] not in ("105", "117", "126", "135"):
            continue
        img = Image.open(fp).convert("RGB").resize((NPH * 2, NTH * 2))
        sheet = Image.new("RGB", (NPH * 2, NTH * 2 * (len(res) + 1)), (20, 20, 20))
        sheet.paste(img, (0, 0))
        base = np.asarray(img, np.uint8)
        for i, vn in enumerate(res):
            G = res[vn][b]["_G"]
            col = np.zeros((NTH, NPH, 3), np.uint8); col[...] = (90, 90, 90)
            col[np.isin(G, list(SKY))] = (60, 130, 255)
            col[np.isin(G, list(VEG))] = (40, 180, 60)
            col[np.isin(G, list(BLD))] = (220, 60, 50)
            col = np.asarray(Image.fromarray(col).resize((NPH * 2, NTH * 2), Image.NEAREST))
            ov = (base * 0.45 + col * 0.55).astype(np.uint8)
            sheet.paste(Image.fromarray(ov), (0, NTH * 2 * (i + 1)))
        sheet.save(os.path.join(OUTDIR, f"cmp_{b[:-4]}.png"))
        print(f"  그림 저장: cmp_{b[:-4]}.png  (위→아래: 원본, " +
              ", ".join(res) + ")")

    dump = {vn: {b: {k: v for k, v in d.items() if not k.startswith("_")}
                 for b, d in r.items()} for vn, r in res.items()}
    json.dump(dump, open(os.path.join(OUTDIR, "ab.json"), "w"), ensure_ascii=False, indent=1)
    np.savez_compressed(os.path.join(OUTDIR, "grids.npz"),
                        **{f"{vn}|{b}": d["_G"] for vn, r in res.items() for b, d in r.items()})
    print("격자 저장: grids.npz")


if __name__ == "__main__":
    main()
