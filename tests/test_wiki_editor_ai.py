import json

from test_local_knowledge import client as client


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
