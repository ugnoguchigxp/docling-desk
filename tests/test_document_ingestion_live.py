"""Real MCP -> gateway -> Bun/SQLite -> Python processor, without cloud calls."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from pdf_samples import pdf_bytes

from docling_desk.viewer.gateway import create_gateway

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", ["ingestion", "document_reads", "workflows"])
def test_real_ingestion_replacement_extraction_indexing_and_search(tmp_path, scenario):
    pytest.importorskip("mcp")
    if not shutil.which("bun") or not (ROOT / "knowledge-api/node_modules/hono").exists():
        pytest.skip("MCP integration requires Bun and installed knowledge-api dependencies")
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        worker_port = reservation.getsockname()[1]
    worker_url = f"http://127.0.0.1:{worker_port}"
    worker_token = "p" * 40
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DOCLING_", "AZURE_", "OPENAI_", "KNOWLEDGE_"))
    }
    env.update(
        DOCLING_ENV_FILE=os.devnull,
        KNOWLEDGE_ARTIFACT_ROOT=str(artifacts),
        KNOWLEDGE_WORKER_TOKEN=worker_token,
    )
    config = {
        "api_url": "http://127.0.0.1",
        "public_url": "http://localhost",
        "api_token": "a" * 40,
        "signing_key": "k" * 40,
        "connector_token": "c" * 40,
        "client_id": "p3",
        "issuer": "p3",
        "kid": "v1",
        "users": {
            "writer": {
                "scopes": [{"collection_id": "documents"}],
                "actions": ["read", "write"],
                "mcp_token": "w" * 40,
            }
        },
    }
    api_config = tmp_path / "api.json"
    api_config.write_text(
        json.dumps(
            {
                "root": str(artifacts),
                "processor_url": worker_url,
                "processor_token": worker_token,
                "client": {
                    "id": "p3",
                    "issuer": "p3",
                    "token": "a" * 40,
                    "keys": {"v1": "k" * 40},
                    "mode": "actor",
                    "scopes": [{"collection_id": "documents"}],
                    "actions": ["read", "write"],
                },
            }
        )
    )
    processes = []
    logs = []
    server = thread = None
    try:
        worker_log = (tmp_path / "processor.log").open("w+")
        logs.append(worker_log)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "docling_desk.knowledge.worker:app_factory",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(worker_port),
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=env,
                stdout=worker_log,
                stderr=worker_log,
            )
        )
        deadline = time.monotonic() + 20
        with httpx.Client(timeout=1) as http:
            while True:
                try:
                    if http.get(worker_url + "/health/live").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert processes[0].poll() is None and time.monotonic() < deadline, (
                    tmp_path / "processor.log"
                ).read_text()
                time.sleep(0.05)
        api_log = (tmp_path / "api.log").open("w+")
        logs.append(api_log)
        processes.append(
            subprocess.Popen(
                ["bun", str(ROOT / "tests/fixtures/p3-api.ts"), str(api_config)],
                cwd=ROOT,
                env=env,
                stdout=api_log,
                stderr=api_log,
            )
        )
        deadline = time.monotonic() + 10
        while True:
            lines = (tmp_path / "api.log").read_text().splitlines()
            if lines and lines[0].startswith('{"url":'):
                config["api_url"] = json.loads(lines[0])["url"]
                break
            assert processes[1].poll() is None and time.monotonic() < deadline, lines
            time.sleep(0.05)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            config["public_url"] = gateway_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
            path = tmp_path / "gateway.json"
            path.write_text(json.dumps(config))
            server = uvicorn.Server(
                uvicorn.Config(create_gateway(path, mcp_enabled=True), log_level="error")
            )
            thread = threading.Thread(
                target=server.run, kwargs={"sockets": [sock.dup()]}, daemon=True
            )
            thread.start()
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started

            async def run():
                headers = {"Authorization": "Bearer " + "w" * 40}
                async with httpx.AsyncClient(timeout=35) as host:
                    async with streamablehttp_client(gateway_url + "/mcp/", headers=headers) as (
                        read,
                        write,
                        _,
                    ):
                        async with ClientSession(read, write) as mcp:
                            await mcp.initialize()

                            async def call(name, arguments):
                                result = await mcp.call_tool(name, {"arguments": arguments})
                                assert not result.isError, result.content
                                if name in {
                                    "import_document",
                                    "replace_document_source",
                                    "get_document_status",
                                    "reextract_document",
                                    "reindex_document",
                                }:
                                    assert result.structuredContent is not None
                                return result.structuredContent or json.loads(
                                    result.content[0].text
                                )

                            async def upload(body, extra=None, filename="synthetic.md"):
                                response = await host.post(
                                    gateway_url + "/api/v1/document-transfers",
                                    headers=headers,
                                    files={"file": (filename, body)},
                                    data={
                                        "metadata": json.dumps(
                                            {
                                                "title": "Synthetic",
                                                "collection_id": "documents",
                                                "source_kind": "document",
                                                "language": "ja",
                                            }
                                        ),
                                        **(extra or {}),
                                    },
                                )
                                assert response.status_code == 200, response.text
                                return response.json()["transfer_id"]

                            async def finished(job_id):
                                deadline = time.monotonic() + 30
                                while True:
                                    job = await call("get_document_job", {"job_id": job_id})
                                    if job["state"] not in {"queued", "running"}:
                                        assert job["state"] == "completed", job
                                        return
                                    assert time.monotonic() < deadline, job
                                    await asyncio.sleep(0.05)

                            if scenario == "workflows":
                                from unittest.mock import patch

                                from test_mcp_workflows_live import exercise_workflows

                                from docling_desk.operations.mcp_check import Connection, probe

                                settings = json.loads(
                                    (
                                        ROOT / "integrations/eumenes/connection.example.json"
                                    ).read_text()
                                )
                                settings["gateway_url"] = gateway_url
                                with patch.dict(os.environ, {settings["mcp_token_env"]: "w" * 40}):
                                    inspected = await probe(Connection.model_validate(settings))
                                assert (
                                    inspected["tool_count"] == 36
                                    and len(inspected["workflow_ids"]) == 5
                                )
                                await exercise_workflows(mcp, host, gateway_url, headers, upload)
                                return
                            if scenario == "document_reads":
                                await exercise_document_reads(
                                    mcp, host, gateway_url, headers, call, upload, finished
                                )
                                return

                            transfer_id = await upload(
                                b"# Document\n\nSynthetic inventory original.\n"
                            )
                            args = {"transfer_id": transfer_id, "idempotency_key": "live-import"}
                            saved = await call("import_document", args)
                            assert (await call("import_document", args)) == saved
                            await finished(saved["job_id"])
                            status = await call(
                                "get_document_status", {"source_id": saved["source_id"]}
                            )
                            assert status["fts_ready"] and not status["semantic_ready"]
                            assert status["source_etag"]
                            transfer_id = await upload(
                                b"# Document\n\nSynthetic inventory replacement.\n",
                                {"source_id": saved["source_id"], "etag": status["source_etag"]},
                            )
                            replaced = await call(
                                "replace_document_source",
                                {
                                    "transfer_id": transfer_id,
                                    "source_id": saved["source_id"],
                                    "etag": status["source_etag"],
                                    "idempotency_key": "live-replace",
                                },
                            )
                            assert (
                                replaced["source_id"] == saved["source_id"]
                                and replaced["source_revision"] != saved["source_revision"]
                            )
                            await finished(replaced["job_id"])
                            status = await call(
                                "get_document_status", {"source_id": saved["source_id"]}
                            )
                            for name, extra in [
                                ("reextract_document", {"ocr_provider": "local"}),
                                ("reindex_document", {}),
                            ]:
                                job = await call(
                                    name,
                                    {
                                        "source_id": saved["source_id"],
                                        "etag": status["source_etag"],
                                        **extra,
                                    },
                                )
                                await finished(job["job_id"])
                                status = await call(
                                    "get_document_status", {"source_id": saved["source_id"]}
                                )
                                assert (
                                    status["fts_ready"]
                                    and status["source_revision"] == replaced["source_revision"]
                                )
                            search = await mcp.call_tool(
                                "search_documents",
                                {
                                    "query": "replacement",
                                    "scope": {"collection_ids": ["documents"]},
                                    "mode": "text",
                                },
                            )
                            assert not search.isError, search.content
                            searched = search.structuredContent or json.loads(
                                search.content[0].text
                            )
                            assert searched["results"]
                            hit = searched["results"][0]
                            assert hit["source_revision"] == replaced["source_revision"]
                            context = await mcp.call_tool(
                                "get_document_context",
                                {
                                    "retrieval_id": searched["retrieval_id"],
                                    "references": [
                                        {
                                            k: hit[k]
                                            for k in (
                                                "source_id",
                                                "source_revision",
                                                "evidence_revision",
                                                "context_id",
                                            )
                                        }
                                    ],
                                },
                            )
                            assert not context.isError and "replacement" in json.dumps(
                                context.structuredContent or json.loads(context.content[0].text)
                            )
                            guide = await mcp.call_tool(
                                "get_tool_guide", {"guide_id": "workflow.import-and-read"}
                            )
                            assert not guide.isError

            asyncio.run(run())
    finally:
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=10)
            assert not thread.is_alive()
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


async def exercise_document_reads(mcp, host, gateway_url, headers, call, upload, finished):
    import base64
    from io import BytesIO

    from PIL import Image

    async def register(body, filename):
        transfer = await upload(body, {"ocr_provider": "disabled"}, filename)
        saved = await call(
            "import_document", {"transfer_id": transfer, "idempotency_key": "reads-" + filename}
        )
        await finished(saved["job_id"])
        status = await call("get_document_status", {"source_id": saved["source_id"]})
        ref = {key: status[key] for key in ("source_id", "source_revision", "evidence_revision")}
        return status, ref

    status, ref = await register(
        b"# First\n\nFirst content.\n\n## Nested\n\n| Name | Amount |\n| --- | --- |\n| Synthetic | 42 |\n\n# Second\n\nSecond content.\n",
        "read.md",
    )

    async def read(name, extra=None, reference=None):
        result = await mcp.call_tool(name, {"arguments": {**(reference or ref), **(extra or {})}})
        assert not result.isError, result.content
        assert result.structuredContent is not None
        assert all(result.structuredContent[key] == (reference or ref)[key] for key in ref)
        return result.structuredContent

    first = await read("get_document_outline", {"limit": 2})
    rest = await read("get_document_outline", {"offset": first["next_offset"]})
    entries = first["entries"] + rest["entries"]
    assert first["kind"] == "document" and first["units"] == []
    section = await read(
        "get_document_section",
        {
            "selector": {
                "kind": "section",
                "ref": next(e["ref"] for e in entries if e["heading"] == "First"),
            }
        },
    )
    assert "Second content" not in json.dumps(section) and "First content" in json.dumps(section)
    table_list = await read("list_document_tables")
    assert table_list["total"] == 1
    table_ref = table_list["tables"][0]["table_ref"]
    table = await read("get_document_table", {"table_ref": table_ref, "format": "csv"})
    assert "Synthetic" in table["csv"] and "42" in table["csv"]
    http = await host.post(
        gateway_url + "/host/tools/get_document_table",
        headers={"Authorization": "Bearer " + "c" * 40},
        json={"user_id": "writer", "arguments": {**ref, "table_ref": table_ref, "format": "csv"}},
    )
    assert http.status_code == 200, http.text
    # The host JSON and MCP structured data describe the same cell coordinates.
    assert http.json()["data"] == table["data"]
    unsupported = await mcp.call_tool("get_document_page_image", {"arguments": {**ref, "unit": 1}})
    assert unsupported.isError and "page_image_unsupported" in str(unsupported.content)
    invalid = await mcp.call_tool(
        "get_document_section",
        {
            "arguments": {
                **ref,
                "selector": {
                    "kind": "elements",
                    "start_ref": "#/texts/9999",
                    "end_ref": "#/texts/9999",
                },
            }
        },
    )
    assert invalid.isError and "invalid_document_range" in str(invalid.content)
    reextracted = await call(
        "reextract_document",
        {"source_id": ref["source_id"], "etag": status["source_etag"], "ocr_provider": "disabled"},
    )
    await finished(reextracted["job_id"])
    stale = await mcp.call_tool(
        "get_document_table", {"arguments": {**ref, "table_ref": table_ref}}
    )
    assert stale.isError and "evidence_changed" in str(stale.content)
    sample = pdf_bytes(
        ("Synthetic PDF content", "Second page content"), size=(400, 600), at=(30, 30)
    )
    _, pdf_ref = await register(sample, "read.pdf")
    outline = await read("get_document_outline", reference=pdf_ref)
    assert outline["kind"] == "page" and [u["number"] for u in outline["units"]] == [1, 2]
    page = await read(
        "get_document_section", {"selector": {"kind": "units", "start": 2, "end": 2}}, pdf_ref
    )
    assert "Second page content" in json.dumps(page)
    result = await mcp.call_tool("get_document_page_image", {"arguments": {**pdf_ref, "unit": 2}})
    assert not result.isError, result.content
    image = next(c for c in result.content if c.type == "image")
    assert image.mimeType == "image/webp"
    metadata = result.structuredContent
    assert metadata["source_revision"] == pdf_ref["source_revision"] and metadata["unit"] == 2
    assert "body_base64" not in metadata
    with Image.open(BytesIO(base64.b64decode(image.data))) as decoded:
        assert decoded.size == (metadata["width"], metadata["height"])
    guide = await mcp.call_tool("get_tool_guide", {"guide_id": "workflow.read-document"})
    assert not guide.isError
