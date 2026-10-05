from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from docling_desk import config
from docling_desk.documents.conversion import Job
from docling_desk.documents.library import (
    LOCK,
    decorate,
)
from docling_desk.documents.library import (
    load as load_library,
)
from docling_desk.storage import document_folder, job_file, safe_path


def job_folder(job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(404)
    folder = document_folder(config.DATA, job_id)
    if (
        not safe_path(config.DATA, folder)
        or not safe_path(config.DATA, job_file(folder))
        or not job_file(folder).is_file()
    ):
        raise HTTPException(404)
    return folder


def job_detail(job_id: str) -> Job:
    with LOCK:
        job = Job.model_validate_json(job_file(job_folder(job_id)).read_text())
        return decorate(load_library(config.DATA), job)


def json_job(folder: Path) -> dict:
    return Job.model_validate_json((job_file(folder)).read_text()).model_dump()
