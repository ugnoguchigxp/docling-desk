"""Fixed public API operations used by MCP and the short-lived browser editor."""

from __future__ import annotations

from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException

# name: (method, relative route, identity parameters, query parameters)
DOCUMENT_TOOLS = {
    "list_documents": ("GET", "/sources", (), ("offset", "limit")),
    "get_document": ("GET", "/sources/{source_id}/body", ("source_id",), ("revision",)),
    "create_markdown_document": ("POST", "/markdown-sources", (), ()),
    "update_document_metadata": ("PATCH", "/sources/{source_id}/metadata", ("source_id",), ()),
    "list_document_drafts": ("GET", "/drafts", (), ("source_id", "offset", "limit")),
    "create_document_draft": ("POST", "/drafts", (), ()),
    "get_document_draft": ("GET", "/drafts/{draft_id}", ("draft_id",), ()),
    "update_document_draft": ("PUT", "/drafts/{draft_id}", ("draft_id",), ()),
    "edit_document_draft": ("PATCH", "/drafts/{draft_id}", ("draft_id",), ()),
    "commit_document_draft": ("POST", "/drafts/{draft_id}/commit", ("draft_id",), ()),
    "get_document_operation": ("POST", "/operations/lookup", (), ()),
    "add_document_attachment": ("POST", "/drafts/{draft_id}/attachments", ("draft_id",), ()),
    "list_document_attachments": ("GET", "/sources/{source_id}/attachments", ("source_id",), ()),
    "list_draft_attachments": ("GET", "/drafts/{draft_id}/attachments", ("draft_id",), ()),
    "get_document_attachment": (
        "GET",
        "/sources/{source_id}/attachments/{attachment_id}",
        ("source_id", "attachment_id"),
        (),
    ),
    "get_draft_attachment": (
        "GET",
        "/drafts/{draft_id}/attachments/{attachment_id}",
        ("draft_id", "attachment_id"),
        (),
    ),
    "get_document_job": ("GET", "/jobs/{job_id}", ("job_id",), ()),
    "retry_document_job": ("POST", "/jobs/{job_id}/retry", ("job_id",), ()),
}
READ_POST = {"get_document_operation"}


async def document_tool(gateway, name: str, user: str, arguments: dict, *, grants=None) -> dict:
    if name not in DOCUMENT_TOOLS:
        raise HTTPException(404, "unknown_tool")
    method, route, identities, queries = DOCUMENT_TOOLS[name]
    args = dict(arguments)
    headers = {}
    for field, header in (("etag", "If-Match"), ("idempotency_key", "Idempotency-Key")):
        value = args.pop(field, None)
        if value is not None:
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 256
                or "\n" in value
                or "\r" in value
            ):
                raise HTTPException(422, "invalid_input")
            headers[header] = value
    values = {}
    for field in identities:
        try:
            values[field] = str(UUID(args.pop(field)))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise HTTPException(422, "invalid_input") from None
    query = {field: args.pop(field) for field in queries if field in args}
    if method == "GET" and args:
        raise HTTPException(422, "invalid_input")
    action = "read" if method == "GET" or name in READ_POST else "write"
    config, _ = gateway.actor(user)
    if action not in config["users"][user]["actions"]:
        raise HTTPException(403, "forbidden")
    route = "/api/v1" + route.format(**values)
    if query:
        route += "?" + urlencode(query)
    return await gateway.api(
        user,
        route,
        None if method == "GET" else args,
        method=method,
        headers=headers,
        grants=grants,
    )
