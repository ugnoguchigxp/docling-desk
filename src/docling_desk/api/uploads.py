from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO
from uuid import uuid4

from docling_azure_ocr.config import api_key
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from docling_desk import config
from docling_desk.config import MAX_BYTES, MIN_FREE
from docling_desk.documents.conversion import (
    Job,
    convert_job,
    fail_job_last_resort,
    save_job,
    validate_input,
)
from docling_desk.documents.diagram_store import SUFFIX as EXCALIDRAW_SUFFIX
from docling_desk.documents.diagram_store import import_scene
from docling_desk.documents.diagrams import (
    DIAGRAM_SUFFIXES,
    diagram_filename,
    png_has_diagram,
    read_diagram,
)
from docling_desk.documents.images import (
    IMAGE_SUFFIXES,
    image_thumbnail,
    inspect_image,
    publish_image,
)
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
from docling_desk.documents.text import SUPPORTED_LABEL, SUPPORTED_SUFFIXES, TEXT_DOCUMENT_SUFFIXES
from docling_desk.execution import SlotLease
from docling_desk.operations.backup import exclusive_write
from docling_desk.storage import document_folder, original_file, record_original, remove_document

router = APIRouter()


def run_job(folder: Path, job: Job) -> None:
    # The slot is owned by the lease it was submitted with; it returns when this ends.
    # Nobody reads the Future, so anything escaping convert_job (a failed fallback
    # save in _publish, for example) is recorded here instead of leaving the job running.
    try:
        convert_job(folder, job)
    except Exception as exc:
        fail_job_last_resort(folder, job, exc)


def _check_destination(folder_id: str | None) -> None:
    with LOCK:
        destination_exists(load_library(config.DATA), folder_id)


def _open_for_write(path: Path) -> BinaryIO:
    return path.open("wb")


def _write_chunk(stream: BinaryIO, chunk: bytes) -> None:
    stream.write(chunk)


async def _receive(file: UploadFile, source: Path) -> None:
    """Copy the upload to ``source`` with every blocking file call off the event loop."""
    total = 0
    stream = await run_in_threadpool(_open_for_write, source)
    try:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_BYTES:
                raise HTTPException(413, "上限50 MiBを超えています。")
            await run_in_threadpool(_write_chunk, stream, chunk)
    finally:
        # Synchronous on purpose: it must also run when the request is cancelled
        # (an await here would be cancelled again), and an unbuffered 1 MiB write
        # leaves nothing noticeable to flush.
        stream.close()


def publish_upload(
    source: Path, job: Job, folder_id: str | None, worker, lease: SlotLease | None
) -> Job:
    # Receive/validate outside the data tree. Publish and roll back under the
    # same lock as backup, before acquiring the library's in-process lock.
    with exclusive_write(config.DATA, "upload"), LOCK:
        folder = document_folder(config.DATA, job.id)
        target = original_file(folder, source.suffix)
        try:
            folder.mkdir(parents=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(source, target)
            if job.image:
                image_thumbnail(folder, target, job.image)
                publish_image(config.DATA, target, job.image)
            record_original(folder, job.filename, job.created)
            register_upload(config.DATA, job, folder_id)
            save_job(folder, job)
            accepted = decorate(load_library(config.DATA), job)
            if lease is None:
                return accepted
            # Last fallible step: once accepted, the queued job owns the folder and
            # the slot, so nothing after this may fail and roll the folder back.
            lease.submit(worker, run_job, folder, job)
        except Exception:
            if folder.is_dir():
                remove_document(folder)
            raise
        try:
            return decorate(load_library(config.DATA), job)
        except Exception:
            # Already accepted: a failed refresh must not report it as rejected.
            return accepted


@router.post("/api/upload", response_model=Job, status_code=202)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    folder_id: str | None = Form(default=None),
    ocr_provider: str | None = Form(default=None),
) -> Job:
    folder_id = folder_id or None
    # LOCK is a threading lock that library operations hold across copytree/rmtree;
    # taking it on the event loop would freeze every request, /health/live included.
    await run_in_threadpool(_check_destination, folder_id)
    filename = Path((file.filename or "").replace("\\", "/")).name
    suffix = Path(filename).suffix.lower()
    is_excalidraw = suffix == EXCALIDRAW_SUFFIX
    is_diagram = diagram_filename(filename)
    is_image = not is_diagram and suffix in IMAGE_SUFFIXES
    needs_conversion = not is_image and not is_diagram and not is_excalidraw
    needs_ocr = needs_conversion and suffix not in TEXT_DOCUMENT_SUFFIXES
    if suffix not in SUPPORTED_SUFFIXES | IMAGE_SUFFIXES | DIAGRAM_SUFFIXES | {EXCALIDRAW_SUFFIX}:
        raise HTTPException(415, f"{SUPPORTED_LABEL}を選んでください。")
    try:
        profile = load_profile(ocr_provider if needs_ocr else "disabled")
        if needs_ocr:
            profile.check_credentials(api_key())
    except (ValueError, RuntimeError):
        raise HTTPException(
            422, "OCR設定が無効です。Azureの送信許可と認証設定を確認してください。"
        ) from None
    if (await run_in_threadpool(shutil.disk_usage, config.DATA)).free < MIN_FREE:
        raise HTTPException(507, "空き容量が2 GiB未満です。処理を開始しません。")
    worker = getattr(request.app.state, "worker", None)
    slots = getattr(request.app.state, "slots", None)
    if worker is None or slots is None:
        raise HTTPException(503, "処理を受け付ける準備ができていません。")
    job = Job(
        id=uuid4().hex,
        filename=filename,
        synthetic=filename.startswith("synthetic-"),
        ocr_profile=profile.model_dump(mode="json"),
    )
    lease = slots.claim() if needs_conversion else None
    if needs_conversion and lease is None:
        raise HTTPException(429, "処理待ちは最大3件です。完了後に追加してください。")
    try:
        with TemporaryDirectory(prefix="docling-upload-") as staging:
            source = Path(staging) / ("original" + suffix)
            await _receive(file, source)
            if is_excalidraw:
                raw = await run_in_threadpool(source.read_bytes)
                return await run_in_threadpool(import_scene, config.DATA, filename, folder_id, raw)
            if not is_diagram and source.suffix == ".png":
                is_diagram = await run_in_threadpool(png_has_diagram, source)
            if is_diagram:
                diagram = await run_in_threadpool(read_diagram, source)
                job.diagram = True
                job.state = "success"
                job.pages = len(diagram.names)
                job.preview = "diagram"
            elif is_image:
                job.image = await run_in_threadpool(inspect_image, source)
                job.state = "success"
                job.pictures = 1
            else:
                await run_in_threadpool(validate_input, source)
            return await run_in_threadpool(publish_upload, source, job, folder_id, worker, lease)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            500,
            "ローカル保存または処理キューの開始に失敗しました。空き容量とログを確認してください。",
        ) from exc
    except (ValueError, HTTPException) as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, str(exc)) from exc
    finally:
        # Every exit path, including cancellation: a lease already handed to the
        # worker is left to its execution.
        if lease is not None:
            lease.abandon()
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
