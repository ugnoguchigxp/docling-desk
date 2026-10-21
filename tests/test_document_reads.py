"""Read accepted extraction artifacts without rerunning extraction or OCR."""

from __future__ import annotations

import base64
import csv
import json
from io import BytesIO, StringIO
from uuid import uuid4

import pytest
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pdf_samples import pdf_bytes
from PIL import Image

from docling_desk.documents.conversion import Job
from docling_desk.knowledge.document_reads import ReadOptions, read_document
from docling_desk.knowledge.worker import create_worker


def artifact(folder, suffix=".pdf"):
    folder.mkdir(parents=True, exist_ok=True)
    job = Job(id=str(uuid4()), filename="synthetic" + suffix, state="success", pages=2)
    (folder / "job.json").write_text(job.model_dump_json())
    doc = DoclingDocument(name="Synthetic")
    for n in (1, 2):
        doc.add_page(n, Size(width=400, height=600))

    def prov(n):
        return ProvenanceItem(
            page_no=n, bbox=BoundingBox(l=10, t=10, r=300, b=60), charspan=(0, 10)
        )

    doc.add_heading("First", level=1, prov=prov(1))
    doc.add_text(label=DocItemLabel.TEXT, text="First content 日本語", prov=prov(1))
    doc.add_heading("Nested", level=2, prov=prov(1))
    doc.add_text(label=DocItemLabel.TEXT, text="Nested content", prov=prov(1))
    doc.add_heading("Second", level=1, prov=prov(2))
    doc.add_text(label=DocItemLabel.TEXT, text="Second content", prov=prov(2))
    cells = [
        TableCell(
            start_row_offset_idx=0,
            end_row_offset_idx=1,
            start_col_offset_idx=0,
            end_col_offset_idx=2,
            col_span=2,
            text='Merged, "heading"',
            column_header=True,
        ),
        TableCell(
            start_row_offset_idx=1,
            end_row_offset_idx=2,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
            text="value\nnew line",
        ),
        TableCell(
            start_row_offset_idx=1,
            end_row_offset_idx=2,
            start_col_offset_idx=1,
            end_col_offset_idx=2,
            text="日本語",
        ),
    ]
    doc.add_table(TableData(num_rows=2, num_cols=2, table_cells=cells), prov=prov(2))
    doc.save_as_json(folder / "document.json")
    (folder / "original.pdf").write_bytes(
        pdf_bytes(("Synthetic page 1", "Synthetic page 2"), size=(400, 600), at=(30, 30))
    )
    return job


def read(folder, operation, **options):
    return read_document(folder, ReadOptions(operation=operation, **options))


@pytest.mark.parametrize(
    "suffix,kind",
    [
        (".pdf", "page"),
        (".pptx", "slide"),
        (".xlsx", "sheet"),
        (".docx", "document"),
        (".md", "document"),
        (".txt", "document"),
    ],
)
def test_outline_sections_units_and_exact_element_boundaries(tmp_path, suffix, kind):
    artifact(tmp_path, suffix)
    first = read(tmp_path, "outline", limit=2)
    assert first["kind"] == kind and first["next_offset"] == 2
    rest = read(tmp_path, "outline", offset=2)
    entries = first["entries"] + rest["entries"]
    assert len(entries) == first["total"] == len({e["ref"] for e in entries})
    assert (first["units"] == []) == (kind == "document")
    section = read(tmp_path, "section", selector={"kind": "section", "ref": entries[0]["ref"]})
    assert [e["ref"] for e in section["elements"]] == [e["ref"] for e in entries[:4]]
    assert "日本語" in section["elements"][1]["text"]
    exact = read(
        tmp_path,
        "section",
        selector={"kind": "elements", "start_ref": entries[1]["ref"], "end_ref": entries[1]["ref"]},
    )
    assert len(exact["elements"]) == 1
    if kind != "document":
        page = read(tmp_path, "section", selector={"kind": "units", "start": 2, "end": 2})
        assert all(e["pages"] == [2] for e in page["elements"])
        assert len(page["elements"]) == 3
    else:
        with pytest.raises(HTTPException, match="invalid_document_range"):
            read(tmp_path, "section", selector={"kind": "units", "start": 1, "end": 1})


@pytest.mark.parametrize(
    "selector",
    [
        {"kind": "section", "ref": "#/texts/1"},
        {"kind": "section", "ref": "#/texts/999"},
        {"kind": "elements", "start_ref": "#/texts/3", "end_ref": "#/texts/1"},
        {"kind": "units", "start": 2, "end": 1},
        {"kind": "units", "start": 0, "end": 1},
        {"kind": "units", "start": 1, "end": 3},
        {"kind": "units", "start": True, "end": 2},
        {"kind": "section", "ref": "#/texts/0", "path": "/etc/passwd"},
    ],
)
def test_invalid_and_unlisted_ranges_are_rejected(tmp_path, selector):
    artifact(tmp_path)
    with pytest.raises(HTTPException, match="invalid_document_range"):
        read(tmp_path, "section", selector=selector)


def test_text_limit_is_unicode_and_never_silently_truncates(tmp_path):
    artifact(tmp_path)
    selector = {"kind": "elements", "start_ref": "#/texts/1", "end_ref": "#/texts/1"}
    value = read(tmp_path, "section", selector=selector)
    assert value["chars"] == len("First content 日本語")
    assert read(tmp_path, "section", selector=selector, max_chars=value["chars"]) == value
    with pytest.raises(HTTPException, match="document_read_too_large") as error:
        read(tmp_path, "section", selector=selector, max_chars=value["chars"] - 1)
    assert error.value.status_code == 413


def test_table_windows_preserve_merged_cells_csv_and_continuations(tmp_path):
    artifact(tmp_path)
    listed = read(tmp_path, "tables")
    assert listed["total"] == 1
    info = listed["tables"][0]
    assert info["merged"] and info["header_rows"] == 1 and info["pages"] == [2]
    args = {"table_ref": info["table_ref"]}
    table = read(tmp_path, "table", **args, format="csv")
    assert list(csv.reader(StringIO(table["csv"]))) == table["data"]
    window = read(tmp_path, "table", **args, row_limit=1, column_limit=1, column_offset=1)
    assert window["data"] == [[""]] and window["next_row_offset"] == 1
    assert window["next_column_offset"] is None
    assert window["cells"][0]["col_span"] == 2
    assert window["cells"][0]["start_col_offset_idx"] == 0
    bottom = read(tmp_path, "table", **args, row_offset=1)
    assert bottom["data"] == [["value\nnew line", "日本語"]]
    for options, code in [({"table_ref": "../../secret"}, 404), ({**args, "row_offset": 2}, 422)]:
        with pytest.raises(HTTPException) as error:
            read(tmp_path, "table", **options)
        assert error.value.status_code == code
    assert read(tmp_path, "tables", offset=1)["tables"] == []


def test_large_sparse_table_reads_allocate_only_requested_rectangle(tmp_path):
    artifact(tmp_path)
    doc = json.loads((tmp_path / "document.json").read_text())
    doc["tables"][0]["data"].update(num_rows=100000, num_cols=100000)
    (tmp_path / "document.json").write_text(json.dumps(doc))
    assert read(tmp_path, "tables")["tables"][0]["rows"] == 100000
    window = read(
        tmp_path,
        "table",
        table_ref="#/tables/0",
        row_offset=99999,
        column_offset=99999,
        row_limit=1,
        column_limit=1,
    )
    assert window["data"] == [[""]]


def test_pdf_image_is_bounded_decodable_and_does_not_extract(tmp_path, monkeypatch):
    artifact(tmp_path)
    monkeypatch.setattr(
        "docling_desk.knowledge.worker.convert_job",
        lambda *a, **kw: pytest.fail("read re-extracted"),
    )
    image = read(tmp_path, "page-image", unit=2)
    data = base64.b64decode(image["body_base64"], validate=True)
    assert len(data) <= 1024 * 1024
    with Image.open(BytesIO(data)) as decoded:
        assert decoded.format == "WEBP"
        assert decoded.size == (image["width"], image["height"])
    assert max(image["width"], image["height"]) <= 1536
    with pytest.raises(HTTPException, match="invalid_document_range"):
        read(tmp_path, "page-image", unit=3)


def test_slide_uses_existing_preview_without_ocr(tmp_path, monkeypatch):
    artifact(tmp_path, ".pptx")
    target = tmp_path / "slide.webp"
    Image.new("RGB", (240, 135), "white").save(target)
    monkeypatch.setattr(
        "docling_desk.knowledge.document_reads.thumbnail", lambda folder, number: target
    )
    result = read(tmp_path, "page-image", unit=1)
    assert result["kind"] == "slide" and result["width"] == 240


@pytest.mark.parametrize("suffix", [".docx", ".xlsx", ".md", ".txt"])
def test_formats_without_page_images_are_explicit(tmp_path, suffix):
    artifact(tmp_path, suffix)
    with pytest.raises(HTTPException, match="page_image_unsupported") as error:
        read(tmp_path, "page-image", unit=1)
    assert error.value.status_code == 422


def test_private_reader_auth_attempt_isolation_and_symlinks(tmp_path):
    sid, rid, jid = (str(uuid4()) for _ in range(3))
    revision = tmp_path / "sources" / sid / rid
    folder = revision / "attempts" / (jid + "-1")
    artifact(folder)
    (revision / "original.pdf").write_bytes((folder / "original.pdf").read_bytes())
    client = TestClient(create_worker(tmp_path, "p" * 40))
    args = {
        "source_id": sid,
        "source_revision": rid,
        "job_id": jid,
        "run": 1,
        "suffix": ".pdf",
        "options": {"operation": "outline"},
    }
    route = "/internal/v1/document-read"
    assert client.post(route, json=args).status_code == 401
    auth = {"Authorization": "Bearer " + "p" * 40}
    assert client.post(route, json=args, headers=auth).status_code == 200
    assert client.post(route, json={**args, "run": 2}, headers=auth).status_code == 404
    data = (folder / "document.json").read_bytes()
    external = tmp_path / "outside.json"
    external.write_bytes(data)
    (folder / "document.json").unlink()
    (folder / "document.json").symlink_to(external)
    assert client.post(route, json=args, headers=auth).status_code == 409


def test_docling_title_is_a_level_zero_section_boundary(tmp_path):
    artifact(tmp_path, ".md")
    doc = DoclingDocument.load_from_json(tmp_path / "document.json")
    # Markdown H1 is Docling TITLE; nested headings are SECTION_HEADER.
    from docling_core.types.doc import TitleItem

    for index in (0, 4):
        data = doc.texts[index].model_dump(exclude={"level"})
        doc.texts[index] = TitleItem.model_validate({**data, "label": "title"})
    doc.save_as_json(tmp_path / "document.json")
    outline = read(tmp_path, "outline")
    assert outline["entries"][0]["heading"] == "First"
    assert outline["entries"][0]["level"] == 0
    section = read(tmp_path, "section", selector={"kind": "section", "ref": "#/texts/0"})
    assert len(section["elements"]) == 4


def test_section_rejects_large_table_before_materializing_grid(tmp_path, monkeypatch):
    from docling_core.types.doc import TableItem

    artifact(tmp_path)
    path = tmp_path / "document.json"
    data = json.loads(path.read_text())
    data["tables"][0]["data"].update(num_rows=100000, num_cols=100000)
    path.write_text(json.dumps(data))
    called = []
    monkeypatch.setattr(
        TableItem, "export_to_markdown", lambda *a, **kw: called.append(True) or "small"
    )
    with pytest.raises(HTTPException) as error:
        read(
            tmp_path,
            "section",
            selector={"kind": "elements", "start_ref": "#/tables/0", "end_ref": "#/tables/0"},
        )
    assert error.value.status_code == 413
    assert called == []
