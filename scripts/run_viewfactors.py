#!/usr/bin/env python3
"""360 파노라마 -> 하늘·나무·건물 뷰팩터 (SVF, TVF, BVF) — 일관 정의판 (2026-09-21).

왜 다시 하나
  9/14 확정판(run_indices.py)은 세 지표를 서로 다른 방식으로 셌다.
    SVF  위쪽 반구 · Steyn 가중 · ×1.0585
    GVI  구 전체   · 단순 면적비 · ×1.2221
    BVI  위쪽 반구 · 단순 면적비 · ×1.2651
  보정계수는 3월 표(세 값 합 0.49~1.34, 정의가 섞인 표)에 맞춘 것이다.
  열환경·복사 연구의 관행(Gong et al. 2018; Middel et al. 2018)은 세 뷰팩터를
  **같은 반구·같은 가중**으로 내는 것이고, 그래야 하늘+나무+건물+기타 = 1 이 된다.
  엔진의 복사 계산도 반구 뷰팩터를 전제로 한다.

이번 정의
  같은 사진, 같은 분할(SegFormer-b0 ADE20K, 기울임 큐브맵 B)에서
    SVF = Steyn sin(2θ) 가중 · 위쪽 반구 · 하늘
    TVF = 같은 가중 · 같은 반구 · 나무(tree/plant/grass/palm/flower)
    BVF = 같은 가중 · 같은 반구 · 건물(building/house/wall/...)
    OTH = 1 − SVF − TVF − BVF  (전주·간판·차량·사람 등)
  **보정계수 없음.** SVF 는 보정 전에도 3월 표 대비 bias −0.007 이었다.
  GVI(구 전체 픽셀 비율, Li et al. 2015 관행)는 기술용으로만 따로 남긴다.
"""
from __future__ import annotations
import csv, glob, math, os, sys
import numpy as np
from PIL import Image
from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pano_ab as M

IMGDIR = os.environ.get("PANO_DIR", "/mnt/user-data/uploads/segdbg/aug4096")
OUT = os.environ.get("PANO_OUT", "/tmp/claude-0/vf/aug_viewfactors.csv")


def steyn(G, th, ids):
    up = th < math.pi / 2
    w = np.sin(2 * th[up]); w = w / w.sum()
    f = np.isin(G, list(ids))[up].mean(axis=1)
    return float((f * w).sum())


def main():
    files = sorted(glob.glob(os.path.join(IMGDIR, "*.jpg")))
    print(f"사진 {len(files)}장", flush=True)
    proc = SegformerImageProcessor.from_pretrained(M.MODEL)
    model = SegformerForSemanticSegmentation.from_pretrained(M.MODEL).eval()
    id2l = {int(k): v.strip() for k, v in model.config.id2label.items()}
    SKY = {k for k, v in id2l.items() if v == "sky"}
    VEG = {k for k, v in id2l.items() if v in ("tree", "plant", "grass", "palm", "flower")}
    BLD = {k for k, v in id2l.items() if v in ("building", "house", "skyscraper", "wall",
                                               "hovel", "tower", "fence", "bridge", "column")}
    th, D = M.grid_dirs()
    specB = M.VARIANTS["B_기울임"]
    rows, grids = [], {}
    for fp in files:
        b = os.path.basename(fp)
        img = np.asarray(Image.open(fp).convert("RGB"), np.uint8)
        labels = {}
        for nm, f, fov in specB:
            fb, r, u = M.basis(f)
            labels[nm] = M.seg(proc, model, M.face_image(img, fb, r, u, fov))
        GB, _ = M.gather(labels, specB, D)
        svf, tvf, bvf = steyn(GB, th, SKY), steyn(GB, th, VEG), steyn(GB, th, BLD)
        eq = np.asarray(Image.fromarray(img).resize((1024, 512)), np.uint8)
        lab = M.seg(proc, model, eq)
        gvi_pix = float(np.isin(lab, list(VEG)).mean())
        rows.append(dict(사진=b[-7:-4], 파일=b, SVF=round(svf, 4), TVF=round(tvf, 4),
                         BVF=round(bvf, 4), OTH=round(1 - svf - tvf - bvf, 4),
                         GVI_pix=round(gvi_pix, 4)))
        grids[b] = GB
        print(f"  {rows[-1]['사진']}  SVF {svf:.3f} TVF {tvf:.3f} BVF {bvf:.3f} "
              f"OTH {1-svf-tvf-bvf:.3f}  GVIpix {gvi_pix:.3f}", flush=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    np.savez_compressed(OUT.replace(".csv", "_grids.npz"), th=th, **grids)
    print("저장", OUT)


if __name__ == "__main__":
    main()
