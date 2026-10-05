from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from docling_desk.api.dependencies import job_folder
from docling_desk.documents.conversion import Job
from docling_desk.preview.document_frames import interactive_html
from docling_desk.storage import job_file
from docling_desk.translation.provider import TranslationError
from docling_desk.translation.service import overview
from docling_desk.translation.source import source_map
from docling_desk.translation.store import read_result, usable
from docling_desk.translation.view import apply_text

router = APIRouter()


class TranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_language: str
    unit_ids: list[str] | None = Field(default=None, max_length=10000)
    force: bool = False
    interval_seconds: int | None = Field(default=None, ge=0, le=3600, strict=True)


def translation_folder(job_id: str) -> Path:
    folder = job_folder(job_id)
    job = Job.model_validate_json((job_file(folder)).read_text())
    if job.state not in {"success", "partial"}:
        raise HTTPException(409, "抽出完了後に翻訳できます。")
    return folder


def translation_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, TranslationError):
        status = {"input": 422, "conflict": 409, "queue_full": 429}.get(exc.code, 503)
        return HTTPException(status, str(exc))
    return HTTPException(
        422, "翻訳データを読み込めません。原文プレビューと空き容量を確認してください。"
    )


@router.get("/api/jobs/{job_id}/translations")
def translations_status(job_id: str) -> dict:
    folder = translation_folder(job_id)
    try:
        return overview(folder)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        raise translation_failure(exc) from exc


@router.post("/api/jobs/{job_id}/translations", status_code=202)
def translations_start(job_id: str, payload: TranslationRequest, request: Request) -> dict:
    folder = translation_folder(job_id)
    try:
        return request.app.state.translation_manager.submit(
            folder,
            payload.target_language,
            payload.unit_ids,
            payload.force,
            interval_seconds=payload.interval_seconds,
        )
    except (TranslationError, OSError, ValueError, KeyError, StopIteration) as exc:
        raise translation_failure(exc) from exc


@router.get("/api/jobs/{job_id}/translations/{language}/{unit_id}")
def translation_result(
    job_id: str, language: str, unit_id: str, download: bool = False
) -> JSONResponse:
    folder = translation_folder(job_id)
    try:
        source = source_map(folder)
        unit = next((u for u in source["units"] if u["id"] == unit_id), None)
        if unit is None:
            raise HTTPException(404, "翻訳対象が見つかりません。")
        record = read_result(folder, language, unit_id)
        result = {**record, "available": usable(record, unit), "mode": unit["mode"]}
        if result["available"] and unit["mode"] == "panel":
            result["texts"] = [record["result"]["translations"][s["id"]] for s in unit["segments"]]
        headers = {"Cache-Control": "no-store"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{unit_id}-{language}.json"'
        return JSONResponse(result, headers=headers)
    except (OSError, ValueError, KeyError) as exc:
        raise translation_failure(exc) from exc


def translated_html(job_id: str, unit_id: str, raw: str, language: str) -> HTMLResponse:
    if language not in {"original", "en", "ja"}:
        raise HTTPException(422, "表示言語が不正です。")
    headers = {"Cache-Control": "no-store", "X-Translation-State": "original"}
    if language != "original":
        folder = translation_folder(job_id)
        try:
            unit = next(u for u in source_map(folder)["units"] if u["id"] == unit_id)
            record = read_result(folder, language, unit_id)
            if usable(record, unit) and unit["mode"] == "replace":
                raw = apply_text(raw, unit, record["result"]["translations"])
                headers["X-Translation-State"] = "translated"
            else:
                headers["X-Translation-State"] = "unavailable"
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            raise translation_failure(exc) from exc
    return HTMLResponse(interactive_html(raw, job_id, unit_id), headers=headers)
