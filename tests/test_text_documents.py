import hashlib
import json
from types import SimpleNamespace

import pytest
from docling_core.types.doc import DoclingDocument
from fastapi.testclient import TestClient
from lxml import html

import docling_desk.app as web
import docling_desk.documents.conversion as service
from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, validate_input
from docling_desk.documents.tables import project_tables
from docling_desk.explanation.source import snapshot
from docling_desk.translation.source import source_map
from docling_desk.translation.store import atomic_json, result_path

MARKDOWN = "# 日本語の見出し\n\n本文と **強調**。\n\n| 区分 | 件数 |\n| --- | --- |\n| A | 120 |\n"
PLAIN = "# 見出しではない\n  字下げと *記号* & <script>alert(1)</script>\n\n\n次の本文。\n"


@pytest.fixture(params=[".md", ".markdown", ".txt", ".text"])
def document(request, tmp_path):
    folder = tmp_path / ("e" * 32)
    folder.mkdir()
    suffix = request.param
    text = MARKDOWN if suffix in {".md", ".markdown"} else PLAIN
    source = folder / ("original" + suffix)
    source.write_text(text, encoding="utf-8")
    job = Job(id=folder.name, filename="sample" + suffix)
    service.convert_job(folder, job)
    assert job.state == "success", job.error
    return folder, job, text


def test_extraction_preserves_literal_text_or_markdown_structure(document):
    folder, job, text = document
    assert job.pages == 0 and job.preview == "text-preview.html"
    assert not job.slide_layout
    doc = DoclingDocument.load_from_json(folder / "document.json")
    chunks = [json.loads(line) for line in (folder / "rag.jsonl").read_text().splitlines()]
    assert chunks and all(
        c["source_sha256"]
        == hashlib.sha256(
            (folder / ("original" + job.filename[job.filename.rfind(".") :])).read_bytes()
        ).hexdigest()
        for c in chunks
    )
    if job.filename.endswith((".md", ".markdown")):
        assert job.tables == 1
        table = project_tables(doc, job.filename)[0]
        assert table.source_label == "文書全体"
        assert table.rows == [["区分", "件数"], ["A", "120"]]
        assert any(item.label.value in {"title", "section_header"} for item in doc.texts)
    else:
        assert job.tables == 0
        assert (folder / "text.txt").read_text() == text
        tree = html.fromstring((folder / job.preview).read_text())
        assert tree.xpath('//pre[@class="plain-text"]/text()') == [text]
        assert not tree.xpath("//script")
        assert "*記号*" in "".join(c["text"] for c in chunks)


def test_document_preview_translation_explanation_and_download(document, monkeypatch):
    folder, job, text = document
    monkeypatch.setattr(desk_config, "DATA", folder.parent)
    client = TestClient(web.app)
    url = f"/view/{job.id}/document"
    response = client.get(url)
    assert response.status_code == 200
    tree = html.fromstring(response.text)
    assert tree.xpath("//body/@data-unit") == ["document-1"]
    assert not tree.xpath("//iframe|//@onload")
    assert tree.xpath("//script/@src") == ["/static/document-frame.js", "/static/file-drop.js"]
    assert "sandbox allow-scripts" in response.headers["content-security-policy"]
    source = source_map(folder)
    assert source["unlocated_count"] == 0 and len(source["units"]) == 1
    unit = source["units"][0]
    assert unit["mode"] == "replace" and unit["kind"] == "document"
    atomic_json(
        result_path(folder, "en", unit["id"]),
        {
            "unit_id": unit["id"],
            "state": "completed",
            "result": {
                "source_hash": unit["source_hash"],
                "translations": {
                    s["id"]: "Translated " + s["source_text"] for s in unit["segments"]
                },
            },
        },
    )
    translated = client.get(url + "?language=en")
    assert translated.headers["x-translation-state"] == "translated"
    assert "Translated " in translated.text
    assert client.get(url + "?language=invalid").status_code == 422
    explanation = snapshot(folder)
    assert explanation["unlocated_count"] == 0 and len(explanation["units"]) == 1
    assert explanation["units"][0]["blocks"]
    original = folder / ("original" + job.filename[job.filename.rfind(".") :])
    assert client.get(f"/files/{job.id}/{original.name}?download=true").content == text.encode()


@pytest.mark.parametrize("suffix", [".MD", ".markdown", ".TXT", ".text"])
def test_upload_text_files_and_keep_destination(suffix, tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    with TestClient(web.app) as client:
        client.app.state.worker = SimpleNamespace(submit=lambda fn, *args: fn(*args))
        folder = client.post("/api/folders", json={"name": "Text", "parent_id": None}).json()
        response = client.post(
            "/api/upload",
            files={"file": ("sample" + suffix, MARKDOWN.encode())},
            data={"folder_id": folder["id"]},
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["state"] == "success" and job["folder_id"] == folder["id"]


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp932"])
def test_japanese_encodings(encoding, tmp_path):
    source = tmp_path / "original.txt"
    source.write_bytes("日本語\r\n本文。".encode(encoding))
    validate_input(source)
    job = Job(id="e" * 32, filename="sample.txt")
    service.convert_job(tmp_path, job)
    assert job.state == "success", job.error
    assert "日本語" in (tmp_path / "text.txt").read_text()


def test_binary_text_upload_is_rejected_and_cleaned_up(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    with TestClient(web.app) as client:
        response = client.post("/api/upload", files={"file": ("binary.txt", b"hello\x00\x01")})
        assert response.status_code == 422 and "バイナリ" in response.text
        assert not list(tmp_path.glob("*/original*"))


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_empty_text_document(suffix, tmp_path):
    (tmp_path / ("original" + suffix)).write_bytes(b"")
    job = Job(id="e" * 32, filename="empty" + suffix)
    service.convert_job(tmp_path, job)
    assert job.state == "success", job.error
    assert job.chunks == 0 and job.preview


def test_markdown_does_not_fetch_images_or_execute_embedded_html(tmp_path, monkeypatch):
    from docling.backend.utils.image_resource_loader import ImageResourceLoader

    def reject_fetch(*args, **kwargs):
        pytest.fail("Markdown must not fetch local or remote images")

    monkeypatch.setattr(ImageResourceLoader, "load_image_ref", reject_fetch)
    folder = tmp_path / ("e" * 32)
    folder.mkdir()
    (folder / "original.md").write_text(
        "# Title\n\n![remote](https://example.test/image.png)\n\n"
        "![local](/tmp/private.png)\n\n"
        '<p onclick="alert(1)">本文</p><script>alert(1)</script>\n',
        encoding="utf-8",
    )
    job = Job(id=folder.name, filename="sample.md")
    service.convert_job(folder, job)
    assert job.state == "success", job.error
    monkeypatch.setattr(desk_config, "DATA", folder.parent)
    tree = html.fromstring(TestClient(web.app).get(f"/view/{job.id}/document").text)
    assert not tree.xpath("//@onclick|//script[not(@src)]|//img[starts-with(@src, 'http')]")
