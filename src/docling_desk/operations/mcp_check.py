"""Read-only MCP connection and guide probe for an authenticated host."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    gateway_url: str
    mcp_token_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def valid_gateway(self):
        url = urlsplit(self.gateway_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("gateway_url must be an origin without credentials")
        self.gateway_url = self.gateway_url.rstrip("/")
        return self


def result_json(result) -> dict:
    if result.isError:
        raise ValueError("mcp_tool_failed")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(c.text for c in result.content if c.type == "text"))


async def inspect_session(session) -> dict:
    """Inspect discovery and every workflow using the live guide catalogue."""
    tools = []
    cursor = None
    while True:
        page = await session.list_tools(cursor=cursor)
        tools.extend(t.name for t in page.tools)
        cursor = page.nextCursor
        if cursor is None:
            break
    scopes = result_json(await session.call_tool("list_document_scopes", {}))
    workflows = []
    revisions = set()
    cursor = None
    while True:
        args = {"kind": "workflow", "limit": 2}
        if cursor:
            args["cursor"] = cursor
        page = result_json(await session.call_tool("list_tool_guides", args))
        revisions.add(page["catalog_revision"])
        workflows.extend(page["guides"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    for entry in workflows:
        cursor = None
        while True:
            args = {"guide_id": entry["guide_id"], "max_chars": 256}
            if cursor:
                args["cursor"] = cursor
            guide = result_json(await session.call_tool("get_tool_guide", args))
            revisions.add(guide["catalog_revision"])
            if not all(name in tools for name in guide["tool_names"]):
                raise ValueError("guide_tool_missing")
            cursor = guide["next_cursor"]
            if cursor is None:
                break
    if len(revisions) != 1:
        raise ValueError("guide_catalog_changed")
    return {
        "tool_count": len(tools),
        "workflow_ids": [g["guide_id"] for g in workflows],
        "catalog_revision": revisions.pop(),
        "actions": scopes["actions"],
        "scopes": scopes["scopes"],
    }


async def probe(connection: Connection) -> dict:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    token = os.environ.get(connection.mcp_token_env)
    if not token or "\r" in token or "\n" in token:
        raise ValueError("mcp_token_environment_missing_or_invalid")
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=35) as http:
        async with streamable_http_client(connection.gateway_url + "/mcp/", http_client=http) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await inspect_session(session)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", type=Path, required=True)
    args = parser.parse_args()
    try:
        connection = Connection.model_validate_json(args.connection.read_text())
        report = asyncio.run(probe(connection))
    except Exception:
        # SDK/network exceptions can include request headers or private settings.
        parser.exit(
            1,
            "MCP probe failed; check configuration, token environment, service health and guides.\n",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
