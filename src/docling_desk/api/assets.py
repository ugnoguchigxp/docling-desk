from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from docling_desk import config
from docling_desk.api.dependencies import job_detail, job_folder
from docling_desk.documents.text import SUPPORTED_SUFFIXES
from docling_desk.storage import original_file, safe_path
from docling_desk.web.frontend import default_index

router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    return default_index()


@router.get("/static/{name}")
def static(name: str) -> FileResponse:
    if name not in {
        "app.js",
        "library.js",
        "file-drop.js",
        "document-frame.js",
        "document-frame.css",
        "style.css",
        "tables.js",
        "slides.js",
        "rag.js",
        "table-common.js",
        "tabulens-grid.js",
        "table-ui.css",
        "inline-tables.js",
        "extracted-view.css",
        "sheets.js",
        "sheets.css",
        "sheet-frame.js",
        "pdf-viewer.js",
        "pdf-viewer.css",
        "translation.js",
        "translation.css",
        "explanation.js",
        "explanation.css",
    }:
        raise HTTPException(404)
    return FileResponse(config.RESOURCES / "static" / name)


@router.get("/static/vendor/{name}")
def vendor(name: str) -> FileResponse:
    if name not in {
        "ag-grid-community.min.js",
        "ag-grid.min.css",
        "ag-theme-quartz.min.css",
    }:
        raise HTTPException(404)
    return FileResponse(config.RESOURCES / "static/vendor" / name)


@router.get("/files/{job_id}/{relative:path}", response_model=None)
def artifact(
    job_id: str,
    relative: str,
    download: bool = False,
    inline_fonts: bool = False,
    thumbnail: bool = False,
) -> Response:
    folder = job_folder(job_id)
    if relative in {"original" + suffix for suffix in SUPPORTED_SUFFIXES}:
        target = original_file(folder, relative[len("original") :])
        if not safe_path(config.DATA, target) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target, filename=job_detail(job_id).filename if download else None)
    target = (folder / relative).resolve()
    if (
        not target.is_relative_to(folder.resolve())
        or target.is_relative_to((folder / "explanations").resolve())
        or target.is_relative_to((folder / "ocr-private").resolve())
        or not target.is_file()
        or target.name in {"job.tmp", "job.json"}
        or target.name.endswith(".tmp")
    ):
        raise HTTPException(404)
    # Keep the passive, opaque-origin sandbox without authenticated CSS
    # subrequests. Only generated recovery pages and their matching font file
    # are eligible; never inline arbitrary links or alter stored artifacts.
    match = re.fullmatch(r"progressive-preview/page-(\d+)\.html", relative)
    if (inline_fonts or thumbnail) and not download and match:
        font = target.with_name(f"fonts-{match[1]}.css")
        if not font.is_file() or not safe_path(folder, font):
            raise HTTPException(404, "ページのフォントを取得できません。")
        try:
            raw = target.read_text(encoding="utf-8")
            css = font.read_text(encoding="utf-8").replace("<", r"\3c ")
        except UnicodeError as error:
            raise HTTPException(
                409, "保存済みページまたはフォントの文字形式が不正です。"
            ) from error
        except OSError as error:
            raise HTTPException(404, "保存済みページまたはフォントを読み込めません。") from error
        link = f'<link rel="stylesheet" href="fonts-{match[1]}.css">'
        if raw.count(link) != 1:
            raise HTTPException(409, "ページのフォント情報を確認できません。")
        if thumbnail:
            start = raw.find("<svg ")
            end = raw.rfind("</svg>")
            if start < 0 or end < start:
                raise HTTPException(409, "ページ画像を確認できません。")
            svg = raw[start : end + len("</svg>")]
            pos = svg.find(">") + 1
            return Response(
                svg[:pos] + "<style>" + css + "</style>" + svg[pos:], media_type="image/svg+xml"
            )
        return HTMLResponse(raw.replace(link, "<style>" + css + "</style>"))
    filename = job_detail(job_id).filename if relative.startswith("original.") else target.name
    return FileResponse(target, filename=filename if download else None)
