from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from docling_desk import activity, config
from docling_desk.api import (
    assets,
    diagrams,
    explanation,
    health,
    library,
    media,
    preview,
    translation,
    uploads,
)
from docling_desk.blob_mirror import MirrorWorker, create_mirror
from docling_desk.documents.conversion import read_job, save_job
from docling_desk.documents.diagram_store import repair_diagrams
from docling_desk.execution import SlotPool
from docling_desk.explanation.service import ExplanationManager
from docling_desk.explanation.store import recover as explanation_recover
from docling_desk.knowledge.api import create_router as knowledge_router
from docling_desk.operations.backup import exclusive_write
from docling_desk.storage import document_folders, migrate_layout
from docling_desk.translation.service import TranslationManager
from docling_desk.translation.store import interrupt_pending
from docling_desk.web.auth import authenticate
from docling_desk.web.body import BodyLimit
from docling_desk.web.frontend import router as frontend_router
from docling_desk.web.rootpath import prefix_urls
from docling_desk.web.security import local_only


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Each startup owns its executor and admission slots. Work cancelled while
    # queued at shutdown returns its slot through its lease; running work keeps
    # it until it ends. Both are discarded with this lifespan.
    worker = ThreadPoolExecutor(max_workers=1)
    slots = SlotPool(3)
    app.state.worker = worker
    app.state.slots = slots
    try:
        config.DATA.mkdir(parents=True, exist_ok=True)
        mirror = create_mirror(config.DATA)
        if mirror is not None:
            # A new or rebuilt host starts empty: restore from the container first.
            if mirror.restore_incomplete() or not mirror.has_local_content():
                restored = mirror.pull()
                if restored.errors:
                    # Served read-only data is better than nothing, but not silently:
                    # /health/ready stays 503 and the worker keeps resuming the restore.
                    logging.getLogger("docling_desk.blob").error(
                        "restore incomplete: %s", restored.errors[:3]
                    )
            app.state.blob_mirror = mirror
            app.state.blob_worker = MirrorWorker(mirror, config.BLOB_INTERVAL)
        with exclusive_write(config.DATA, "migration"):
            migrate_layout(config.DATA)
            repair_diagrams(config.DATA)
        interrupt_pending(config.DATA)
        explanation_recover(config.DATA)
        app.state.explanation_manager = ExplanationManager()
        app.state.explanation_manager.resume_pending(config.DATA)
        app.state.translation_manager = TranslationManager()
        # Another process may still be converting; its held activity lock says so.
        # Read the live set and save under the write lock, so an attempt that begins
        # meanwhile (it registers under the same lock) cannot be failed by mistake.
        with exclusive_write(config.DATA, "migration"):
            running = {item["attempt"] for item in activity.live(config.DATA)}
            for folder in document_folders(config.DATA):
                job = read_job(folder)
                if activity.read_attempt(config.DATA, folder.name).get("attempt") in running:
                    continue
                if job.state in {"queued", "running"}:
                    job.state, job.error = (
                        "failed",
                        "前回のサーバー停止で処理が中断されました。再アップロードしてください。",
                    )
                    save_job(folder, job)
        if worker_mirror := getattr(app.state, "blob_worker", None):
            worker_mirror.start()
        yield
        if knowledge := getattr(app.state, "knowledge", None):
            knowledge.close()
            del app.state.knowledge
        app.state.explanation_manager.close()
        app.state.translation_manager.close()
        # Last, so writes the managers finish while stopping still reach the mirror.
        if worker_mirror := getattr(app.state, "blob_worker", None):
            worker_mirror.close()
    finally:
        app.state.blob_worker = app.state.blob_mirror = None
        worker.shutdown(wait=False, cancel_futures=True)
        app.state.worker = None
        app.state.slots = None


def create_app() -> FastAPI:
    application = FastAPI(title="Docling Desk", lifespan=lifespan)
    application.include_router(frontend_router)
    application.include_router(knowledge_router(lambda: config.DATA))
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)
    application.add_middleware(BodyLimit, limit=config.MAX_BYTES + 1024 * 1024)
    application.middleware("http")(prefix_urls)
    application.middleware("http")(local_only)
    # Registered last so it runs first: nothing is served before the login check.
    application.middleware("http")(authenticate)
    application.include_router(health.router)
    application.include_router(library.router)
    application.include_router(media.router)
    application.include_router(uploads.router)
    application.include_router(preview.router)
    application.include_router(explanation.router)
    application.include_router(translation.router)
    application.include_router(diagrams.router)
    application.include_router(assets.router)
    return application


app = create_app()
