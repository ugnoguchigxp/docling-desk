"""Private, stateless Docling adapter for the independently managed knowledge API.

Only IDs select paths under the configured shared artifact directory. This app has
no legacy upload/view routes and never opens the knowledge SQLite database.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import threading
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import pymupdf
from docling_azure_ocr.config import OcrError, OcrProfile, api_key, load_profile
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docling_desk.config import MAX_PAGES, MIN_FREE, MODEL_MANIFEST, MODELS
from docling_desk.documents.conversion import Job, convert_job, validate_input
from docling_desk.documents.ocr_operations import FileOcrLedger, HttpOcrLedger
from docling_desk.documents.ocr_profiles import make_runtime
from docling_desk.operations.models import problems as model_problems
from docling_desk.storage import job_file


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    source_revision: UUID
    suffix: Literal[".pdf", ".pptx", ".xlsx", ".docx", ".md", ".markdown", ".txt", ".text"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExtractInput(Input):
    job_id: UUID
    run: int = Field(ge=1, le=2**31 - 1)
    profile: Literal["local-v1", "azure-read-v1", "disabled-v1", "azure-read-full-page-v1"]
    ocr_profile: OcrProfile | None = None
    allow_ocr_resubmit: bool = False
    filename: str = Field(min_length=1, max_length=256)


class ViewerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    source_revision: UUID
    job_id: UUID
    run: int = Field(ge=1, le=2**31 - 1)
    suffix: Literal[".pdf", ".pptx", ".xlsx", ".docx", ".md", ".markdown", ".txt", ".text"]
    resource: str = Field(min_length=1, max_length=512)
    prefix: str = Field(pattern=r"^/viewer/session/[A-Za-z0-9_-]{43}/$")


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["local", "azure_read", "disabled"] | None = None


class PrivateRequests:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app, self.token = app, token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/internal/"):
            await self.app(scope, receive, send)
            return
        header = Headers(scope=scope).get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not secrets.compare_digest(supplied.encode(), self.token.encode()):
            await JSONResponse({"detail": "unauthenticated"}, status_code=401)(scope, receive, send)
            return
        parts: list[bytes] = []
        length = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            part = message.get("body", b"")
            length += len(part)
            if length > 16384:
                await JSONResponse({"detail": "request_too_large"}, status_code=413)(
                    scope, receive, send
                )
                return
            parts.append(part)
            if not message.get("more_body", False):
                break
        body = b"".join(parts)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_worker(root: Path, token: str) -> FastAPI:
    if len(token) < 32:
        raise ValueError("KNOWLEDGE_WORKER_TOKEN must have at least 32 characters")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    gate = threading.BoundedSemaphore(1)

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        header = authorization or ""
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "unauthenticated")

    def source_path(value: Input) -> Path:
        path = root / "sources" / str(value.source_id) / str(value.source_revision)
        path = path / f"original{value.suffix}"
        # Refuse symlinks or paths resolving outside the isolated artifact volume.
        if root not in path.resolve().parents or path.is_symlink():
            raise HTTPException(422, "invalid_source")
        if not path.is_file():
            raise HTTPException(404, "source_not_found")
        return path

    def checked_source(value: Input) -> Path:
        path = source_path(value)
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != value.sha256:
                raise ValueError("source_changed")
            validate_input(path)
            if value.suffix == ".pdf":
                with pymupdf.open(path) as pdf:
                    if pdf.is_encrypted or len(pdf) > MAX_PAGES:
                        raise ValueError("invalid_pdf")
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(422, "invalid_document") from exc
        return path

    app = FastAPI(
        title="Docling Desk document processor",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(PrivateRequests, token=token)

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        try:
            reasons = model_problems(MODELS, MODEL_MANIFEST)
        except ValueError:
            reasons = ["manifest_invalid"]
        try:
            if shutil.disk_usage(root).free < MIN_FREE:
                reasons.append("disk_space")
        except OSError:
            reasons.append("disk_unreadable")
        if sys.platform != "darwin" and shutil.which("tesseract") is None:
            reasons.append("tesseract_missing")
        available = not reasons
        body: dict[str, object] = {"status": "ready" if available else "unavailable"}
        if reasons:
            body["reasons"] = reasons
        return JSONResponse(body, status_code=200 if available else 503)

    @app.post("/internal/v1/validate", dependencies=[Depends(authenticate)])
    def validate(value: Input) -> dict:
        checked_source(value)
        return {"valid": True, "sha256": value.sha256}

    @app.post("/internal/v1/ocr-profile", dependencies=[Depends(authenticate)])
    def ocr_profile(value: ProfileRequest) -> dict:
        try:
            profile = load_profile(value.provider)
            profile.check_credentials(api_key())
        except (ValueError, OcrError):
            raise HTTPException(422, "ocr_configuration_invalid") from None
        return {"profile": profile_id(profile), "ocr_profile": profile.model_dump(mode="json")}

    @app.post("/internal/v1/extract", dependencies=[Depends(authenticate)])
    def extract(value: ExtractInput) -> dict:
        if not gate.acquire(blocking=False):
            raise HTTPException(429, "processor_busy")
        folder: Path | None = None
        ledger = None
        try:
            source = checked_source(value)
            if (
                Path(value.filename).name != value.filename
                or Path(value.filename).suffix.lower() != value.suffix
            ):
                raise HTTPException(422, "invalid_filename")
            folder = source.parent / "attempts" / f"{value.job_id}-{value.run}"
            # Runs have separate output paths. A retried local run can replace only its own output.
            if folder.is_symlink() or root not in folder.resolve().parents:
                raise HTTPException(422, "invalid_attempt")
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True)
            shutil.copyfile(source, folder / f"original{value.suffix}")
            if (
                hashlib.sha256((folder / f"original{value.suffix}").read_bytes()).hexdigest()
                != value.sha256
            ):
                raise HTTPException(409, "source_changed")
            profile = value.ocr_profile or OcrProfile.model_validate(
                {
                    "provider": {
                        "local-v1": "local",
                        "disabled-v1": "disabled",
                    }.get(value.profile, "azure_read")
                }
            )
            if profile_id(profile) != value.profile:
                raise HTTPException(422, "ocr_profile_mismatch")
            if profile.provider == "azure_read":
                # Removing/changing the connection also stops queued jobs using the old endpoint.
                configured = load_profile()
                if not configured.enabled or configured.endpoint != profile.endpoint:
                    raise HTTPException(422, "ocr_disabled")
                ledger = HttpOcrLedger(
                    os.environ.get("KNOWLEDGE_API_INTERNAL_URL", "http://127.0.0.1:18766"),
                    token,
                    str(value.job_id),
                    value.run,
                    str(value.source_revision),
                )
            runtime = make_runtime(
                profile,
                folder,
                value.sha256,
                str(value.source_id),
                ledger=ledger
                or FileOcrLedger(
                    source.parent / "ocr-private" / "operations",
                    profile.max_submissions,
                    live=lambda: source.is_file(),
                ),
                cache=source.parent / "ocr-private" / "results",
                allow_resubmit=value.allow_ocr_resubmit,
            )
            job = Job(
                id=str(value.job_id),
                filename=value.filename,
                ocr_profile=profile.model_dump(mode="json"),
            )
            convert_job(folder, job, ocr_runtime=runtime)
            if job.state != "success":
                # No partially extracted document is published as a complete index.
                raise HTTPException(422, job.error_code or "extraction_failed")
            contexts = read_jsonl(folder / "rag.jsonl")
            chunks = read_jsonl(folder / "rag-index.jsonl")
            document = json.loads((folder / "document.json").read_text(encoding="utf-8"))
            tables = {t["self_ref"]: t for t in document.get("tables", [])}
            for record in contexts:
                record["tables"] = [tables[ref] for ref in record.get("refs", []) if ref in tables]
            return {
                "source_id": str(value.source_id),
                "source_revision": str(value.source_revision),
                "job_id": str(value.job_id),
                "run": value.run,
                "source_sha256": value.sha256,
                "profile": value.profile,
                "contexts": contexts,
                "chunks": chunks,
                "warnings": job.warnings,
                "viewer_ready": bool(job.preview),
            }
        except HTTPException:
            raise
        except OcrError as exc:
            raise HTTPException(422, exc.code) from None
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            raise HTTPException(422, "extraction_failed") from exc
        finally:
            if ledger:
                ledger.close()
            # Main source can be deleted while conversion is in flight. Remove late output.
            if folder and not (folder.parent.parent / f"original{value.suffix}").is_file():
                shutil.rmtree(folder.parent.parent, ignore_errors=True)
            gate.release()

    @app.post("/internal/v1/viewer", dependencies=[Depends(authenticate)])
    def viewer(value: ViewerInput) -> dict:
        from docling_desk.viewer.render import render_resource

        revision = root / "sources" / str(value.source_id) / str(value.source_revision)
        folder = revision / "attempts" / f"{value.job_id}-{value.run}"
        if (
            root not in folder.resolve().parents
            or any(p.is_symlink() for p in (revision.parent, revision, folder.parent, folder))
            or not (revision / f"original{value.suffix}").is_file()
            or (revision / f"original{value.suffix}").is_symlink()
            or (job_file(folder)).is_symlink()
        ):
            raise HTTPException(404, "preview_unavailable")
        try:
            return render_resource(folder, value.resource, value.prefix)
        except (OSError, ValueError, KeyError, RuntimeError, IndexError):
            raise HTTPException(404, "viewer_resource_not_found") from None

    return app


def profile_id(profile: OcrProfile) -> str:
    if profile.provider == "azure_read" and profile.profile == "read-full-page-v1":
        return "azure-read-full-page-v1"
    return {"local": "local-v1", "azure_read": "azure-read-v1", "disabled": "disabled-v1"}[
        profile.provider
    ]


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def app_factory() -> FastAPI:
    raw = os.environ.get("KNOWLEDGE_ARTIFACT_ROOT")
    token = os.environ.get("KNOWLEDGE_WORKER_TOKEN", "")
    if not raw:
        raise ValueError("KNOWLEDGE_ARTIFACT_ROOT is required; legacy data/ is never used")
    return create_worker(Path(raw), token)
