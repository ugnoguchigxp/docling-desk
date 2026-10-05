from __future__ import annotations

import math
from io import BytesIO

from PIL import Image

from .config import OcrError, OcrProfile


def png_bytes(image: Image.Image, profile: OcrProfile) -> tuple[bytes, tuple[int, int]]:
    if image.width * image.height > 100_000_000:
        raise OcrError("ocr_image_too_large")
    image = image.convert("RGB")
    # Maintain aspect ratio, including a minimum dimension for small embedded icons.
    factor = min(1, 10000 / max(image.size))
    if min(image.size) * factor < 50:
        factor = 50 / min(image.size)
    if max(image.size) * factor > 10000:
        raise OcrError("ocr_image_dimensions")
    if factor != 1:
        image = image.resize((round(image.width * factor), round(image.height * factor)))
    limit = 4_000_000 if profile.tier == "F0" else 50 * 1024**2
    while True:
        stream = BytesIO()
        image.save(stream, "PNG")
        raw = stream.getvalue()
        if len(raw) <= limit:
            return raw, image.size
        size = (round(image.width * 0.8), round(image.height * 0.8))
        if min(size) < 50:
            raise OcrError("ocr_image_too_large")
        image = image.resize(size)


def normalize(result: dict, size: tuple[int, int], profile: OcrProfile) -> list[dict]:
    try:
        analysis = result["analyzeResult"]
        pages = analysis["pages"]
        if (
            result["status"] != "succeeded"
            or len(pages) != 1
            or analysis["modelId"] != profile.model
            or analysis["apiVersion"] != profile.api_version
        ):
            raise ValueError()
        page = pages[0]
        if page["unit"] != "pixel" or (page["width"], page["height"]) != size:
            raise ValueError()
        words = []
        for word in page.get("words", []):
            polygon = word["polygon"]
            if len(polygon) != 8 or not all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                for x in polygon
            ):
                raise ValueError()
            confidence = word.get("confidence")
            if confidence is not None and (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                raise ValueError()
            text = word["content"]
            if not isinstance(text, str):
                raise ValueError()
            if any(x < -1 or x > size[i % 2] + 1 for i, x in enumerate(polygon)):
                raise ValueError()
            if text.strip():
                words.append({"text": text, "polygon": polygon, "confidence": confidence})
        return words
    except (KeyError, TypeError, ValueError) as exc:
        raise OcrError("ocr_invalid_response") from exc
