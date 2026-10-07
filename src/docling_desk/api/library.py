from __future__ import annotations

from fastapi import APIRouter, HTTPException

from docling_desk import config
from docling_desk.api.dependencies import job_detail
from docling_desk.documents.conversion import Job
from docling_desk.documents.library import (
    LOCK,
    Folder,
    FolderCreate,
    Operation,
    create_folder,
    decorate,
    operate,
    read_jobs,
    snapshot,
)
from docling_desk.documents.library import (
    load as load_library,
)
from docling_desk.explanation.provider import ExplanationError
from docling_desk.storage import document_folder
from docling_desk.translation.store import summary

router = APIRouter()


@router.get("/api/jobs", response_model=list[Job])
def jobs() -> list[Job]:
    with LOCK:
        library = load_library(config.DATA)
        visible = [decorate(library, job) for job in read_jobs(config.DATA)]
        return sorted(visible, key=lambda j: j.created, reverse=True)


@router.get("/api/library")
def library_snapshot() -> dict:
    with LOCK:
        result = snapshot(config.DATA)
    for job in result["jobs"]:
        job["translations"] = summary(document_folder(config.DATA, job["id"]))
    return result


@router.post("/api/folders", response_model=Folder, status_code=201)
def folder_create(request: FolderCreate) -> Folder:
    return create_folder(config.DATA, request)


@router.post("/api/library/operations")
def library_operation(request: Operation) -> dict:
    try:
        return operate(config.DATA, request)
    except ExplanationError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            500, "保存できませんでした。空き容量を確認して、もう一度操作してください。"
        ) from exc


router.add_api_route("/api/jobs/{job_id}", job_detail, response_model=Job)
