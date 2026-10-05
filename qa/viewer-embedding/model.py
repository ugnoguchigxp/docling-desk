"""Deterministic OpenAI-compatible QA model; never calls an external LLM."""

import json
import re
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[2]
app = FastAPI()


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [{"id": "docling-viewer-qa", "object": "model", "created": 1, "owned_by": "qa"}],
    }


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    last = messages[-1] if messages else {}
    tools = body.get("tools", [])
    user = next((m.get("content", "") for m in reversed(messages) if m["role"] == "user"), "")
    if not isinstance(user, str):
        user = json.dumps(user)
    selected = None
    args = {}
    if last.get("role") != "tool":
        code = re.search(r"接続コード\s*([0-9A-F]{8})", user)
        if code:
            selected = "connect_document_view"
            args = {"code": code[1]}
        elif "open" in user.lower() or "開いて" in user:
            selected = "request_document_view"
            refs = json.loads((ROOT / ".cache/viewer-embedding/refs.json").read_text())
            args = {**refs["pdf"], "location": {"kind": "page", "number": 2}}
        elif "context" in user.lower():
            selected = None
        else:
            selected = "search_documents"
            args = {
                "query": "Bの件数" if "PDF" in user else "合成",
                "scope": {"collection_ids": ["synthetic"]},
                "mode": "text",
                "limit": 6,
            }
    function = next(
        (
            t["function"]["name"]
            for t in tools
            if t.get("function", {}).get("name", "").endswith(selected or "NONE")
        ),
        None,
    )
    delta = {"role": "assistant"}
    finish = "stop"
    if function:
        delta["tool_calls"] = [
            {
                "index": 0,
                "id": "call_" + uuid4().hex,
                "type": "function",
                "function": {"name": function, "arguments": json.dumps(args, ensure_ascii=False)},
            }
        ]
        finish = "tool_calls"
    else:
        delta["content"] = (
            "合成資料を確認しました。[1] 閲覧画面の接続コードを承認すると、資料をチャット内で読むことができます。"
        )
    (ROOT / ".cache/viewer-embedding/model-diagnostics.json").write_text(
        json.dumps(
            {
                "tools": [t.get("function", {}).get("name") for t in tools],
                "selected": function,
                "last_role": last.get("role"),
            }
        )
    )
    identifier = "chatcmpl-" + uuid4().hex
    if not body.get("stream"):
        message = {k: v for k, v in delta.items()}
        message.pop("index", None)
        return {
            "id": identifier,
            "object": "chat.completion",
            "created": 1,
            "model": "docling-viewer-qa",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def stream():
        for value in [
            {
                "id": identifier,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "docling-viewer-qa",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": identifier,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "docling-viewer-qa",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]:
            yield "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


uvicorn.run(app, host="0.0.0.0", port=18881)
