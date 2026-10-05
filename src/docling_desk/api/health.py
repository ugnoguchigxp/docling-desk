from __future__ import annotations

import shutil

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from docling_desk import config
from docling_desk.config import MIN_FREE
from docling_desk.operations.models import problems as model_problems

router = APIRouter()


@router.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready() -> JSONResponse:
    reasons: list[str] = []
    try:
        if not (config.RESOURCES / "static/frontend/index.html").is_file():
            reasons.append("frontend_missing")
        if not config.DATA.is_dir():
            reasons.append("data_missing")
        elif shutil.disk_usage(config.DATA).free < MIN_FREE:
            reasons.append("disk_space")
        reasons.extend(model_problems(config.MODELS, config.RESOURCES / "models/manifest.json"))
    except ValueError:
        reasons.append("manifest_invalid")
    except OSError:
        reasons.append("unreadable")
    ready = not reasons
    body: dict[str, object] = {"status": "ready" if ready else "unavailable"}
    if reasons:
        body["reasons"] = reasons
    return JSONResponse(body, status_code=200 if ready else 503)
