from __future__ import annotations

import asyncio
import json
import shutil
import socket
import threading
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from docling_desk.viewer.gateway import create_gateway
from docling_desk.viewer.tool_contract import document_arguments
from docling_desk.viewer.tool_guides import GUIDE_ROOT, ToolGuides


@pytest.fixture
def settings(tmp_path):
    config = {
        "api_url": "http://127.0.0.1:1",
        "public_url": "http://localhost",
        "api_token": "a" * 40,
        "signing_key": "k" * 40,
        "connector_token": "c" * 40,
        "client_id": "synthetic",
        "issuer": "synthetic",
        "kid": "v1",
        "users": {
            "reader": {"scopes": [{"collection_id": "documents"}], "mcp_token": "r" * 40},
            "writer": {
                "scopes": [{"collection_id": "documents"}],
                "mcp_token": "w" * 40,
                "actions": ["read", "write"],
            },
            "no-read": {
                "scopes": [{"collection_id": "documents"}],
                "mcp_token": "n" * 40,
                "actions": ["write"],
            },
        },
    }
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(config))
    return path, config


def auth(token="r"):
    return {"Authorization": "Bearer " + token * 40}


@pytest.fixture
def http(settings):
    path, config = settings
    calls = []

    def offline(request):
        calls.append(request.url.path)
        raise httpx.ConnectError("processor/API unavailable")

    app = create_gateway(path, httpx.MockTransport(offline))
    with TestClient(app) as client:
        yield client, app.state.gateway, path, config, calls


def test_http_host_and_offline_guides_share_content(http):
    client, service, _, _, calls = http
    response = client.get("/api/v1/tool-guides/overview", headers=auth())
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert "独立ナレッジAPI" in result["sections"][0]["body"]
    assert not calls
    host = client.post(
        "/host/tools/get_tool_guide",
        headers=auth("c"),
        json={"user_id": "reader", "arguments": {"guide_id": "overview"}},
    )
    assert host.status_code == 200
    assert host.json() == result
    writer = client.get("/api/v1/tool-guides/overview", headers=auth("w"))
    assert writer.json() == result
    assert service.guides().revision == result["catalog_revision"]


def test_authentication_revocation_and_read_permission(http):
    client, _, path, config, _ = http
    assert client.get("/api/v1/tool-guides").status_code == 401
    assert client.get("/api/v1/tool-guides", headers=auth("x")).status_code == 401
    assert client.get("/api/v1/tool-guides", headers=auth("n")).status_code == 403
    # Reading write instructions does not authorize the described write operation.
    assert (
        client.get("/api/v1/tool-guides/tool.commit_document_draft", headers=auth()).status_code
        == 200
    )
    write = client.post(
        "/host/tools/commit_document_draft",
        headers=auth("c"),
        json={
            "user_id": "reader",
            "arguments": {
                "draft_id": "11111111-1111-4111-8111-111111111111",
                "etag": "draft-1",
                "source_etag": None,
                "idempotency_key": "read-only-test",
            },
        },
    )
    assert write.status_code == 403
    config["users"]["reader"]["mcp_token"] = "z" * 40
    path.write_text(json.dumps(config))
    assert client.get("/api/v1/tool-guides", headers=auth()).status_code == 401


def test_http_rechecks_token_owner_before_return(http, monkeypatch):
    client, service, path, config, _ = http
    original = service.tool

    async def revoked(*args):
        result = await original(*args)
        config["users"]["reader"]["mcp_token"] = "z" * 40
        config["users"]["writer"]["mcp_token"] = "r" * 40
        path.write_text(json.dumps(config))
        return result

    monkeypatch.setattr(service, "tool", revoked)
    assert client.get("/api/v1/tool-guides/overview", headers=auth()).status_code == 401


def test_list_filters_and_cursor_binding(http):
    client, _, _, _, _ = http
    url = "/api/v1/tool-guides"
    params = {"kind": "tool", "limit": 3}
    seen = []
    while True:
        result = client.get(url, params=params, headers=auth()).json()
        seen.extend(g["guide_id"] for g in result["guides"])
        if not result["next_cursor"]:
            break
        params["cursor"] = result["next_cursor"]
    assert len(seen) == len(set(seen)) == 36
    first = client.get(url, params={"limit": 1}, headers=auth()).json()
    assert first["guides"][0]["guide_id"] == "overview"
    assert (
        client.get(
            url, params={"kind": "tool", "cursor": first["next_cursor"]}, headers=auth()
        ).status_code
        == 422
    )
    filtered = client.get(url, params={"tool_name": "search_documents"}, headers=auth()).json()
    assert filtered["guides"] and all(
        "search_documents" in e["tool_names"] for e in filtered["guides"]
    )


def test_partial_guides_reassemble_without_losing_unicode_or_examples(http):
    client, _, _, _, _ = http
    url = "/api/v1/tool-guides/tool.edit_document_draft"
    complete = client.get(url, headers=auth()).json()
    bodies = {s["id"]: "" for s in complete["sections"]}
    params = {"max_chars": 256}
    while True:
        part = client.get(url, params=params, headers=auth()).json()
        assert sum(len(s["body"]) for s in part["sections"]) <= 256
        for s in part["sections"]:
            assert s["start"] == len(bodies[s["id"]])
            bodies[s["id"]] += s["body"]
        if not part["next_cursor"]:
            break
        params["cursor"] = part["next_cursor"]
    assert bodies == {s["id"]: s["body"] for s in complete["sections"]}
    schemas = client.get(url, params={"section": "schemas"}, headers=auth()).json()
    assert schemas["schemas"] == complete["schemas"]
    assert schemas["sections"][0]["body"] == ""


@pytest.mark.parametrize(
    "path,params,status",
    [
        ("unknown", {}, 404),
        ("overview", {"section": "unknown"}, 404),
        ("overview", {"max_chars": 0}, 422),
        ("overview", {"max_chars": "bad"}, 422),
        ("overview", {"cursor": "not-a-cursor"}, 422),
        ("overview", {"user_id": "writer"}, 422),
    ],
)
def test_invalid_inputs(http, path, params, status):
    response = http[0].get("/api/v1/tool-guides/" + path, params=params, headers=auth())
    assert response.status_code == status
    if status == 422 and "cursor" not in params:
        assert response.json()["detail"] == "invalid_tool_guide_input"


def test_broken_guides_preserve_existing_gateway_liveness(settings, tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import docling_desk.viewer.tool_guides as module

    def missing():
        raise OSError("missing packaged guide")

    monkeypatch.setattr(module, "GUIDE_ROOT", tmp_path / "missing")
    monkeypatch.setattr(module, "ToolGuides", missing)
    app = create_gateway(settings[0], mcp_enabled=True)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/api/v1/tool-guides", headers=auth()).status_code == 503


def test_contract_check_rejects_edited_examples_and_unknown_references(tmp_path):
    from docling_desk.operations.tool_guides import validate_examples

    root = tmp_path / "guides"
    shutil.copytree(GUIDE_ROOT, root)
    example = root / "get_tool_guide-example.md"
    example.write_text(example.read_text().replace('"overview"', '"different-guide"'))
    with pytest.raises(ValueError, match="examples differ"):
        validate_examples(ToolGuides(root))
    manifest = root / "manifest.json"
    value = json.loads(manifest.read_text())
    value["guides"][0]["related_guides"].append("unknown.guide")
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Unknown related guide"):
        ToolGuides(root)


def test_catalog_changes_invalidate_old_cursor_and_missing_resources_fail(tmp_path):
    first = ToolGuides()
    payload = {"guide_id": "overview", "max_chars": 256}
    cursor = first.invoke("get_tool_guide", payload)["next_cursor"]
    root = tmp_path / "guides"
    shutil.copytree(GUIDE_ROOT, root)
    path = root / "overview.md"
    path.write_text(path.read_text() + "\n変更😀\n")
    second = ToolGuides(root)
    assert first.revision != second.revision
    with pytest.raises(HTTPException) as error:
        second.invoke("get_tool_guide", {**payload, "cursor": cursor})
    assert error.value.status_code == 409
    path.unlink()
    with pytest.raises(OSError):
        ToolGuides(root)


def test_published_contracts_and_examples_are_current():
    pytest.importorskip("mcp")
    from docling_desk.operations.tool_guides import check

    result = asyncio.run(check())
    assert result["tools"] == result["examples"] == 36
    assert result["guides"] == 42
    assert document_arguments("get_document_draft")["required"] == ["draft_id"]
    assert set(document_arguments("list_draft_attachments")["properties"]) == {"draft_id"}


def test_unavailable_or_mismatched_guides_fail_closed(http):
    client, service, _, _, _ = http
    catalog = service.guides()
    with pytest.raises(ValueError, match="contracts differ"):
        catalog.check_tools([])
    service.guide_contract_error = True
    assert client.get("/api/v1/tool-guides", headers=auth()).status_code == 503


def test_live_mcp_transport_discovery_guide_schema_errors_and_revocation(settings):
    pytest.importorskip("mcp")
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    path, config = settings
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        address = f"http://127.0.0.1:{sock.getsockname()[1]}"
        config["public_url"] = address
        path.write_text(json.dumps(config))
        app = create_gateway(path, mcp_enabled=True)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started

            async def run():
                async with streamablehttp_client(address + "/mcp/", headers=auth()) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        tools = (await client.list_tools()).tools
                        assert {t.name for t in tools} == set(app.state.gateway.guides().tools)
                        assert all("get_tool_guide" in t.description for t in tools)
                        result = await client.call_tool("get_tool_guide", {"guide_id": "overview"})
                        assert not result.isError
                        async with httpx.AsyncClient() as http:
                            expected = await http.get(
                                address + "/api/v1/tool-guides/overview", headers=auth()
                            )
                        assert result.structuredContent == expected.json()
                        listing = await client.call_tool(
                            "list_tool_guides", {"kind": "tool", "limit": 2}
                        )
                        assert not listing.isError and len(listing.structuredContent["guides"]) == 2
                        assert (
                            await client.call_tool("get_tool_guide", {"guide_id": "unknown"})
                        ).isError
                        assert (
                            await client.call_tool(
                                "get_tool_guide", {"guide_id": "overview", "max_chars": 0}
                            )
                        ).isError
                        assert (await client.call_tool("unknown_tool", {})).isError
                        config["users"]["reader"]["mcp_token"] = "z" * 40
                        path.write_text(json.dumps(config))
                        async with httpx.AsyncClient() as http:
                            revoked = await http.post(address + "/mcp/", headers=auth(), json={})
                        assert revoked.status_code == 401

            asyncio.run(run())
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("search_documents", {"query": "private", "scope": {"collection_ids": ["documents"]}}),
        (
            "get_document_context",
            {"retrieval_id": "11111111-1111-4111-8111-111111111111", "references": []},
        ),
        (
            "request_document_view",
            {
                "source_id": "11111111-1111-4111-8111-111111111111",
                "source_revision": "11111111-1111-4111-8111-111111111111",
                "evidence_revision": "11111111-1111-4111-8111-111111111111",
            },
        ),
        ("connect_document_view", {"code": "12345678"}),
        ("connect_document_editor", {"code": "12345678"}),
        ("list_document_scopes", {}),
    ],
)
def test_host_reads_require_local_read_policy_before_api(http, monkeypatch, name, arguments):
    client, service, _, _, _ = http
    calls = []

    async def api(*a, **kw):
        calls.append(True)
        return {"kind": "document", "units": 1, "job": {"filename": "private.md", "id": "private"}}

    monkeypatch.setattr(service, "api", api)
    response = client.post(
        "/host/tools/" + name,
        headers={"Authorization": "Bearer " + "c" * 40},
        json={"user_id": "no-read", "arguments": arguments},
    )
    assert response.status_code == 403
    assert calls == []


def test_listing_schema_uses_actual_api_limits_and_revision_query():
    from jsonschema import Draft202012Validator, ValidationError

    for name in ("list_documents", "list_document_drafts"):
        schema = document_arguments(name)
        Draft202012Validator(schema).validate({"offset": 1000000, "limit": 50})
        for arguments in ({"limit": 0}, {"limit": 51}, {"offset": 1000001}):
            with pytest.raises(ValidationError):
                Draft202012Validator(schema).validate(arguments)
    assert document_arguments("get_document")["properties"]["revision"]["format"] == "uuid"


def test_transfer_guide_embeds_the_json_metadata_input_schema():
    catalog = ToolGuides()
    schema = catalog.schemas(catalog.by_id["tool.import_document"])["import_document"]
    field = schema["transfer_http"]["paths"]["/api/v1/document-transfers"]["post"]["requestBody"][
        "content"
    ]["multipart/form-data"]["schema"]["properties"]["metadata"]
    assert field["contentMediaType"] == "application/json"
    assert field["contentSchema"]["required"] == ["title", "collection_id"]


@pytest.mark.parametrize(
    "name,arguments,remaining",
    [
        (
            "search_documents",
            {"query": "private", "scope": {"collection_ids": ["documents"]}},
            ["write"],
        ),
        (
            "create_markdown_document",
            {
                "body": "private",
                "metadata": {"title": "Synthetic", "collection_id": "documents"},
                "idempotency_key": "revoked-write",
            },
            ["read"],
        ),
    ],
)
def test_policy_revoked_before_api_snapshot_blocks_read_and_write(
    http, monkeypatch, name, arguments, remaining
):
    client, service, path, config, _ = http
    sent = []
    service.transport = httpx.MockTransport(
        lambda request: (
            sent.append(request) or httpx.Response(200, json={"private": "must not return"})
        )
    )
    original = service.api

    async def revoke(*args, **kwargs):
        config["users"]["writer"]["actions"] = remaining
        path.write_text(json.dumps(config))
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "api", revoke)
    response = client.post(
        "/host/tools/" + name,
        headers={"Authorization": "Bearer " + "c" * 40},
        json={"user_id": "writer", "arguments": arguments},
    )
    assert response.status_code == 403
    assert sent == []
