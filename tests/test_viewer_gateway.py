from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from docling_desk.viewer.gateway import create_gateway


@pytest.fixture
def gateway(tmp_path):
    config = {
        "api_url": "http://knowledge.test",
        "public_url": "http://viewer.test",
        "api_token": "a" * 40,
        "signing_key": "k" * 40,
        "connector_token": "c" * 40,
        "client_id": "viewer",
        "issuer": "viewer",
        "kid": "v1",
        "users": {
            "alice": {"scopes": [{"collection_id": "docs"}]},
            "bob": {"scopes": [{"collection_id": "other"}]},
        },
    }
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps(config))
    ref = {key: str(uuid4()) for key in ("source_id", "source_revision", "evidence_revision")}
    calls = []
    state = {"deleted": False, "updated": False}

    def api(request):
        body = json.loads(request.content)
        calls.append(body)
        claims = json.loads(
            __import__("base64").urlsafe_b64decode(
                request.headers["x-knowledge-actor"].split(".")[1] + "=="
            )
        )
        if claims["sub"] != "alice" or state["deleted"]:
            return httpx.Response(404, json={"error": {"code": "source_not_found"}})
        if state["updated"]:
            return httpx.Response(409, json={"error": {"code": "source_changed"}})
        if body["resource"] == "manifest":
            return httpx.Response(
                200,
                json={
                    "job": {"id": str(uuid4()), "filename": "synthetic.pdf"},
                    "kind": "page",
                    "units": 2,
                },
            )
        return httpx.Response(200, json={"media_type": "text/html", "body_base64": "b2s="})

    app = create_gateway(path, httpx.MockTransport(api))
    with TestClient(app) as client:
        yield client, app.state.gateway, path, config, ref, calls, state


def approve(client, code, user="alice"):
    return client.post(
        "/host/tools/connect_document_view",
        headers={"authorization": "Bearer " + "c" * 40},
        json={"user_id": user, "arguments": {"code": code}},
    )


def paired(client, ref):
    challenge = client.post("/viewer/challenges", json=ref).json()
    assert approve(client, challenge["code"]).status_code == 200
    poll = client.get("/viewer/challenges/" + challenge["challenge"])
    assert poll.status_code == 200
    return poll.json()["session"], challenge


def test_history_reference_has_no_authority_and_pairing_is_single_use(gateway):
    client, _, _, _, ref, calls, _ = gateway
    assert client.get("/viewer/session/unknown/manifest").status_code == 401
    challenge = client.post("/viewer/challenges", json=ref).json()
    assert not calls
    assert client.get("/viewer/challenges/" + challenge["challenge"]).json() == {
        "status": "pending"
    }
    assert approve(client, challenge["code"], "bob").status_code == 404
    token, challenge = paired(client, ref)
    assert client.get("/viewer/challenges/" + challenge["challenge"]).status_code == 410
    assert approve(client, challenge["code"]).status_code == 404
    assert (
        client.get(f"/viewer/session/{token}/manifest", headers={"origin": "null"}).status_code
        == 200
    )
    view = client.post(
        "/host/tools/request_document_view",
        headers={"authorization": "Bearer " + "c" * 40},
        json={"user_id": "alice", "arguments": ref},
    ).json()
    assert "session/" not in view["embed_url"] and token not in json.dumps(view)
    assert not any(value in json.dumps(view) for value in ("a" * 40, "k" * 40, "c" * 40))
    shared = client.post("/viewer/challenges", json=ref).json()
    assert shared["challenge"] != challenge["challenge"]
    assert client.get("/viewer/challenges/" + shared["challenge"]).json() == {"status": "pending"}


def test_expiry_revocation_delete_and_update_apply_to_every_resource(gateway):
    client, service, path, config, ref, _, state = gateway
    token, _ = paired(client, ref)
    assert client.get(f"/viewer/session/{token}/view/pdf").content == b"ok"
    state["updated"] = True
    assert client.get(f"/viewer/session/{token}/file/image.png").status_code == 409
    state["updated"] = False
    state["deleted"] = True
    assert client.get(f"/viewer/session/{token}/static/pdf-viewer.js").status_code == 404
    state["deleted"] = False
    config["users"]["alice"]["scopes"] = [{"collection_id": "other"}]
    path.write_text(json.dumps(config))
    assert client.get(f"/viewer/session/{token}/manifest").status_code == 401
    config["users"]["alice"]["scopes"] = [{"collection_id": "docs"}]
    path.write_text(json.dumps(config))
    service.sessions[token]["expires"] = time.time() - 1
    assert client.get(f"/viewer/session/{token}/manifest").status_code == 401


def test_invalid_location_untrusted_user_and_spoofed_approval(gateway):
    client, _, _, _, ref, _, _ = gateway
    challenge = client.post(
        "/viewer/challenges", json={**ref, "location": {"kind": "page", "number": 3}}
    ).json()
    assert approve(client, challenge["code"]).status_code == 422
    challenge = client.post("/viewer/challenges", json=ref).json()
    response = client.post(
        "/host/tools/connect_document_view",
        json={"user_id": "alice", "arguments": {"code": challenge["code"]}},
    )
    assert response.status_code == 401
    assert approve(client, challenge["code"], "unregistered").status_code == 403
    assert (
        client.post("/viewer/challenges", json={**ref, "source_id": "../data"}).status_code == 422
    )


def test_large_chunked_bodies_do_not_reach_pairing_and_logs_redact_runtime_tokens(gateway):
    import logging

    from docling_desk.viewer.gateway import RedactViewerCredentials

    client, _, _, _, _, calls, _ = gateway
    response = client.post(
        "/viewer/challenges",
        content=(b"x" * 20000 for _ in range(3)),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413 and not calls
    token = "x" * 43
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s %s %s %s %s",
        ("ip", "GET", f"/viewer/session/{token}/view/pdf", "HTTP/1.1", 200),
        None,
    )
    RedactViewerCredentials().filter(record)
    assert token not in record.getMessage() and "[redacted]" in record.getMessage()
    record.args = ("ip", "GET", f"/viewer/challenges/{'%78' * 43}", "HTTP/1.1", 200)
    RedactViewerCredentials().filter(record)
    assert "%78" not in record.getMessage()


def test_non_ascii_host_authorization_is_rejected(gateway):
    client, _, _, _, ref, _, _ = gateway
    result = client.post(
        "/host/tools/request_document_view",
        headers={b"authorization": b"Bearer \xff"},
        json={"user_id": "alice", "arguments": ref},
    )
    assert result.status_code == 401


def test_user_policy_change_while_reading_prevents_returning_metadata(gateway):
    client, service, path, config, ref, _, _ = gateway
    original = service.transport

    def raced(request):
        config["users"]["alice"]["scopes"] = [{"collection_id": "other"}]
        path.write_text(json.dumps(config))
        return httpx.Response(
            200,
            json={
                "job": {"id": str(uuid4()), "filename": "private.pdf"},
                "kind": "page",
                "units": 2,
            },
        )

    service.transport = httpx.MockTransport(raced)
    result = client.post(
        "/host/tools/request_document_view",
        headers={"authorization": "Bearer " + "c" * 40},
        json={"user_id": "alice", "arguments": ref},
    )
    assert result.status_code == 401 and "private.pdf" not in result.text
    service.transport = original


def test_invalid_connection_code_is_rejected_without_consuming_challenge(gateway):
    client, _, _, _, ref, _, _ = gateway
    challenge = client.post("/viewer/challenges", json=ref).json()
    assert approve(client, "日本語接続コード").status_code == 422
    assert approve(client, "NOTHEX!!").status_code == 422
    assert approve(client, challenge["code"].lower()).status_code == 200


def test_policy_change_after_manifest_does_not_grant_a_session(gateway, monkeypatch):
    client, service, path, config, ref, _, _ = gateway
    original = service.manifest

    async def raced(*args):
        result = await original(*args)
        config["users"]["alice"]["scopes"] = [{"collection_id": "other"}]
        path.write_text(json.dumps(config))
        return result

    monkeypatch.setattr(service, "manifest", raced)
    challenge = client.post("/viewer/challenges", json=ref).json()
    result = approve(client, challenge["code"])
    assert result.status_code == 401
    assert not service.sessions
    assert client.get("/viewer/challenges/" + challenge["challenge"]).status_code == 410


def test_api_response_limit_stops_consuming_an_oversized_stream(gateway, monkeypatch):
    import docling_desk.viewer.gateway as viewer_gateway

    client, service, _, _, ref, _, _ = gateway
    consumed = []

    class Oversized(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in (b"x" * 800, b"x" * 800, b"never consume this chunk"):
                consumed.append(chunk)
                yield chunk

    monkeypatch.setattr(viewer_gateway, "MAX_API_RESPONSE", 1024, raising=False)
    service.transport = httpx.MockTransport(lambda r: httpx.Response(200, stream=Oversized()))
    result = client.post(
        "/host/tools/request_document_view",
        headers={"authorization": "Bearer " + "c" * 40},
        json={"user_id": "alice", "arguments": ref},
    )
    assert result.status_code == 502
    assert len(consumed) == 2


@pytest.mark.parametrize(
    "status, body", [(200, []), (200, {}), (200, {"kind": []}), (503, {"error": []})]
)
def test_malformed_upstream_metadata_is_a_recoverable_error(gateway, status, body):
    client, service, _, _, ref, _, _ = gateway
    service.transport = httpx.MockTransport(lambda r: httpx.Response(status, json=body))
    result = client.post(
        "/host/tools/request_document_view",
        headers={"authorization": "Bearer " + "c" * 40},
        json={"user_id": "alice", "arguments": ref},
    )
    assert result.status_code in {502, 503}


@pytest.mark.parametrize(
    "body",
    [
        {"media_type": "text/html", "body_base64": "not base64!"},
        {"media_type": "text/html\r\nInvalid: header", "body_base64": "b2s="},
    ],
)
def test_malformed_upstream_asset_is_rejected(gateway, body):
    client, service, _, _, ref, _, _ = gateway
    token, _ = paired(client, ref)
    service.transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    assert client.get(f"/viewer/session/{token}/view/pdf").status_code == 502


@pytest.mark.parametrize("change_during_call", [False, True])
def test_mcp_revalidates_the_same_token_owner_before_returning(
    gateway, monkeypatch, change_during_call
):
    import asyncio

    pytest.importorskip("mcp")
    from mcp.server.auth.provider import AccessToken
    from mcp.server.fastmcp.exceptions import ToolError

    import docling_desk.viewer.mcp as viewer_mcp

    _, service, path, config, _, _, _ = gateway
    token = "m" * 40
    config["users"]["alice"]["mcp_token"] = token if change_during_call else "n" * 40
    config["users"]["bob"]["mcp_token"] = "b" * 40 if change_during_call else token
    path.write_text(json.dumps(config))
    actor = AccessToken(token=token, client_id="alice", scopes=["documents:read"])
    monkeypatch.setattr(viewer_mcp, "get_access_token", lambda: actor)
    if change_during_call:
        original = service.tool

        async def raced(*args):
            result = await original(*args)
            config["users"]["alice"]["mcp_token"] = "n" * 40
            config["users"]["bob"]["mcp_token"] = token
            path.write_text(json.dumps(config))
            return result

        monkeypatch.setattr(service, "tool", raced)
    server = viewer_mcp.create_mcp(service)
    with pytest.raises(ToolError, match="unauthenticated"):
        asyncio.run(server.call_tool("list_document_scopes", {}))


def test_one_issuer_cannot_consume_the_pairing_capacity(gateway):
    client, service, path, config, ref, _, _ = gateway
    config["trusted_proxies"] = ["testclient"]
    path.write_text(json.dumps(config))
    first = {"x-forwarded-for": "203.0.113.10"}
    accepted = 0
    for _ in range(40):
        response = client.post("/viewer/challenges", json=ref, headers=first)
        if response.status_code == 200:
            accepted += 1
            continue
        assert response.status_code == 429
        assert response.json()["detail"] == "viewer_issuer_limited"
        assert response.headers["retry-after"] == "60"
        break
    assert accepted == 20
    spoofed_chain = client.post(
        "/viewer/challenges",
        json=ref,
        headers={"x-forwarded-for": "198.51.100.9, 203.0.113.10"},
    )
    assert spoofed_chain.status_code == 429
    assert spoofed_chain.json()["detail"] == "viewer_issuer_limited"
    other = client.post("/viewer/challenges", json=ref, headers={"x-forwarded-for": "203.0.113.20"})
    assert other.status_code == 200
    assert approve(client, other.json()["code"]).status_code == 200
    config["trusted_proxies"] = []
    path.write_text(json.dumps(config))
    for _ in range(20):
        response = client.post(
            "/viewer/challenges", json=ref, headers={"x-forwarded-for": "198.51.100.8"}
        )
        assert response.status_code == 200
    spoofed = client.post(
        "/viewer/challenges", json=ref, headers={"x-forwarded-for": "198.51.100.9"}
    )
    assert spoofed.status_code == 429
    assert spoofed.json()["detail"] == "viewer_issuer_limited"
    for item in service.pending.values():
        item["expires"] = time.time() - 1
    recovered = client.post("/viewer/challenges", json=ref)
    assert recovered.status_code == 200
