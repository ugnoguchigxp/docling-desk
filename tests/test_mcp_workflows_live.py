"""Guided workflow exercise used by the isolated real-MCP acceptance harness."""

from __future__ import annotations

import asyncio
import json
import time

from jsonschema import Draft202012Validator, FormatChecker

from docling_desk.operations.mcp_check import inspect_session, result_json


async def exercise_workflows(mcp, host, gateway_url, headers, upload):
    inspected = await inspect_session(mcp)
    assert inspected["tool_count"] == 36 and len(inspected["workflow_ids"]) == 5
    assert set(inspected["actions"]) == {"read", "write"}
    guide_cache = {}
    called = set()

    async def guided(name, arguments, error=None):
        if name not in guide_cache:
            result = await mcp.call_tool("get_tool_guide", {"guide_id": "tool." + name})
            guide = result_json(result)
            assert guide["catalog_revision"] == inspected["catalog_revision"]
            assert guide["next_cursor"] is None
            guide_cache[name] = guide["schemas"][name]["mcp"]["inputSchema"]
        schema = guide_cache[name]
        # Use the actual discovery contract to determine the transport envelope.
        payload = {"arguments": arguments} if "arguments" in schema["properties"] else arguments
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
        result = await mcp.call_tool(name, payload)
        called.add(name)
        if error:
            assert result.isError and error in str(result.content), result.content
            return None
        return result_json(result)

    async def wait(job):
        deadline = time.monotonic() + 30
        while True:
            current = await guided("get_document_job", {"job_id": job})
            if current["state"] not in {"queued", "running"}:
                assert current["state"] == "completed", current
                return
            assert time.monotonic() < deadline, current
            await asyncio.sleep(0.05)

    for guide_id in inspected["workflow_ids"]:
        guide = result_json(await mcp.call_tool("get_tool_guide", {"guide_id": guide_id}))
        response = await host.get(gateway_url + "/api/v1/tool-guides/" + guide_id, headers=headers)
        assert response.status_code == 200
        assert response.json() == guide  # Same version, body and schema over HTTP/MCP.

    scopes = await guided("list_document_scopes", {})
    collection = scopes["scopes"][0]["collection_id"]
    original = b"# WorkflowInventory\n\nUniqueInventory original.\n\n| Name | Value |\n| --- | --- |\n| Synthetic | 42 |\n"
    transfer = await upload(original, {"ocr_provider": "disabled"})
    saved = await guided(
        "import_document", {"transfer_id": transfer, "idempotency_key": "p5-import"}
    )
    await wait(saved["job_id"])
    status = await guided("get_document_status", {"source_id": saved["source_id"]})
    assert status["fts_ready"] and not status["semantic_ready"]
    search = await guided(
        "search_documents",
        {
            "query": "UniqueInventory",
            "scope": {"collection_ids": [collection]},
            "mode": "text",
            "limit": 10,
        },
    )
    hit = next(h for h in search["results"] if h["source_id"] == saved["source_id"])
    ref = {k: hit[k] for k in ("source_id", "source_revision", "evidence_revision")}
    context = await guided(
        "get_document_context",
        {
            "retrieval_id": search["retrieval_id"],
            "references": [{**ref, "context_id": hit["context_id"]}],
            "max_chars": 80000,
        },
    )
    assert "UniqueInventory original" in json.dumps(context)
    view = await guided("request_document_view", ref)
    assert view["status"] == "connection_required" and "token" not in view["embed_url"]
    # The isolated browser supplies a real device code, as the workflow requires.
    challenge = await host.post(gateway_url + "/viewer/challenges", json=view["reference"])
    assert challenge.status_code == 200, challenge.text
    code = challenge.json()
    await guided("connect_document_view", {"code": code["code"]})
    poll = await host.get(gateway_url + "/viewer/challenges/" + code["challenge"])
    token = poll.json()["session"]
    rendered = await host.get(gateway_url + "/viewer/session/" + token + "/view/document")
    assert rendered.status_code == 200 and "UniqueInventory" in rendered.text
    outline = await guided("get_document_outline", ref)
    heading = next(e for e in outline["entries"] if e["heading"] == "WorkflowInventory")
    section = await guided(
        "get_document_section", {**ref, "selector": {"kind": "section", "ref": heading["ref"]}}
    )
    assert "UniqueInventory" in json.dumps(section)
    tables = await guided("list_document_tables", ref)
    table = await guided(
        "get_document_table",
        {**ref, "table_ref": tables["tables"][0]["table_ref"], "format": "csv"},
    )
    assert "42" in table["csv"]

    document = await guided("get_document", {"source_id": saved["source_id"]})
    draft = await guided(
        "create_document_draft",
        {
            "source_id": document["source_id"],
            "source_revision": document["source_revision"],
            "source_etag": document["etag"],
            "idempotency_key": "p5-draft",
        },
    )
    draft = await guided("get_document_draft", {"draft_id": draft["draft_id"]})
    edit_args = {
        "draft_id": draft["draft_id"],
        "etag": draft["etag"],
        "base_sha256": draft["sha256"],
        "edits": [
            {"expected": "UniqueInventory original.", "replacement": "UniqueInventory revised."}
        ],
        "idempotency_key": "p5-edit",
    }
    edited = await guided("edit_document_draft", edit_args)
    assert "revised" in edited["body"]
    await guided(
        "edit_document_draft",
        {**edit_args, "idempotency_key": "p5-stale-edit"},
        error="precondition_failed",
    )
    current = await guided("get_document_draft", {"draft_id": draft["draft_id"]})
    assert current["etag"] == edited["etag"] and current["body"] == edited["body"]
    commit_args = {
        "draft_id": draft["draft_id"],
        "etag": edited["etag"],
        "source_etag": current["base_etag"],
        "idempotency_key": "p5-commit",
    }
    # Execute the save but discard the response: the client knows only its original key.
    await guided("commit_document_draft", commit_args)
    recovered = await guided(
        "get_document_operation",
        {"operation": "commit", "target_id": draft["draft_id"], "key": "p5-commit"},
    )
    committed = recovered["result"]
    assert committed["source_revision"] != document["source_revision"]
    replay = await guided("commit_document_draft", commit_args)
    assert (
        replay["source_revision"] == committed["source_revision"]
        and replay["job_id"] == committed["job_id"]
    )
    await wait(committed["job_id"])
    latest = await guided("get_document", {"source_id": saved["source_id"]})
    assert "revised" in latest["body"] and latest["body"] == edited["body"]
    assert latest["sha256"] == committed["sha256"]
    listed = await guided("list_documents", {"offset": 0, "limit": 20})
    assert len(listed["documents"]) == 1
    new_search = await guided(
        "search_documents",
        {
            "query": "revised",
            "scope": {"collection_ids": [collection]},
            "mode": "text",
            "limit": 10,
        },
    )
    assert new_search["results"][0]["source_revision"] == committed["source_revision"]
    await guided("get_document_outline", ref, error="source_changed")
    print(
        json.dumps(
            {
                "guided_workflows": inspected["workflow_ids"],
                "guided_tool_calls": sorted(called),
                "lost_response_recovered": True,
                "etag_conflict_preserved": True,
            }
        )
    )
