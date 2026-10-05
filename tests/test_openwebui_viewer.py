import asyncio
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest


def tools(monkeypatch, handler):
    path = Path(__file__).resolve().parents[1] / "integrations/open-webui/docling_desk_tools.py"
    spec = importlib.util.spec_from_file_location("openwebui_viewer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    instance = module.Tools()
    instance.valves.GATEWAY_URL = "http://viewer.test"
    instance.valves.CONNECTOR_TOKEN = "c" * 40
    return instance


def test_preview_timeout_preserves_search_and_citations_and_is_not_retried_per_chunk(monkeypatch):
    ref = {key: str(uuid4()) for key in ("source_id", "source_revision", "evidence_revision")}
    items = [
        {**ref, "context_id": str(uuid4()), "excerpt": "synthetic evidence", "locator": {}}
        for _ in range(2)
    ]
    attempts = []

    def handler(request):
        if request.url.path.endswith("/search_documents"):
            return httpx.Response(200, json={"results": items})
        attempts.append(request.url.path)
        raise httpx.ReadTimeout("test timeout", request=request)

    instance = tools(monkeypatch, handler)
    events = []

    async def emit(event):
        events.append(event)

    result = asyncio.run(
        instance.search_documents(
            "synthetic",
            {"collection_id": "docs"},
            __user__={"id": "alice"},
            __event_emitter__=emit,
        )
    )
    assert json.loads(result)["results"] == items
    assert len(events) == 2
    assert all("embed_url" not in e["data"]["source"] for e in events)
    assert len(attempts) == 1


@pytest.mark.parametrize("body", [b"not json", b"[]"])
def test_invalid_gateway_response_has_a_controlled_error(monkeypatch, body):
    instance = tools(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises(ValueError, match="invalid viewer service response"):
        asyncio.run(instance.list_document_scopes(__user__={"id": "alice"}))


@pytest.mark.parametrize(
    "status, code",
    [
        (401, "viewer_session_revoked"),
        (403, "viewer_user_forbidden"),
        (404, "source_not_found"),
        (409, "source_changed"),
        (409, "evidence_changed"),
    ],
)
def test_search_stops_before_emitting_stale_evidence_when_preview_detects_revocation(
    monkeypatch,
    status,
    code,
):
    ref = {key: str(uuid4()) for key in ("source_id", "source_revision", "evidence_revision")}
    item = {**ref, "context_id": str(uuid4()), "excerpt": "private synthetic evidence"}

    def handler(request):
        if request.url.path.endswith("/search_documents"):
            return httpx.Response(200, json={"results": [item]})
        return httpx.Response(status, json={"detail": code})

    instance = tools(monkeypatch, handler)
    events = []

    async def emit(event):
        events.append(event)

    with pytest.raises(ValueError, match=code):
        asyncio.run(
            instance.search_documents(
                "synthetic",
                {"collection_id": "docs"},
                __user__={"id": "alice"},
                __event_emitter__=emit,
            )
        )
    assert not events
