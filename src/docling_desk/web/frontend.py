"""React bundle delivery; document viewers continue to use their existing routes."""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from docling_desk import config
from docling_desk.config import RESOURCES as ROOT
from docling_desk.web.rootpath import base_meta

router = APIRouter()
BUNDLE = ROOT / "static" / "frontend"


def frontend_index() -> Response:
    index = BUNDLE / "index.html"
    if not index.is_file():
        raise HTTPException(503, "画面のビルドがありません。frontendをビルドしてください。")
    headers = {"Cache-Control": "no-cache"}
    if not config.ROOT_PATH:
        return FileResponse(index, headers=headers)
    # The page and its assets live below the proxy prefix; the UI reads it from here.
    page = index.read_text(encoding="utf-8").replace(
        "<head>", "<head>" + base_meta(config.ROOT_PATH), 1
    )
    return HTMLResponse(page, headers=headers)


def default_index() -> FileResponse:
    # Set only this flag to roll back the UI; saved documents are never restored.
    if os.environ.get("DOCLING_LEGACY_UI") == "1":
        return legacy_index()
    return frontend_index()


@router.get("/ui/")
def ui_index() -> FileResponse:
    return frontend_index()


@router.get("/legacy/")
def legacy_index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@router.get("/static/frontend/{name:path}")
def frontend_asset(name: str) -> FileResponse:
    path = (BUNDLE / name).resolve()
    if not path.is_relative_to(BUNDLE.resolve()) or not path.is_file():
        raise HTTPException(404)
    if path.suffix not in {".html", ".js", ".css", ".svg", ".txt"}:
        raise HTTPException(404)
    immutable = bool(re.fullmatch(r"assets/[\w-]+-[\w-]{8,}\.(?:js|css)", name))
    return FileResponse(
        path,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"
        },
    )
