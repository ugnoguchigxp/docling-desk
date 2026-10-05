"""Coverage for viewer rendering, document conversion, and the knowledge worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import types
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pymupdf
import pytest
from docling_azure_ocr.config import OcrError, OcrProfile
from fastapi import HTTPException
from fastapi.testclient import TestClient

from docling_desk.documents import conversion
from docling_desk.documents.conversion import (
    Element,
    Job,
    _convert_job,
    convert_job,
    job_converter,
    make_sheet_preview,
    office_preview,
    save_job,
    validate_input,
)
from docling_desk.documents.text import plain_document
from docling_desk.knowledge import worker as worker_mod
from docling_desk.knowledge.worker import (
    PrivateRequests,
    app_factory,
    create_worker,
    profile_id,
    read_jsonl,
)
from docling_desk.viewer import render

TOKEN = "private-worker-token-" * 3
PREFIX = "/viewer/session/" + ("a" * 43) + "/"


def _job(
    folder: Path,
    filename="notes.txt",
    pages=2,
    state="success",
    preview="preview/index.html",
    slide=False,
):
    folder.mkdir(parents=True, exist_ok=True)
    job = Job(
        id="job-1",
        filename=filename,
        original_filename=filename,
        folder_id="folder",
        state=state,
        pages=pages,
        tables=1,
        pictures=0,
        chunks=1,
        search_chunks=1,
        rag_policy="v1",
        error=None,
        preview=preview,
        slide_layout=slide,
    )
    (folder / "job.json").write_text(job.model_dump_json(), encoding="utf-8")
    return job


def test_render_manifest_views_and_files(tmp_path, monkeypatch):
    folder = tmp_path / "attempt"
    with pytest.raises(HTTPException):
        render.render_resource(folder, "manifest", "/viewer/session/short/")
    _job(folder)
    with pytest.raises(HTTPException) as exc:
        render.render_resource(folder, "../secret", PREFIX)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        render.render_resource(folder, "a\\b", PREFIX)
    (folder / "job.json").write_text("{", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        render.render_resource(folder, "manifest", PREFIX)
    assert exc.value.status_code == 404
    _job(folder, state="running", preview=None)
    with pytest.raises(HTTPException) as exc:
        render.render_resource(folder, "manifest", PREFIX)
    assert exc.value.status_code == 409

    _job(folder, filename="notes.pdf", pages=2)
    body = render.render_resource(folder, "manifest?x=1", PREFIX)
    assert body["kind"] == "page" and body["units"] == 2

    html = "<html><head></head><body><a href='/static/app.css' style=\"background:url('/files/job-1/a.png')\">text /static/keep</a><style>body{background:url(/view/job-1/x)}</style></body></html>"
    monkeypatch.setattr(render, "ensure_pdf_preview", lambda source, folder: "p")
    monkeypatch.setattr(render, "pdf_pages", lambda source: [{"number": 1}])
    monkeypatch.setattr(render, "pdf_html", lambda job_id, filename, pages: html)
    monkeypatch.setattr(render, "original_file", lambda folder, suffix: folder / "original.pdf")
    (folder / "original.pdf").write_bytes(b"%PDF-1.4")
    page = render.render_resource(folder, "view/pdf", PREFIX)
    decoded = __import__("base64").b64decode(page["body_base64"]).decode()
    assert PREFIX + "static/" in decoded and "file-drop.js" not in decoded

    _job(folder, filename="book.xlsx", pages=2)
    monkeypatch.setattr(
        render, "preview_sheets", lambda folder, preview: [{"number": 1}, {"number": 2}]
    )
    monkeypatch.setattr(render, "workbook_html", lambda job_id, filename, sheets: html)
    monkeypatch.setattr(render, "sheet_html", lambda folder, job_id, sheet: html)
    assert "html" in render.render_resource(folder, "view/workbook", PREFIX)["media_type"]
    assert render.render_resource(folder, "view/sheets/2", PREFIX)["media_type"].startswith(
        "text/html"
    )
    with pytest.raises(HTTPException):
        render.render_resource(folder, "view/sheets/9", PREFIX)
    monkeypatch.setattr(render, "preview_sheets", lambda folder, preview: [{"number": 1}])
    with pytest.raises(HTTPException):
        render.render_resource(folder, "view/sheets/2", PREFIX)

    _job(folder, filename="deck.pptx", pages=2, slide=True)
    monkeypatch.setattr(render, "template_for", lambda folder, job, number: html)
    assert render.render_resource(folder, "view/slides/1", PREFIX)["body_base64"]
    with pytest.raises(HTTPException):
        render.render_resource(folder, "view/pages/1", PREFIX)
    thumb = folder / "thumb.webp"
    thumb.write_bytes(b"RIFF")
    monkeypatch.setattr(render, "thumbnail", lambda folder, number: thumb)
    image = render.render_resource(folder, "api/slides/1/thumbnail", PREFIX)
    assert image["media_type"] == "image/webp"
    _job(folder, filename="notes.pdf", pages=1)
    monkeypatch.setattr(render, "pdf_thumbnail", lambda source, number: thumb)
    assert (
        render.render_resource(folder, "api/pdf/pages/1/thumbnail", PREFIX)["media_type"]
        == "image/webp"
    )
    with pytest.raises(HTTPException):
        render.render_resource(folder, "api/slides/1/thumbnail", PREFIX)
    with pytest.raises(HTTPException):
        render.render_resource(folder, "api/pdf/pages/8/thumbnail", PREFIX)

    _job(folder, filename="notes.txt", pages=1)
    monkeypatch.setattr(render, "template_for", lambda folder, job, number: html)
    assert render.render_resource(folder, "view/document", PREFIX)["body_base64"]
    assert render.render_resource(folder, "view/word", PREFIX)["body_base64"]
    with pytest.raises(HTTPException):
        render.render_resource(folder, "view/missing", PREFIX)
    monkeypatch.setattr(
        render,
        "project_tables",
        lambda doc, filename: [types.SimpleNamespace(model_dump=lambda: {"id": 1})],
    )
    monkeypatch.setattr(
        render.DoclingDocument, "load_from_json", staticmethod(lambda path: object())
    )
    (folder / "document.json").write_text("{}", encoding="utf-8")
    tables = render.render_resource(folder, "api/tables", PREFIX)
    assert tables["media_type"] == "application/json"

    static = tmp_path / "static"
    static.mkdir()
    (static / "app.css").write_text("body{background:url('/static/app.css')}", encoding="utf-8")
    (static / "app.js").write_text("const x='/view/${jobId}/file';", encoding="utf-8")
    monkeypatch.setattr(render, "STATIC", static)
    css = (
        __import__("base64")
        .b64decode(render.render_resource(folder, "static/app.css", PREFIX)["body_base64"])
        .decode()
    )
    assert PREFIX + "static/app.css" in css
    script = (
        __import__("base64")
        .b64decode(render.render_resource(folder, "static/app.js", PREFIX)["body_base64"])
        .decode()
    )
    assert PREFIX + "view/" in script
    (static / "frontend").mkdir()
    (static / "frontend/app.js").write_text("nope", encoding="utf-8")
    with pytest.raises(HTTPException):
        render.render_resource(folder, "static/frontend/app.js", PREFIX)
    (static / "plain.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(HTTPException):
        render.render_resource(folder, "static/plain.txt", PREFIX)
    with pytest.raises(HTTPException):
        render.render_resource(folder, "static/missing.js", PREFIX)

    (folder / "elements.json").write_text("[]", encoding="utf-8")
    (folder / "rag.jsonl").write_text("{}\n", encoding="utf-8")
    (folder / "picture.png").write_bytes(b"png")
    assert render.render_resource(folder, "file/elements.json", PREFIX)["media_type"]
    ndjson = render.render_resource(folder, "file/rag.jsonl", PREFIX)
    assert ndjson["media_type"] == "application/x-ndjson"
    assert render.render_resource(folder, "file/picture.png", PREFIX)["media_type"]
    (folder / "page.html").write_text(html, encoding="utf-8")
    assert (
        PREFIX
        in __import__("base64")
        .b64decode(render.render_resource(folder, "file/page.html", PREFIX)["body_base64"])
        .decode()
    )
    for relative in (
        "ocr-private/a.json",
        "translations/a.json",
        "explanations/a.json",
        "original.txt",
        "notes.exe",
    ):
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
        with pytest.raises(HTTPException):
            render.render_resource(folder, "file/" + relative, PREFIX)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = folder / "linked.txt"
    link.symlink_to(outside)
    with pytest.raises(HTTPException):
        render.render_resource(folder, "file/linked.txt", PREFIX)
    with pytest.raises(HTTPException):
        render.render_resource(folder, "file/../../outside.txt", PREFIX)
    huge = folder / "huge.png"
    with huge.open("wb") as handle:
        handle.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(HTTPException):
        render.render_resource(folder, "file/huge.png", PREFIX)
    with pytest.raises(HTTPException):
        render.render_resource(folder, "elsewhere", PREFIX)


def _profile(provider="local", **extra):
    if provider == "azure_read":
        extra.setdefault("enabled", True)
        extra.setdefault("endpoint", "https://example.cognitiveservices.azure.com")
    return OcrProfile.model_validate({"provider": provider, **extra})


def _document(tmp_path):
    source = tmp_path / "seed.txt"
    source.write_text("在庫連携の時刻です。\n", encoding="utf-8")
    return plain_document(source, source.read_text(encoding="utf-8"))


def test_conversion_validation_previews_and_jobs(tmp_path, monkeypatch):
    text = tmp_path / "note.txt"
    text.write_text("本文", encoding="utf-8")
    validate_input(text)
    missing = tmp_path / "nope.bin"
    missing.write_bytes(b"x")
    with pytest.raises(ValueError, match="のみ対応"):
        validate_input(missing)
    huge = tmp_path / "big.txt"
    huge.write_text("x", encoding="utf-8")
    with huge.open("wb") as handle:
        handle.truncate(conversion.MAX_BYTES + 1)
    with pytest.raises(ValueError, match="50 MiB"):
        validate_input(huge)
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"not a pdf")
    with pytest.raises(ValueError, match="PDF"):
        validate_input(pdf)
    pdf.write_bytes(b"  %PDF-1.7\n")
    validate_input(pdf)
    office = tmp_path / "bad.docx"
    office.write_bytes(b"notzip")
    with pytest.raises(ValueError, match="ZIP"):
        validate_input(office)
    with ZipFile(tmp_path / "empty.docx", "w") as archive:
        archive.writestr("word/other.xml", "<x/>")
    with pytest.raises(ValueError, match="Office構造"):
        validate_input(tmp_path / "empty.docx")
    with ZipFile(tmp_path / "ok.docx", "w") as archive:
        archive.writestr("word/document.xml", "<x/>")
    validate_input(tmp_path / "ok.docx")

    class Big:
        file_size = 300 * 1024**2

    class FakeZip:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def infolist(self):
            return [Big()]

        def namelist(self):
            return ["word/document.xml"]

    monkeypatch.setattr(conversion, "ZipFile", lambda path: FakeZip())
    with pytest.raises(ValueError, match="250 MiB"):
        validate_input(tmp_path / "ok.docx")
    monkeypatch.setattr(conversion, "ZipFile", ZipFile)

    class FakeConverter:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(conversion, "DocumentConverter", FakeConverter)
    conversion.converter.cache_clear()
    assert isinstance(conversion.converter(), FakeConverter)
    assert conversion.converter() is conversion.converter()
    monkeypatch.setattr(conversion.sys, "platform", "linux")
    conversion.converter.cache_clear()
    assert isinstance(conversion.converter(), FakeConverter)
    monkeypatch.setattr(conversion.sys, "platform", "darwin")
    conversion.converter.cache_clear()
    runtime = types.SimpleNamespace(error=None, transport=None)
    assert isinstance(job_converter(_profile("local"), runtime), FakeConverter)
    assert isinstance(job_converter(_profile("disabled"), runtime), FakeConverter)
    assert isinstance(
        job_converter(_profile("azure_read", profile="read-full-page-v1"), runtime), FakeConverter
    )
    assert isinstance(job_converter(_profile("azure_read"), runtime), FakeConverter)

    preview = tmp_path / "Preview.html"
    preview.write_text("<div>plain</div>", encoding="utf-8")
    make_sheet_preview(preview)
    assert "plain" in preview.read_text(encoding="utf-8")
    preview.write_text(
        '<div class="TabHeader">A &amp; B</div><a href="sheet.html">open</a>'
        '<div class="TabHeader">skip</div><a href="../x.html">no</a>',
        encoding="utf-8",
    )
    make_sheet_preview(preview)
    assert "このシートを大きく開く" in preview.read_text(encoding="utf-8")

    source = tmp_path / "deck.pptx"
    source.write_bytes(b"pptx")
    out = tmp_path / "preview-out"
    out.mkdir()
    monkeypatch.setattr(conversion, "editable_preview", lambda source, folder: "slides/index.html")
    monkeypatch.setattr(conversion, "export_powerpoint", lambda source, folder: folder / "deck.pdf")
    monkeypatch.setattr(
        conversion, "build_editable_preview", lambda source, pdf, folder: "built.html"
    )
    (out / "powerpoint-export.json").write_text(
        json.dumps({"renderer": "LibreOffice Impress"}), encoding="utf-8"
    )
    path, notice = office_preview(source, out)
    assert path == "slides/index.html" and "LibreOffice" in notice
    (out / "powerpoint-export.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(conversion, "editable_preview", lambda source, folder: None)
    assert office_preview(source, out)[0] == "built.html"
    monkeypatch.setattr(
        conversion, "editable_preview", lambda source, folder: (_ for _ in ()).throw(OSError("no"))
    )
    assert office_preview(source, out)[0] is None
    monkeypatch.setattr(conversion.sys, "platform", "linux")
    sheet = tmp_path / "book.xlsx"
    sheet.write_bytes(b"xlsx")
    monkeypatch.setattr(conversion, "office_html", lambda source, folder: "libre.html")
    assert office_preview(sheet, out)[0] == "libre.html"
    monkeypatch.setattr(
        conversion, "office_html", lambda source, folder: (_ for _ in ()).throw(RuntimeError("lo"))
    )
    assert office_preview(sheet, out)[0] is None
    monkeypatch.setattr(conversion.sys, "platform", "darwin")

    class Completed:
        returncode = 0

    class Failed:
        returncode = 1

    docx = tmp_path / "file.docx"
    docx.write_bytes(b"docx")
    quick = tmp_path / "quick"
    quick.mkdir()
    monkeypatch.setattr(
        conversion.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            conversion.subprocess.TimeoutExpired(cmd="ql", timeout=1)
        ),
    )
    assert office_preview(docx, quick)[0] is None
    monkeypatch.setattr(conversion.subprocess, "run", lambda *args, **kwargs: Failed())
    assert office_preview(docx, quick)[0] is None
    monkeypatch.setattr(conversion.subprocess, "run", lambda *args, **kwargs: Completed())
    preview_file = quick / "quicklook" / (docx.name + ".qlpreview") / "Preview.html"
    preview_file.parent.mkdir(parents=True)
    preview_file.write_text(
        '<div class="TabHeader">S</div><a href="s.html">x</a>', encoding="utf-8"
    )
    assert office_preview(docx, quick)[0].endswith("Preview.html")
    xlsx = tmp_path / "grid.xlsx"
    xlsx.write_bytes(b"xlsx")
    sheet_preview = quick / "quicklook" / (xlsx.name + ".qlpreview") / "Preview.html"
    sheet_preview.parent.mkdir(parents=True, exist_ok=True)
    sheet_preview.write_text("<div>sheet</div>", encoding="utf-8")
    monkeypatch.setattr(conversion.subprocess, "run", lambda *args, **kwargs: Completed())
    made = []
    monkeypatch.setattr(conversion, "make_sheet_preview", lambda path: made.append(path))
    located = office_preview(xlsx, quick)
    assert made and located[0]

    folder = tmp_path / "convert"
    folder.mkdir()
    (folder / "original.txt").write_text("在庫です。\n", encoding="utf-8")
    job = Job(id="text-job", filename="original.txt")
    convert_job(folder, job)
    assert job.state == "success" and (folder / "document.json").is_file()
    saved = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert saved["state"] == "success"
    element = Element(
        ref="#/texts/0",
        label="text",
        text="在庫",
        pages=[1],
        provenance=[],
        parent=None,
        captions=[],
    )
    assert element.text == "在庫"

    document = _document(tmp_path)

    class ErrorItem:
        error_message = "部分的です"

    class Result:
        def __init__(self, status, errors=None):
            self.status = status
            self.errors = errors or []
            self.document = document

    def install_result(status, errors=None, fail=None):
        class Converter:
            def convert(self, *args, **kwargs):
                if fail:
                    raise fail
                return Result(status, errors)

            def convert_string(self, *args, **kwargs):
                return Result(status, errors)

        monkeypatch.setattr(conversion, "job_converter", lambda profile, runtime: Converter())
        monkeypatch.setattr(conversion, "converter", lambda: Converter())

    monkeypatch.setattr(conversion, "enrich_pdf_evidence", lambda doc, runtime: None)
    monkeypatch.setattr(conversion, "enrich_office_images", lambda doc, runtime: ["図を補完"])
    monkeypatch.setattr(conversion, "ensure_pdf_preview", lambda source, folder: "pdf/index.html")
    monkeypatch.setattr(
        conversion, "office_preview", lambda source, folder: ("office/index.html", "office-notice")
    )
    monkeypatch.setattr(
        conversion, "export_slide_layout", lambda doc, source, folder, preview: None
    )
    install_result(conversion.ConversionStatus.SUCCESS)
    pdf_dir = tmp_path / "pdf-job"
    pdf_dir.mkdir()
    (pdf_dir / "original.pdf").write_bytes(b"%PDF-1.4\n")
    pdf_job = Job(
        id="pdf-job",
        filename="original.pdf",
        ocr_profile=_profile("azure_read").model_dump(mode="json"),
    )
    transport = types.SimpleNamespace(
        closed=False, close=lambda: setattr(transport, "closed", True)
    )
    runtime = types.SimpleNamespace(error=None, transport=transport)
    _convert_job(pdf_dir, pdf_job, ocr_runtime=runtime)
    assert (
        pdf_job.state == "success"
        and transport.closed
        and any("PDF" in item or item for item in pdf_job.warnings)
    )

    install_result(conversion.ConversionStatus.PARTIAL_SUCCESS, [ErrorItem()])
    md = tmp_path / "md-job"
    md.mkdir()
    (md / "original.md").write_text("# 題\n\n本文です。\n", encoding="utf-8")
    md_job = Job(id="md-job", filename="original.md")
    _convert_job(md, md_job)
    assert md_job.state == "partial" and any("Markdown" in item for item in md_job.warnings)

    install_result(conversion.ConversionStatus.FAILURE, [ErrorItem()])
    fail = tmp_path / "fail-job"
    fail.mkdir()
    (fail / "original.docx").write_bytes(
        ZipFile(tmp_path / "ok.docx").read(tmp_path / "ok.docx") if False else b"PK"
    )
    with ZipFile(fail / "original.docx", "w") as archive:
        archive.writestr("word/document.xml", "<x/>")
    failed = Job(id="fail", filename="original.docx")
    _convert_job(fail, failed)
    assert failed.state == "failed" and "Docling" in failed.error

    install_result(conversion.ConversionStatus.SUCCESS)
    ppt = tmp_path / "ppt-job"
    ppt.mkdir()
    with ZipFile(ppt / "original.pptx", "w") as archive:
        archive.writestr("ppt/presentation.xml", "<p/>")
    ppt_job = Job(
        id="ppt", filename="original.pptx", ocr_profile=_profile("local").model_dump(mode="json")
    )
    _convert_job(ppt, ppt_job)
    assert ppt_job.slide_layout is True and ppt_job.preview

    monkeypatch.setattr(
        conversion, "office_preview", lambda source, folder: (None, "preview missing")
    )
    ppt2 = tmp_path / "ppt-missing"
    ppt2.mkdir()
    with ZipFile(ppt2 / "original.pptx", "w") as archive:
        archive.writestr("ppt/presentation.xml", "<p/>")
    missing_job = Job(id="ppt2", filename="original.pptx")
    _convert_job(ppt2, missing_job)
    assert missing_job.state == "partial"

    xdir = tmp_path / "xlsx-job"
    xdir.mkdir()
    with ZipFile(xdir / "original.xlsx", "w") as archive:
        archive.writestr("xl/workbook.xml", "<x/>")
    xjob = Job(
        id="xlsx",
        filename="original.xlsx",
        ocr_profile=_profile("disabled").model_dump(mode="json"),
    )
    monkeypatch.setattr(
        conversion, "office_preview", lambda source, folder: ("sheet.html", "sheet-notice")
    )
    _convert_job(xdir, xjob)
    assert any("XLSX" in item for item in xjob.warnings)

    err_dir = tmp_path / "ocr-job"
    err_dir.mkdir()
    (err_dir / "original.pdf").write_bytes(b"%PDF-1.4\n")
    err_job = Job(id="ocr", filename="original.pdf")
    runtime = types.SimpleNamespace(
        error=OcrError("ocr_failed", "failed"), transport=types.SimpleNamespace(close=lambda: None)
    )
    _convert_job(err_dir, err_job, ocr_runtime=runtime)
    assert err_job.state == "failed" and err_job.error_code == "ocr_failed"

    save_job(tmp_path / "saved", Job(id="saved", filename="a.txt"))
    assert (tmp_path / "saved" / "job.json").is_file()
    # Drop the cached fake converter after the real class is restored.
    monkeypatch.undo()
    conversion.converter.cache_clear()


def _upload(root: Path, raw: bytes, suffix=".txt") -> dict:
    source, revision = str(uuid4()), str(uuid4())
    folder = root / "sources" / source / revision
    folder.mkdir(parents=True)
    (folder / f"original{suffix}").write_bytes(raw)
    return {
        "source_id": source,
        "source_revision": revision,
        "suffix": suffix,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_worker_auth_validation_extract_and_viewer(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="32"):
        create_worker(tmp_path / "short", "tiny")
    monkeypatch.delenv("KNOWLEDGE_ARTIFACT_ROOT", raising=False)
    with pytest.raises(ValueError, match="KNOWLEDGE_ARTIFACT_ROOT"):
        app_factory()
    monkeypatch.setenv("KNOWLEDGE_ARTIFACT_ROOT", str(tmp_path / "factory"))
    monkeypatch.setenv("KNOWLEDGE_WORKER_TOKEN", TOKEN)
    assert app_factory().title
    root = tmp_path / "artifacts"
    app = create_worker(root, TOKEN)
    client = TestClient(app)
    assert client.get("/health/live").json()["status"] == "alive"
    monkeypatch.setattr(worker_mod, "model_problems", lambda *args, **kwargs: ["models"])
    monkeypatch.setattr(worker_mod.shutil, "disk_usage", lambda path: types.SimpleNamespace(free=0))
    monkeypatch.setattr(worker_mod.sys, "platform", "linux")
    monkeypatch.setattr(worker_mod.shutil, "which", lambda name: None)
    ready = client.get("/health/ready")
    assert ready.status_code == 503 and "tesseract_missing" in ready.json()["reasons"]
    monkeypatch.setattr(
        worker_mod,
        "model_problems",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad")),
    )
    monkeypatch.setattr(
        worker_mod.shutil, "disk_usage", lambda path: (_ for _ in ()).throw(OSError("disk"))
    )
    monkeypatch.setattr(worker_mod.sys, "platform", "darwin")
    again = client.get("/health/ready")
    assert again.status_code == 503 and "manifest_invalid" in again.json()["reasons"]
    monkeypatch.setattr(worker_mod, "model_problems", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        worker_mod.shutil, "disk_usage", lambda path: types.SimpleNamespace(free=10**12)
    )
    assert client.get("/health/ready").status_code == 200

    payload = _upload(root, "本文".encode())
    assert client.post("/internal/v1/validate", json=payload).status_code == 401
    assert (
        client.post(
            "/internal/v1/validate", json=payload, headers={"Authorization": "Token x"}
        ).status_code
        == 401
    )
    assert client.post("/internal/v1/validate", json=payload, headers=_headers()).status_code == 200
    changed = dict(payload, sha256="a" * 64)
    assert client.post("/internal/v1/validate", json=changed, headers=_headers()).status_code == 422
    missing = dict(payload, source_revision=str(uuid4()))
    assert client.post("/internal/v1/validate", json=missing, headers=_headers()).status_code == 404
    linked = _upload(root, b"text")
    path = root / "sources" / linked["source_id"] / linked["source_revision"] / "original.txt"
    path.unlink()
    path.symlink_to(tmp_path / "nope")
    assert client.post("/internal/v1/validate", json=linked, headers=_headers()).status_code == 422
    pdf = pymupdf.open()
    for _ in range(101):
        pdf.new_page()
    too_many = _upload(root, pdf.tobytes(), ".pdf")
    pdf.close()
    assert (
        client.post("/internal/v1/validate", json=too_many, headers=_headers()).status_code == 422
    )
    secret = pymupdf.open()
    secret.new_page()
    secret.save(
        tmp_path / "enc.pdf",
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    secret.close()
    encrypted = _upload(root, (tmp_path / "enc.pdf").read_bytes(), ".pdf")
    assert (
        client.post("/internal/v1/validate", json=encrypted, headers=_headers()).status_code == 422
    )
    assert (
        client.post(
            "/internal/v1/validate",
            content=b"x" * 17000,
            headers={**_headers(), "content-type": "application/json"},
        ).status_code
        == 413
    )

    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(
        PrivateRequests(app, TOKEN)(
            {
                "type": "http",
                "path": "/internal/v1/validate",
                "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
            },
            receive,
            send,
        )
    )
    assert sent == []
    asyncio.run(
        PrivateRequests(app, TOKEN)(
            {"type": "lifespan", "path": "/internal/v1/validate", "headers": []},
            receive,
            send,
        )
    )

    local = client.post("/internal/v1/ocr-profile", json={"provider": "local"}, headers=_headers())
    assert local.status_code == 200 and local.json()["profile"] == "local-v1"
    monkeypatch.setattr(
        worker_mod, "load_profile", lambda provider=None: (_ for _ in ()).throw(OcrError("bad"))
    )
    assert client.post("/internal/v1/ocr-profile", json={}, headers=_headers()).status_code == 422
    azure = _profile("azure_read", profile="read-full-page-v1")
    monkeypatch.setattr(worker_mod, "load_profile", lambda provider=None: azure)
    listed = client.post(
        "/internal/v1/ocr-profile", json={"provider": "azure_read"}, headers=_headers()
    )
    assert listed.json()["profile"] == "azure-read-full-page-v1"
    assert profile_id(_profile("disabled")) == "disabled-v1"
    assert profile_id(_profile("azure_read")) == "azure-read-v1"
    lines = tmp_path / "rows.jsonl"
    lines.write_text('\n{"a": 1}\n\n', encoding="utf-8")
    assert read_jsonl(lines) == [{"a": 1}]

    def succeed(folder, job, ocr_runtime=None):
        job.state = "success"
        job.preview = "preview.html"
        job.warnings = ["注意"]
        (folder / "rag.jsonl").write_text('{"refs":["#/tables/0"],"text":"a"}\n', encoding="utf-8")
        (folder / "rag-index.jsonl").write_text("{}\n", encoding="utf-8")
        (folder / "document.json").write_text(
            json.dumps({"tables": [{"self_ref": "#/tables/0", "data": []}]}), encoding="utf-8"
        )

    monkeypatch.setattr(worker_mod, "convert_job", succeed)
    monkeypatch.setattr(worker_mod, "make_runtime", lambda *args, **kwargs: object())
    body = _upload(root, "在庫です。".encode())
    request = {
        **body,
        "job_id": str(uuid4()),
        "run": 1,
        "profile": "local-v1",
        "filename": "資料.txt",
    }
    extracted = client.post("/internal/v1/extract", json=request, headers=_headers())
    assert extracted.status_code == 200, extracted.text
    assert extracted.json()["contexts"][0]["tables"]
    request["filename"] = "nested/資料.txt"
    assert client.post("/internal/v1/extract", json=request, headers=_headers()).status_code == 422
    request["filename"] = "資料.pdf"
    assert client.post("/internal/v1/extract", json=request, headers=_headers()).status_code == 422
    request["filename"] = "資料.txt"
    request["profile"] = "azure-read-v1"
    request["ocr_profile"] = _profile("local").model_dump(mode="json")
    assert client.post("/internal/v1/extract", json=request, headers=_headers()).status_code == 422

    def fail_job(folder, job, ocr_runtime=None):
        job.state = "failed"
        job.error_code = "extraction_failed"

    monkeypatch.setattr(worker_mod, "convert_job", fail_job)
    request["profile"] = "local-v1"
    request["ocr_profile"] = None
    request["job_id"] = str(uuid4())
    assert client.post("/internal/v1/extract", json=request, headers=_headers()).status_code == 422
    monkeypatch.setattr(
        worker_mod,
        "convert_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(OcrError("ocr_auth_missing")),
    )
    request["job_id"] = str(uuid4())
    assert client.post("/internal/v1/extract", json=request, headers=_headers()).status_code == 422
    monkeypatch.setattr(
        worker_mod, "convert_job", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk"))
    )
    request["job_id"] = str(uuid4())
    assert client.post("/internal/v1/extract", json=request, headers=_headers()).status_code == 422

    configured = _profile("azure_read")
    monkeypatch.setattr(worker_mod, "load_profile", lambda provider=None: configured)

    def azure_convert(folder, job, ocr_runtime=None):
        succeed(folder, job, ocr_runtime)
        source = folder.parent.parent / "original.txt"
        if source.exists():
            source.unlink()

    monkeypatch.setattr(worker_mod, "convert_job", azure_convert)
    monkeypatch.setattr(
        worker_mod,
        "HttpOcrLedger",
        lambda *args, **kwargs: types.SimpleNamespace(close=lambda: None),
    )
    azure_body = _upload(root, "azure".encode())
    azure_request = {
        **azure_body,
        "job_id": str(uuid4()),
        "run": 2,
        "profile": "azure-read-v1",
        "filename": "資料.txt",
        "ocr_profile": configured.model_dump(mode="json"),
        "allow_ocr_resubmit": True,
    }
    assert (
        client.post("/internal/v1/extract", json=azure_request, headers=_headers()).status_code
        == 200
    )
    disabled = _profile("azure_read", endpoint="https://other.cognitiveservices.azure.com")
    monkeypatch.setattr(worker_mod, "load_profile", lambda provider=None: _profile("disabled"))
    azure_request["job_id"] = str(uuid4())
    azure_request["source_id"], azure_request["source_revision"] = (
        _upload(root, b"again")["source_id"],
        None,
    )
    fresh = _upload(root, b"again")
    azure_request.update(fresh)
    azure_request["ocr_profile"] = disabled.model_dump(mode="json")
    azure_request["profile"] = "azure-read-v1"
    assert (
        client.post("/internal/v1/extract", json=azure_request, headers=_headers()).status_code
        == 422
    )

    started = threading.Event()
    release = threading.Event()

    def block(folder, job, ocr_runtime=None):
        started.set()
        assert release.wait(5)
        succeed(folder, job)

    monkeypatch.setattr(worker_mod, "convert_job", block)
    busy_body = _upload(root, b"busy")
    busy = {
        **busy_body,
        "job_id": str(uuid4()),
        "run": 1,
        "profile": "local-v1",
        "filename": "資料.txt",
    }
    result = {}

    def call():
        result["response"] = client.post("/internal/v1/extract", json=busy, headers=_headers())

    thread = threading.Thread(target=call)
    thread.start()
    assert started.wait(5)
    other = _upload(root, b"other")
    waiting = {
        **other,
        "job_id": str(uuid4()),
        "run": 1,
        "profile": "local-v1",
        "filename": "資料.txt",
    }
    assert client.post("/internal/v1/extract", json=waiting, headers=_headers()).status_code == 429
    release.set()
    thread.join(5)

    attempt_root = _upload(root, b"view")
    job_id = str(uuid4())
    attempt = (
        root
        / "sources"
        / attempt_root["source_id"]
        / attempt_root["source_revision"]
        / "attempts"
        / f"{job_id}-1"
    )
    _job(attempt, filename="資料.txt")
    view_ids = {key: attempt_root[key] for key in ("source_id", "source_revision", "suffix")}
    viewed = client.post(
        "/internal/v1/viewer",
        json={**view_ids, "job_id": job_id, "run": 1, "resource": "manifest", "prefix": PREFIX},
        headers=_headers(),
    )
    assert viewed.status_code == 200, viewed.text
    link_root = root / "sources" / attempt_root["source_id"]
    alias = str(uuid4())
    linked_revision = link_root / alias
    linked_revision.symlink_to(attempt.parent.parent, target_is_directory=True)
    assert (
        client.post(
            "/internal/v1/viewer",
            json={
                **view_ids,
                "source_revision": alias,
                "job_id": job_id,
                "run": 1,
                "resource": "manifest",
                "prefix": PREFIX,
            },
            headers=_headers(),
        ).status_code
        == 404
    )
    monkeypatch.setattr(
        render, "render_resource", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad"))
    )
    assert (
        client.post(
            "/internal/v1/viewer",
            json={**view_ids, "job_id": job_id, "run": 1, "resource": "manifest", "prefix": PREFIX},
            headers=_headers(),
        ).status_code
        == 404
    )
