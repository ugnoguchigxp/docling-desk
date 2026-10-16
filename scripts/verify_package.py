"""Build an sdist and wheel, then exercise the installation without cloud calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def assert_public_resource(name: str, content: bytes) -> None:
    """Application distributions contain code/resources, never saved documents or DBs."""
    path = Path(name)
    assert not set(path.parts).intersection(
        {"data", "qa", "samples", ".git", ".cache", ".knowledge-api", ".venv"}
    ), name
    assert not (path.name.startswith(".env") and path.name != ".env.example"), name
    assert path.suffix.lower() not in {
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".doc",
        ".xls",
        ".ppt",
    }, name
    assert not path.name.endswith(
        (
            ".db-wal",
            ".db-shm",
            ".db-journal",
            ".sqlite-wal",
            ".sqlite-shm",
            ".sqlite-journal",
            ".sqlite3-wal",
            ".sqlite3-shm",
            ".sqlite3-journal",
        )
    ), name
    assert not content.startswith(b"SQLite format 3\x00"), name


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="docling-desk-wheel-") as temp:
        workspace = Path(temp)
        wheels = workspace / "wheels"
        wheels.mkdir()
        sources = workspace / "sources"
        sources.mkdir()
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from setuptools.build_meta import build_sdist; build_sdist(__import__('sys').argv[1])",
                str(sources),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with tarfile.open(next(sources.glob("*.tar.gz"))) as archive:
            names = archive.getnames()
            relative = [name.split("/", 1)[-1] for name in names]
            assert not any(
                name.startswith(("data/", "qa/", ".env", "models/")) for name in relative
            )
            for member in archive.getmembers():
                if member.isfile():
                    stream = archive.extractfile(member)
                    assert stream is not None
                    with stream:
                        assert_public_resource(member.name.split("/", 1)[-1], stream.read(16))
            archive.extractall(sources, filter="data")
        source = next(path for path in sources.iterdir() if path.is_dir())
        code = (
            "from setuptools.build_meta import build_wheel; build_wheel(__import__('sys').argv[1])"
        )
        subprocess.run(
            [sys.executable, "-c", code, str(wheels)],
            cwd=source,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        wheel = next(wheels.glob("*.whl"))
        target = workspace / "installed"
        with ZipFile(wheel) as archive:
            names = archive.namelist()
            assert not any(name.startswith(("data/", "qa/")) for name in names)
            for name in names:
                with archive.open(name) as stream:
                    assert_public_resource(name, stream.read(16))
            for suffix in [
                "resources/models/manifest.json",
                "resources/ops/retention-policy.json",
                "resources/static/frontend/index.html",
                "resources/scripts/render-slide-thumbnail.swift",
                "resources/tool-guides/manifest.json",
                "resources/integrations/eumenes/README.md",
                "resources/integrations/eumenes/connection.example.json",
                "resources/integrations/eumenes/connection.schema.json",
                "resources/integrations/documents/clients.example.json",
                "resources/integrations/documents/gateway.example.json",
                "resources/integrations/documents/gateway.container.example.json",
                "resources/contracts/mcp-tools.json",
                "resources/contracts/tool-guides.openapi.json",
                "resources/contracts/document-transfers.openapi.json",
            ]:
                assert "docling_desk/" + suffix in names, suffix
            assert any(name.endswith("/licenses/LICENSE") for name in names)
            archive.extractall(target)
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("DOCLING_", "AZURE_", "OPENAI_", "KNOWLEDGE_", "PYTHONPATH"))
        }
        env.update(
            DOCLING_STATE_DIR=str(workspace / "state"),
            DOCLING_ENV_FILE=os.devnull,
            PYTHONDONTWRITEBYTECODE="1",
        )
        probe = r"""
import json, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from docling_desk import config
from docling_desk.app import app
from fastapi.testclient import TestClient
assert pathlib.Path(config.__file__).is_relative_to(pathlib.Path(sys.argv[1]))
assert config.PROJECT_ROOT is None
assert not config.DATA.is_relative_to(config.PACKAGE_ROOT)
from docling_desk.viewer.tool_guides import ToolGuides, GuideGetInput
guides = ToolGuides()
assert guides.get(GuideGetInput(guide_id="overview"))["sections"]
for entry in guides.entries:
    result = guides.get(GuideGetInput(guide_id=entry["guide_id"]))
    assert result["sections"]
    if entry["kind"] == "tool":
        name = entry["tool_names"][0]
        assert result["schemas"][name]["mcp"]["inputSchema"]
from docling_desk.operations.mcp_check import Connection
from jsonschema import Draft202012Validator
resource_root = config.PACKAGE_ROOT / "resources/integrations"
example = resource_root / "eumenes/connection.example.json"
Connection.model_validate_json(example.read_text())
assert json.loads((resource_root / "eumenes/connection.schema.json").read_text()) == Connection.model_json_schema()
Draft202012Validator(Connection.model_json_schema()).validate(json.loads(example.read_text()))
from docling_desk.viewer.gateway import GatewayConfig
for name in ("gateway.example.json", "gateway.container.example.json"):
    GatewayConfig.model_validate_json((resource_root / "documents" / name).read_text())
from docling_desk.viewer.gateway import create_gateway
gateway_config = config.STATE_ROOT / "synthetic-gateway.json"
gateway_config.parent.mkdir(parents=True, exist_ok=True)
gateway_config.write_text(json.dumps({
    "api_url": "http://127.0.0.1:1", "public_url": "http://localhost",
    "api_token": "a" * 40, "signing_key": "k" * 40, "connector_token": "c" * 40,
    "client_id": "synthetic", "issuer": "synthetic", "kid": "v1",
    "users": {
        "reader": {"scopes": [{"collection_id": "documents"}], "mcp_token": "r" * 40},
        "writer": {"scopes": [{"collection_id": "documents"}], "mcp_token": "w" * 40, "actions": ["read", "write"]},
    },
}))
import httpx
transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"actions": ["read", "write"], "scopes": [{"collection_id": "documents"}]}))
with TestClient(create_gateway(gateway_config, transport)) as gateway:
    headers = {"Authorization": "Bearer " + "r" * 40}
    assert gateway.get("/api/v1/tool-guides").status_code == 401
    for guide_id in [entry["guide_id"] for entry in guides.entries]:
        response = gateway.get("/api/v1/tool-guides/" + guide_id, headers=headers)
        assert response.status_code == 200, response.text
    writer = {"Authorization": "Bearer " + "w" * 40}
    transfer = gateway.post("/api/v1/document-transfers", headers=writer,
        files={"file": ("synthetic.txt", b"installed transfer")},
        data={"metadata": json.dumps({"title": "Synthetic", "collection_id": "documents", "source_kind": "document", "language": "ja"})})
    assert transfer.status_code == 200, transfer.text
    checked = gateway.get("/api/v1/document-transfers/" + transfer.json()["transfer_id"], headers=writer)
    assert checked.status_code == 200 and checked.json()["state"] == "staged", checked.text
# Restart the installed gateway against the same persistent transfer directory.
with TestClient(create_gateway(gateway_config, transport)) as restarted:
    checked = restarted.get("/api/v1/document-transfers/" + transfer.json()["transfer_id"], headers=writer)
    assert checked.status_code == 200 and checked.json()["sha256"] == transfer.json()["sha256"]

# Exercise installed MCP code and the shipped host configuration outside checkout.
import asyncio, os, socket, threading, uvicorn
from docling_desk.operations.mcp_check import probe
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    url = "http://127.0.0.1:" + str(sock.getsockname()[1])
    value = json.loads(gateway_config.read_text())
    value["public_url"] = url
    gateway_config.write_text(json.dumps(value))
    server = uvicorn.Server(uvicorn.Config(create_gateway(gateway_config, transport, mcp_enabled=True), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock.dup()]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        settings = json.loads(example.read_text())
        settings["gateway_url"] = url
        os.environ[settings["mcp_token_env"]] = "r" * 40
        report = asyncio.run(probe(Connection.model_validate(settings)))
        assert report["tool_count"] == len(guides.tools)
        assert set(report["workflow_ids"]) == {e["guide_id"] for e in guides.entries if e["kind"] == "workflow"}
        assert report["actions"] == ["read"]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
print(json.dumps({"installed_mcp": True, "eumenes_connection_example": True, "transfer_restart": True, "workflow_count": len(report["workflow_ids"])}), flush=True)
print(json.dumps({"installed_tool_guides": True, "guide_count": len(guides.entries), "authenticated_http": True, "installed_document_transfers": True}), flush=True)
with TestClient(app, base_url="http://localhost") as client:
    response = client.get("/")
    assert response.status_code == 200, response.text
    assert client.get("/health/live").status_code == 200
    initial = client.get("/api/library")
    assert initial.status_code == 200 and initial.json()["jobs"] == [], initial.text
    result = client.post("/api/upload", files={"file": ("synthetic-installed.txt", "配布検証の本文です。".encode())})
    assert result.status_code == 202, result.text
    job_id = result.json()["id"]
    for _ in range(100):
        job = client.get("/api/jobs/" + job_id).json()
        if job["state"] in {"success", "failed"}:
            break
        time.sleep(.05)
    assert job["state"] == "success", job
    search = client.post("/api/knowledge/search", json={"query": "配布検証", "mode": "text"})
    assert search.status_code == 202 and search.json()["output"]["results"], search.text
    removed = client.post("/api/library/operations", json={"action": "delete", "items": [{"kind": "file", "id": job_id}]})
    assert removed.status_code == 200, removed.text
    assert not (config.DATA / "content/documents" / job_id).exists()
    assert not (config.DATA / "derived/documents" / job_id).exists()
    assert not (config.DATA / "runtime/documents" / job_id).exists()
    assert client.get("/api/library").json()["jobs"] == []
assert not (config.PACKAGE_ROOT / "data").exists()
assert not (config.PACKAGE_ROOT / ".cache").exists()
print(json.dumps({"installed_package": True, "resources": True, "initial_library_empty": True, "upload_search_delete": True, "separate_state": True}))
"""
        subprocess.run(
            [sys.executable, "-c", probe, str(target)], cwd=workspace, env=env, check=True
        )
        print(
            json.dumps(
                {"sdist_build": True, "wheel_bytes": wheel.stat().st_size, "status": "passed"}
            )
        )


if __name__ == "__main__":
    main()
