"""Cache a locally exported PowerPoint PDF as fixed-layout slide previews."""

import html
import json
from pathlib import Path

from pptx import Presentation

from docling_desk.preview.hashing import digest
from docling_desk.preview.pdf_view import pdf_pages, render_pdf_page

NOTICE = "原本側はPowerPointからローカルで書き出したPDFの画像表示です。文字選択は構造・RAGタブをご利用ください。"


def native_preview(source: Path, folder: Path) -> str | None:
    """Only reuse a complete cache tied to the exact original and exported PDF."""
    directory = folder / "native-preview"
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        pdf = (folder / manifest["pdf"]).resolve()
        if not pdf.is_relative_to(folder.resolve()):
            return None
        if manifest["version"] != 1 or digest(source) != manifest["source_sha256"]:
            return None
        if digest(pdf) != manifest["pdf_sha256"]:
            return None
        count = manifest["pages"]
        if type(count) is not int or not 1 <= count <= 10000:
            return None
        assets = [directory / "Preview.html"] + [
            directory / f"slide-{number}.png" for number in range(1, count + 1)
        ]
        if not all(path.is_file() for path in assets):
            return None
        return (directory / "Preview.html").relative_to(folder).as_posix()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_native_preview(source: Path, pdf: Path, folder: Path) -> str:
    """The caller supplies the matching PDF exported locally from PowerPoint."""
    pdf_relative = pdf.resolve().relative_to(folder.resolve()).as_posix()
    deck = Presentation(str(source))
    if deck.slide_width is None or deck.slide_height is None:
        raise ValueError("PowerPointのページ寸法がありません。")
    width, height = deck.slide_width / 12700, deck.slide_height / 12700
    sizes = pdf_pages(pdf)
    if len(sizes) != len(deck.slides) or not sizes:
        raise ValueError("PowerPointとPDFのページ数が一致しません。")
    if any(abs(w - width) > 0.1 or abs(h - height) > 0.1 for w, h in sizes):
        raise ValueError("PowerPointとPDFのページ寸法が一致しません。")
    original_digest, pdf_digest = digest(source), digest(pdf)
    directory = folder / "native-preview"
    directory.mkdir(exist_ok=True)
    # An interrupted rebuild must never leave the old cache marked valid.
    (directory / "manifest.json").unlink(missing_ok=True)
    for number in range(1, len(sizes) + 1):
        temporary = directory / f"slide-{number}.tmp"
        temporary.write_bytes(render_pdf_page(pdf, number, 3))
        temporary.replace(directory / f"slide-{number}.png")
    slides = "".join(
        f'<div class="slide"><img src="slide-{number}.png" '
        f'alt="原本 スライド {number}" draggable="false"></div>'
        for number in range(1, len(sizes) + 1)
    )
    preview = directory / "Preview.html"
    preview.write_text(
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        f"<title>{html.escape(source.name)}</title><style>"
        "html,body{margin:0;padding:0;background:#e8edf2}"
        f".slide{{position:relative;width:{width:g}px;height:{height:g}px;"
        "margin:12px auto;background:white}"
        ".slide img{display:block;width:100%;height:100%}</style></head><body>"
        + slides
        + "</body></html>",
        encoding="utf-8",
    )
    if digest(source) != original_digest or digest(pdf) != pdf_digest:
        raise ValueError("生成中に原本またはPDFが変更されました。再生成してください。")
    manifest = {
        "version": 1,
        "renderer": "PowerPoint local PDF export",
        "source_sha256": original_digest,
        "pdf_sha256": pdf_digest,
        "pdf": pdf_relative,
        "pages": len(sizes),
        "width": width,
        "height": height,
        "scale": 3,
    }
    temporary = directory / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(directory / "manifest.json")
    return preview.relative_to(folder).as_posix()
