"""Text inputs share document units without inventing page provenance."""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

from docling_core.types.doc import DocItemLabel, DoclingDocument, DocumentOrigin

from docling_desk.documents.pagination import referenced_html

MARKDOWN_SUFFIXES = {".md", ".markdown"}
TEXT_SUFFIXES = {".txt", ".text"}
TEXT_DOCUMENT_SUFFIXES = MARKDOWN_SUFFIXES | TEXT_SUFFIXES
DOCUMENT_SUFFIXES = {".docx"} | TEXT_DOCUMENT_SUFFIXES
SUPPORTED_SUFFIXES = {".pdf", ".pptx", ".xlsx"} | DOCUMENT_SUFFIXES
SUPPORTED_LABEL = "PDF / PPTX / XLSX / DOCX / Markdown / テキスト"


def read_text_input(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    else:
        encodings = ["utf-8-sig", "cp932"]
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
            raise ValueError("テキストにバイナリデータが含まれています。")
        return text
    raise ValueError("文字コードを読み込めません。UTF-8、UTF-16、Shift_JISで保存してください。")


def plain_document(source: Path, text: str) -> DoclingDocument:
    doc = DoclingDocument(
        name=source.stem,
        origin=DocumentOrigin(
            filename=source.name,
            mimetype="text/plain",
            binary_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
        ),
    )
    for paragraph in re.split(r"(?:\r?\n){2,}", text):
        if paragraph.strip():
            doc.add_text(label=DocItemLabel.TEXT, text=paragraph)
    return doc


def text_preview(doc: DoclingDocument, source: Path, text: str, folder: Path) -> str:
    if source.suffix in TEXT_SUFFIXES:
        raw = (
            '<!doctype html><html lang="ja"><head><meta charset="utf-8"></head>'
            '<body><div class="page"><pre class="plain-text">'
            + html.escape(text)
            + "</pre></div></body></html>"
        )
    else:
        raw = referenced_html(doc)
    style = """<style>
body{margin:0;padding:12px;font-family:system-ui,sans-serif;line-height:1.7}
.page{box-sizing:border-box;width:840px;margin:auto;padding:32px;background:white}
.plain-text{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;margin:0}
pre:not(.plain-text){white-space:pre-wrap;overflow-wrap:anywhere}
table{max-width:100%;border-collapse:collapse}td,th{border:1px solid #ccd3da;padding:6px 10px}
img{max-width:100%}
</style>"""
    raw = raw.replace("</head>", style + "</head>", 1)
    target = folder / "text-preview.html"
    target.write_text(raw, encoding="utf-8")
    return target.name
