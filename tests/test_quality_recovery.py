import hashlib
import importlib.util
import json
import os
import threading
from pathlib import Path

import pytest

from docling_desk.documents.conversion import Job, save_job
from docling_desk.knowledge.store import Store
from docling_desk.operations.backup import (
    backup_local,
    exclusive_write,
    restore_local,
    writer_status,
)
from docling_desk.operations.fault import QualityFault
from docling_desk.operations.supply import inventory

ROOT = Path(__file__).resolve().parents[1]


def article(store: Store, path: str = "guide.md", body: str = "# 案内\n\n共通の文です。") -> str:
    return store.import_wiki("Wiki", path, body)["id"]


def document(data, text: str, job_id: str = "a" * 32):
    folder = data / job_id
    folder.mkdir()
    save_job(folder, Job(id=job_id, filename="資料.pdf", state="success", pages=1))
    original = folder / "original.pdf"
    original.write_bytes(b"%PDF-1.4\n")
    parent = {
        "id": job_id + ":p",
        "text": text,
        "pages": [1],
        "refs": ["#/texts/1"],
        "headings": ["章"],
        "unit": "ページ 1",
    }
    (folder / "rag.jsonl").write_text(json.dumps(parent, ensure_ascii=False), encoding="utf-8")
    (folder / "rag-index.jsonl").write_text(
        json.dumps(
            {**parent, "parent_id": parent["id"], "id": parent["id"] + ":0"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return original


def test_wiki_batch_crash_rolls_back_every_article(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "wiki_between_articles")
    with pytest.raises(QualityFault):
        store.import_many(
            "Wiki",
            [
                {"path": "a.md", "body": "# A\n\n一つ目"},
                {"path": "b.md", "body": "# B\n\n二つ目"},
            ],
        )
    assert Store(tmp_path).sources("wiki") == []


def test_crash_before_wiki_commit_publishes_nothing(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "wiki_import_before_commit")
    with pytest.raises(QualityFault):
        article(store)
    assert Store(tmp_path).sources("wiki") == []


def test_crash_after_wiki_commit_keeps_the_article(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "wiki_import_after_commit")
    with pytest.raises(QualityFault):
        article(store, body="# 案内\n\n保存済み")
    restored = Store(tmp_path)
    assert restored.sources("wiki")[0]["body"] == "# 案内\n\n保存済み"


def test_delete_crash_keeps_the_searchable_article(tmp_path, monkeypatch):
    store = Store(tmp_path)
    source_id = article(store)
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "wiki_delete_before_chunks")
    with pytest.raises(QualityFault):
        store.delete_wiki(source_id)
    restored = Store(tmp_path)
    assert restored.source(source_id)["body"].endswith("共通の文です。")
    assert restored.chunks()


def test_index_crash_keeps_the_published_revision_and_original(tmp_path, monkeypatch):
    original = document(tmp_path, "公開済みの本文")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    Store(tmp_path).sync_documents()
    document_again = tmp_path / ("a" * 32)
    parent = {
        "id": "a" * 32 + ":p",
        "text": "未公開の新しい本文",
        "pages": [1],
        "refs": ["#/texts/1"],
        "headings": ["章"],
        "unit": "ページ 1",
    }
    (document_again / "rag.jsonl").write_text(
        json.dumps(parent, ensure_ascii=False), encoding="utf-8"
    )
    (document_again / "rag-index.jsonl").write_text(
        json.dumps(
            {**parent, "parent_id": parent["id"], "id": parent["id"] + ":0"}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "index_after_unpublish")
    with pytest.raises(QualityFault):
        Store(tmp_path).sync_documents()
    restored = Store(tmp_path)
    texts = [chunk["text"] for chunk in restored.chunks()]
    assert texts == ["公開済みの本文"]
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest


def test_backup_restore_keeps_wiki_deletion_and_skips_secrets(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    store = Store(data)
    kept = article(store, "keep.md", "# 残す\n\n本文A")
    removed = article(store, "drop.md", "# 消す\n\n本文B")
    store.delete_wiki(removed)
    original = document(data, "資料の本文")
    Store(data).sync_documents()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret-token", encoding="utf-8")
    (data / "leak.txt").symlink_to(secret)
    (data / ".env").write_text("KEY=secret-token", encoding="utf-8")
    (data / "api_key.txt").write_text("secret-token", encoding="utf-8")
    wal = data / "runtime" / "knowledge" / "local.sqlite-wal"
    wal.write_text("secret-token-wal", encoding="utf-8")
    archive = tmp_path / "archive"
    manifest = backup_local(data, archive)
    copied = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in archive.rglob("*")
        if path.is_file()
    )
    assert "secret-token" not in copied
    assert not list(archive.rglob("*.sqlite-wal"))
    assert {item["id"] for item in manifest["wiki"]} == {kept, removed}
    destination = tmp_path / "restored"
    restore_local(archive, destination)
    restored = Store(destination)
    assert restored.source(kept)["body"].endswith("本文A")
    assert restored.source(removed) is None
    assert any(chunk["text"] == "資料の本文" for chunk in restored.chunks())
    assert (
        hashlib.sha256((destination / ("a" * 32) / "original.pdf").read_bytes()).hexdigest()
        == hashlib.sha256(original.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="空である"):
        restore_local(archive, destination)
    forged = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    forged["wiki"].append(
        {"id": "extra", "path": "x.md", "revision": "ab", "deleted": 0, "body_sha256": "cd"}
    )
    (archive / "manifest.json").write_text(json.dumps(forged), encoding="utf-8")
    rejected = tmp_path / "rejected"
    with pytest.raises(ValueError, match="一致しません"):
        restore_local(archive, rejected)
    assert not rejected.exists()


def test_backup_skips_sidecars_and_a_failed_restore_leaves_nothing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    Store(data).import_wiki("Wiki", "a.md", "# A\n\n本文")
    (data / "runtime" / "knowledge" / "local.sqlite-wal").write_bytes(b"stale-wal")
    (data / "api-key.txt").write_text("secret-token", encoding="utf-8")
    (data / "keyboard.md").write_text("通常の文書", encoding="utf-8")
    archive = tmp_path / "archive"
    backup_local(data, archive)
    names = [path.name for path in archive.rglob("*") if path.is_file()]
    assert "local.sqlite-wal" not in names
    assert "api-key.txt" not in names
    assert "keyboard.md" in names
    (archive / "knowledge.sqlite").write_bytes(b"broken")
    destination = tmp_path / "partial"
    with pytest.raises(ValueError):
        restore_local(archive, destination)
    assert not destination.exists()


def test_backup_keeps_ordinary_filenames_in_a_spaced_directory(tmp_path):
    data = tmp_path / "my data"
    data.mkdir()
    (data / "keyboard.txt").write_text("keep-me", encoding="utf-8")
    (data / "key-findings.md").write_text("keep-findings", encoding="utf-8")
    (data / "password-policy.md").write_text("keep-policy", encoding="utf-8")
    (data / "api_key.txt").write_text("secret-token", encoding="utf-8")
    Store(data).import_wiki("Wiki", "a.md", "# A\n\n本文")
    archive = tmp_path / "archive out"
    backup_local(data, archive)
    names = [path.name for path in (archive / "files").rglob("*") if path.is_file()]
    assert "keyboard.txt" in names
    assert "key-findings.md" in names
    assert "password-policy.md" in names
    assert "api_key.txt" not in names
    destination = tmp_path / "restored data"
    restore_local(archive, destination)
    assert (destination / "keyboard.txt").read_text(encoding="utf-8") == "keep-me"
    assert Store(destination).sources(kind="wiki")


def test_library_save_removes_the_temporary_file_when_replace_is_interrupted(tmp_path, monkeypatch):
    from docling_desk.documents.library import Library, save
    from docling_desk.operations.faults import Fault

    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "library_save_before_replace")
    with pytest.raises(Fault):
        save(tmp_path, Library())
    assert not (tmp_path / "library.tmp").exists()
    assert not (tmp_path / "library.json").exists()


def test_synthetic_fixture_uses_repository_documents_only(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "synthetic_data", ROOT / "qa/frontend-migration/synthetic_data.py"
    )
    assert spec and spec.loader
    synthetic_data = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synthetic_data)
    synthetic_data.materialize(tmp_path, synthetic=True)
    page = tmp_path / ("9" * 32) / "job.json"
    assert json.loads(page.read_text(encoding="utf-8"))["filename"] == "synthetic-report.pdf"
    glyph = (tmp_path / ("a" * 32) / "preview" / "slide-1.html").read_text(encoding="utf-8")
    assert "PT.合成" in glyph and "<text" in glyph
    assert (tmp_path / ("b" * 32) / "original.xlsx").is_file()
    assert (tmp_path / ("a" * 32) / "original.pptx").is_file()
    assert not (tmp_path / "cca5aede53b04a63b4f165b1c72794b2").exists()
    assert not (tmp_path / "library.json").exists()


def test_backup_follows_the_lock_not_a_stale_owner_record(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "note.txt").write_text("kept", encoding="utf-8")
    (data / ".write-lock").write_text(
        json.dumps({"pid": os.getpid(), "purpose": "convert"}), encoding="utf-8"
    )
    assert writer_status(data)["state"] == "idle"
    archive = tmp_path / "archive"
    backup_local(data, archive)
    assert (archive / "files" / "note.txt").read_text(encoding="utf-8") == "kept"
    started = threading.Event()
    release = threading.Event()

    def hold():
        with exclusive_write(data, "index"):
            started.set()
            assert release.wait(2)

    worker = threading.Thread(target=hold)
    worker.start()
    assert started.wait(2)
    assert worker.is_alive()
    assert writer_status(data)["purpose"] == "index"
    with pytest.raises(ValueError, match="拒否"):
        backup_local(data, tmp_path / "during")
    release.set()
    worker.join()
    assert writer_status(data)["state"] == "idle"


def test_backup_refuses_until_conversion_reaches_a_consistent_generation(tmp_path, monkeypatch):
    import docling_desk.documents.conversion as service
    from docling_desk.documents.conversion import Job

    data = tmp_path / "data"
    folder = data / ("a" * 32)
    folder.mkdir(parents=True)
    (folder / "original.txt").write_text("資料の本文\n", encoding="utf-8")
    seen = []

    def spy(name):
        if not name.startswith("convert_"):
            return
        seen.append(name)
        archive = tmp_path / f"partial-{len(seen)}"
        with pytest.raises(ValueError, match="拒否"):
            backup_local(data, archive)
        assert not archive.exists()

    monkeypatch.setattr(service, "checkpoint", spy)
    service.convert_job(folder, Job(id=folder.name, filename="sample.txt"))
    assert seen == [
        "convert_after_document",
        "convert_after_rag",
        "convert_before_job_success",
    ]
    Store(data).sync_documents()
    archive = tmp_path / "archive"
    backup_local(data, archive)
    destination = tmp_path / "restored"
    restore_local(archive, destination)
    restored = Store(destination)
    chunk = next(item for item in restored.chunks() if "資料の本文" in item["text"])
    assert "資料の本文" in restored.context(chunk["id"])
    with restored.connection() as db:
        queued = db.execute(
            "SELECT count(*) FROM tasks WHERE state IN ('queued','running')"
        ).fetchone()[0]
    assert queued == 0


def test_supply_inventory_does_not_clear_unknown_licenses():
    report = inventory(ROOT)
    names = {item["name"] for item in report["packages"]}
    assert "fastapi" in names
    assert "hono" in names
    assert report["models"]
    assert all(item["cleared"] is False for item in report["packages"] + report["models"])
    fastapi = next(item for item in report["packages"] if item["name"] == "fastapi")
    assert fastapi["version"] and fastapi["ecosystem"] == "pypi"
    indirect = next(item for item in report["packages"] if item["name"] == "@adobe/css-tools")
    assert indirect["version"] == "4.5.0"
    assert indirect["direct"] is False
    assert indirect["scope"] == "development"
    hono = next(item for item in report["packages"] if item["name"] == "hono")
    assert hono["version"] == "4.12.8" and hono["scope"] == "runtime"
    models = {item["name"]: item for item in report["assets"] if item["kind"] == "model"}
    assert set(models) == {
        "docling-project/docling-layout-heron",
        "docling-project/docling-models",
    }
    assert all(
        item["cleared"] is False and item["license"] == "unknown" for item in models.values()
    )
    names = {item["name"] for item in report["assets"]}
    assert {"libreoffice-writer", "fonts-noto-cjk"} <= names
    fixture = next(item for item in report["assets"] if item["kind"] == "fixture")
    assert fixture["redistributable"] is True and fixture["cleared"] is False
    from docling_desk.operations.supply import assess_advisory

    missed = assess_advisory(
        report,
        {"id": "TEST-ABSENT", "ecosystem": "npm", "name": "not-a-package", "versions": ["1"]},
    )
    assert missed["applies"] is False
    hit = assess_advisory(
        report,
        {"id": "TEST-CSS", "ecosystem": "npm", "name": "@adobe/css-tools", "versions": ["4.5.0"]},
    )
    assert hit["applies"] is True
    assert hit["scopes"] == ["development"]
    assert hit["decision"]["status"] == "unreviewed"
