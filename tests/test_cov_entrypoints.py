import runpy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from setuptools.dist import Distribution

from docling_desk._build import BuildPy, SourceDist, active_assets, build_py, sdist
from docling_desk.api import health
from docling_desk.cli import main


def test_active_assets_follow_html_references_and_ignore_missing_entries(tmp_path):
    bundle = tmp_path / "frontend"
    assets = bundle / "assets"
    assets.mkdir(parents=True)
    (bundle / "index.html").write_text("assets/app.js", encoding="utf-8")
    (bundle / "embed.html").write_text("plain", encoding="utf-8")
    (assets / "app.js").write_text("chunk.js app.js", encoding="utf-8")
    (assets / "chunk.js").write_text("ready", encoding="utf-8")
    (assets / "old.js").write_text("stale", encoding="utf-8")
    (assets / "notes").mkdir()
    assert active_assets(bundle) == {"app.js", "chunk.js"}
    assert active_assets(tmp_path / "missing") == set()


def test_build_and_sdist_drop_unreferenced_frontend_assets(tmp_path, monkeypatch):
    bundle = tmp_path / "docling_desk/resources/static/frontend"
    assets = bundle / "assets"
    assets.mkdir(parents=True)
    (bundle / "index.html").write_text("keep.js", encoding="utf-8")
    (assets / "keep.js").write_text("ok", encoding="utf-8")
    (assets / "drop.js").write_text("no", encoding="utf-8")
    monkeypatch.setattr(build_py, "run", lambda self: None)
    command = BuildPy(Distribution(attrs={"name": "docling-desk"}))
    command.build_lib = str(tmp_path)
    command.run()
    assert (assets / "keep.js").is_file()
    assert not (assets / "drop.js").exists()

    monkeypatch.setattr(sdist, "get_file_list", lambda self: None)
    source = SourceDist(Distribution(attrs={"name": "docling-desk"}))
    prefix = "src/docling_desk/resources/static/frontend/assets/"
    source.filelist = SimpleNamespace(
        files=[prefix + "not-referenced.js", prefix + "index.html", "README.md"]
    )
    source.get_file_list()
    assert prefix + "not-referenced.js" not in source.filelist.files
    assert "README.md" in source.filelist.files


def test_cli_passes_the_bind_address_to_uvicorn(monkeypatch):
    seen = {}

    def run(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", run)
    assert main(["--host", "127.0.0.1", "--port", "9"]) == 0
    assert seen["kwargs"]["port"] == 9
    assert seen["kwargs"]["host"] == "127.0.0.1"
    with pytest.raises(SystemExit):
        main(["--port", "nope"])


def test_module_entry_delegates_to_the_cli(monkeypatch):
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr("sys.argv", ["docling-desk"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("docling_desk.__main__", run_name="__main__")
    assert raised.value.code == 0


def _health(monkeypatch, root: Path, free: int, problems):
    frontend = root / "static/frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    data = root / "data"
    data.mkdir()
    monkeypatch.setattr(health.config, "RESOURCES", root)
    monkeypatch.setattr(health.config, "DATA", data)
    monkeypatch.setattr(health.config, "MODELS", root / "models")
    monkeypatch.setattr(health, "model_problems", problems)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(free=free))
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app), data


def test_health_reports_ready_and_each_unavailable_reason(tmp_path, monkeypatch):
    client, data = _health(monkeypatch, tmp_path, 9 * 1024**3, lambda *args: [])
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    (tmp_path / "static/frontend/index.html").unlink()
    data.rmdir()
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(free=1))
    missing = client.get("/health/ready")
    assert missing.status_code == 503
    assert set(missing.json()["reasons"]) >= {"frontend_missing", "data_missing"}
    data.mkdir()
    monkeypatch.setattr(health, "model_problems", lambda *args: ["model_missing"])
    body = client.get("/health/ready").json()
    assert "disk_space" in body["reasons"]
    assert "model_missing" in body["reasons"]
    monkeypatch.setattr(
        health, "model_problems", lambda *args: (_ for _ in ()).throw(ValueError("bad"))
    )
    assert "manifest_invalid" in client.get("/health/ready").json()["reasons"]
    monkeypatch.setattr(
        health, "model_problems", lambda *args: (_ for _ in ()).throw(OSError("io"))
    )
    assert "unreadable" in client.get("/health/ready").json()["reasons"]
