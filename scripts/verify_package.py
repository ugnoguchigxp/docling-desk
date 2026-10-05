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
with TestClient(app) as client:
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
