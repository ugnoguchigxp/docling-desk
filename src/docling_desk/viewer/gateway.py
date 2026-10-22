"""Browser pairing and trusted host mediation for read-only knowledge viewers.

Persistent embeds contain only versioned references. The opaque sandbox acquires
an in-memory, short-lived, document-scoped session after a trusted host tool
approves its single-use device code. No API token/key is delivered to a browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from docling_desk.config import RESOURCES
from docling_desk.web.body import BodyLimit

ROOT = RESOURCES
MAX_API_RESPONSE = 46 * 1024 * 1024


class RedactViewerCredentials(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(
                re.sub(
                    r"(/(?:viewer|editor)/(?:session|challenges)/)[^/?\s]+", r"\1[redacted]", value
                )
                if isinstance(value, str)
                else value
                for value in record.args
            )
        return True


class ViewerBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        if scope["path"] == "/api/v1/document-transfers":
            # Multipart files spool to disk; bound the stream without buffering
            # another 50 MiB copy before authentication or form parsing.
            await BodyLimit(self.app, 51 * 1024 * 1024, "document_transfer_too_large")(
                scope, receive, send
            )
            return
        parts = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            part = message.get("body", b"")
            size += len(part)
            limit = (
                2 * 1024 * 1024
                if scope["path"].startswith(("/editor/", "/mcp/", "/host/tools/"))
                else 32768
            )
            if size > limit:
                await JSONResponse({"detail": "request_too_large"}, status_code=413)(
                    scope, receive, send
                )
                return
            parts.append(part)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(parts), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class OriginalRange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["page", "slide", "sheet", "document"]
    number: int = Field(default=1, ge=1, le=100000)
    original_range: OriginalRange | None = None


class DocumentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    source_id: UUID
    source_revision: UUID
    evidence_revision: UUID
    location: Location | None = None


class HostCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=256)
    arguments: dict


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    region: str | None = Field(default=None, min_length=1, max_length=128)


class UserPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[Grant] = Field(min_length=1, max_length=100)
    mcp_token: str | None = Field(default=None, min_length=32, max_length=256)
    actions: list[Literal["read", "write"]] = Field(default=["read"], min_length=1)
    api_client: str | None = None


class ApiCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str
    issuer: str
    kid: str
    api_token: str = Field(pattern=r"^[A-Za-z0-9._~-]{32,256}$")
    signing_key: str = Field(min_length=32, max_length=256)


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_url: str
    public_url: str
    client_id: str = Field(min_length=1, max_length=128)
    issuer: str = Field(min_length=1, max_length=256)
    kid: str = Field(min_length=1, max_length=128)
    api_token: str = Field(pattern=r"^[A-Za-z0-9._~-]{32,256}$")
    signing_key: str = Field(min_length=32, max_length=256)
    connector_token: str = Field(min_length=32, max_length=256)
    users: dict[str, UserPolicy]
    api_clients: dict[str, ApiCredential] = Field(default_factory=dict)
    mcp_hosts: list[str] = Field(default_factory=list, max_length=10)
    trusted_proxies: list[str] = Field(default_factory=list, max_length=20)
    transfer_dir: str | None = None


def encoded(value: dict) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


class Gateway:
    guide_contract_error: bool = False

    def __init__(self, config_file: Path, transport: httpx.AsyncBaseTransport | None = None):
        self.config_file, self.transport = config_file, transport
        self.pending: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.approvals: dict[str, list[float]] = {}
        self.issuer_hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def config(self) -> dict:
        try:
            config = GatewayConfig.model_validate_json(self.config_file.read_text()).model_dump(
                exclude_none=True
            )
            for key in ("api_token", "signing_key", "connector_token"):
                if len(config[key]) < 32:
                    raise ValueError()
            for key in ("api_url", "public_url"):
                url = urlsplit(config[key])
                if (
                    url.scheme not in {"http", "https"}
                    or not url.netloc
                    or url.username
                    or url.password
                    or url.query
                    or url.fragment
                    or url.path not in {"", "/"}
                ):
                    raise ValueError()
            if not isinstance(config["users"], dict):
                raise ValueError()
            tokens = [u["mcp_token"] for u in config["users"].values() if u.get("mcp_token")]
            if len(tokens) != len(set(tokens)) or not 1 <= len(config["users"]) <= 10000:
                raise ValueError()
            return config
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(503, "viewer_configuration_invalid") from None

    def actor(self, user: str) -> tuple[dict, str]:
        config = self.config()
        entry = config["users"].get(user)
        if not entry or not entry.get("scopes"):
            raise HTTPException(403, "viewer_user_forbidden")
        if entry.get("api_client"):
            credential = config["api_clients"].get(entry["api_client"])
            if not credential:
                raise HTTPException(503, "viewer_configuration_invalid")
            config = {**config, **credential}
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    config["api_token"],
                    config["signing_key"],
                    config["client_id"],
                    config["issuer"],
                    config["api_url"],
                    config["kid"],
                    entry,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return config, fingerprint

    def token_actor(self, token: str) -> tuple[str, str]:
        """Resolve the configured user without importing the optional MCP SDK."""
        for user, entry in self.config()["users"].items():
            configured = entry.get("mcp_token", "")
            if configured and secrets.compare_digest(configured.encode(), token.encode()):
                _, fingerprint = self.actor(user)
                return user, fingerprint
        raise HTTPException(401, "unauthenticated")

    def guides(self):
        from docling_desk.viewer.tool_guides import ToolGuides

        try:
            if not hasattr(self, "_guides"):
                self._guides = ToolGuides()
            if getattr(self, "guide_contract_error", False):
                raise ValueError("MCP contract mismatch")
            return self._guides
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            raise HTTPException(503, "tool_guides_invalid") from None

    async def api(
        self,
        user: str,
        path: str,
        body: dict | None,
        *,
        method="POST",
        headers=None,
        grants=None,
        files=None,
        form=None,
        include_etag=False,
    ) -> dict:
        from docling_desk.viewer.document_tools import DOCUMENT_TOOLS, READ_POST

        config, fingerprint = self.actor(user)
        read_posts = {"/api/v1/search", "/api/v1/context", "/api/v1/viewer"} | {
            "/api/v1" + DOCUMENT_TOOLS[name][1] for name in READ_POST
        }
        action = (
            "read"
            if method == "GET" or (method == "POST" and path.split("?", 1)[0] in read_posts)
            else "write"
        )
        if action not in config["users"][user]["actions"]:
            raise HTTPException(403, "forbidden")
        if grants is not None and any(g not in config["users"][user]["scopes"] for g in grants):
            raise HTTPException(403, "scope_forbidden")
        now = int(time.time())
        head = encoded({"alg": "HS256", "typ": "JWT", "kid": config["kid"]})
        claims = encoded(
            {
                "iss": config["issuer"],
                "aud": "docling-desk-api",
                "sub": user,
                "client_id": config["client_id"],
                "iat": now,
                "exp": now + 60,
                "scopes": grants if grants is not None else config["users"][user]["scopes"],
            }
        )
        signature = (
            base64.urlsafe_b64encode(
                hmac.digest(config["signing_key"].encode(), f"{head}.{claims}".encode(), "sha256")
            )
            .decode()
            .rstrip("=")
        )
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=35, follow_redirects=False
            ) as client:
                async with client.stream(
                    method,
                    config["api_url"].rstrip("/") + path,
                    files=files,
                    data=form if files is not None else None,
                    json=body if files is None else None,
                    headers={
                        "authorization": f"Bearer {config['api_token']}",
                        "x-knowledge-actor": f"{head}.{claims}.{signature}",
                        **(headers or {}),
                    },
                ) as response:
                    response_body = bytearray()
                    async for part in response.aiter_bytes():
                        if len(response_body) + len(part) > MAX_API_RESPONSE:
                            raise HTTPException(502, "viewer_response_too_large")
                        response_body.extend(part)
                    try:
                        data = json.loads(response_body)
                    except ValueError:
                        raise HTTPException(502, "invalid_viewer_response") from None
                    if not isinstance(data, dict):
                        raise HTTPException(502, "invalid_viewer_response")
                    if not response.is_success:
                        error = data.get("error")
                        code = error.get("code") if isinstance(error, dict) else None
                        raise HTTPException(
                            response.status_code,
                            code if isinstance(code, str) else "viewer_api_unavailable",
                        )
                    if include_etag:
                        etag = response.headers.get("etag")
                        if not etag:
                            raise HTTPException(502, "document_etag_missing")
                        data = {**data, "source_etag": etag}
                if self.actor(user)[1] != fingerprint:
                    raise HTTPException(401, "viewer_session_revoked")
                return data
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(503, "viewer_api_unavailable") from None

    async def manifest(self, user: str, reference: DocumentReference, token: str) -> dict:
        ref = reference.model_dump(mode="json", exclude={"location", "version"})
        result = await self.api(
            user,
            "/api/v1/viewer",
            {
                **ref,
                **(
                    {"original_range": reference.location.original_range.model_dump()}
                    if reference.location and reference.location.original_range
                    else {}
                ),
                "resource": "manifest",
                "prefix": f"/viewer/session/{token}/",
            },
        )
        if (
            not isinstance(result.get("kind"), str)
            or result["kind"] not in {"page", "slide", "sheet", "document"}
            or type(result.get("units")) is not int
            or result["units"] < 1
            or not isinstance(result.get("job"), dict)
            or not isinstance(result["job"].get("filename"), str)
            or not isinstance(result["job"].get("id"), str)
        ):
            raise HTTPException(502, "invalid_viewer_response")
        location = reference.location
        if location and (
            location.kind != result["kind"]
            or location.number > result["units"]
            or (location.kind == "document" and location.number != 1)
        ):
            raise HTTPException(422, "invalid_location")
        return {**result, "reference": reference.model_dump(mode="json")}

    def clean(self):
        now = time.time()
        for entries in (self.pending, self.sessions):
            for key in list(entries):
                if entries[key]["expires"] <= now:
                    del entries[key]
        for key, stamps in list(self.issuer_hits.items()):
            fresh = [stamp for stamp in stamps if stamp > now - 60]
            if fresh:
                self.issuer_hits[key] = fresh
            else:
                del self.issuer_hits[key]

    def issuer(self, peer: str | None, forwarded: str | None) -> str:
        """Use the hop added by a trusted proxy. The left-hand value is client-controlled."""
        host = peer or "unknown"
        trusted = set(self.config().get("trusted_proxies") or [])
        if host in trusted and forwarded:
            hops = [part.strip() for part in forwarded.split(",") if part.strip()]
            hop = hops[-1] if hops else ""
            if hop and len(hop) <= 128 and "\n" not in hop and "\r" not in hop:
                return hop
        return host

    def challenge(self, reference: DocumentReference, issuer: str) -> dict:
        with self.lock:
            self.clean()
            now = time.time()
            recent = [stamp for stamp in self.issuer_hits.get(issuer, []) if stamp > now - 60]
            own = sum(
                1
                for item in self.pending.values()
                if item.get("issuer") == issuer and item.get("status") == "pending"
            )
            if own >= 20 or len(recent) >= 30:
                raise HTTPException(429, "viewer_issuer_limited", headers={"Retry-After": "60"})
            if len(self.pending) >= 1000:
                raise HTTPException(429, "viewer_busy", headers={"Retry-After": "60"})
            self.issuer_hits[issuer] = [*recent, now]
            nonce = secrets.token_urlsafe(32)
            codes = {v["code"] for v in self.pending.values()}
            code = secrets.token_hex(4).upper()
            while code in codes:
                code = secrets.token_hex(4).upper()
            self.pending[nonce] = {
                "code": code,
                "reference": reference,
                "expires": now + 180,
                "status": "pending",
                "issuer": issuer,
            }
            return {"challenge": nonce, "code": code, "expires_in": 180}

    async def approve(self, user: str, code: str) -> dict:
        with self.lock:
            self.clean()
            attempts = [t for t in self.approvals.get(user, []) if t > time.time() - 60]
            if len(attempts) >= 20:
                raise HTTPException(429, "viewer_approval_rate_limited")
            self.approvals[user] = [*attempts, time.time()]
            candidate = next(
                (
                    (key, value)
                    for key, value in self.pending.items()
                    if secrets.compare_digest(value["code"], code.upper())
                    and value["status"] == "pending"
                    and value.get("purpose") != "editor"
                ),
                None,
            )
            if not candidate:
                raise HTTPException(404, "viewer_code_expired")
            nonce, pending = candidate
            pending["status"] = "approving"
        token = secrets.token_urlsafe(32)
        try:
            _, fingerprint = self.actor(user)
            manifest = await self.manifest(user, pending["reference"], token)
            if self.actor(user)[1] != fingerprint:
                raise HTTPException(401, "viewer_session_revoked")
        except Exception:
            with self.lock:
                self.pending.pop(nonce, None)
            raise
        with self.lock:
            if nonce not in self.pending or pending["expires"] <= time.time():
                raise HTTPException(410, "viewer_code_expired")
            if len(self.sessions) >= 5000:
                self.pending.pop(nonce, None)
                raise HTTPException(429, "viewer_busy")
            self.sessions[token] = {
                "user": user,
                "fingerprint": fingerprint,
                "reference": pending["reference"],
                "expires": time.time() + 300,
            }
            pending.update(status="approved", token=token)
        return {
            "status": "connected",
            "reference": manifest["reference"],
            "filename": manifest["job"]["filename"],
            "expires_in": 300,
        }

    def session(self, token: str) -> dict:
        with self.lock:
            self.clean()
            session = self.sessions.get(token)
        if not session:
            raise HTTPException(401, "viewer_session_expired")
        _, fingerprint = self.actor(session["user"])
        if fingerprint != session["fingerprint"]:
            raise HTTPException(401, "viewer_session_revoked")
        return session

    def view_url(self, reference: DocumentReference) -> str:
        return (
            self.config()["public_url"].rstrip("/")
            + "/viewer?"
            + urlencode(
                {"ref": json.dumps(reference.model_dump(mode="json"), separators=(",", ":"))}
            )
        )

    async def tool(self, name: str, user: str, args: dict) -> dict:
        from docling_desk.viewer.document_tools import DOCUMENT_TOOLS, document_tool
        from docling_desk.viewer.editor import approve_editor

        config, _ = self.actor(user)
        if (
            name
            in {
                "list_document_scopes",
                "search_documents",
                "get_document_context",
                "request_document_view",
                "connect_document_view",
                "connect_document_editor",
            }
            and "read" not in config["users"][user]["actions"]
        ):
            raise HTTPException(403, "forbidden")
        if name in {"import_document", "replace_document_source"}:
            from docling_desk.viewer.transfers import transfer_tool

            return await transfer_tool(self, name, user, args)
        if name in {"list_tool_guides", "get_tool_guide"}:
            config, fingerprint = self.actor(user)
            if "read" not in config["users"][user]["actions"]:
                raise HTTPException(403, "forbidden")
            result = self.guides().invoke(name, args)
            if self.actor(user)[1] != fingerprint:
                raise HTTPException(401, "unauthenticated")
            return result
        if name in DOCUMENT_TOOLS:
            return await document_tool(self, name, user, args)
        if name == "connect_document_editor":
            if (
                set(args) != {"code"}
                or not isinstance(args["code"], str)
                or not re.fullmatch(r"[A-Fa-f0-9]{8}", args["code"])
            ):
                raise HTTPException(422, "invalid_editor_code")
            return await approve_editor(self, user, args["code"])

        if name == "list_document_scopes":
            capabilities = await self.api(user, "/api/v1/scopes", None, method="GET")
            policy = self.actor(user)[0]["users"][user]["actions"]
            return {
                **capabilities,
                "actions": [action for action in capabilities["actions"] if action in policy],
            }
        if name == "search_documents":
            return await self.api(user, "/api/v1/search", args)
        if name == "get_document_context":
            return await self.api(user, "/api/v1/context", args)
        if name == "request_document_view":
            locator = args.pop("locator", None)
            reference = DocumentReference.model_validate(args)
            manifest = await self.manifest(user, reference, secrets.token_urlsafe(32))
            if locator is not None:
                if not isinstance(locator, dict):
                    raise HTTPException(422, "invalid_location")
                number = (
                    locator.get("slide_number")
                    if manifest["kind"] == "slide"
                    else locator.get("page_number")
                    if manifest["kind"] == "page"
                    else None
                )
                if manifest["kind"] == "sheet":
                    number = locator.get("sheet_number")
                if manifest["kind"] == "document" and locator.get("original_range"):
                    reference.location = Location(
                        kind="document", number=1, original_range=locator["original_range"]
                    )
                    await self.manifest(user, reference, secrets.token_urlsafe(32))
                if number is not None:
                    reference.location = Location(kind=manifest["kind"], number=number)
                    await self.manifest(user, reference, secrets.token_urlsafe(32))
            return {
                "reference": reference.model_dump(mode="json"),
                "embed_url": self.view_url(reference),
                "status": "connection_required",
                "location_known": reference.location is not None,
            }
        if name == "connect_document_view":
            if (
                set(args) != {"code"}
                or not isinstance(args["code"], str)
                or not re.fullmatch(r"[A-Fa-f0-9]{8}", args["code"])
            ):
                raise HTTPException(422, "invalid_viewer_code")
            return await self.approve(user, args["code"])
        raise HTTPException(404, "unknown_tool")


def create_gateway(
    config_file: Path,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    mcp_enabled: bool = False,
) -> FastAPI:
    gateway = Gateway(config_file, transport)
    origin = gateway.config()["public_url"].rstrip("/")
    mcp_app = None
    if mcp_enabled:
        from docling_desk.viewer.mcp import create_mcp

        server = create_mcp(gateway)
        mcp_app = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        if mcp_app:
            try:
                gateway.guides().check_tools(await server.list_tools())
            except (HTTPException, ValueError):
                gateway.guide_contract_error = True
            async with server.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(
        title="Docling Desk viewer gateway", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.gateway = gateway
    logging.getLogger("uvicorn.access").addFilter(RedactViewerCredentials())
    if mcp_app:
        app.mount("/mcp", mcp_app)
    # Opaque sandbox Origin:null is supported. CORS never grants authorization;
    # private reads always require a scoped random session, no cookies involved.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    from docling_desk.viewer.editor import install_editor

    install_editor(app, gateway)
    from docling_desk.viewer.tool_guides import install_tool_guides

    install_tool_guides(app, gateway)
    from docling_desk.viewer.transfers import install_transfers

    install_transfers(app, gateway)
    app.add_middleware(ViewerBodyLimit)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        # CSP applies both to entry and untrusted document frames. Resources are
        # same server; connect-src permits opaque-origin requests to this server.
        response.headers["Content-Security-Policy"] = (
            f"default-src 'none'; script-src {origin}; style-src {origin} 'unsafe-inline'; img-src {origin} data:; font-src {origin} data:; frame-src {origin}; connect-src {origin}; base-uri 'none'; object-src 'none'; form-action 'none'"
        )
        if request.url.path in {"/viewer", "/editor"} or (
            request.url.path.startswith("/viewer/session/")
            and ("/view/" in request.url.path or request.url.path.endswith(".html"))
        ):
            response.headers["Content-Security-Policy"] += "; sandbox allow-scripts allow-downloads"
        return response

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready():
        gateway.guides()
        config = gateway.config()
        user = next(iter(config["users"]))
        result = await gateway.api(user, "/health/ready", None, method="GET")
        await gateway.api(user, "/api/v1/scopes", None, method="GET")
        return {"status": "ready", "api": result["status"], "editor": True, "mcp": mcp_enabled}

    @app.get("/viewer", response_class=HTMLResponse)
    def entry():
        return HTMLResponse((ROOT / "static/frontend/embed.html").read_text())

    @app.get("/static/{path:path}")
    def asset(path: str):
        target = ROOT / "static" / path
        if (
            target.is_symlink()
            or (ROOT / "static").resolve() not in target.resolve().parents
            or target.suffix not in {".js", ".css"}
            or not target.is_file()
        ):
            raise HTTPException(404)
        return FileResponse(target)

    @app.post("/viewer/challenges")
    def challenge(reference: DocumentReference, request: Request):
        peer = request.client.host if request.client else None
        return gateway.challenge(
            reference, gateway.issuer(peer, request.headers.get("x-forwarded-for"))
        )

    @app.get("/viewer/challenges/{nonce}")
    def poll(nonce: str):
        with gateway.lock:
            gateway.clean()
            pending = gateway.pending.get(nonce)
            if not pending or pending.get("purpose", "viewer") != "viewer":
                raise HTTPException(410, "viewer_code_expired")
            if pending["status"] == "approved":
                gateway.pending.pop(nonce)
                return {"status": "approved", "session": pending["token"]}
            return {"status": "pending"}

    @app.get("/viewer/session/{token}/{resource:path}")
    async def resource(token: str, resource: str):
        session = gateway.session(token)
        ref = session["reference"]
        if resource == "manifest":
            result = await gateway.manifest(session["user"], ref, token)
            gateway.session(token)
            return result
        data = await gateway.api(
            session["user"],
            "/api/v1/viewer",
            {
                **ref.model_dump(mode="json", exclude={"location", "version"}),
                **(
                    {"original_range": ref.location.original_range.model_dump()}
                    if ref.location and ref.location.original_range
                    else {}
                ),
                "resource": resource,
                "prefix": f"/viewer/session/{token}/",
            },
        )
        gateway.session(token)
        media_type = data.get("media_type")
        if (
            not isinstance(data.get("body_base64"), str)
            or not isinstance(media_type, str)
            or len(media_type) > 128
            or not re.fullmatch(
                r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+(?:;[ -~]+)?", media_type
            )
        ):
            raise HTTPException(502, "invalid_viewer_response")
        try:
            body = base64.b64decode(data["body_base64"], validate=True)
        except ValueError:
            raise HTTPException(502, "invalid_viewer_response") from None
        return Response(
            body,
            media_type=media_type,
            headers={"Content-Disposition": "attachment"}
            if resource.startswith("attachment/")
            else None,
        )

    @app.post("/host/tools/{name}")
    async def host(name: str, call: HostCall, authorization: str = Header(default="")):
        if not secrets.compare_digest(
            authorization.encode(), ("Bearer " + gateway.config()["connector_token"]).encode()
        ):
            raise HTTPException(401, "unauthenticated")
        try:
            return await gateway.tool(name, call.user_id, call.arguments)
        except ValueError:
            raise HTTPException(422, "invalid_viewer_reference") from None

    return app


def app_factory() -> FastAPI:
    return create_gateway(Path(os.environ["VIEWER_CONFIG_FILE"]), mcp_enabled=True)
