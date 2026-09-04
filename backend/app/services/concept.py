"""
개선 시뮬 개념도 — "이 격자에 그늘막/가로수를 넣으면 이런 모습" (2026-09-05, 대표 전용)

원본 사진 없이, 격자의 공간지표(SVF·GVI·BVI)로 '이런 형태의 부산 거리'를 생성하고(before),
같은 장면에 개입을 넣은 그림(after)을 만든다. 실제 그 장소의 사진이 아니라 **개념도**다 —
화면·제안서에 반드시 그렇게 표기한다. (구글·카카오 거리 사진은 약관상 편집 불가라 쓰지 않는다.)

생성: Gemini 이미지 모델 REST. 결과는 디스크 캐시(같은 격자·개입은 재생성 안 함).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
from loguru import logger

from app.config import get_settings

CACHE_DIR = Path("/app/.cache/concept") if Path("/app").is_dir() else Path(".cache/concept")

_SCENE_STYLE = ("Photorealistic street-level photograph, eye level, 35mm lens, Busan, South Korea, "
                "midsummer early afternoon, clear sky, harsh sunlight, no people in the foreground, "
                "Korean signage, realistic asphalt road and concrete sidewalk.")


def _describe(svf: float, gvi: float, bvi: float) -> str:
    """공간지표 → 장면 묘사. 숫자를 그대로 말로 옮긴다(추측 최소)."""
    if svf >= 0.6:
        sky = "very open sky, wide road (4+ lanes) with low buildings set back"
    elif svf >= 0.4:
        sky = "moderately open sky, a 2–4 lane street with mid-rise (4–8 storey) buildings on both sides"
    else:
        sky = "narrow street canyon, sky mostly blocked by tall buildings close to the road"
    if gvi >= 0.3:
        green = "many mature street trees and planted strips"
    elif gvi >= 0.12:
        green = "a few small street trees, mostly hard surfaces"
    else:
        green = "almost no vegetation, bare sidewalk and asphalt"
    if bvi >= 0.4:
        bld = "dense commercial buildings with shop fronts right at the sidewalk"
    elif bvi >= 0.2:
        bld = "mixed residential/commercial buildings"
    else:
        bld = "sparse buildings, open lots"
    return f"{sky}; {green}; {bld}."


AFTER_PROMPT = {
    "shade": ("Edit this photo: install a permanent fixed shade canopy (Korean 그늘막 style — a sturdy "
              "steel-post umbrella or sail canopy about 4 m wide, beige or dark green fabric) over the "
              "sidewalk waiting area / crosswalk corner. Cast a realistic shadow on the pavement under it. "
              "Keep everything else exactly the same: same buildings, road, lighting, camera angle."),
    "trees": ("Edit this photo: plant a row of mature street trees (8–10 m tall, broad canopy, like zelkova "
              "or plane trees) along the sidewalk edge with tree pits, spaced about 10 m, casting dappled "
              "shade on the sidewalk. Keep everything else exactly the same: same buildings, road, lighting, camera angle."),
    "coolpave": ("Edit this photo: resurface the road and sidewalk with light grey high-albedo cool pavement. "
                 "Keep everything else exactly the same."),
    "combo": ("Edit this photo: plant a row of mature street trees along the sidewalk AND resurface the road "
              "with light grey high-albedo cool pavement. Keep everything else exactly the same."),
}
SCEN_KO = {"shade": "그늘막 설치 후", "trees": "가로수 식재 후", "coolpave": "차열포장 후", "combo": "가로수+차열포장 후"}


def _key(lat: float, lon: float, tag: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{lat:.4f}_{lon:.4f}_{tag}.png"


async def _gemini_image(prompt: str, image_b64: str | None = None) -> bytes:
    s = get_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY 미설정")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{s.gemini_image_model}:generateContent?key={s.gemini_api_key}")
    parts: list[dict] = [{"text": prompt}]
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/png", "data": image_b64}})
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, json=body)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise RuntimeError("Gemini 응답에 이미지 없음: " + json.dumps(data)[:300])


async def concept_pair(lat: float, lon: float, scenario: str,
                       svf: float, gvi: float, bvi: float, force: bool = False) -> dict:
    """before/after PNG(base64) 반환. 캐시 우선."""
    if scenario not in AFTER_PROMPT:
        return {"ok": False, "reason": f"알 수 없는 개입 {scenario}"}
    lat, lon = round(lat, 4), round(lon, 4)
    pb, pa = _key(lat, lon, "base"), _key(lat, lon, scenario)
    generated = []
    try:
        if force or not pb.exists():
            prompt = f"{_SCENE_STYLE} Scene: {_describe(svf, gvi, bvi)} No shade structures, no new trees."
            pb.write_bytes(await _gemini_image(prompt)); generated.append("base")
        if force or not pa.exists():
            base_b64 = base64.b64encode(pb.read_bytes()).decode()
            pa.write_bytes(await _gemini_image(AFTER_PROMPT[scenario], base_b64)); generated.append(scenario)
    except Exception as e:  # noqa: BLE001
        logger.warning("[concept] 생성 실패 {}: {}", type(e).__name__, e)
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    return {"ok": True, "scenario": scenario, "label": SCEN_KO[scenario], "generated": generated,
            "scene": _describe(svf, gvi, bvi),
            "before": "data:image/png;base64," + base64.b64encode(pb.read_bytes()).decode(),
            "after": "data:image/png;base64," + base64.b64encode(pa.read_bytes()).decode(),
            "disclaimer": "실제 장소 사진이 아닌 개념도 — 격자의 공간지표(SVF·GVI·BVI)로 생성한 대표 장면"}
