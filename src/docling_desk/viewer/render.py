"""Read-only rendering of one accepted extraction attempt (no public storage mount)."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path

from docling_core.types.doc import DoclingDocument
from fastapi import HTTPException
from lxml import html

from docling_desk.config import RESOURCES
from docling_desk.documents.conversion import Job
from docling_desk.documents.tables import project_tables
from docling_desk.preview.document_frames import interactive_html
from docling_desk.preview.editable_preview import ensure_pdf_preview
from docling_desk.preview.pdf_view import pdf_html, pdf_pages, pdf_thumbnail
from docling_desk.preview.sheets import preview_sheets, sheet_html, workbook_html
from docling_desk.preview.thumbnails import thumbnail
from docling_desk.storage import job_file, original_file
from docling_desk.translation.source import serialize, template_for

STATIC = RESOURCES / "static"
JSON_FILES = {
    "elements.json",
    "slides.json",
    "rag-policy.json",
    "rag.jsonl",
    "rag-index.jsonl",
    "rag-docling.jsonl",
}
ASSET_SUFFIXES = {
    ".html",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".css",
}


def render_resource(folder: Path, resource: str, prefix: str) -> dict:
    if not re.fullmatch(r"/viewer/session/[A-Za-z0-9_-]{43}/", prefix):
        raise HTTPException(422, "invalid_viewer_prefix")
    if "?" in resource:
        resource = resource.split("?", 1)[0]
    if any(p in {"", ".", ".."} for p in resource.split("/")) or "\\" in resource:
        raise HTTPException(422, "invalid_viewer_resource")
    try:
        job = Job.model_validate_json((job_file(folder)).read_text())
    except (OSError, ValueError):
        raise HTTPException(404, "preview_unavailable") from None
    if job.state != "success" or not job.preview:
        raise HTTPException(409, "preview_unavailable")
    kind = (
        "slide"
        if job.slide_layout
        else "sheet"
        if job.filename.lower().endswith(".xlsx")
        else "page"
        if job.filename.lower().endswith(".pdf")
        else "document"
    )
    if resource == "manifest":
        fields = {
            "id",
            "filename",
            "original_filename",
            "folder_id",
            "state",
            "created",
            "duration",
            "pages",
            "tables",
            "pictures",
            "chunks",
            "search_chunks",
            "rag_policy",
            "error",
            "preview",
            "slide_layout",
        }
        return {
            "job": {k: v for k, v in job.model_dump().items() if k in fields},
            "kind": kind,
            "units": job.pages if kind != "document" else 1,
        }

    def url(value: str) -> str:
        for ident in {job.id, folder.name}:
            for start, end in (
                (f"/files/{ident}/", "file/"),
                (f"/view/{ident}/", "view/"),
                (f"/api/jobs/{ident}/", "api/"),
            ):
                if value.startswith(start):
                    return prefix + end + value[len(start) :]
        if value.startswith("/static/"):
            return prefix + "static/" + value[8:]
        return value

    def css(raw: str) -> str:
        return re.sub(
            r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)",
            lambda m: "url(" + m[1] + url(m[2].strip()) + m[1] + ")",
            raw,
        )

    def rewrite(raw: str, kind: str = ".html") -> str:
        # Rewrite actual resource locations, never visible text: a document
        # quoting /static/ must not acquire a session credential in its text.
        if kind == ".js":
            return raw.replace("/view/${jobId}/", prefix + "view/")
        if kind == ".css":
            return css(raw)
        tree = html.fromstring(raw)
        for node in list(tree.iter()):
            if not isinstance(node.tag, str):
                continue
            for attr in (
                "src",
                "href",
                "xlink:href",
                "data-url",
                "poster",
                "{http://www.w3.org/1999/xlink}href",
            ):
                if attr in node.attrib:
                    node.set(attr, url(node.get(attr)))
            if "style" in node.attrib:
                node.set("style", css(node.get("style")))
            if node.tag.lower() == "style" and node.text:
                node.text = css(node.text)
            if node.tag.lower() == "script" and (node.get("src") or "").endswith("/file-drop.js"):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)
        return serialize(tree, raw)

    media = "text/html; charset=utf-8"
    if resource == "api/tables":
        tables = project_tables(
            DoclingDocument.load_from_json(folder / "document.json"), job.filename
        )
        data = json.dumps([t.model_dump() for t in tables], ensure_ascii=False).encode()
        media = "application/json"
    elif resource.startswith("view/"):
        route = resource[5:]
        if route == "pdf" and kind == "page":
            source = original_file(folder, ".pdf")
            ensure_pdf_preview(source, folder)
            raw = pdf_html(job.id, job.filename, pdf_pages(source))
        elif route == "workbook" and kind == "sheet":
            raw = workbook_html(job.id, job.filename, preview_sheets(folder, job.preview))
        elif route in {"word", "document"} and kind == "document":
            raw = interactive_html(template_for(folder, job.model_dump(), 1), job.id, "document-1")
        elif re.fullmatch(r"(pages|slides|sheets)/[1-9][0-9]*", route):
            group, number = route.split("/")
            number = int(number)
            expected = {"page": "pages", "slide": "slides", "sheet": "sheets"}.get(kind)
            if group != expected or not 1 <= number <= job.pages:
                raise HTTPException(422, "invalid_location")
            if kind == "sheet":
                sheets = preview_sheets(folder, job.preview)
                sheet = next((s for s in sheets if s["number"] == number), None)
                if sheet is None:
                    raise HTTPException(422, "invalid_location")
                raw = sheet_html(folder, job.id, sheet)
            else:
                raw = template_for(folder, job.model_dump(), number)
            raw = interactive_html(raw, job.id, f"{kind}-{number}")
        else:
            raise HTTPException(404, "viewer_resource_not_found")
        data = rewrite(raw).encode()
    elif resource.startswith("api/"):
        match = re.fullmatch(r"api/(slides|pdf/pages)/([1-9][0-9]*)/thumbnail", resource)
        if (
            not match
            or (match[1] == "slides") != (kind == "slide")
            or kind not in {"slide", "page"}
            or not 1 <= int(match[2]) <= job.pages
        ):
            raise HTTPException(404, "viewer_resource_not_found")
        target = (
            thumbnail(folder, int(match[2]))
            if kind == "slide"
            else pdf_thumbnail(original_file(folder, ".pdf"), int(match[2]))
        )
        data, media = target.read_bytes(), "image/webp"
    elif resource.startswith(("file/", "static/")):
        group, relative = resource.split("/", 1)
        root = folder if group == "file" else STATIC
        target = root / relative
        if root.resolve() not in target.resolve().parents or any(
            (root / Path(*Path(relative).parts[:i])).is_symlink()
            for i in range(1, len(Path(relative).parts) + 1)
        ):
            raise HTTPException(404, "viewer_resource_not_found")
        if group == "file" and (
            relative.startswith(("ocr-private/", "translations/", "explanations/"))
            or relative.startswith("original.")
            or (relative not in JSON_FILES and target.suffix.lower() not in ASSET_SUFFIXES)
        ):
            raise HTTPException(404, "viewer_resource_not_found")
        if group == "static" and (
            target.suffix not in {".css", ".js"} or relative.startswith("frontend/")
        ):
            raise HTTPException(404, "viewer_resource_not_found")
        if not target.is_file() or target.stat().st_size > 32 * 1024 * 1024:
            raise HTTPException(404, "viewer_resource_not_found")
        data = target.read_bytes()
        media = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if target.suffix in {".html", ".js", ".css"}:
            data = rewrite(data.decode("utf-8"), target.suffix).encode()
        if target.suffix == ".jsonl":
            media = "application/x-ndjson"
    else:
        raise HTTPException(404, "viewer_resource_not_found")
    return {"media_type": media, "body_base64": base64.b64encode(data).decode()}
