from __future__ import annotations

import hashlib
import html
import math
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from docling.utils.locks import pypdfium2_lock

from docling_desk.preview.thumbnails import (
    MAX_THUMBNAIL_BYTES,
    WEBP_METHOD,
    WEBP_QUALITY,
    compact_cached_thumbnail,
    encode_thumbnail,
    prune_cache,
)
from docling_desk.storage import document_cache, source_folder


def pdf_thumbnail(source: Path, number: int) -> Path:
    if number < 1:
        raise IndexError(number)
    stat = source.stat()
    signature = hashlib.sha256(
        f"{stat.st_mtime_ns}:{stat.st_size}:240:{WEBP_QUALITY}:{WEBP_METHOD}".encode()
    ).hexdigest()[:16]
    target = (
        document_cache(source_folder(source)) / "thumbnails" / f"page-{number}-{signature}.webp"
    )
    if target.is_file() and target.stat().st_size <= MAX_THUMBNAIL_BYTES:
        return target
    with pypdfium2_lock:
        if target.is_file():
            compact_cached_thumbnail(target)
            return target
        with pdfium.PdfDocument(source) as document:
            if number > len(document):
                raise IndexError(number)
            page = document[number - 1]
            temporary = target.with_suffix(".tmp")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                width, height = page.get_size()
                bitmap = page.render(scale=240 / max(width, height))
                try:
                    temporary.write_bytes(encode_thumbnail(bitmap.to_pil()))
                finally:
                    bitmap.close()
                temporary.replace(target)
                prune_cache(target, number, "page")
            finally:
                temporary.unlink(missing_ok=True)
                page.close()
    return target


def pdf_pages(source: Path) -> list[tuple[float, float]]:
    # Share Docling's lock: PDFium must not run concurrently with conversion.
    with pypdfium2_lock, pdfium.PdfDocument(source) as document:
        sizes = []
        for index in range(len(document)):
            page = document[index]
            try:
                sizes.append(page.get_size())
            finally:
                page.close()
        return sizes


def render_pdf_page(source: Path, number: int, scale: float) -> bytes:
    with pypdfium2_lock, pdfium.PdfDocument(source) as document:
        if not 1 <= number <= len(document):
            raise IndexError(number)
        page = document[number - 1]
        try:
            width, height = page.get_size()
            # Bound memory even for unusually large PDF page dimensions.
            scale = min(scale, math.sqrt(12_000_000 / (width * height)), 8192 / max(width, height))
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil()
                with BytesIO() as stream:
                    image.save(stream, format="PNG")
                    return stream.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()


def pdf_html(job_id: str, filename: str, sizes: list[tuple[float, float]]) -> str:
    thumbnails = "".join(
        f'<button type="button" class="pdf-thumbnail" data-number="{number}" '
        f'aria-label="ページ {number}" aria-current="false" tabindex="-1">'
        f'<span class="pdf-thumbnail-image" style="aspect-ratio:{width}/{height}">'
        f'<img data-url="/api/jobs/{job_id}/pdf/pages/{number}/thumbnail" '
        f'alt="ページ {number}のプレビュー" decoding="async"></span>'
        f"<span>{number}</span></button>"
        for number, (width, height) in enumerate(sizes, 1)
    )
    pages = "".join(
        f'<figure class="pdf-page" id="page-{number}" data-number="{number}" '
        f'data-width="{width * 4 / 3}" data-height="{height * 4 / 3}">'
        f'<iframe class="pdf-page-frame" data-url="/view/{job_id}/pages/{number}" '
        f'title="原本 {number} ページ" sandbox="allow-scripts"></iframe><figcaption>{number} / {len(sizes)}</figcaption></figure>'
        for number, (width, height) in enumerate(sizes, 1)
    )
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(filename)} · PDFプレビュー</title>
<link rel="stylesheet" href="/static/pdf-viewer.css">
<script src="/static/file-drop.js" defer></script>
<script src="/static/document-frame.js" defer></script>
<script src="/static/pdf-viewer.js" defer></script></head>
<body data-job="{job_id}">
<div class="pdf-toolbar" role="toolbar" aria-label="PDFの表示操作">
<div class="pdf-navigation"><button id="pdfThumbnailsToggle" aria-label="サムネイルを表示・非表示" aria-pressed="true" title="サムネイルを表示・非表示"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="4" width="5" height="6" rx="1"/><rect x="3" y="14" width="5" height="6" rx="1"/><path d="M12 5h9v14h-9z"/></svg></button><button id="pdfPrevious" aria-label="前のページ">‹</button>
<label>ページ <input id="pdfPage" type="number" min="1" max="{len(sizes)}" value="1" aria-label="ページ番号"></label>
<span>/ {len(sizes)}</span><button id="pdfNext" aria-label="次のページ">›</button></div>
<div class="pdf-zoom"><button id="pdfZoomOut" aria-label="縮小">−</button>
<select id="pdfZoom" aria-label="ズーム倍率"><option value="current"></option>
{"".join(f'<option value="{zoom}">{zoom}%</option>' for zoom in [25, 50, 75, 100, 125, 150, 200, 300, 400])}</select>
<button id="pdfZoomIn" aria-label="拡大">＋</button></div>
<div class="pdf-fit"><button id="pdfWidth" aria-pressed="true">幅に合わせる</button>
<button id="pdfFit" aria-pressed="false">ページ全体</button></div></div>
<p id="pdfStatus" role="status" hidden></p>
<div class="pdf-workspace"><nav id="pdfThumbnails" aria-label="ページのサムネイル">{thumbnails}</nav>
<div id="pdfStage" tabindex="0" role="region" aria-label="PDF表示領域">
<div id="pdfPages">{pages}</div></div></div></body></html>'''
