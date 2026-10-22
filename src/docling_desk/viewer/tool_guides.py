"""Read-only, versioned usage guides shared by HTTP, MCP and trusted hosts."""

from __future__ import annotations

import base64
import builtins
import hashlib
import json
from pathlib import Path
from typing import Literal

from fastapi import Header, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from docling_desk.config import RESOURCES
from docling_desk.viewer.document_tools import DOCUMENT_TOOLS
from docling_desk.viewer.tool_contract import document_arguments, read_contract

GUIDE_ROOT = RESOURCES / "tool-guides"
MAX_RESPONSE_BYTES = 256 * 1024


class GuideListInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["overview", "tool", "workflow"] | None = None
    tool_name: str | None = Field(default=None, max_length=128)
    cursor: str | None = Field(default=None, max_length=1024)
    limit: int = Field(default=20, ge=1, le=100)


class GuideGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    guide_id: str = Field(min_length=1, max_length=160)
    section: str | None = Field(default=None, max_length=64)
    cursor: str | None = Field(default=None, max_length=1024)
    max_chars: int = Field(default=12000, ge=256, le=48000)


GUIDE_INPUTS = {"list_tool_guides": GuideListInput, "get_tool_guide": GuideGetInput}


class GuideSummary(BaseModel):
    guide_id: str
    title: str
    summary: str
    kind: Literal["overview", "tool", "workflow"]
    tool_names: list[str]
    required_actions: list[str]
    guide_revision: str


class GuideSection(BaseModel):
    id: str
    title: str
    body: str
    start: int
    end: int
    total_chars: int


class GuideListResult(BaseModel):
    schema_version: Literal[1]
    catalog_revision: str
    guides: list[GuideSummary]
    next_cursor: str | None


class GuideGetResult(BaseModel):
    schema_version: Literal[1]
    catalog_revision: str
    guide_revision: str
    guide_id: str
    title: str
    kind: Literal["overview", "tool", "workflow"]
    tool_names: list[str]
    required_actions: list[str]
    related_guides: list[str]
    section_ids: list[str]
    sections: list[GuideSection]
    schemas: dict[str, dict]
    schema_refs: list[str]
    next_cursor: str | None


def tool_summary(name: str) -> str:
    try:
        manifest = json.loads((GUIDE_ROOT / "manifest.json").read_text(encoding="utf-8"))
        entry = next(item for item in manifest["guides"] if item["guide_id"] == "tool." + name)
        summary = entry["summary"]
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        # A bad guide distribution is reported by readiness and guide calls;
        # keep the existing document tools and viewer available.
        summary = name
    return summary + f" Guide: get_tool_guide(guide_id='tool.{name}')."


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ToolGuides:
    def __init__(self, root: Path = GUIDE_ROOT):
        self.root = root
        self.entries = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["guides"]
        self.tools = read_contract("mcp-tools.json")
        self.api = read_contract()
        guide_api = read_contract("tool-guides.openapi.json")
        self.transfer_api = read_contract("document-transfers.openapi.json")
        self.by_id = {entry["guide_id"]: entry for entry in self.entries}
        if len(self.by_id) != len(self.entries):
            raise ValueError("Duplicate guide ID")
        self.texts = {}
        for entry in self.entries:
            if entry["kind"] not in {"overview", "tool", "workflow"}:
                raise ValueError("Invalid guide kind")
            for name in entry["tool_names"]:
                if name not in self.tools:
                    raise ValueError(f"Unknown guide tool: {name}")
            for related in entry["related_guides"]:
                if related not in self.by_id:
                    raise ValueError(f"Unknown related guide: {related}")
            sections = entry["sections"]
            if entry["kind"] == "tool":
                if entry["tool_names"] != [entry["guide_id"].removeprefix("tool.")]:
                    raise ValueError("Guide name and tool differ")
                if not {"usage", "examples", "recovery"} <= {s["id"] for s in sections}:
                    raise ValueError("Missing required tool guide section")
            if not sections or len({section["id"] for section in sections}) != len(sections):
                raise ValueError("Duplicate section")
            if any(s["id"] == "schemas" for s in sections):
                raise ValueError("Reserved schema section")
            for section in sections:
                path = (root / section["file"]).resolve()
                if not path.is_relative_to(root.resolve()):
                    raise ValueError("Invalid guide resource")
                self.texts[section["file"]] = path.read_text(encoding="utf-8")
                if not self.texts[section["file"]].strip():
                    raise ValueError("Empty guide section")
        expected = {"tool." + name for name in self.tools}
        actual = {e["guide_id"] for e in self.entries if e["kind"] == "tool"}
        if actual != expected:
            raise ValueError("Public tool guide coverage differs")
        for name in DOCUMENT_TOOLS:
            published = self.tools[name]["inputSchema"]["properties"]["arguments"]
            if any(published.get(k) != v for k, v in document_arguments(name).items()):
                raise ValueError("Document API and bundled MCP input differ")
        self.revision = digest(
            [self.entries, self.texts, self.tools, self.api, guide_api, self.transfer_api]
        )

    def check_tools(self, tools) -> None:
        actual = {
            tool.name: {"inputSchema": tool.inputSchema, "outputSchema": tool.outputSchema}
            for tool in tools
        }
        if actual != self.tools:
            raise ValueError("MCP tool contracts differ from bundled guides")

    def schemas(self, entry: dict) -> dict:
        result = {}
        for name in entry["tool_names"]:
            item = {"mcp": self.tools[name]}
            item["host_http"] = {
                "method": "POST",
                "path": "/host/tools/" + name,
                "body": {
                    "user_id": "host-authenticated user",
                    "arguments": "business arguments; unwrap the inner arguments of document MCP tools",
                },
                "authentication": "trusted host connector token; never model-supplied identity",
            }
            if name in DOCUMENT_TOOLS:
                method, route, _, _ = DOCUMENT_TOOLS[name]
                item["http"] = {
                    "method": method,
                    "path": "/api/v1" + route,
                    "operation": {
                        key: value
                        for key, value in self.api["paths"]["/api/v1" + route][
                            method.lower()
                        ].items()
                        if method != "GET" or key != "requestBody"
                    },
                }
            elif name in {"list_document_scopes", "search_documents", "get_document_context"}:
                method, route = {
                    "list_document_scopes": ("GET", "/api/v1/scopes"),
                    "search_documents": ("POST", "/api/v1/search"),
                    "get_document_context": ("POST", "/api/v1/context"),
                }[name]
                item["http"] = {
                    "method": method,
                    "path": route,
                    "operation": self.api["paths"][route][method.lower()],
                }
            elif name in {"import_document", "replace_document_source"}:
                route = (
                    "/api/v1/sources"
                    if name == "import_document"
                    else "/api/v1/sources/{source_id}/revisions"
                )
                item["http"] = {
                    "method": "POST",
                    "path": route,
                    "operation": self.api["paths"][route]["post"],
                }
                item["transfer_http"] = self.transfer_api
            result[name] = item
        return result

    def cursor(self, kind: str, identity: builtins.list, offset: int) -> str:
        payload = [self.revision, kind, identity, offset]
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    def offset(self, cursor: str | None, kind: str, identity: builtins.list) -> int:
        if cursor is None:
            return 0
        try:
            revision, saved_kind, saved_identity, offset = json.loads(
                base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            )
            if revision != self.revision:
                raise HTTPException(409, "tool_guide_cursor_stale")
            if (
                saved_kind != kind
                or saved_identity != identity
                or type(offset) is not int
                or offset < 0
            ):
                raise ValueError()
            return offset
        except (ValueError, TypeError, UnicodeError):
            raise HTTPException(422, "invalid_tool_guide_cursor") from None

    def list(self, payload: GuideListInput) -> dict:
        identity = [payload.kind, payload.tool_name]
        entries = [
            entry
            for entry in self.entries
            if (payload.kind is None or entry["kind"] == payload.kind)
            and (payload.tool_name is None or payload.tool_name in entry["tool_names"])
        ]
        offset = self.offset(payload.cursor, "list", identity)
        if offset > len(entries):
            raise HTTPException(422, "invalid_tool_guide_cursor")
        end = min(offset + payload.limit, len(entries))
        return {
            "schema_version": 1,
            "catalog_revision": self.revision,
            "guides": [
                {
                    key: entry[key]
                    for key in (
                        "guide_id",
                        "title",
                        "summary",
                        "kind",
                        "tool_names",
                        "required_actions",
                    )
                }
                | {"guide_revision": self.guide_revision(entry)}
                for entry in entries[offset:end]
            ],
            "next_cursor": self.cursor("list", identity, end) if end < len(entries) else None,
        }

    def guide_revision(self, entry: dict) -> str:
        return digest(
            [entry, [self.texts[s["file"]] for s in entry["sections"]], self.schemas(entry)]
        )

    def get(self, payload: GuideGetInput) -> dict:
        entry = self.by_id.get(payload.guide_id)
        if entry is None:
            raise HTTPException(404, "tool_guide_not_found")
        sections = [
            {"id": s["id"], "title": s["title"], "body": self.texts[s["file"]]}
            for s in entry["sections"]
            if payload.section is None or s["id"] == payload.section
        ]
        if payload.section == "schemas":
            sections = [{"id": "schemas", "title": "入出力スキーマ", "body": ""}]
        if not sections:
            raise HTTPException(404, "tool_guide_section_not_found")
        identity = [payload.guide_id, payload.section]
        offset = self.offset(payload.cursor, "get", identity)
        total = sum(len(s["body"]) for s in sections)
        if offset > total:
            raise HTTPException(422, "invalid_tool_guide_cursor")
        remaining, skip, selected = payload.max_chars, offset, []
        for section in sections:
            if skip >= len(section["body"]) and section["body"]:
                skip -= len(section["body"])
                continue
            body = section["body"][skip : skip + remaining]
            selected.append(
                {
                    **section,
                    "body": body,
                    "start": skip,
                    "end": skip + len(body),
                    "total_chars": len(section["body"]),
                }
            )
            remaining -= len(body)
            skip = 0
            if not remaining:
                break
        end = offset + payload.max_chars - remaining
        return {
            "schema_version": 1,
            "catalog_revision": self.revision,
            "guide_revision": self.guide_revision(entry),
            **{
                key: entry[key]
                for key in (
                    "guide_id",
                    "title",
                    "kind",
                    "tool_names",
                    "required_actions",
                    "related_guides",
                )
            },
            "section_ids": [s["id"] for s in entry["sections"]] + ["schemas"],
            "sections": selected,
            "schemas": self.schemas(entry) if entry["kind"] == "tool" else {},
            "schema_refs": ["mcp-tools.json@" + self.revision],
            "next_cursor": self.cursor("get", identity, end) if end < total else None,
        }

    def invoke(self, name: str, arguments: dict) -> dict:
        try:
            payload = GUIDE_INPUTS[name].model_validate(arguments)
        except ValidationError:
            raise HTTPException(422, "invalid_tool_guide_input") from None
        result = self.list(payload) if isinstance(payload, GuideListInput) else self.get(payload)
        if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES:
            raise HTTPException(413, "tool_guide_response_too_large")
        return result


def install_tool_guides(app, gateway) -> None:
    @app.exception_handler(RequestValidationError)
    async def invalid_query(request: Request, exc: RequestValidationError):
        if request.url.path == "/api/v1/tool-guides" or request.url.path.startswith(
            "/api/v1/tool-guides/"
        ):
            return JSONResponse({"detail": "invalid_tool_guide_input"}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    async def read(request: Request, name: str, authorization: str, guide_id: str | None = None):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        user, fingerprint = gateway.token_actor(token)
        arguments: dict[str, str | int] = dict(request.query_params)
        for field in ("limit", "max_chars"):
            if field in arguments:
                try:
                    arguments[field] = int(arguments[field])
                except ValueError:
                    raise HTTPException(422, "invalid_tool_guide_input") from None
        if guide_id is not None:
            arguments["guide_id"] = guide_id
        result = await gateway.tool(name, user, arguments)
        if gateway.token_actor(token) != (user, fingerprint):
            raise HTTPException(401, "unauthenticated")
        return result

    # Concrete query signatures also make the HTTP contract discoverable.
    @app.get("/api/v1/tool-guides", response_model=GuideListResult)
    async def listing(
        request: Request,
        authorization: str = Header(default=""),
        kind: Literal["overview", "tool", "workflow"] | None = Query(default=None),
        tool_name: str | None = Query(default=None, max_length=128),
        cursor: str | None = Query(default=None, max_length=1024),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        return await read(request, "list_tool_guides", authorization)

    @app.get("/api/v1/tool-guides/{guide_id}", response_model=GuideGetResult)
    async def detail(
        guide_id: str,
        request: Request,
        authorization: str = Header(default=""),
        section: str | None = Query(default=None, max_length=64),
        cursor: str | None = Query(default=None, max_length=1024),
        max_chars: int = Query(default=12000, ge=256, le=48000),
    ):
        return await read(request, "get_tool_guide", authorization, guide_id)
