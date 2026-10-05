"""Job-specific OCR configuration and Office picture enrichment for RAG."""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from docling_azure_ocr.config import OcrError, OcrProfile, api_key, load_profile
from docling_azure_ocr.runtime import OcrRuntime
from docling_azure_ocr.transport import AzureTransport
from docling_core.types.doc import ContentLayer, DocItemLabel, DoclingDocument
from docling_core.types.doc.common.meta import BaseMeta, FloatingMeta

from docling_desk.documents.evidence import OCR_META, evidence_for, picture_refs
from docling_desk.documents.ocr_operations import FileOcrLedger
from docling_desk.storage import original_file


def local_image_reader(png: bytes, size: tuple[int, int], profile: OcrProfile) -> dict:
    words: list[dict] = []
    with TemporaryDirectory(prefix="docling-image-ocr-") as directory:
        path = Path(directory) / "image.png"
        path.write_bytes(png)
        if sys.platform == "darwin":
            from ocrmac.ocrmac import OCR

            for text, confidence, rect in OCR(
                str(path), language_preference=["ja-JP", "en-US"]
            ).recognize():
                x, y, w, h = rect
                left, top, right, bottom = (
                    x * size[0],
                    (1 - y - h) * size[1],
                    (x + w) * size[0],
                    (1 - y) * size[1],
                )
                words.append(
                    {
                        "content": text,
                        "confidence": confidence,
                        "polygon": [left, top, right, top, right, bottom, left, bottom],
                    }
                )
        else:
            try:
                process = subprocess.run(
                    ["tesseract", str(path), "stdout", "-l", "jpn+eng", "tsv"],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise OcrError("ocr_local_failed") from None
            if process.returncode:
                raise OcrError("ocr_local_failed")
            for row in csv.DictReader(io.StringIO(process.stdout), delimiter="\t"):
                if row["text"].strip() and float(row["conf"]) >= 0:
                    left, top = int(row["left"]), int(row["top"])
                    right, bottom = left + int(row["width"]), top + int(row["height"])
                    words.append(
                        {
                            "content": row["text"],
                            "confidence": float(row["conf"]) / 100,
                            "polygon": [left, top, right, top, right, bottom, left, bottom],
                        }
                    )
    return {
        "status": "succeeded",
        "analyzeResult": {
            "apiVersion": profile.api_version,
            "modelId": profile.model,
            "content": " ".join(w["content"] for w in words),
            "pages": [
                {
                    "pageNumber": 1,
                    "unit": "pixel",
                    "width": size[0],
                    "height": size[1],
                    "words": words,
                }
            ],
        },
    }


def make_runtime(
    profile: OcrProfile,
    folder: Path,
    checksum: str,
    boundary: str,
    ledger=None,
    cache: Path | None = None,
    allow_resubmit: bool = False,
) -> OcrRuntime:
    profile.check_credentials(api_key())
    return OcrRuntime(
        profile,
        ledger
        or FileOcrLedger(
            folder / "ocr-private" / "operations",
            profile.max_submissions,
            live=lambda: (
                folder.is_dir()
                and any(
                    original_file(folder, suffix).is_file()
                    for suffix in (".pdf", ".pptx", ".xlsx", ".docx")
                )
            ),
        ),
        cache or folder / "ocr-private" / "results",
        boundary,
        checksum,
        transport=AzureTransport(profile, api_key()) if profile.provider == "azure_read" else None,
        local_reader=local_image_reader,
        allow_resubmit=allow_resubmit,
    )


def enrich_office_images(doc: DoclingDocument, runtime: OcrRuntime) -> list[str]:
    warnings = []
    pictures = list(doc.pictures)
    if len(pictures) > 1000:
        raise OcrError("ocr_picture_limit")
    for picture in pictures:
        # Docling marks uniform/tiny DOCX layout spacers invisible.
        if picture.content_layer in {ContentLayer.INVISIBLE, ContentLayer.FURNITURE}:
            continue
        image = picture.get_image(doc)
        if image is None:
            # A native Office chart can carry structured data without being an embedded image.
            if picture.meta and picture.meta.tabular_chart:
                warnings.append(
                    f"{picture.self_ref}: ネイティブグラフに画像がなくOCRは実行していません。"
                )
                continue
            raise OcrError(
                "ocr_picture_unavailable", f"{picture.self_ref}: 埋め込み画像を取得できません。"
            )
        value = runtime.read(
            image,
            "picture:" + picture.self_ref,
            {
                "picture_ref": picture.self_ref,
                "provenance": [p.model_dump(mode="json") for p in picture.prov],
                "coordinate_frame": "embedded_image_pixels",
                "content_layer": picture.content_layer.value,
            },
        )
        text = value.get("text") or " ".join(w["text"] for w in value["words"])
        if not text:
            continue
        # Add a sibling, not a caption/description. Only this explicitly tagged text enters RAG.
        added = doc.insert_text(
            sibling=picture,
            label=DocItemLabel.TEXT,
            text=text,
            prov=picture.prov[0] if picture.prov else None,
        )
        added.content_layer = ContentLayer.BODY
        added.meta = BaseMeta.model_validate({OCR_META: value})
    if pictures:
        warnings.append(
            "埋め込み画像のOCR文字をRAGへ含めています。画像の意味説明は生成していません。"
        )
    return warnings


__all__ = ["OcrProfile", "load_profile", "make_runtime", "enrich_office_images", "evidence_for"]


def enrich_pdf_evidence(doc: DoclingDocument, runtime: OcrRuntime) -> None:
    """Keep accepted OCR words searchable when the layout classifies them as a picture."""
    from docling_core.types.doc import BoundingBox, CoordOrigin, ProvenanceItem, TableItem, TextItem

    excluded = picture_refs(doc)
    for value in runtime.evidence.values():
        if value.get("coordinate_frame") != "docling_backend_page_top_left":
            continue
        page = value["page_number"]
        words = value.get("accepted_words", [])
        value = {k: v for k, v in value.items() if k != "accepted_words"}
        remaining = list(words)
        for item, _ in doc.iterate_items():
            if (
                not isinstance(item, (TextItem, TableItem))
                or item.self_ref in excluded
                or page not in {p.page_no for p in item.prov}
            ):
                continue
            text = item.text if isinstance(item, TextItem) else item.export_to_markdown(doc)
            matches = [w for w in remaining if w["text"] in text]
            if matches:
                metadata = {
                    **(item.meta.model_dump() if item.meta else {}),
                    OCR_META: {**value, "words": matches},
                }
                if isinstance(item, TableItem):
                    item.meta = FloatingMeta.model_validate(metadata)
                else:
                    item.meta = BaseMeta.model_validate(metadata)
                remaining = [w for w in remaining if w not in matches]
        if remaining:
            sx, _, _, _, sy, _ = value["image_to_page"]
            xs = [x * sx for w in remaining for x in w["polygon"][0::2]]
            ys = [y * sy for w in remaining for y in w["polygon"][1::2]]
            text = " ".join(w["text"] for w in remaining)
            added = doc.add_text(
                label=DocItemLabel.TEXT,
                text=text,
                prov=ProvenanceItem(
                    page_no=page,
                    charspan=(0, len(text)),
                    bbox=BoundingBox(
                        l=min(xs), t=min(ys), r=max(xs), b=max(ys), coord_origin=CoordOrigin.TOPLEFT
                    ),
                ),
            )
            added.meta = BaseMeta.model_validate({OCR_META: {**value, "words": remaining}})
