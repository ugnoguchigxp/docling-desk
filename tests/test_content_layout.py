"""Storage behavior across migration, index loss and filesystem publication failures."""

import hashlib
import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from docling_desk import config
from docling_desk.app import create_app
from docling_desk.documents.conversion import Job, save_job
from docling_desk.documents.text import SUPPORTED_SUFFIXES
from docling_desk.knowledge.store import Store
from docling_desk.operations.backup import backup_local, exclusive_write, restore_local
from docling_desk.operations.faults import Fault
from docling_desk.storage import (
    document_folder,
    document_folders,
    job_file,
    knowledge_database,
    migrate_database,
    migrate_layout,
    original_file,
)


def migrate(data):
    with exclusive_write(data, "migration"):
        migrate_layout(data)


def legacy_document(data, identifier="a" * 32):
    folder = data / identifier
    folder.mkdir(parents=True)
    save_job(folder, Job(id=identifier, filename="資料.pdf", state="success"))
    (folder / "original.pdf").write_bytes(b"%PDF-1.4 original bytes")
    (folder / "rag.jsonl").write_text(
        json.dumps({"id": identifier + ":pdf:1", "text": "検索用の本文", "pages": [1]})
    )
    return folder


def test_migration_keeps_original_bytes_ids_and_urls(tmp_path, monkeypatch):
    old = legacy_document(tmp_path)
    expected = hashlib.sha256((old / "original.pdf").read_bytes()).hexdigest()
    (tmp_path / "library.json").write_text('{"folders":{},"files":{}}')
    (old / "explanations").mkdir()
    with sqlite3.connect(old / "explanations/index.sqlite") as db:
        db.execute("CREATE TABLE sample (value TEXT)")
        db.execute("INSERT INTO sample VALUES ('preserved')")
    monkeypatch.setattr(config, "DATA", tmp_path)
    with TestClient(create_app()) as client:
        folder = document_folder(tmp_path, old.name)
        source = original_file(folder, ".pdf")
        assert source == tmp_path / "content/documents" / old.name / "original.pdf"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
        assert not old.exists()
        assert not (folder / "job.json").exists()
        assert job_file(folder).is_relative_to(tmp_path / "runtime")
        assert client.get(f"/files/{old.name}/original.pdf").content == source.read_bytes()
        assert client.get(f"/api/jobs/{old.name}").json()["id"] == old.name
        assert (
            client.get(f"/files/{old.name}/../../runtime/documents/{old.name}/job.json").status_code
            == 404
        )
        assert not list((tmp_path / "content").rglob("*.sqlite"))
        assert not list((tmp_path / "derived").rglob("*.sqlite"))
        with sqlite3.connect(
            tmp_path / "runtime/documents" / old.name / "explanation.sqlite"
        ) as db:
            assert db.execute("SELECT value FROM sample").fetchone() == ("preserved",)
    migrate(tmp_path)
    assert document_folders(tmp_path) == [folder]


def test_wiki_files_rebuild_without_database_and_keep_metadata(tmp_path):
    store = Store(tmp_path)
    raw = "---\nlanguage: en\ntranslation_group: guide\n---\n# Guide\n\n日本語の本文 [別記事](other.md)"
    article = store.import_wiki("分類", "manual/guide.md", raw)
    other = store.import_wiki("分類", "manual/other.md", "# 別記事\n\n参照先")
    csv = store.import_wiki("分類", "index.csv", "title,path\nGuide,manual/guide.md\n")
    removed = store.import_wiki("分類", "deleted.md", "# 削除する記事")
    store.delete_wiki(removed["id"])
    records = store.files.load()
    assert store.files.read(records[article["id"]]) == raw
    shutil.rmtree(tmp_path / "runtime")
    restored = Store(tmp_path)
    source = restored.source(article["id"])
    assert source["body"] == article["body"]
    assert source["language"] == "en"
    assert source["translation_group"] == "guide"
    assert {s["id"] for s in restored.sources("wiki")} == {article["id"], other["id"], csv["id"]}
    assert restored.source(removed["id"]) is None
    assert {c["id"] for c in restored.chunks()} == {c["id"] for c in store.chunks()}
    assert knowledge_database(tmp_path).is_file()
    assert not list((tmp_path / "content").rglob("*.sqlite"))


def test_failed_multi_article_import_keeps_published_files_and_index(tmp_path, monkeypatch):
    store = Store(tmp_path)
    original = store.import_wiki("Wiki", "a.md", "# 元の記事")
    manifest = store.files.manifest.read_bytes()
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "wiki_between_articles")
    with pytest.raises(Fault):
        store.import_many(
            "Wiki", [{"path": "a.md", "body": "# 更新"}, {"path": "b.md", "body": "# 新規"}]
        )
    monkeypatch.delenv("DOCLING_QUALITY_FAULT")
    assert store.files.manifest.read_bytes() == manifest
    assert Store(tmp_path).sources("wiki") == [original]


def test_published_files_recover_when_database_transaction_rolls_back(tmp_path, monkeypatch):
    store = Store(tmp_path)
    original = store.import_wiki("Wiki", "a.md", "# 元の記事")
    publish = store.files.publish

    def crash(records):
        publish(records)
        raise OSError("stopped after content publication")

    monkeypatch.setattr(store.files, "publish", crash)
    with pytest.raises(OSError):
        store.import_wiki("Wiki", "a.md", "# 更新済みの本文")
    assert Store(tmp_path).source(original["id"])["body"] == "# 更新済みの本文"


def test_new_layout_backup_restore_and_document_copy_delete(tmp_path, monkeypatch):
    data = tmp_path / "data"
    old = legacy_document(data)
    migrate(data)
    article = Store(data).import_wiki("Wiki", "guide.md", "# 保存する本文")
    monkeypatch.setattr(config, "DATA", data)
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/library/operations",
            json={"action": "copy", "items": [{"kind": "file", "id": old.name}]},
        )
        assert response.status_code == 200, response.text
        copied = response.json()["items"][0]["id"]
        source = original_file(document_folder(data, copied), ".pdf")
        assert (
            source.read_bytes()
            == original_file(document_folder(data, old.name), ".pdf").read_bytes()
        )
        assert job_file(document_folder(data, copied)).is_file()
        archive = tmp_path / "backup"
        backup_local(data, archive)
        restored = tmp_path / "restored"
        restore_local(archive, restored)
        assert Store(restored).source(article["id"])["body"] == article["body"]
        assert (
            original_file(document_folder(restored, copied), ".pdf").read_bytes()
            == source.read_bytes()
        )
        response = client.post(
            "/api/library/operations",
            json={"action": "delete", "items": [{"kind": "file", "id": copied}]},
        )
        assert response.status_code == 200
        assert not source.exists()
        assert not (data / "runtime/documents" / copied).exists()
        assert not (data / "derived/documents" / copied).exists()


def test_migration_refuses_conflicting_original_without_overwrite(tmp_path):
    old = legacy_document(tmp_path)
    target = tmp_path / "content/documents" / old.name / "original.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"different document")
    with pytest.raises(ValueError, match="異なる内容"):
        migrate(tmp_path)
    assert target.read_bytes() == b"different document"
    assert (
        document_folder(tmp_path, old.name) / "original.pdf"
    ).read_bytes() == b"%PDF-1.4 original bytes"


def test_crlf_markdown_preserves_bytes_after_restart(tmp_path):
    raw = "---\r\nlanguage: en\r\n---\r\n# Windows\r\n\r\n本文\r\n"
    store = Store(tmp_path)
    article = store.import_wiki("Wiki", "guide.md", raw)
    assert store.files.read(store.files.load()[article["id"]]) == raw
    assert Store(tmp_path).source(article["id"])["body"] == article["body"]


def test_database_migration_resumes_after_publishing_snapshot(tmp_path, monkeypatch):
    from pathlib import Path

    source, target = tmp_path / "old.sqlite", tmp_path / "runtime/new.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES ('preserved')")
    unlink = Path.unlink

    def stop(path, *args, **kwargs):
        if path == source:
            raise OSError("stopped after publishing database")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", stop)
        with pytest.raises(OSError):
            migrate_database(source, target)
    assert target.exists() and source.exists()
    migrate_database(source, target)
    assert not source.exists()
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT value FROM sample").fetchone() == ("preserved",)


def test_backup_preserves_explicit_wiki_articles_with_credential_words(tmp_path):
    data = tmp_path / "data"
    store = Store(data)
    article = store.import_wiki("Wiki", "password.md", "# パスワードの設定方法")
    backup_local(data, tmp_path / "backup")
    restore_local(tmp_path / "backup", tmp_path / "restored")
    assert Store(tmp_path / "restored").source(article["id"])["body"] == article["body"]


@pytest.mark.parametrize("suffix", sorted(SUPPORTED_SUFFIXES))
def test_original_download_preserves_each_supported_format(tmp_path, monkeypatch, suffix):
    identifier = "b" * 32
    folder = tmp_path / identifier
    folder.mkdir()
    save_job(folder, Job(id=identifier, filename="original-name" + suffix, state="success"))
    raw = b"original bytes for " + suffix.encode()
    (folder / ("original" + suffix)).write_bytes(raw)
    monkeypatch.setattr(config, "DATA", tmp_path)
    with TestClient(create_app()) as client:
        response = client.get(f"/files/{identifier}/original{suffix}?download=true")
        assert response.status_code == 200
        assert response.content == raw
        manifest = json.loads(
            (tmp_path / "content/documents" / identifier / "manifest.json").read_text()
        )
        assert manifest["sha256"] == hashlib.sha256(raw).hexdigest()
