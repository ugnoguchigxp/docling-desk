from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from docling_desk.api.dependencies import job_folder
from docling_desk.explanation.provider import ExplanationError
from docling_desk.explanation.service import overview as explanation_overview
from docling_desk.explanation.service import result as explanation_result
from docling_desk.explanation.store import markdown as explanation_markdown

router = APIRouter()


class ExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    unit_id: str = Field(max_length=40)
    force: bool = False


def explanation_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, ExplanationError):
        return HTTPException(
            {
                "input": 422,
                "not_found": 404,
                "conflict": 409,
                "queue_full": 429,
                "storage": 500,
                "storage_limit": 507,
                "storage_space": 507,
            }.get(exc.code, 503),
            str(exc),
        )
    return HTTPException(
        422, "解説データを読み込めません。原文データと空き容量を確認してください。"
    )


@router.get("/api/jobs/{job_id}/explanations")
def explanations_status(job_id: str) -> JSONResponse:
    try:
        return JSONResponse(
            explanation_overview(job_folder(job_id)), headers={"Cache-Control": "no-store"}
        )
    except (ExplanationError, OSError, ValueError, KeyError, TypeError) as exc:
        raise explanation_failure(exc) from exc


@router.post("/api/jobs/{job_id}/explanations", status_code=202)
def explanations_start(job_id: str, payload: ExplanationRequest, request: Request) -> JSONResponse:
    try:
        value = request.app.state.explanation_manager.submit(
            job_folder(job_id), payload.unit_id, payload.force
        )
        return JSONResponse(
            value,
            status_code=202 if value["state"]["state"] in {"queued", "running"} else 200,
            headers={"Cache-Control": "no-store"},
        )
    except (ExplanationError, OSError, ValueError, KeyError, TypeError) as exc:
        raise explanation_failure(exc) from exc


@router.get("/api/jobs/{job_id}/explanations/{unit_id}")
def explanations_result(job_id: str, unit_id: str, download: str | None = None) -> Response:
    try:
        value = explanation_result(job_folder(job_id), unit_id)
        headers = {"Cache-Control": "no-store"}
        if download:
            if download not in {"json", "markdown"}:
                raise HTTPException(422, "保存形式が不正です。")
            if not value["available"]:
                raise HTTPException(404, "完成版はまだありません。")
            suffix = "md" if download == "markdown" else "json"
            headers["Content-Disposition"] = (
                f'attachment; filename="explanation-{unit_id}.{suffix}"'
            )
            if download == "markdown":
                return Response(
                    explanation_markdown(value["result"]),
                    media_type="text/markdown",
                    headers=headers,
                )
            return JSONResponse(value["result"], headers=headers)
        return JSONResponse(value, headers=headers)
    except (ExplanationError, OSError, ValueError, KeyError, TypeError) as exc:
        raise explanation_failure(exc) from exc
