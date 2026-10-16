"""Streamable HTTP tools sharing the gateway's actor and version checks."""

import base64
import json
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import AnyHttpUrl, Field

from docling_desk.viewer.gateway import DocumentReference, Gateway
from docling_desk.viewer.read_results import PageImageResult
from docling_desk.viewer.tool_guides import GuideGetResult, GuideListResult, tool_summary


def create_mcp(gateway: Gateway):
    class Verifier:
        async def verify_token(self, token: str):
            try:
                user, _ = gateway.token_actor(token)
            except HTTPException:
                return None
            config, _ = gateway.actor(user)
            return AccessToken(
                token=token,
                client_id=user,
                scopes=["documents:" + action for action in config["users"][user]["actions"]],
            )

    server = FastMCP(
        "Docling documents",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                urlsplit(gateway.config()["public_url"]).netloc,
                "127.0.0.1:*",
                "localhost:*",
                "[::1]:*",
                *gateway.config()["mcp_hosts"],
            ],
            allowed_origins=[gateway.config()["public_url"]],
        ),
        token_verifier=Verifier(),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(gateway.config()["public_url"]),
            resource_server_url=AnyHttpUrl(gateway.config()["public_url"] + "/mcp"),
            required_scopes=["documents:read"],
        ),
    )

    async def invoke(name: str, args: dict):
        actor = get_access_token()
        if actor is None:
            raise ValueError("unauthenticated")
        # Verify on every call, including a token revoked since initialization.
        verified = await Verifier().verify_token(actor.token)
        if verified is None or verified.client_id != actor.client_id:
            raise ValueError("unauthenticated")
        try:
            result = await gateway.tool(name, actor.client_id, args)
            verified = await Verifier().verify_token(actor.token)
            if verified is None or verified.client_id != actor.client_id:
                raise ValueError("unauthenticated")
            return result
        except HTTPException as exc:
            raise ValueError(
                json.dumps(
                    {
                        "error": {
                            "code": str(exc.detail),
                            "status": exc.status_code,
                            "retryable": exc.status_code in {429, 503},
                        }
                    }
                )
            ) from None

    @server.tool(description=tool_summary("list_tool_guides"))
    async def list_tool_guides(
        kind: Literal["overview", "tool", "workflow"] | None = None,
        tool_name: str | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> GuideListResult:
        return GuideListResult.model_validate(
            await invoke(
                "list_tool_guides",
                {
                    "kind": kind,
                    "tool_name": tool_name,
                    "cursor": cursor,
                    "limit": limit,
                },
            )
        )

    @server.tool(description=tool_summary("get_tool_guide"))
    async def get_tool_guide(
        guide_id: str,
        section: str | None = None,
        cursor: str | None = None,
        max_chars: Annotated[int, Field(ge=256, le=48000)] = 12000,
    ) -> GuideGetResult:
        return GuideGetResult.model_validate(
            await invoke(
                "get_tool_guide",
                {
                    "guide_id": guide_id,
                    "section": section,
                    "cursor": cursor,
                    "max_chars": max_chars,
                },
            )
        )

    @server.tool(description=tool_summary("list_document_scopes"))
    async def list_document_scopes() -> dict:
        """List this authenticated user's collection/project/region grants before searching."""
        return await invoke("list_document_scopes", {})

    @server.tool(description=tool_summary("search_documents"))
    async def search_documents(
        query: str, scope: dict, mode: str = "text", limit: int = 10
    ) -> dict:
        """Search authorized documents; preserve retrieval_id and versioned references."""
        return await invoke(
            "search_documents", {"query": query, "scope": scope, "mode": mode, "limit": limit}
        )

    @server.tool(description=tool_summary("get_document_context"))
    async def get_document_context(
        retrieval_id: str, references: list[dict], max_chars: int = 80000
    ) -> dict:
        """Get parent context/tables from a prior search belonging to this user."""
        return await invoke(
            "get_document_context",
            {"retrieval_id": retrieval_id, "references": references, "max_chars": max_chars},
        )

    @server.tool(description=tool_summary("request_document_view"))
    async def request_document_view(
        source_id: str, source_revision: str, evidence_revision: str, location: dict | None = None
    ) -> dict:
        """Validate a versioned reference and return a token-free viewer URL."""
        reference = DocumentReference(
            source_id=source_id,
            source_revision=source_revision,
            evidence_revision=evidence_revision,
            location=location,
        )
        return await invoke("request_document_view", reference.model_dump(mode="json"))

    @server.tool(description=tool_summary("connect_document_view"))
    async def connect_document_view(code: str) -> dict:
        """Approve the user-provided 8-character code shown in their viewer."""
        return await invoke("connect_document_view", {"code": code})

    from docling_desk.viewer.document_tools import DOCUMENT_OUTPUTS, DOCUMENT_TOOLS
    from docling_desk.viewer.tool_contract import document_arguments
    from docling_desk.viewer.transfers import TRANSFER_INPUTS

    def register(name):
        async def operation(arguments: dict) -> dict:
            result = await invoke(name, arguments)
            if name == "get_document_page_image":
                encoded = result.pop("body_base64")
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError):
                    raise ValueError("invalid_document_image") from None
                if len(data) > 1024 * 1024 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
                    raise ValueError("invalid_document_image")
                metadata = PageImageResult.model_validate(result).model_dump(mode="json")
                return CallToolResult(
                    content=[
                        TextContent(type="text", text=json.dumps(metadata)),
                        ImageContent(type="image", data=encoded, mimeType=metadata["media_type"]),
                    ],
                    structuredContent=metadata,
                )
            return (
                DOCUMENT_OUTPUTS[name].model_validate(result)
                if name in DOCUMENT_OUTPUTS
                else result
            )

        if name == "get_document_page_image":
            operation.__annotations__["return"] = Annotated[CallToolResult, PageImageResult]
        elif name in DOCUMENT_OUTPUTS:
            operation.__annotations__["return"] = DOCUMENT_OUTPUTS[name]

        operation.__annotations__["arguments"] = Annotated[
            dict,
            Field(
                json_schema_extra=(
                    TRANSFER_INPUTS[name].model_json_schema()
                    if name in TRANSFER_INPUTS
                    else document_arguments(name)
                )
            ),
        ]
        operation.__doc__ = tool_summary(name) + (
            " Identity and grants come from this connection. Obtain exact IDs, etags, hashes "
            "and raw body from the get operation; read the guide for prerequisites and recovery."
        )
        server.tool(name=name)(operation)

    for name in [*DOCUMENT_TOOLS, *TRANSFER_INPUTS]:
        register(name)

    @server.tool(description=tool_summary("connect_document_editor"))
    async def connect_document_editor(code: str) -> dict:
        """Approve the user-provided editor pairing code for this authenticated writer."""
        return await invoke("connect_document_editor", {"code": code})

    return server


def app_factory():
    from docling_desk.viewer.gateway import create_gateway

    return create_gateway(Path(os.environ["VIEWER_CONFIG_FILE"]), mcp_enabled=True)
