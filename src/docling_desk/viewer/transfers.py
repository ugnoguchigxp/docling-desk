"""Bounded host uploads consumed by MCP using owner-bound transfer IDs.

The gateway runs as one process, as required by its existing pairing sessions.
Only temporary bytes and retry receipts live here; documents stay in the API.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Header, HTTPException, Request
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import UploadFile

from docling_desk.atomic_files import atomic_write_bytes
from docling_desk.viewer.tool_contract import read_contract

MAX_FILE = 50 * 1024 * 1024
MAX_TRANSFERS = 6
MAX_RECEIPTS = 10000
TRANSFER_TTL = 3600
RECEIPT_TTL = 7 * 86400


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transfer_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    idempotency_key: str = Field(pattern=r"^[\x21-\x7e]{1,128}$")


class ReplaceInput(ImportInput):
    source_id: str
    etag: str = Field(min_length=1, max_length=256, pattern=r"^[^\r\n]+$")


TRANSFER_INPUTS = {"import_document": ImportInput, "replace_document_source": ReplaceInput}


class TransferInfo(BaseModel):
    transfer_id: str
    filename: str
    size: int
    sha256: str
    expires_at: float
    state: str
    operation: str
    source_id: str | None
    result: dict | None


def write_actor(gateway, user):
    config, fingerprint = gateway.actor(user)
    if "write" not in config["users"][user]["actions"]:
        raise HTTPException(403, "forbidden")
    return config, fingerprint


class Transfers:
    def __init__(self, gateway):
        self.gateway = gateway
        configured = gateway.config().get("transfer_dir")
        path = (
            Path(configured).expanduser()
            if configured
            else gateway.config_file.parent / "document-transfers"
        )
        self.root = path.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_mode & 0o077:
            raise HTTPException(503, "document_transfer_directory_not_private")

    def path(self, transfer_id, suffix):
        if len(transfer_id) != 32 or any(c not in "0123456789abcdef" for c in transfer_id):
            raise HTTPException(404, "document_transfer_not_found")
        return self.root / (transfer_id + suffix)

    def save(self, record):
        path = self.path(record["transfer_id"], ".json")
        atomic_write_bytes(path, json.dumps(record, ensure_ascii=False).encode("utf-8"), mode=0o600)

    def load(self, transfer_id, user, fingerprint):
        try:
            record = json.loads(self.path(transfer_id, ".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(404, "document_transfer_not_found") from None
        if record["user"] != user or record["fingerprint"] != fingerprint:
            raise HTTPException(404, "document_transfer_not_found")
        if record["retain_until"] <= time.time():
            raise HTTPException(410, "document_transfer_expired")
        return record

    def clean(self):
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            # Never remove bytes while an API call may still be reading them.
            if record.get("busy_until", 0) > time.time():
                continue
            if record["expires_at"] <= time.time():
                self.path(record["transfer_id"], ".bin").unlink(missing_ok=True)
            if record["retain_until"] <= time.time():
                path.unlink(missing_ok=True)
        for path in self.root.glob("*.bin"):
            if (
                not path.with_suffix(".json").exists()
                and path.stat().st_mtime < time.time() - TRANSFER_TTL
            ):
                path.unlink(missing_ok=True)

    def info(self, record):
        return TransferInfo(
            **{
                k: record[k]
                for k in (
                    "transfer_id",
                    "filename",
                    "size",
                    "sha256",
                    "expires_at",
                    "operation",
                    "source_id",
                )
            },
            state="completed"
            if record["result"]
            else "expired"
            if record["expires_at"] <= time.time()
            else "in_progress"
            if record.get("busy_until", 0) > time.time()
            else "unconfirmed"
            if record["key"]
            else "staged",
            result=record["result"],
        )


def transfers(gateway):
    try:
        if not hasattr(gateway, "_transfers"):
            gateway._transfers = Transfers(gateway)
    except OSError:
        raise HTTPException(507, "document_transfer_storage_unavailable") from None
    return gateway._transfers


async def check_scope(gateway, user, metadata):
    config, fingerprint = write_actor(gateway, user)
    grant = {
        k: metadata[k]
        for k in ("collection_id", "project_id", "region")
        if metadata.get(k) is not None
    }
    if grant not in config["users"][user]["scopes"]:
        raise HTTPException(403, "scope_forbidden")
    capabilities = await gateway.api(user, "/api/v1/scopes", None, method="GET", grants=[grant])
    if "write" not in capabilities["actions"] or gateway.actor(user)[1] != fingerprint:
        raise HTTPException(403, "forbidden")
    return fingerprint


async def check_result(gateway, user, record):
    # Revocation/deletion checks also apply to cached successful receipts.
    if record["result"]:
        await gateway.api(
            user, "/api/v1/sources/" + record["result"]["source_id"], None, method="GET"
        )


async def transfer_tool(gateway, name, user, arguments):
    try:
        payload = TRANSFER_INPUTS[name].model_validate(arguments)
        if name == "replace_document_source":
            if not isinstance(payload, ReplaceInput):
                raise ValueError()
            source_id = str(UUID(payload.source_id))
        else:
            source_id = None
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(422, "invalid_document_transfer_input") from None
    _, fingerprint = write_actor(gateway, user)
    store = transfers(gateway)
    with gateway.lock:
        store.clean()
        record = store.load(payload.transfer_id, user, fingerprint)
        expected = "replace" if source_id else "import"
        if record["operation"] != expected or record["source_id"] != source_id:
            raise HTTPException(409, "document_transfer_target_mismatch")
        if isinstance(payload, ReplaceInput) and record["etag"] != payload.etag:
            raise HTTPException(409, "document_transfer_base_mismatch")
        if record["key"] and record["key"] != payload.idempotency_key:
            raise HTTPException(409, "document_transfer_key_mismatch")
        if not record["result"] and record["expires_at"] <= time.time():
            raise HTTPException(410, "document_transfer_expired")
        if record.get("busy_until", 0) > time.time():
            raise HTTPException(409, "document_transfer_busy")
        cached = record["result"]
        if not cached:
            record.update(
                key=payload.idempotency_key,
                busy_until=time.time() + 120,
                retain_until=max(record["retain_until"], time.time() + RECEIPT_TTL),
            )
            store.save(record)
    try:
        if await check_scope(gateway, user, record["metadata"]) != fingerprint:
            raise HTTPException(401, "document_transfer_revoked")
        if cached:
            await check_result(gateway, user, record)
            return cached
        route = f"/api/v1/sources/{source_id}/revisions" if source_id else "/api/v1/sources"
        headers = {"Idempotency-Key": payload.idempotency_key}
        if isinstance(payload, ReplaceInput):
            headers["If-Match"] = payload.etag
        form = {"metadata": json.dumps(record["metadata"], ensure_ascii=False)}
        if record["ocr_provider"] is not None:
            form["ocr_provider"] = record["ocr_provider"]
        with store.path(payload.transfer_id, ".bin").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                raise HTTPException(409, "document_transfer_integrity_failed")
            stream.seek(0)
            result = await gateway.api(
                user,
                route,
                None,
                headers=headers,
                files={"file": (record["filename"], stream, "application/octet-stream")},
                form=form,
            )
        if not all(
            isinstance(result.get(k), str) for k in ("source_id", "source_revision", "job_id")
        ):
            raise HTTPException(502, "invalid_document_registration_response")
        try:
            for field in ("source_id", "source_revision", "job_id"):
                UUID(result[field])
        except ValueError:
            raise HTTPException(502, "invalid_document_registration_response") from None
        with gateway.lock:
            record["result"] = result
            store.save(record)
            store.path(payload.transfer_id, ".bin").unlink(missing_ok=True)
        return result
    except OSError:
        raise HTTPException(503, "document_transfer_storage_unavailable") from None
    finally:
        if not cached:
            with gateway.lock:
                record["busy_until"] = 0
                store.save(record)


def install_transfers(app, gateway):
    contract = read_contract()
    body_schema = copy.deepcopy(contract["paths"]["/api/v1/sources"]["post"]["requestBody"])
    fields = body_schema["content"]["multipart/form-data"]["schema"]
    fields["properties"].update(
        {
            "source_id": {"type": "string", "format": "uuid"},
            "etag": {"type": "string", "minLength": 1, "maxLength": 256},
        }
    )
    fields["dependentRequired"] = {"source_id": ["etag"], "etag": ["source_id"]}

    @app.post(
        "/api/v1/document-transfers",
        response_model=TransferInfo,
        openapi_extra={"requestBody": body_schema},
    )
    async def stage(request: Request, authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        user, fingerprint = gateway.token_actor(token)
        write_actor(gateway, user)
        if not request.headers.get("content-type", "").startswith("multipart/form-data;"):
            raise HTTPException(415, "multipart_required")
        store = transfers(gateway)
        # Admission precedes multipart parsing and disk spooling.
        with gateway.lock:
            store.clean()
            active = len(list(store.root.glob("*.bin"))) + getattr(gateway, "transfer_uploading", 0)
            if active >= MAX_TRANSFERS or len(list(store.root.glob("*.json"))) >= MAX_RECEIPTS:
                raise HTTPException(429, "document_transfer_queue_full")
            gateway.transfer_uploading = getattr(gateway, "transfer_uploading", 0) + 1
        transfer_id = uuid4().hex
        target = store.path(transfer_id, ".bin")
        try:
            async with request.form(max_files=1, max_fields=4, max_part_size=16384) as form:
                if (
                    len(form.getlist("file")) != 1
                    or len(form.getlist("metadata")) != 1
                    or any(len(form.getlist(k)) > 1 for k in form)
                    or set(form) - {"file", "metadata", "ocr_provider", "source_id", "etag"}
                ):
                    raise HTTPException(422, "invalid_document_transfer_input")
                file = form.get("file")
                if not isinstance(file, UploadFile):
                    raise HTTPException(422, "invalid_document_transfer_input")
                try:
                    metadata_raw = form["metadata"]
                    if not isinstance(metadata_raw, str):
                        raise ValueError()
                    metadata = json.loads(metadata_raw)
                    Draft202012Validator(
                        contract["components"]["schemas"]["SourceMetadata"]
                    ).validate(metadata)
                    if ("source_id" in form) != ("etag" in form):
                        raise ValueError()
                    source_raw = form.get("source_id")
                    if source_raw is not None and not isinstance(source_raw, str):
                        raise ValueError()
                    source_id = str(UUID(source_raw)) if source_raw is not None else None
                    etag = form.get("etag")
                    if source_id and (
                        not isinstance(etag, str)
                        or not 1 <= len(etag) <= 256
                        or "\n" in etag
                        or "\r" in etag
                    ):
                        raise ValueError()
                    if etag and not source_id:
                        raise ValueError()
                    provider = form.get("ocr_provider")
                    allowed = body_schema["content"]["multipart/form-data"]["schema"]["properties"][
                        "ocr_provider"
                    ]["enum"]
                    if provider is not None and provider not in allowed:
                        raise ValueError()
                except (ValueError, TypeError, SchemaError):
                    raise HTTPException(422, "invalid_document_transfer_input") from None
                await check_scope(gateway, user, metadata)
                if source_id:
                    await gateway.api(user, f"/api/v1/sources/{source_id}", None, method="GET")
                filename = (file.filename or "").replace("\\", "/").split("/")[-1]
                if (
                    not filename
                    or len(filename) > 256
                    or any(ord(c) < 32 or ord(c) == 127 for c in filename)
                ):
                    raise HTTPException(422, "invalid_document_filename")
                sha, size = hashlib.sha256(), 0
                with target.open("xb") as stream:
                    os.chmod(target, 0o600)
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE:
                            raise HTTPException(413, "document_transfer_too_large")
                        stream.write(chunk)
                        sha.update(chunk)
                if size == 0:
                    raise HTTPException(413, "invalid_file_size")
                if gateway.token_actor(token) != (user, fingerprint):
                    raise HTTPException(401, "unauthenticated")
                now = time.time()
                record = {
                    "transfer_id": transfer_id,
                    "user": user,
                    "fingerprint": fingerprint,
                    "filename": filename,
                    "size": size,
                    "sha256": sha.hexdigest(),
                    "metadata": metadata,
                    "source_id": source_id,
                    "etag": etag,
                    "operation": "replace" if source_id else "import",
                    "ocr_provider": provider,
                    "expires_at": now + TRANSFER_TTL,
                    "retain_until": now + RECEIPT_TTL,
                    "result": None,
                    "key": None,
                    "busy_until": 0,
                }
                with gateway.lock:
                    store.save(record)
                return store.info(record)
        except OSError:
            target.unlink(missing_ok=True)
            raise HTTPException(507, "document_transfer_storage_unavailable") from None
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            with gateway.lock:
                gateway.transfer_uploading -= 1

    @app.get("/api/v1/document-transfers/{transfer_id}", response_model=TransferInfo)
    async def status(transfer_id: str, authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        user, fingerprint = gateway.token_actor(token)
        write_actor(gateway, user)
        store = transfers(gateway)
        with gateway.lock:
            store.clean()
            record = store.load(transfer_id, user, fingerprint)
        await check_scope(gateway, user, record["metadata"])
        await check_result(gateway, user, record)
        if gateway.token_actor(token) != (user, fingerprint):
            raise HTTPException(401, "unauthenticated")
        return store.info(record)
