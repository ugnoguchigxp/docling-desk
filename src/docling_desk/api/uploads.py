from __future__ import annotations

import shutil
import threading
from pathlib import Path
from uuid import uuid4

from docling_azure_ocr.config import api_key
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from docling_desk import config
from docling_desk.config import MAX_BYTES, MIN_FREE
from docling_desk.documents.conversion import Job, convert_job, save_job, validate_input
from docling_desk.documents.library import (
    LOCK,
    decorate,
    destination_exists,
    register_upload,
)
from docling_desk.documents.library import (
    load as load_library,
)
from docling_desk.documents.ocr_profiles import load_profile
from docling_desk.documents.text import SUPPORTED_LABEL, SUPPORTED_SUFFIXES
from docling_desk.storage import document_folder, original_file, record_original, remove_document

router = APIRouter()


def run_job(folder: Path, job: Job, slots: threading.BoundedSemaphore) -> None:
    try:
        convert_job(folder, job)
    finally:
        slots.release()


@router.post("/api/upload", response_model=Job, status_code=202)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    folder_id: str | None = Form(default=None),
    ocr_provider: str | None = Form(default=None),
) -> Job:
    folder_id = folder_id or None
    with LOCK:
        destination_exists(load_library(config.DATA), folder_id)
    filename = Path((file.filename or "").replace("\\", "/")).name
    if Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(415, f"{SUPPORTED_LABEL}を選んでください。")
    try:
        profile = load_profile(ocr_provider)
        profile.check_credentials(api_key())
    except (ValueError, RuntimeError):
        raise HTTPException(
            422, "OCR設定が無効です。Azureの送信許可と認証設定を確認してください。"
        ) from None
    if shutil.disk_usage(config.DATA).free < MIN_FREE:
        raise HTTPException(507, "空き容量が2 GiB未満です。処理を開始しません。")
    worker = getattr(request.app.state, "worker", None)
    slots = getattr(request.app.state, "slots", None)
    if worker is None or slots is None:
        raise HTTPException(503, "処理を受け付ける準備ができていません。")
    if not slots.acquire(blocking=False):
        raise HTTPException(429, "処理待ちは最大3件です。完了後に追加してください。")
    job = Job(
        id=uuid4().hex,
        filename=filename,
        synthetic=filename.startswith("synthetic-"),
        ocr_profile=profile.model_dump(mode="json"),
    )
    folder = document_folder(config.DATA, job.id)
    source = original_file(folder, Path(filename).suffix.lower())
    try:
        folder.mkdir(parents=True)
        total = 0
        source.parent.mkdir(parents=True, exist_ok=True)
        with source.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise HTTPException(413, "上限50 MiBを超えています。")
                stream.write(chunk)
        validate_input(source)
        record_original(folder, filename, job.created)
        with LOCK:
            register_upload(config.DATA, job, folder_id)
            save_job(folder, job)
            worker.submit(run_job, folder, job, slots)
            return decorate(load_library(config.DATA), job)
    except (OSError, RuntimeError) as exc:
        slots.release()
        if folder.is_dir():
            remove_document(folder)
        raise HTTPException(
            500,
            "ローカル保存または処理キューの開始に失敗しました。空き容量とログを確認してください。",
        ) from exc
    except (ValueError, HTTPException) as exc:
        slots.release()
        remove_document(folder)  # Only this request's newly created incomplete upload.
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()


@router.get("/api/ocr")
def ocr_capabilities() -> dict:
    available = False
    try:
        load_profile("azure_read").check_credentials(api_key())
        available = True
    except (ValueError, RuntimeError):
        pass
    return {"azure_available": available, "default_provider": load_profile("local").provider}
