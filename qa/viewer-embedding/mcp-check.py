"""Real Streamable HTTP client: tools, evidence, refs, and scoped pairing."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[2]
base = ROOT / ".cache/viewer-embedding"


async def main():
    config = json.loads((base / "config.json").read_text())
    refs = json.loads((base / "refs.json").read_text())
    users = list(config["users"])
    evidence = {}
    for index, user in enumerate(users):
        async with streamablehttp_client(
            config["public_url"] + "/mcp/",
            headers={"authorization": "Bearer " + config["users"][user]["mcp_token"]},
        ) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                listed = await client.list_tools()
                evidence["tools"] = [t.name for t in listed.tools]
                if index == 0:
                    search = await client.call_tool(
                        "search_documents",
                        {
                            "query": "Synthetic",
                            "scope": {"collection_ids": ["synthetic"]},
                            "mode": "text",
                        },
                    )
                    assert not search.isError, search.content
                    value = json.loads(search.content[0].text)
                    assert value["results"]
                    hit = value["results"][0]
                    ref = {
                        k: hit[k]
                        for k in ["source_id", "source_revision", "evidence_revision", "context_id"]
                    }
                    context = await client.call_tool(
                        "get_document_context",
                        {"retrieval_id": value["retrieval_id"], "references": [ref]},
                    )
                    assert not context.isError, context.content
                    view = await client.call_tool("request_document_view", refs["pdf"])
                    assert not view.isError, view.content
                    stale = await client.call_tool(
                        "request_document_view", {**refs["pdf"], "source_revision": str(uuid4())}
                    )
                    assert stale.isError
                    invalid = await client.call_tool(
                        "request_document_view",
                        {**refs["pdf"], "location": {"kind": "page", "number": 999}},
                    )
                    assert invalid.isError
                    challenge = httpx.post(
                        config["public_url"] + "/viewer/challenges", json=refs["pdf"]
                    ).json()
                    approval = await client.call_tool(
                        "connect_document_view", {"code": challenge["code"]}
                    )
                    assert not approval.isError, approval.content
                    poll = httpx.get(
                        config["public_url"] + "/viewer/challenges/" + challenge["challenge"]
                    ).json()
                    assert poll["status"] == "approved"
                    assert (
                        httpx.get(
                            config["public_url"]
                            + "/viewer/session/"
                            + poll["session"]
                            + "/manifest"
                        ).status_code
                        == 200
                    )
                    evidence.update(
                        search=True,
                        context=True,
                        view=True,
                        stale_rejected=True,
                        location_rejected=True,
                        pairing=True,
                    )
                else:
                    result = await client.call_tool("request_document_view", refs["pdf"])
                    assert result.isError
                    evidence["second_user_denied"] = True
    assert httpx.post(config["public_url"] + "/mcp/", json={}).status_code == 401
    evidence["unauthenticated_denied"] = True
    (ROOT / "qa/viewer-embedding/mcp-results.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    print("Real MCP client checks passed")


asyncio.run(main())
