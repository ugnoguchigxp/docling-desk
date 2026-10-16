import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from docling_desk import config
from docling_desk.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    with TestClient(create_app()) as client:
        yield client


class Provider:
    def preflight(self):
        pass

    def cancel(self):
        pass

    def complete_structured(self, payload, timeout, instructions, task, schema):
        assert payload["messages"][0]["parts"][0]["text"] == "改善してください"
        assert "source material" in instructions
        assert timeout == 180
        assert schema["additionalProperties"] is False
        return {"toolName": "edit", "text": "改善した本文", "comments": [], "cells": []}


def test_standard_plate_ai_stream_uses_real_provider_contract(client):
    client.app.state.wiki_editor_ai_provider = Provider
    try:
        response = client.post(
            "/api/wiki/editor/ai",
            json={
                "messages": [
                    {"role": "user", "parts": [{"type": "text", "text": "改善してください"}]}
                ]
            },
        )
        assert response.status_code == 200
        assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        assert any(event.get("delta") == "改善した本文" for event in events)
        assert {"type": "data-toolName", "data": "edit", "transient": True} in events
    finally:
        del client.app.state.wiki_editor_ai_provider


def test_ai_failure_is_reported_without_fake_generated_content(client):
    class Failed(Provider):
        def complete_structured(self, *args):
            raise RuntimeError("provider unavailable")

    client.app.state.wiki_editor_ai_provider = Failed
    try:
        response = client.post("/api/wiki/editor/ai", json={})
        assert '"type": "error"' in response.text
        assert "text-delta" not in response.text
    finally:
        del client.app.state.wiki_editor_ai_provider


def test_ai_admission_is_shared_with_explanations_and_recovers_after_errors(client):
    manager = client.app.state.explanation_manager
    # A slot held by an ordinary explanation also limits editor operations.
    assert manager.slots.acquire(blocking=False)
    release, admitted = threading.Event(), threading.Event()
    count, guard = 0, threading.Lock()

    class Waiting(Provider):
        def preflight(self):
            nonlocal count
            with guard:
                count += 1
                if count == 2:
                    admitted.set()

        def complete_structured(self, *args):
            assert release.wait(10)
            raise RuntimeError("synthetic failure")

    client.app.state.wiki_editor_ai_provider = Waiting
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(client.post, "/api/wiki/editor/ai", json={}) for _ in range(2)]
        try:
            assert admitted.wait(5)
            assert client.post("/api/wiki/editor/ai", json={}).status_code == 429
            assert client.get("/api/jobs").status_code == 200
        finally:
            release.set()
            manager.slots.release()
        assert all(call.result(timeout=5).status_code == 200 for call in calls)
    assert all(manager.slots.acquire(blocking=False) for _ in range(3))
    assert not manager.slots.acquire(blocking=False)
    for _ in range(3):
        manager.slots.release()


def test_ai_preflight_error_does_not_leak_slots(client):
    from docling_desk.explanation.provider import ExplanationError

    class Failed(Provider):
        def preflight(self):
            raise ExplanationError("not_configured", "synthetic preflight failure")

    client.app.state.wiki_editor_ai_provider = Failed
    for _ in range(5):
        assert client.post("/api/wiki/editor/ai", json={}).status_code == 503
    assert client.app.state.explanation_manager.slots.acquire(blocking=False)
    client.app.state.explanation_manager.slots.release()


def test_disconnected_editor_holds_slot_until_provider_actually_stops(client):
    import asyncio

    from fastapi import Request

    manager = client.app.state.explanation_manager
    started, cancel, release = threading.Event(), threading.Event(), threading.Event()

    class Waiting(Provider):
        def preflight(self):
            pass

        def cancel(self):
            cancel.set()

        def complete_structured(self, *args):
            started.set()
            assert release.wait(10)
            return {"toolName": "edit", "text": "safe", "comments": [], "cells": []}

    client.app.state.wiki_editor_ai_provider = Waiting
    from docling_desk.knowledge.editor_ai import create_router

    endpoint = create_router().routes[0].endpoint

    async def run():
        response = await endpoint(Request({"type": "http", "app": client.app}), {})
        assert await asyncio.to_thread(started.wait, 5)
        await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert cancel.is_set()
        # Closing the stream must not release a still-running provider's slot.
        assert manager.slots.acquire(blocking=False)
        assert manager.slots.acquire(blocking=False)
        assert not manager.slots.acquire(blocking=False)
        manager.slots.release()
        manager.slots.release()
        release.set()
        for _ in range(500):
            acquired = []
            for _ in range(3):
                if manager.slots.acquire(blocking=False):
                    acquired.append(True)
            for _ in acquired:
                manager.slots.release()
            if len(acquired) == 3:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("editor capacity did not recover after cancellation")

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_disconnected_queued_editor_never_starts_provider_and_returns_slot(client):
    import asyncio

    from fastapi import Request

    from docling_desk.knowledge.editor_ai import create_router

    manager = client.app.state.explanation_manager
    started, release = threading.Event(), threading.Event()
    assert manager.slots.acquire(blocking=False)

    def occupying_explanation():
        started.set()
        assert release.wait(10)

    occupying = manager.executor.submit(occupying_explanation)
    calls = []

    class Waiting(Provider):
        def preflight(self):
            pass

        def complete_structured(self, *args):
            calls.append(True)
            raise AssertionError("cancelled queued work must not run")

    client.app.state.wiki_editor_ai_provider = Waiting

    async def run():
        assert started.wait(5)
        response = (
            await create_router()
            .routes[0]
            .endpoint(Request({"type": "http", "app": client.app}), {})
        )
        await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert manager.slots.acquire(blocking=False)
        assert manager.slots.acquire(blocking=False)
        assert not manager.slots.acquire(blocking=False)
        manager.slots.release()
        manager.slots.release()

    try:
        asyncio.run(run())
    finally:
        release.set()
        occupying.result(timeout=5)
        manager.slots.release()
    assert not calls
