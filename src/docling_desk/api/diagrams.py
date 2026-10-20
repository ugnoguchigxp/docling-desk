"""HTTP routes for standalone Excalidraw diagrams (see docs/diagram-p1-p2-contract.md)."""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from docling_desk import config
from docling_desk.documents import diagram_store as store
from docling_desk.documents.diagram_models import (
    MAX_DEPTH,
    MAX_SCENE_BYTES,
    parse_object,
)

router = APIRouter()
SCENE = "application/vnd.excalidraw+json"
ENVELOPE = 1024 * 1024


async def read_body(request: Request, limit: int = MAX_SCENE_BYTES) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "図表は16 MiB以下にしてください。")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "図表は16 MiB以下にしてください。")
        chunks.append(chunk)
    return b"".join(chunks)


def media_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";")[0].strip().lower()


def require_scene_type(request: Request) -> None:
    if media_type(request) not in {SCENE, "application/json"}:
        raise HTTPException(
            415,
            "Content-Typeはapplication/vnd.excalidraw+jsonまたはapplication/jsonにしてください。",
        )


async def scene_body(request: Request) -> bytes:
    require_scene_type(request)
    return await read_body(request)


async def json_body(request: Request) -> dict:
    if media_type(request) != "application/json":
        raise HTTPException(415, "Content-Typeはapplication/jsonにしてください。")
    return parse_object(await read_body(request, ENVELOPE), "要求")


def required(value: str | None, header: str) -> str:
    if not value:
        raise HTTPException(428, f"{header}ヘッダーが必要です。")
    return value


def idempotency(value: str | None) -> str:
    key = required(value, "Idempotency-Key")
    if len(key) > 200 or not key.isprintable():
        raise HTTPException(422, "Idempotency-Keyは200文字以内の表示可能な文字列にしてください。")
    return key


def text(body: dict, field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value:
        raise HTTPException(422, f"{field}を文字列で指定してください。")
    return value


def reply(result: dict, status: int) -> JSONResponse:
    return JSONResponse(result, status_code=status, headers={"ETag": result["etag"]})


def scene_response(raw: bytes, etag: str, headers: dict[str, str] | None = None) -> Response:
    return Response(raw, media_type=SCENE, headers={"ETag": etag, **(headers or {})})


@router.put("/api/diagrams/{diagram_id}", status_code=201)
async def diagram_create(
    diagram_id: str,
    request: Request,
    if_none_match: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> JSONResponse:
    if required(if_none_match, "If-None-Match") != "*":
        raise HTTPException(412, "If-None-Matchは*を指定してください。")
    key = idempotency(idempotency_key)
    if not store.valid_id(diagram_id):
        raise HTTPException(422, "図表IDは32桁の小文字16進数で指定してください。")
    if media_type(request) != "application/json":
        raise HTTPException(415, "Content-Typeはapplication/jsonにしてください。")
    body = parse_object(await read_body(request, MAX_SCENE_BYTES + ENVELOPE), "要求", MAX_DEPTH + 1)
    name = text(body, "name")
    folder_id = body.get("folder_id")
    if folder_id is not None and not isinstance(folder_id, str):
        raise HTTPException(422, "folder_idは文字列またはnullで指定してください。")
    if not isinstance(body.get("scene"), dict):
        raise HTTPException(422, "sceneをオブジェクトで指定してください。")
    raw = json.dumps(body["scene"], ensure_ascii=False).encode("utf-8")
    status, result = await run_in_threadpool(
        store.create, config.DATA, diagram_id, name, folder_id or None, raw, key
    )
    return reply(result, status)


@router.get("/api/diagrams/{diagram_id}")
def diagram_info(diagram_id: str) -> JSONResponse:
    result = store.info(config.DATA, diagram_id).model_dump()
    return reply(result, 200)


@router.get("/api/diagrams/{diagram_id}/content")
def diagram_content(
    diagram_id: str, revision: str | None = None, download: bool = False
) -> Response:
    raw, revision_id, name = store.scene_content(config.DATA, diagram_id, revision)
    headers = {"X-Content-Type-Options": "nosniff"}
    if download:
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    return scene_response(raw, store.source_etag(revision_id), headers)


@router.post("/api/diagrams/{diagram_id}/revisions", status_code=201)
async def diagram_save(
    diagram_id: str,
    request: Request,
    if_match: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> JSONResponse:
    tag, key = required(if_match, "If-Match"), idempotency(idempotency_key)
    raw = await scene_body(request)
    status, result = await run_in_threadpool(store.save, config.DATA, diagram_id, raw, tag, key)
    return reply(result, status)


@router.get("/api/diagrams/{diagram_id}/revisions")
def diagram_revisions(diagram_id: str, before: int | None = None, limit: int = 50) -> dict:
    if not 1 <= limit <= 100:
        raise HTTPException(422, "limitは1から100で指定してください。")
    items, following = store.revisions(config.DATA, diagram_id, before, limit)
    return {"items": [item.model_dump() for item in items], "next": following}


@router.post("/api/diagrams/{diagram_id}/restore", status_code=201)
async def diagram_restore(
    diagram_id: str,
    request: Request,
    if_match: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> JSONResponse:
    tag, key = required(if_match, "If-Match"), idempotency(idempotency_key)
    body = await json_body(request)
    status, result = await run_in_threadpool(
        store.restore, config.DATA, diagram_id, text(body, "revision_id"), tag, key
    )
    return reply(result, status)


@router.post("/api/diagrams/{diagram_id}/drafts", status_code=201)
async def draft_create(
    diagram_id: str, request: Request, if_match: str | None = Header(default=None)
) -> JSONResponse:
    tag = required(if_match, "If-Match")
    raw = await read_body(request)
    if raw:
        require_scene_type(request)
    draft = await run_in_threadpool(store.draft_create, config.DATA, diagram_id, raw or None, tag)
    return reply(draft.model_dump(), 201)


@router.get("/api/diagrams/{diagram_id}/drafts/{draft_id}")
def draft_info(diagram_id: str, draft_id: str) -> JSONResponse:
    return reply(store.draft_info(config.DATA, diagram_id, draft_id).model_dump(), 200)


@router.get("/api/diagrams/{diagram_id}/drafts/{draft_id}/content")
def draft_content(diagram_id: str, draft_id: str) -> Response:
    raw, draft = store.draft_content(config.DATA, diagram_id, draft_id)
    return scene_response(raw, draft.etag, {"X-Content-Type-Options": "nosniff"})


@router.put("/api/diagrams/{diagram_id}/drafts/{draft_id}")
async def draft_update(
    diagram_id: str,
    draft_id: str,
    request: Request,
    if_match: str | None = Header(default=None),
) -> JSONResponse:
    tag = required(if_match, "If-Match")
    raw = await scene_body(request)
    draft = await run_in_threadpool(store.draft_update, config.DATA, diagram_id, draft_id, raw, tag)
    return reply(draft.model_dump(), 200)


@router.post("/api/diagrams/{diagram_id}/drafts/{draft_id}/commit", status_code=201)
async def draft_commit(
    diagram_id: str,
    draft_id: str,
    request: Request,
    if_match: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None),
) -> JSONResponse:
    tag, key = required(if_match, "If-Match"), idempotency(idempotency_key)
    body = await json_body(request)
    status, result = await run_in_threadpool(
        store.draft_commit,
        config.DATA,
        diagram_id,
        draft_id,
        text(body, "source_etag"),
        tag,
        key,
    )
    return reply(result, status)


@router.delete("/api/diagrams/{diagram_id}/drafts/{draft_id}", status_code=204)
def draft_discard(
    diagram_id: str, draft_id: str, if_match: str | None = Header(default=None)
) -> Response:
    store.draft_discard(config.DATA, diagram_id, draft_id, required(if_match, "If-Match"))
    return Response(status_code=204)


@router.get("/api/diagrams/{diagram_id}/operations/{operation_id}")
def diagram_operation(diagram_id: str, operation_id: str) -> dict:
    return store.operation_result(config.DATA, diagram_id, operation_id)
