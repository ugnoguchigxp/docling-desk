from __future__ import annotations

import base64
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from docling_desk.viewer.gateway import create_gateway


@pytest.fixture
def editor(tmp_path):
    draft_id, source_id = str(uuid4()), str(uuid4())
    config = {
        "api_url": "http://api.test",
        "public_url": "http://gateway.test",
        "client_id": "editor",
        "issuer": "editor",
        "kid": "v1",
        "api_token": "a" * 40,
        "signing_key": "k" * 40,
        "connector_token": "c" * 40,
        "users": {
            "writer": {"actions": ["read", "write"], "scopes": [{"collection_id": "docs"}]},
            "reader": {"scopes": [{"collection_id": "docs"}]},
        },
    }
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(config))
    calls = []
    revoked = {"value": False}

    def api(request):
        claims = json.loads(
            base64.urlsafe_b64decode(request.headers["x-knowledge-actor"].split(".")[1] + "==")
        )
        calls.append((request, claims))
        if revoked["value"]:
            return httpx.Response(404, json={"error": {"code": "source_not_found"}})
        if request.url.path == "/api/v1/scopes":
            return httpx.Response(
                200, json={"actions": ["read", "write"], "scopes": claims["scopes"]}
            )
        if request.url.path.startswith("/api/v1/drafts"):
            return httpx.Response(
                200,
                json={
                    "draft_id": draft_id,
                    "source_id": source_id,
                    "body": "本文",
                    "metadata": {"title": "文書", "collection_id": "docs"},
                },
            )
        return httpx.Response(
            200, json={"source_id": source_id, "metadata": {"collection_id": "docs"}}
        )

    app = create_gateway(path, httpx.MockTransport(api))
    with TestClient(app) as client:
        yield client, app.state.gateway, config, path, draft_id, calls, revoked


def host(client, name, arguments, user="writer"):
    return client.post(
        "/host/tools/" + name,
        headers={"authorization": "Bearer " + "c" * 40},
        json={"user_id": user, "arguments": arguments},
    )


def connect(client, draft_id):
    challenge = client.post("/editor/challenges", json={"draft_id": draft_id}).json()
    assert host(client, "connect_document_editor", {"code": challenge["code"]}).status_code == 200
    return client.get("/editor/challenges/" + challenge["challenge"]).json()["session"]


def test_reader_cannot_approve_editor_and_unapproved_session_cannot_read(editor):
    client, _, _, _, draft_id, _, _ = editor
    challenge = client.post("/editor/challenges", json={"draft_id": draft_id}).json()
    assert (
        host(client, "connect_document_editor", {"code": challenge["code"]}, "reader").status_code
        == 403
    )
    assert client.get("/editor/session/" + "x" * 43).status_code == 401
    assert client.get("/editor/challenges/" + challenge["challenge"]).json()["status"] == "pending"


def test_editor_calls_public_api_with_trusted_actor_etag_and_key(editor):
    client, _, _, _, draft_id, calls, _ = editor
    token = connect(client, draft_id)
    response = client.post(
        f"/editor/session/{token}/tools",
        json={
            "name": "update_document_draft",
            "arguments": {
                "draft_id": draft_id,
                "body": "AIと人",
                "etag": '"draft:1"',
                "idempotency_key": "one",
            },
        },
    )
    assert response.status_code == 200
    request, claims = calls[-1]
    assert request.method == "PUT"
    assert claims["sub"] == "writer"
    assert claims["scopes"] == [{"collection_id": "docs"}]
    assert request.headers["if-match"] == '"draft:1"'
    assert request.headers["idempotency-key"] == "one"
    assert json.loads(request.content) == {"body": "AIと人"}
    assert "api_token" not in response.text and "signing_key" not in response.text


def test_other_draft_and_arbitrary_paths_are_denied(editor):
    client, _, _, _, draft_id, _, _ = editor
    token = connect(client, draft_id)
    for name in ("get_document_status", "reextract_document", "reindex_document"):
        assert (
            client.post(
                f"/editor/session/{token}/tools", json={"name": name, "arguments": {}}
            ).status_code
            == 403
        )
    assert (
        client.post(
            f"/editor/session/{token}/tools",
            json={"name": "get_document_draft", "arguments": {"draft_id": str(uuid4())}},
        ).status_code
        == 403
    )
    assert host(client, "get_document", {"source_id": "../../private"}).status_code == 422
    assert host(client, "shell", {"command": "anything"}).status_code == 404


def test_editor_revocation_and_process_restart_do_not_grant_access(editor):
    client, service, config, path, draft_id, _, revoked = editor
    token = connect(client, draft_id)
    revoked["value"] = True
    assert client.get(f"/editor/session/{token}").status_code == 404
    revoked["value"] = False
    config["users"]["writer"]["actions"] = ["read"]
    path.write_text(json.dumps(config))
    assert client.get(f"/editor/session/{token}").status_code == 401
    service.editor_sessions.clear()
    assert client.get(f"/editor/session/{token}").status_code == 401


def test_mcp_arguments_publish_the_http_contract(editor):
    import asyncio

    from docling_desk.viewer.mcp import create_mcp

    _, service, _, _, _, _, _ = editor
    server = create_mcp(service)
    tools = asyncio.run(server.list_tools())
    edit = next(tool for tool in tools if tool.name == "edit_document_draft")
    arguments = edit.inputSchema["properties"]["arguments"]
    assert set(arguments["required"]) >= {
        "draft_id",
        "etag",
        "idempotency_key",
        "base_sha256",
        "edits",
    }
    assert arguments["additionalProperties"] is False


def test_source_bound_session_cannot_write_a_draft_from_another_document(editor):
    client, _, _, _, draft_id, calls, _ = editor
    target_source = str(uuid4())
    challenge = client.post("/editor/challenges", json={"source_id": target_source}).json()
    assert host(client, "connect_document_editor", {"code": challenge["code"]}).status_code == 200
    token = client.get("/editor/challenges/" + challenge["challenge"]).json()["session"]
    before = len(calls)
    assert (
        client.post(
            f"/editor/session/{token}/tools",
            json={
                "name": "update_document_draft",
                "arguments": {
                    "draft_id": draft_id,
                    "body": "overwrite",
                    "etag": "old",
                    "idempotency_key": "one",
                },
            },
        ).status_code
        == 403
    )
    assert all(request.method != "PUT" for request, _ in calls[before:])
    assert (
        client.post(
            f"/editor/session/{token}/tools",
            json={
                "name": "create_document_draft",
                "arguments": {
                    "body": "other",
                    "metadata": {"title": "other", "collection_id": "docs"},
                    "idempotency_key": "two",
                },
            },
        ).status_code
        == 403
    )


def test_source_bound_session_does_not_list_other_documents(editor):
    client, _, _, _, _, calls, _ = editor
    challenge = client.post("/editor/challenges", json={"source_id": str(uuid4())}).json()
    assert host(client, "connect_document_editor", {"code": challenge["code"]}).status_code == 200
    token = client.get("/editor/challenges/" + challenge["challenge"]).json()["session"]
    before = len(calls)
    response = client.post(
        f"/editor/session/{token}/tools", json={"name": "list_documents", "arguments": {}}
    )
    assert response.status_code == 403
    assert len(calls) == before


def test_draft_bound_session_cannot_lookup_another_draft_in_same_source(editor, monkeypatch):
    import docling_desk.viewer.editor as module

    client, service, _, _, draft_id, _, _ = editor
    token = connect(client, draft_id)
    session = service.editor_sessions[token]

    async def lookup(*args, **kwargs):
        return {
            "result": {
                "draft_id": str(uuid4()),
                "source_id": session["source_id"],
                "body": "other draft",
            }
        }

    monkeypatch.setattr(module, "document_tool", lookup)
    response = client.post(
        f"/editor/session/{token}/tools",
        json={
            "name": "get_document_operation",
            "arguments": {"operation": "draft", "key": "other"},
        },
    )
    assert response.status_code == 403


def test_concurrent_creation_cannot_bind_one_editor_session_to_two_drafts(editor, monkeypatch):
    import asyncio

    import docling_desk.viewer.editor as module

    client, service, _, _, _, _, _ = editor
    challenge = client.post("/editor/challenges", json={"scope": {"collection_id": "docs"}}).json()
    assert host(client, "connect_document_editor", {"code": challenge["code"]}).status_code == 200
    token = client.get("/editor/challenges/" + challenge["challenge"]).json()["session"]
    mutated = []

    async def operation(gateway, name, user, arguments, **kwargs):
        await asyncio.sleep(0.01)
        if name == "get_document_operation":
            from fastapi import HTTPException

            raise HTTPException(404, "operation_unknown")
        assert name == "create_document_draft"
        selected = str(uuid4())
        mutated.append(selected)
        return {"draft_id": selected, "source_id": selected}

    monkeypatch.setattr(module, "document_tool", operation)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app), base_url="http://testserver"
        ) as browser:
            return await asyncio.gather(
                *[
                    browser.post(
                        f"/editor/session/{token}/tools",
                        json={
                            "name": "create_document_draft",
                            "arguments": {
                                "body": "text",
                                "metadata": {"collection_id": "docs", "title": "test"},
                                "idempotency_key": str(uuid4()),
                            },
                        },
                    )
                    for _ in range(2)
                ]
            )

    responses = asyncio.run(run())
    assert sorted(r.status_code for r in responses) in ([200, 403], [200, 404])
    assert len(mutated) == 1


def test_new_draft_creation_can_be_replayed_after_session_binds(editor, monkeypatch):
    import docling_desk.viewer.editor as module

    client, _, _, _, draft_id, _, _ = editor
    source_id = str(uuid4())
    challenge = client.post("/editor/challenges", json={"scope": {"collection_id": "docs"}}).json()
    assert host(client, "connect_document_editor", {"code": challenge["code"]}).status_code == 200
    token = client.get("/editor/challenges/" + challenge["challenge"]).json()["session"]
    result = {"draft_id": draft_id, "source_id": source_id, "body": "new"}

    async def operation(gateway, name, user, arguments, **kwargs):
        return {"result": result} if name == "get_document_operation" else result

    monkeypatch.setattr(module, "document_tool", operation)
    call = {
        "name": "create_document_draft",
        "arguments": {
            "body": "new",
            "metadata": {"collection_id": "docs", "title": "new"},
            "idempotency_key": "lost-create",
        },
    }
    first = client.post(f"/editor/session/{token}/tools", json=call)
    replay = client.post(f"/editor/session/{token}/tools", json=call)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()


def test_viewer_poll_cannot_return_an_editor_session(editor):
    client, _, _, _, draft_id, _, _ = editor
    challenge = client.post("/editor/challenges", json={"draft_id": draft_id}).json()
    assert host(client, "connect_document_editor", {"code": challenge["code"]}).status_code == 200
    assert client.get("/viewer/challenges/" + challenge["challenge"]).status_code == 410
    assert client.get("/editor/challenges/" + challenge["challenge"]).json()["session"]
