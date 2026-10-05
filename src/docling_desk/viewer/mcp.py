"""Streamable HTTP tools sharing the gateway's actor and version checks."""

import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from docling_desk.viewer.gateway import DocumentReference, Gateway


def create_mcp(gateway: Gateway):
    class Verifier:
        async def verify_token(self, token: str):
            config = gateway.config()
            for user, entry in config["users"].items():
                configured = entry.get("mcp_token", "")
                if len(configured) >= 32 and secrets.compare_digest(
                    configured.encode(), token.encode()
                ):
                    gateway.actor(user)
                    return AccessToken(token=token, client_id=user, scopes=["documents:read"])
            return None

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
            raise ValueError(str(exc.detail)) from None

    @server.tool()
    async def list_document_scopes() -> dict:
        """List this authenticated user's collection/project/region grants before searching."""
        return await invoke("list_document_scopes", {})

    @server.tool()
    async def search_documents(
        query: str, scope: dict, mode: str = "text", limit: int = 10
    ) -> dict:
        """Search authorized documents; preserve retrieval_id and versioned references."""
        return await invoke(
            "search_documents", {"query": query, "scope": scope, "mode": mode, "limit": limit}
        )

    @server.tool()
    async def get_document_context(
        retrieval_id: str, references: list[dict], max_chars: int = 80000
    ) -> dict:
        """Get parent context/tables from a prior search belonging to this user."""
        return await invoke(
            "get_document_context",
            {"retrieval_id": retrieval_id, "references": references, "max_chars": max_chars},
        )

    @server.tool()
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

    @server.tool()
    async def connect_document_view(code: str) -> dict:
        """Approve the user-provided 8-character code shown in their viewer."""
        return await invoke("connect_document_view", {"code": code})

    return server


def app_factory():
    from docling_desk.viewer.gateway import create_gateway

    return create_gateway(Path(os.environ["VIEWER_CONFIG_FILE"]), mcp_enabled=True)
