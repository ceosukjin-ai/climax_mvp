"""
SegFormer 기반 세그멘테이션 서비스.

논문: "Semantic segmentation was then applied to the generated 5-view
images using SegFormer-B0, a lightweight transformer-based model
pre-trained on ADE20K."

두 가지 출력:
1. VSI용 — 하늘/식생/건물 3-클래스 비율
2. SMTI용 — 재질별 (아스팔트/콘크리트/식생/유리/금속/토양) 비율

ADE20K는 150 클래스 데이터셋이므로, 그중 관심 클래스만 골라 통합합니다.
파인튜닝된 가중치가 있으면 `settings.segformer_checkpoint_path`에
경로 지정하여 덮어쓸 수 있습니다.

로딩 전략:
- 앱 시작 시 1회 로드 (lifespan 훅)
- GPU 사용 가능하면 cuda, 아니면 cpu (약 3초/view → 5-view 15초)
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger
from PIL import Image

if TYPE_CHECKING:
    import torch
    from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

# ADE20K 150-class → 우리가 쓰는 관심 클래스 매핑.
# 전체 리스트: https://github.com/CSAILVision/sceneparsing/blob/master/objectInfo150.csv
ADE20K_CLASS_MAP = {
    # sky
    2: ("sky", "sky"),
    # vegetation
    4: ("tree", "vegetation"),
    9: ("grass", "vegetation"),
    17: ("plant", "vegetation"),
    66: ("flower", "vegetation"),
    # building
    1: ("building", "building"),
    25: ("house", "building"),
    48: ("skyscraper", "building"),
    # ground / materials
    3: ("floor", "concrete"),  # 실내 floor (거리엔 거의 없음)
    6: ("road", "asphalt"),
    11: ("sidewalk", "concrete"),
    13: ("earth", "soil"),
    14: ("ground", "soil"),
    29: ("field", "soil"),
    46: ("sand", "soil"),
    52: ("path", "concrete"),
    53: ("runway", "asphalt"),
    55: ("land", "soil"),
    91: ("dirt_track", "soil"),
    # water
    21: ("water", "water"),
    26: ("sea", "water"),
    60: ("river", "water"),
    109: ("lake", "water"),
    128: ("pool", "water"),
    # metal / glass-like
    76: ("boat", "metal"),  # 희귀하지만 도시 인근에 나타날 수 있음
}

TARGET_CLASSES = {"sky", "vegetation", "building"}
TARGET_MATERIALS = {
    "asphalt", "concrete", "vegetation", "glass",
    "metal", "soil", "water",
}


@dataclass(frozen=True, slots=True)
class SegmentationOutput:
    """단일 이미지 세그멘테이션 결과."""

    sky_ratio: float
    vegetation_ratio: float
    building_ratio: float
    ground_ratio: float  # soil + concrete (거리 지면 합산)
    material_ratios: dict[str, float]  # 재질별 픽셀 비율
    total_classified_pixels: int


class SegFormerService:
    """SegFormer 추론 래퍼. 앱 수명 동안 1개 인스턴스 공유."""

    def __init__(
        self,
        model_name: str,
        checkpoint_path: str = "",
        device: str = "auto",
    ) -> None:
        self.model_name = model_name
        self.checkpoint_path = checkpoint_path
        self.requested_device = device
        self._model: "SegformerForSemanticSegmentation | None" = None
        self._processor: "SegformerImageProcessor | None" = None
        self._device: str = ""

    def load(self) -> None:
        """모델 로드. 앱 시작 시 한 번만 호출."""
        import torch
        from transformers import (
            SegformerForSemanticSegmentation,
            SegformerImageProcessor,
        )

        if self.requested_device == "auto":
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self._device = self.requested_device

        logger.info(
            "Loading SegFormer | model={} | device={}",
            self.model_name,
            self._device,
        )
        self._processor = SegformerImageProcessor.from_pretrained(self.model_name)

        if self.checkpoint_path:
            logger.info("Loading fine-tuned checkpoint: {}", self.checkpoint_path)
            self._model = SegformerForSemanticSegmentation.from_pretrained(
                self.checkpoint_path
            )
        else:
            self._model = SegformerForSemanticSegmentation.from_pretrained(
                self.model_name
            )

        self._model.to(self._device)
        self._model.eval()
        logger.info("SegFormer ready")

    def is_loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    def segment(self, image_bytes: bytes) -> SegmentationOutput:
        """단일 이미지 → 관심 클래스 비율."""
        if not self.is_loaded():
            raise RuntimeError("SegFormer not loaded. Call load() first.")

        import torch

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        inputs = self._processor(images=image, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._model(**inputs)
        logits = outputs.logits  # [1, num_classes, h, w]

        # 원본 크기로 upsample
        upsampled = torch.nn.functional.interpolate(
            logits,
            size=image.size[::-1],  # (h, w)
            mode="bilinear",
            align_corners=False,
        )
        pred = upsampled.argmax(dim=1)[0].cpu().numpy()  # [H, W]

        return self._aggregate_ratios(pred)

    def _aggregate_ratios(self, pred: np.ndarray) -> SegmentationOutput:
        """픽셀 클래스 분포 → 관심 클래스 비율."""
        total_pixels = pred.size
        unique, counts = np.unique(pred, return_counts=True)
        class_counts = dict(zip(unique.tolist(), counts.tolist()))

        category_pixels = {c: 0 for c in TARGET_CLASSES}
        material_pixels = {m: 0 for m in TARGET_MATERIALS}

        for class_id, px_count in class_counts.items():
            mapping = ADE20K_CLASS_MAP.get(class_id)
            if mapping is None:
                continue
            _ade_name, target = mapping

            if target in category_pixels:
                category_pixels[target] += px_count
            if target in material_pixels:
                material_pixels[target] += px_count

        ground_pixels = material_pixels.get("concrete", 0) + material_pixels.get(
            "soil", 0
        )
        # glass/metal은 ADE20K에 직접 클래스 없음 — 향후 파인튜닝에서 추가

        return SegmentationOutput(
            sky_ratio=category_pixels["sky"] / total_pixels,
            vegetation_ratio=category_pixels["vegetation"] / total_pixels,
            building_ratio=category_pixels["building"] / total_pixels,
            ground_ratio=ground_pixels / total_pixels,
            material_ratios={
                m: px / total_pixels for m, px in material_pixels.items()
            },
            total_classified_pixels=total_pixels,
        )


@cache
def get_segformer_service() -> SegFormerService:
    """앱 전역 SegFormer 싱글톤 (lazy init)."""
    from app.config import get_settings

    settings = get_settings()
    return SegFormerService(
        model_name=settings.segformer_model_name,
        checkpoint_path=settings.segformer_checkpoint_path,
        device=settings.segformer_device,
    )
