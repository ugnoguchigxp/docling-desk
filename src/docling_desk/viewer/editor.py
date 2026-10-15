"""Short-lived editor pairing; all persistence goes through the public API."""

from __future__ import annotations

import asyncio
import secrets
import time
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, model_validator

from docling_desk.config import RESOURCES
from docling_desk.viewer.document_tools import DOCUMENT_TOOLS, document_tool


class EditorTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: UUID | None = None
    source_id: UUID | None = None
    scope: dict | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if sum(value is not None for value in (self.draft_id, self.source_id, self.scope)) != 1:
            raise ValueError("one editor target required")
        return self


class EditorCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    arguments: dict


def metadata_grant(metadata):
    return {
        key: metadata[key]
        for key in ("collection_id", "project_id", "region")
        if metadata.get(key) is not None
    }


async def approve_editor(gateway, user: str, code: str):
    config, fingerprint = gateway.actor(user)
    if "write" not in config["users"][user]["actions"]:
        raise HTTPException(403, "forbidden")
    with gateway.lock:
        gateway.clean()
        now = time.time()
        attempts = [stamp for stamp in gateway.approvals.get(user, []) if stamp > now - 60]
        if len(attempts) >= 20:
            raise HTTPException(429, "viewer_approval_rate_limited")
        gateway.approvals[user] = [*attempts, now]
        candidate = next(
            (
                (nonce, item)
                for nonce, item in gateway.pending.items()
                if item.get("purpose") == "editor"
                and item["status"] == "pending"
                and secrets.compare_digest(item["code"], code.upper())
            ),
            None,
        )
        if not candidate:
            raise HTTPException(404, "editor_code_expired")
        nonce, pending = candidate
        pending["status"] = "approving"
    try:
        target = pending["reference"]
        source_id = str(target.source_id) if target.source_id else None
        draft_id = str(target.draft_id) if target.draft_id else None
        if draft_id:
            data = await document_tool(gateway, "get_document_draft", user, {"draft_id": draft_id})
            source_id = data["source_id"]
            scope = metadata_grant(data["metadata"])
        elif source_id:
            data = await document_tool(gateway, "get_document", user, {"source_id": source_id})
            scope = metadata_grant(data["metadata"])
        else:
            scope = target.scope
        if scope not in config["users"][user]["scopes"]:
            raise HTTPException(403, "scope_forbidden")
        # Confirm both actions at the API, including configured client restrictions.
        capabilities = await gateway.api(user, "/api/v1/scopes", None, method="GET", grants=[scope])
        if "write" not in capabilities["actions"] or gateway.actor(user)[1] != fingerprint:
            raise HTTPException(403, "forbidden")
        token = secrets.token_urlsafe(32)
        with gateway.lock:
            if nonce not in gateway.pending or pending["expires"] <= time.time():
                raise HTTPException(410, "editor_code_expired")
            gateway.editor_sessions = {
                key: value
                for key, value in gateway.editor_sessions.items()
                if value["expires"] > time.time()
            }
            if len(gateway.editor_sessions) >= 5000:
                raise HTTPException(429, "editor_busy")
            gateway.editor_sessions[token] = {
                "user": user,
                "fingerprint": fingerprint,
                "scope": scope,
                "draft_id": draft_id,
                "source_id": source_id,
                "expires": time.time() + 300,
                "operation_lock": asyncio.Lock(),
            }
            pending.update(status="approved", token=token)
        return {"status": "connected", "target": target.model_dump(mode="json"), "expires_in": 300}
    except Exception:
        with gateway.lock:
            gateway.pending.pop(nonce, None)
        raise


def editor_session(gateway, token: str):
    session = gateway.editor_sessions.get(token)
    if not session or session["expires"] <= time.time():
        gateway.editor_sessions.pop(token, None)
        raise HTTPException(401, "editor_session_expired")
    config, fingerprint = gateway.actor(session["user"])
    if (
        fingerprint != session["fingerprint"]
        or "write" not in config["users"][session["user"]]["actions"]
    ):
        raise HTTPException(401, "editor_session_revoked")
    return session


def install_editor(app, gateway):
    gateway.editor_sessions = {}

    @app.get("/editor", response_class=HTMLResponse)
    def entry():
        return HTMLResponse((RESOURCES / "static/frontend/editor.html").read_text())

    @app.post("/editor/challenges")
    def challenge(target: EditorTarget, request: Request):
        peer = request.client.host if request.client else None
        result = gateway.challenge(
            target, gateway.issuer(peer, request.headers.get("x-forwarded-for"))
        )
        with gateway.lock:
            gateway.pending[result["challenge"]]["purpose"] = "editor"
        return result

    @app.get("/editor/challenges/{nonce}")
    def poll(nonce: str):
        with gateway.lock:
            gateway.clean()
            pending = gateway.pending.get(nonce)
            if not pending or pending.get("purpose") != "editor":
                raise HTTPException(410, "editor_code_expired")
            if pending["status"] == "approved":
                gateway.pending.pop(nonce)
                return {"status": "approved", "session": pending["token"]}
            return {"status": "pending"}

    @app.get("/editor/session/{token}")
    async def state(token: str):
        session = editor_session(gateway, token)
        if session["draft_id"]:
            await document_tool(
                gateway,
                "get_document_draft",
                session["user"],
                {"draft_id": session["draft_id"]},
                grants=[session["scope"]],
            )
        elif session["source_id"]:
            await document_tool(
                gateway,
                "get_document",
                session["user"],
                {"source_id": session["source_id"]},
                grants=[session["scope"]],
            )
        editor_session(gateway, token)
        return {key: session[key] for key in ("scope", "draft_id", "source_id", "expires")}

    @app.post("/editor/session/{token}/tools")
    async def tool(token: str, call: EditorCall):
        session = editor_session(gateway, token)
        async with session["operation_lock"]:
            # Expiry/revocation may have happened while waiting for another call.
            session = editor_session(gateway, token)
            return await editor_tool(gateway, token, session, call)


async def editor_tool(gateway, token: str, session: dict, call: EditorCall):
    if call.name not in DOCUMENT_TOOLS or call.name in {
        "retry_document_job",
        "update_document_metadata",
        "create_markdown_document",
        "get_document_job",
    }:
        raise HTTPException(403, "editor_operation_forbidden")
    args = dict(call.arguments)
    if session["draft_id"] and args.get("draft_id", session["draft_id"]) != session["draft_id"]:
        raise HTTPException(403, "editor_target_forbidden")
    if session["source_id"] and args.get("source_id", session["source_id"]) != session["source_id"]:
        raise HTTPException(403, "editor_target_forbidden")
    if (
        session["source_id"]
        and not session["draft_id"]
        and call.name == "create_document_draft"
        and args.get("source_id") != session["source_id"]
    ):
        raise HTTPException(403, "editor_target_forbidden")
    if not session["draft_id"] and args.get("draft_id"):
        selected = await document_tool(
            gateway,
            "get_document_draft",
            session["user"],
            {"draft_id": args["draft_id"]},
            grants=[session["scope"]],
        )
        if session["source_id"] and selected["source_id"] != session["source_id"]:
            raise HTTPException(403, "editor_target_forbidden")
        session["draft_id"] = selected["draft_id"]
        session["source_id"] = selected["source_id"]
    if session["source_id"] and call.name == "list_documents":
        raise HTTPException(403, "editor_target_forbidden")
    if session["source_id"] and call.name == "list_document_drafts":
        args["source_id"] = session["source_id"]
    if session["draft_id"] and call.name == "list_document_drafts":
        raise HTTPException(403, "editor_target_forbidden")
    if session["draft_id"] and call.name == "create_document_draft":
        saved = await document_tool(
            gateway,
            "get_document_operation",
            session["user"],
            {
                "operation": "draft",
                "key": args.get("idempotency_key"),
                **({"target_id": args["source_id"]} if args.get("source_id") else {}),
            },
            grants=[session["scope"]],
        )
        if saved["result"].get("draft_id") != session["draft_id"]:
            raise HTTPException(403, "editor_target_forbidden")
    result = await document_tool(
        gateway, call.name, session["user"], args, grants=[session["scope"]]
    )
    editor_session(gateway, token)
    observed = result.get("result", result) if call.name == "get_document_operation" else result
    if (
        session["source_id"]
        and observed.get("source_id")
        and observed["source_id"] != session["source_id"]
    ):
        raise HTTPException(403, "editor_target_forbidden")
    if call.name == "get_document_operation" and session["draft_id"]:
        if (
            observed.get("draft_id") is not None and observed["draft_id"] != session["draft_id"]
        ) or (
            observed.get("draft_id") is None and observed.get("source_id") != session["source_id"]
        ):
            raise HTTPException(403, "editor_target_forbidden")
    if isinstance(result.get("draft_id"), str) and not session["draft_id"]:
        session["draft_id"] = result["draft_id"]
        session["source_id"] = result["source_id"]
    return result
