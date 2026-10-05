from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import blob_mirror
from docling_desk import config as desk_config
from docling_desk.blob_mirror import AzureBlobStore, Conflict, Mirror, Missing, Remote


class MemoryStore:
    """In-memory container with the same conditional-write rules as Blob Storage."""

    def __init__(self):
        self.blobs: dict[str, tuple[bytes, str, str]] = {}
        self.version = 0
        self.fail: set[str] = set()

    def _etag(self) -> str:
        self.version += 1
        return f'"v{self.version}"'

    def list(self):
        return {k: Remote(e, len(b), s) for k, (b, e, s) in self.blobs.items()}

    def put(self, key, path, sha256, etag):
        if key in self.fail:
            raise OSError("boom")
        current = self.blobs.get(key)
        if etag is None and current is not None:
            raise Conflict(key)
        if etag is not None and (current is None or current[1] != etag):
            raise Conflict(key)
        new = self._etag()
        self.blobs[key] = (path.read_bytes(), new, sha256)
        return new

    def get(self, key, target):
        if key not in self.blobs:
            raise Missing(key)
        target.write_bytes(self.blobs[key][0])
        return self.blobs[key][1]

    def delete(self, key, etag):
        current = self.blobs.get(key)
        if current is None:
            return
        if current[1] != etag:
            raise Conflict(key)
        del self.blobs[key]


def write(data: Path, key: str, content: bytes) -> Path:
    path = data / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    old = time.time() - 60
    os.utime(path, (old, old))
    return path


@pytest.fixture
def setup(tmp_path):
    store = MemoryStore()
    return tmp_path, store, Mirror(tmp_path, store)


def test_push_uploads_changes_once_and_mirrors_deletes(setup):
    data, store, mirror = setup
    original = write(data, "content/documents/a/original.pdf", b"one")
    write(data, "runtime/knowledge/local.sqlite", b"never mirrored")
    assert mirror.push().uploaded == 1
    assert set(store.blobs) == {"content/documents/a/original.pdf"}
    assert mirror.push().unchanged == 1
    write(data, "content/documents/a/original.pdf", b"two")
    assert mirror.push().uploaded == 1
    assert store.blobs["content/documents/a/original.pdf"][0] == b"two"
    original.unlink()
    write(data, "content/documents/b/original.md", b"keep")
    assert mirror.push().deleted == 1
    assert "content/documents/a/original.pdf" not in store.blobs


def test_derived_is_mirrored_only_when_enabled(tmp_path):
    store = MemoryStore()
    write(tmp_path, "derived/documents/a/rag.jsonl", b"x")
    write(tmp_path, "content/documents/a/original.pdf", b"x")
    Mirror(tmp_path, store).push()
    assert set(store.blobs) == {"content/documents/a/original.pdf"}
    Mirror(tmp_path, store, derived=True).push()
    assert "derived/documents/a/rag.jsonl" in store.blobs


def test_a_blob_changed_elsewhere_is_never_overwritten(setup):
    data, store, mirror = setup
    write(data, "content/documents/a/original.pdf", b"mine")
    mirror.push()
    key = "content/documents/a/original.pdf"
    store.blobs[key] = (b"theirs", store._etag(), "x")
    write(data, key, b"mine, edited")
    report = mirror.push()
    assert report.conflicts == [key]
    assert store.blobs[key][0] == b"theirs"
    assert key in mirror.status()["conflicts"]


def test_existing_identical_blob_is_adopted_and_different_one_conflicts(tmp_path):
    store = MemoryStore()
    same = write(tmp_path, "content/a.md", b"same")
    write(tmp_path, "content/b.md", b"local")
    digest = hashlib.sha256(b"same").hexdigest()
    store.blobs["content/a.md"] = (same.read_bytes(), store._etag(), digest)
    store.blobs["content/b.md"] = (b"remote", store._etag(), "other")
    report = Mirror(tmp_path, store).push()
    assert report.conflicts == ["content/b.md"]
    assert report.uploaded == 0 and report.unchanged == 1
    assert Mirror(tmp_path, store).status()["tracked"] == 1


def test_recent_files_wait_and_failures_are_retried(setup):
    data, store, mirror = setup
    fresh = data / "content/new.md"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"x")
    assert mirror.push().deferred == 1 and not store.blobs
    store.fail.add("content/new.md")
    mirror.settle = 0
    assert mirror.push().errors
    store.fail.clear()
    assert mirror.push().uploaded == 1


def test_missing_local_volume_does_not_delete_blobs(setup):
    data, store, mirror = setup
    write(data, "content/a.md", b"x")
    mirror.push()
    (data / "content/a.md").unlink()
    (data / "content").rmdir()
    report = mirror.push()
    assert report.deleted == 0 and report.errors
    assert "content/a.md" in store.blobs


def test_pull_restores_an_empty_host_and_verifies_hashes(tmp_path):
    store = MemoryStore()
    source = tmp_path / "source"
    write(source, "content/documents/a/original.pdf", b"pdf")
    write(source, "content/manifests/library.json", b"{}")
    Mirror(source, store).push()
    store.blobs["../escape"] = (b"x", store._etag(), "")
    target = tmp_path / "target"
    target.mkdir()
    mirror = Mirror(target, store)
    assert not mirror.has_local_content()
    assert mirror.pull().downloaded == 2
    assert (target / "content/documents/a/original.pdf").read_bytes() == b"pdf"
    assert not (tmp_path / "escape").exists()
    assert mirror.push().unchanged == 2  # restored files are already in sync
    store.blobs["content/bad.md"] = (b"x", store._etag(), "wrong")
    assert mirror.pull().errors and not (target / "content/bad.md").exists()


class FakeContainer:
    def __init__(self):
        self.calls = []

    def list_blobs(self, name_starts_with=None, include=None):
        class Blob:
            name, etag, size, metadata = "site/content/a.md", '"e"', 3, {"sha256": "abc"}

        return [Blob()]


def test_azure_store_applies_the_prefix():
    store = AzureBlobStore(FakeContainer(), "site")
    assert store.list() == {"content/a.md": Remote('"e"', 3, "abc")}


def test_config_requires_a_container(monkeypatch):
    import importlib

    monkeypatch.setenv("DOCLING_STORAGE", "azure-blob")
    monkeypatch.delenv("DOCLING_BLOB_CONTAINER", raising=False)
    with pytest.raises(RuntimeError):
        importlib.reload(desk_config)
    monkeypatch.setenv("DOCLING_STORAGE", "elsewhere")
    with pytest.raises(RuntimeError):
        importlib.reload(desk_config)
    monkeypatch.delenv("DOCLING_STORAGE")
    importlib.reload(desk_config)


def test_app_restores_then_mirrors_and_reports_status(tmp_path, monkeypatch):
    store = MemoryStore()
    seed = tmp_path / "seed"
    write(seed, "content/manifests/library.json", b'{"folders": []}')
    Mirror(seed, store).push()
    data = tmp_path / "data"
    monkeypatch.setattr(desk_config, "DATA", data)
    monkeypatch.setattr(desk_config, "STORAGE_BACKEND", "azure-blob")
    monkeypatch.setattr(blob_mirror.AzureBlobStore, "from_config", classmethod(lambda cls: store))
    with TestClient(web.app) as client:
        assert (data / "content/manifests/library.json").is_file()
        assert client.get("/api/storage").json()["backend"] == "azure-blob"
    monkeypatch.setattr(desk_config, "STORAGE_BACKEND", "local")
    assert TestClient(web.app).get("/api/storage").json() == {"backend": "local"}


def explanation_db(data: Path, ident: str, text: str) -> Path:
    import sqlite3

    path = data / "runtime" / "documents" / ident / "explanation.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("create table if not exists note (body text)")
        db.execute("delete from note")
        db.execute("insert into note values (?)", (text,))
    old = time.time() - 60
    os.utime(path, (old, old))
    return path


def test_job_records_and_explanations_are_mirrored_and_restored(tmp_path):
    import sqlite3

    store = MemoryStore()
    source = tmp_path / "source"
    write(source, "content/documents/a/original.pdf", b"pdf")
    write(source, "derived/documents/a/translations/en/1.json", b"{}")
    write(source, "runtime/documents/a/job.json", b'{"state": "success"}')
    explanation_db(source, "a", "解説")
    write(source, "runtime/knowledge/local.sqlite", b"rebuilt from content")
    Mirror(source, store, derived=True).push()
    assert set(store.blobs) == {
        "content/documents/a/original.pdf",
        "derived/documents/a/translations/en/1.json",
        "runtime/documents/a/job.json",
        "runtime/documents/a/explanation.sqlite",
    }
    target = tmp_path / "target"
    target.mkdir()
    Mirror(target, store, derived=True).pull()
    restored = target / "runtime/documents/a/explanation.sqlite"
    with sqlite3.connect(restored) as db:
        assert db.execute("select body from note").fetchone() == ("解説",)
    assert (target / "runtime/documents/a/job.json").read_bytes() == b'{"state": "success"}'


def test_changed_explanations_are_uploaded_again(tmp_path):
    store = MemoryStore()
    mirror = Mirror(tmp_path, store)
    explanation_db(tmp_path, "a", "one")
    assert mirror.push().uploaded == 1
    assert mirror.push().unchanged == 1
    explanation_db(tmp_path, "a", "two")
    assert mirror.push().uploaded == 1


def test_deleted_documents_are_removed_before_manifests_are_updated(tmp_path):
    store = MemoryStore()
    mirror = Mirror(tmp_path, store)
    write(tmp_path, "content/documents/a/original.pdf", b"pdf")
    write(tmp_path, "content/manifests/library.json", b'{"a": 1}')
    mirror.push()
    order: list[str] = []
    real_delete, real_put = store.delete, store.put
    store.delete = lambda key, etag: (order.append(f"delete {key}"), real_delete(key, etag))[1]
    store.put = lambda key, path, sha, etag: (
        order.append(f"put {key}"),
        real_put(key, path, sha, etag),
    )[1]
    (tmp_path / "content/documents/a/original.pdf").unlink()
    write(tmp_path, "content/manifests/library.json", b"{}")
    mirror.push()
    assert order == [
        "delete content/documents/a/original.pdf",
        "put content/manifests/library.json",
    ]
    restored = tmp_path / "restored"
    restored.mkdir()
    Mirror(restored, store).pull()
    assert not (restored / "content/documents/a/original.pdf").exists()


def test_manifests_are_stored_after_what_they_reference(tmp_path):
    store = MemoryStore()
    write(tmp_path, "content/manifests/wiki.json", b"{}")
    write(tmp_path, "content/wiki/revisions/r1/x.md", b"# x")
    write(tmp_path, "content/documents/a/original.pdf", b"pdf")
    order: list[str] = []
    real_put = store.put
    store.put = lambda key, path, sha, etag: (order.append(key), real_put(key, path, sha, etag))[1]
    Mirror(tmp_path, store).push()
    assert order[-1] == "content/manifests/wiki.json"


def test_interrupted_restore_is_resumed_and_reported(tmp_path):
    store = MemoryStore()
    source = tmp_path / "source"
    write(source, "content/documents/a/original.pdf", b"pdf")
    write(source, "content/documents/b/original.pdf", b"pdf2")
    Mirror(source, store).push()
    broken = store.get
    store.get = lambda key, target: (
        (_ for _ in ()).throw(OSError("net")) if "/b/" in key else broken(key, target)
    )
    target = tmp_path / "target"
    target.mkdir()
    mirror = Mirror(target, store)
    assert mirror.pull().errors
    assert mirror.restore_incomplete() and mirror.status()["restore_incomplete"]
    store.get = broken
    assert mirror.pull().downloaded == 1
    assert not mirror.restore_incomplete()
    assert (target / "content/documents/b/original.pdf").read_bytes() == b"pdf2"
