"""Cache tiny raster previews of existing Quick Look slides, one renderer at a time."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import threading
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image

from docling_desk.config import CACHE
from docling_desk.config import RESOURCES as ROOT
from docling_desk.storage import document_cache, original_file

RENDER_LOCK = threading.Lock()
SOURCE = ROOT / "scripts/render-slide-thumbnail.swift"
BINARY = CACHE / "render-slide-thumbnail"
WEBP_QUALITY = 45
WEBP_METHOD = 6
MAX_THUMBNAIL_BYTES = 2000
THUMBNAIL_SIZE = (112, 160)


def encode_thumbnail(image: Image.Image) -> bytes:
    """Preserve current quality where possible, with a strict per-image size cap."""
    candidate = image.convert("RGB")
    candidate.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
    while True:
        for quality in (WEBP_QUALITY, 35, 25):
            with BytesIO() as stream:
                candidate.save(stream, format="WEBP", quality=quality, method=WEBP_METHOD)
                encoded = stream.getvalue()
            if len(encoded) <= MAX_THUMBNAIL_BYTES:
                return encoded
        width, height = candidate.size
        if max(width, height) <= 1:
            raise RuntimeError("サムネイルの容量を制限できませんでした。")
        candidate.thumbnail(
            (max(1, int(width * 0.8)), max(1, int(height * 0.8))), Image.Resampling.LANCZOS
        )


def compact_cached_thumbnail(target: Path) -> None:
    """Also enforce the size limit on thumbnails saved by earlier versions."""
    if target.stat().st_size <= MAX_THUMBNAIL_BYTES:
        return
    temporary = target.with_suffix(".tmp")
    try:
        with Image.open(target) as image:
            temporary.write_bytes(encode_thumbnail(image))
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def slide_source(folder: Path, number: int) -> tuple[Path, float, float]:
    slides = json.loads((folder / "slides.json").read_text(encoding="utf-8"))["slides"]
    slide = next((slide for slide in slides if slide["number"] == number), None)
    if not slide or not slide.get("preview"):
        raise ValueError("スライドのプレビューが見つかりません。")
    source = (folder / slide["preview"]).resolve()
    width, height = float(slide["width"]), float(slide["height"])
    if (
        not source.is_relative_to(folder.resolve())
        or source.suffix != ".html"
        or not source.is_file()
        or not all(math.isfinite(value) and 0 < value <= 10000 for value in (width, height))
    ):
        raise ValueError("スライドのプレビューが見つかりません。")
    return source, width, height


def cache_path(folder: Path, number: int, source: Path) -> Path:
    # Assets and renderer changes invalidate thumbnails without touching originals.
    dependencies = [folder / "slides.json", SOURCE, *sorted(source.parent.iterdir())]
    signature = hashlib.sha256()
    signature.update(f"webp:{WEBP_QUALITY}:{WEBP_METHOD}:{sys.platform}".encode())
    if sys.platform == "linux":
        dependencies.append(folder / "powerpoint-rendered.pdf")
    for path in dependencies:
        if path.is_file():
            stat = path.stat()
            signature.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}\n".encode())
    return (
        document_cache(folder) / "thumbnails" / f"slide-{number}-{signature.hexdigest()[:16]}.webp"
    )


def prune_cache(target: Path, number: int, prefix: str = "slide") -> None:
    """Keep one generated thumbnail per slide, after its replacement is saved."""
    pattern = re.compile(rf"{re.escape(prefix)}-{number}-[0-9a-f]{{16}}\.(?:png|webp)")
    for previous in target.parent.iterdir():
        if previous != target and pattern.fullmatch(previous.name):
            previous.unlink(missing_ok=True)


def renderer() -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("サムネイル生成はmacOSで利用できます。")
    BINARY.parent.mkdir(exist_ok=True)
    if not BINARY.is_file() or BINARY.stat().st_mtime_ns < SOURCE.stat().st_mtime_ns:
        temporary = BINARY.with_suffix(".tmp")
        try:
            subprocess.run(
                ["/usr/bin/xcrun", "swiftc", str(SOURCE), "-o", str(temporary)],
                check=True,
                capture_output=True,
                timeout=60,
            )
            temporary.replace(BINARY)
        finally:
            temporary.unlink(missing_ok=True)
    return BINARY


def thumbnail(folder: Path, number: int) -> Path:
    source, width, height = slide_source(folder, number)
    pdf = None
    if sys.platform == "linux":
        from docling_desk.preview.powerpoint_export import export_powerpoint

        pdf = export_powerpoint(original_file(folder, ".pptx"), folder)
    target = cache_path(folder, number, source)
    if target.is_file() and target.stat().st_size <= MAX_THUMBNAIL_BYTES:
        return target
    with RENDER_LOCK:
        if target.is_file():
            compact_cached_thumbnail(target)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        try:
            if pdf is not None:
                with pymupdf.open(pdf) as document:
                    if not 1 <= number <= len(document):
                        raise ValueError("スライドのプレビューが見つかりません。")
                    page = document[number - 1]
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(240 / page.rect.width, 240 / page.rect.width)
                    )
                    with Image.frombytes(
                        "RGB", (pixmap.width, pixmap.height), pixmap.samples
                    ) as image:
                        temporary.write_bytes(encode_thumbnail(image))
            else:
                result = subprocess.run(
                    [str(renderer()), str(source), str(width), str(height), "-"],
                    check=True,
                    capture_output=True,
                    timeout=20,
                )
                # Intermediate raster bytes stay in memory; only WebP is saved.
                with Image.open(BytesIO(result.stdout)) as image:
                    temporary.write_bytes(encode_thumbnail(image))
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("サムネイルを生成できませんでした。")
            temporary.replace(target)
            prune_cache(target, number)
        finally:
            temporary.unlink(missing_ok=True)
    return target
