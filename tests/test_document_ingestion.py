from __future__ import annotations

import base64
import hashlib
import json
from email.parser import BytesParser
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from docling_desk.viewer.gateway import create_gateway
from docling_desk.viewer.transfers import transfers


@pytest.fixture
def ingestion(tmp_path):
    config = {
        "api_url": "http://knowledge.test",
        "public_url": "http://localhost",
        "api_token": "a" * 40,
        "signing_key": "k" * 40,
        "connector_token": "c" * 40,
        "client_id": "synthetic",
        "issuer": "synthetic",
        "kid": "v1",
        "users": {
            "writer": {
                "scopes": [{"collection_id": "documents"}],
                "actions": ["read", "write"],
                "mcp_token": "w" * 40,
            },
            "other": {
                "scopes": [{"collection_id": "documents"}],
                "actions": ["read", "write"],
                "mcp_token": "o" * 40,
            },
            "reader": {"scopes": [{"collection_id": "documents"}], "mcp_token": "r" * 40},
        },
    }
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(config))
    state = {"sources": {}, "keys": {}, "calls": [], "lost": False, "deny": False}

    def api(request):
        state["calls"].append(request)
        claims = json.loads(
            base64.urlsafe_b64decode(request.headers["x-knowledge-actor"].split(".")[1] + "==")
        )
        assert claims["sub"] in config["users"]
        assert claims["scopes"] == [{"collection_id": "documents"}]
        route = request.url.path
        if route == "/api/v1/scopes":
            return httpx.Response(
                200,
                json={
                    "actions": ["read"] if state["deny"] else ["read", "write"],
                    "scopes": claims["scopes"],
                },
            )
        if request.method == "GET" and route.startswith("/api/v1/sources/"):
            source = state["sources"].get(route.split("/")[-1])
            if not source:
                return httpx.Response(404, json={"error": {"code": "source_not_found"}})
            return httpx.Response(200, json=source, headers={"ETag": source["etag"]})
        if request.method == "POST" and (
            route == "/api/v1/sources" or route.endswith("/revisions")
        ):
            message = BytesParser().parsebytes(
                ("Content-Type: " + request.headers["content-type"] + "\r\n\r\n").encode()
                + request.content
            )
            parts = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.get_payload()
            }
            metadata = json.loads(parts["metadata"])
            key = (claims["sub"], route, request.headers["idempotency-key"])
            signature = hashlib.sha256(parts["file"] + parts["metadata"]).hexdigest()
            if key in state["keys"]:
                before, result = state["keys"][key]
                return (
                    httpx.Response(202, json=result)
                    if before == signature
                    else httpx.Response(409, json={"error": {"code": "idempotency_conflict"}})
                )
            source_id = route.split("/")[-2] if route.endswith("/revisions") else str(uuid4())
            if (
                route.endswith("/revisions")
                and request.headers.get("if-match") != state["sources"][source_id]["etag"]
            ):
                return httpx.Response(412, json={"error": {"code": "source_changed"}})
            result = {
                "source_id": source_id,
                "source_revision": str(uuid4()),
                "job_id": str(uuid4()),
            }
            state["sources"][source_id] = {
                **result,
                "metadata": metadata,
                "state": "queued",
                "fts_ready": False,
                "semantic_ready": False,
                "etag": '"' + result["source_revision"] + '"',
            }
            state["keys"][key] = (signature, result)
            if state["lost"]:
                state["lost"] = False
                raise httpx.ReadError("response lost after registration")
            return httpx.Response(202, json=result)
        if route.endswith(("/reindex", "/reextract")):
            source_id = route.split("/")[-2]
            if request.headers.get("if-match") != state["sources"][source_id]["etag"]:
                return httpx.Response(412, json={"error": {"code": "source_changed"}})
            return httpx.Response(202, json={"source_id": source_id, "job_id": str(uuid4())})
        raise AssertionError(route)

    transport = httpx.MockTransport(api)
    app = create_gateway(path, transport)
    with TestClient(app) as client:
        yield client, app.state.gateway, state, path, config, transport


META = {
    "title": "合成資料",
    "collection_id": "documents",
    "source_kind": "document",
    "language": "ja",
}


def stage(client, body=b"synthetic file", extra=None, user="w", metadata=None):
    return client.post(
        "/api/v1/document-transfers",
        headers={"Authorization": "Bearer " + user * 40},
        files={"file": ("合成資料.txt", body)},
        data={"metadata": json.dumps(metadata or META), **(extra or {})},
    )


def tool(client, name, args, user="writer"):
    return client.post(
        "/host/tools/" + name,
        headers={"Authorization": "Bearer " + "c" * 40},
        json={"user_id": user, "arguments": args},
    )


def test_import_large_binary_status_and_durable_replay(ingestion):
    client, service, state, path, _, transport = ingestion
    body = b"x" * (3 * 1024 * 1024)
    response = stage(client, body)
    assert response.status_code == 200, response.text
    transfer = response.json()
    assert transfer["sha256"] == hashlib.sha256(body).hexdigest()
    args = {"transfer_id": transfer["transfer_id"], "idempotency_key": "first-import"}
    result = tool(client, "import_document", args)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert not transfers(service).path(transfer["transfer_id"], ".bin").exists()
    assert tool(client, "import_document", args).json() == saved
    with TestClient(create_gateway(path, transport)) as restarted:
        assert tool(restarted, "import_document", args).json() == saved
    assert len(state["keys"]) == 1
    status = tool(client, "get_document_status", {"source_id": saved["source_id"]}).json()
    assert status["source_etag"] == state["sources"][saved["source_id"]]["etag"]
    assert not status["fts_ready"] and not status["semantic_ready"]
    assert "api_token" not in result.text


def test_response_loss_recovers_same_registration_after_restart(ingestion):
    client, _, state, path, _, transport = ingestion
    transfer = stage(client).json()
    args = {"transfer_id": transfer["transfer_id"], "idempotency_key": "lost"}
    state["lost"] = True
    assert tool(client, "import_document", args).status_code == 503
    expected = next(iter(state["keys"].values()))[1]
    with TestClient(create_gateway(path, transport)) as restarted:
        replay = tool(restarted, "import_document", args)
        assert replay.status_code == 200, replay.text
        assert replay.json() == expected
    assert len(state["sources"]) == 1


def test_replacement_keeps_id_checks_base_and_returns_new_revision(ingestion):
    client, _, state, _, _, _ = ingestion
    first = tool(
        client,
        "import_document",
        {"transfer_id": stage(client).json()["transfer_id"], "idempotency_key": "original"},
    ).json()
    source_id = first["source_id"]
    etag = state["sources"][source_id]["etag"]
    transfer = stage(client, b"updated", {"source_id": source_id, "etag": etag}).json()
    args = {
        "source_id": source_id,
        "etag": etag,
        "transfer_id": transfer["transfer_id"],
        "idempotency_key": "replacement",
    }
    assert tool(client, "replace_document_source", {**args, "etag": "wrong"}).status_code == 409
    assert (
        tool(
            client,
            "import_document",
            {"transfer_id": transfer["transfer_id"], "idempotency_key": "wrong-operation"},
        ).status_code
        == 409
    )
    replaced = tool(client, "replace_document_source", args)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["source_id"] == source_id
    assert replaced.json()["source_revision"] != first["source_revision"]
    assert tool(client, "replace_document_source", args).json() == replaced.json()
    stale = stage(client, b"another", {"source_id": source_id, "etag": etag}).json()
    assert (
        tool(
            client,
            "replace_document_source",
            {**args, "transfer_id": stale["transfer_id"], "idempotency_key": "stale"},
        ).status_code
        == 412
    )


def test_permissions_scope_binding_keys_and_arbitrary_paths(ingestion):
    client, _, state, _, _, _ = ingestion
    assert stage(client, user="r").status_code == 403
    assert stage(client, metadata={**META, "collection_id": "private"}).status_code == 403
    transfer = stage(client).json()
    args = {"transfer_id": transfer["transfer_id"], "idempotency_key": "bound"}
    assert tool(client, "import_document", args, "other").status_code == 404
    assert tool(client, "import_document", args, "reader").status_code == 403
    assert tool(client, "import_document", {**args, "path": "/etc/passwd"}).status_code == 422
    assert tool(client, "import_document", args).status_code == 200
    assert (
        tool(client, "import_document", {**args, "idempotency_key": "different"}).status_code == 409
    )
    state["deny"] = True
    assert tool(client, "import_document", args).status_code == 403


def test_expiration_integrity_limits_and_deleted_receipts(ingestion, monkeypatch):
    import docling_desk.viewer.transfers as module

    client, service, state, _, _, _ = ingestion
    monkeypatch.setattr(module, "MAX_TRANSFERS", 1)
    first = stage(client).json()
    assert stage(client).status_code == 429
    store = transfers(service)
    _, fingerprint = service.actor("writer")
    record = store.load(first["transfer_id"], "writer", fingerprint)
    record["expires_at"] = 0
    store.save(record)
    assert (
        tool(
            client,
            "import_document",
            {"transfer_id": first["transfer_id"], "idempotency_key": "expired"},
        ).status_code
        == 410
    )
    assert not store.path(first["transfer_id"], ".bin").exists()
    second = stage(client).json()
    store.path(second["transfer_id"], ".bin").write_bytes(b"corrupt")
    args = {"transfer_id": second["transfer_id"], "idempotency_key": "integrity"}
    assert tool(client, "import_document", args).status_code == 409
    store.path(second["transfer_id"], ".bin").write_bytes(b"synthetic file")
    accepted = tool(client, "import_document", args).json()
    state["sources"].pop(accepted["source_id"])
    assert tool(client, "import_document", args).status_code == 404
    monkeypatch.setattr(module, "MAX_FILE", 8)
    assert stage(client, b"x" * 9).status_code == 413
    assert not list(store.root.glob("*.bin"))


def test_reextract_reindex_headers_ocr_and_202(ingestion):
    client, _, state, _, _, _ = ingestion
    saved = tool(
        client,
        "import_document",
        {"transfer_id": stage(client).json()["transfer_id"], "idempotency_key": "source"},
    ).json()
    args = {"source_id": saved["source_id"], "etag": state["sources"][saved["source_id"]]["etag"]}
    for name, body in (("reextract_document", {"ocr_provider": "local"}), ("reindex_document", {})):
        result = tool(client, name, {**args, **body})
        assert result.status_code == 200, result.text
        assert result.json()["job_id"]
        request = state["calls"][-1]
        assert request.headers["if-match"] == args["etag"]
        assert json.loads(request.content) == body
        assert "idempotency-key" not in request.headers
        assert tool(client, name, {**args, **body}, "reader").status_code == 403
        assert tool(client, name, {**args, **body, "etag": "stale"}).status_code == 412


def test_concurrent_calls_are_single_flight(ingestion, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    client, service, state, _, _, _ = ingestion
    transfer = stage(client).json()
    arguments = {"transfer_id": transfer["transfer_id"], "idempotency_key": "parallel"}
    original = service.api

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused(user, path, body, **kwargs):
            if path == "/api/v1/sources":
                entered.set()
                await release.wait()
            return await original(user, path, body, **kwargs)

        monkeypatch.setattr(service, "api", paused)
        first = asyncio.create_task(service.tool("import_document", "writer", arguments))
        await entered.wait()
        try:
            with pytest.raises(HTTPException) as error:
                await service.tool("import_document", "writer", arguments)
            assert error.value.status_code == 409
            assert error.value.detail == "document_transfer_busy"
        finally:
            release.set()
        assert (await first)["source_id"]
        assert len(state["keys"]) == 1

    asyncio.run(run())


def test_upload_revocation_malformed_forms_and_transfer_status(ingestion, monkeypatch):
    client, service, _, path, config, _ = ingestion
    assert client.post("/api/v1/document-transfers").status_code == 401
    assert (
        client.post(
            "/api/v1/document-transfers", headers={"Authorization": "Bearer " + "w" * 40}, json={}
        ).status_code
        == 415
    )
    assert stage(client, extra={"source_id": str(uuid4())}).status_code == 422
    assert stage(client, extra={"source_id": "", "etag": ""}).status_code == 422
    assert stage(client, extra={"etag": ""}).status_code == 422
    assert stage(client, extra={"ocr_provider": "invalid"}).status_code == 422
    transfer = stage(client).json()
    url = "/api/v1/document-transfers/" + transfer["transfer_id"]
    result = client.get(url, headers={"Authorization": "Bearer " + "w" * 40})
    assert result.status_code == 200 and result.json()["state"] == "staged"
    assert client.get(url, headers={"Authorization": "Bearer " + "o" * 40}).status_code == 404
    config["users"]["writer"]["mcp_token"] = "z" * 40
    path.write_text(json.dumps(config))
    assert client.get(url, headers={"Authorization": "Bearer " + "w" * 40}).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer " + "z" * 40}).status_code == 404


def test_transfer_directory_is_not_chmodded_to_fix_private_configuration(ingestion, tmp_path):
    client, _, _, path, config, _ = ingestion
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    config["transfer_dir"] = str(shared)
    path.write_text(json.dumps(config))
    with TestClient(create_gateway(path)) as isolated:
        response = stage(isolated)
        assert response.status_code == 503
        assert response.json()["detail"] == "document_transfer_directory_not_private"
    assert shared.stat().st_mode & 0o077


def test_transfer_accepts_metadata_defaults_like_public_api(ingestion):
    client, _, _, _, _, _ = ingestion
    response = stage(client, metadata={"title": "Default metadata", "collection_id": "documents"})
    assert response.status_code == 200, response.text
