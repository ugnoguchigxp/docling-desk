from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

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


@router.get("/files/{job_id}/{relative:path}")
def artifact(job_id: str, relative: str, download: bool = False) -> FileResponse:
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
    filename = job_detail(job_id).filename if relative.startswith("original.") else target.name
    return FileResponse(target, filename=filename if download else None)
