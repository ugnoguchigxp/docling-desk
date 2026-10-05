import json
from pathlib import Path

import pytest
from docling_core.types.doc import DocItemLabel, DoclingDocument, TableCell, TableData
from fastapi.testclient import TestClient
from lxml import html

from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, save_job
from docling_desk.preview.html_view import sanitize_body, viewer_html


@pytest.mark.parametrize(
    "filename", ["synthetic-report.pdf", "synthetic-slides.pptx", "synthetic-sheet.xlsx"]
)
def test_inline_tables_keep_exact_references_pages_and_embedded_images(filename):
    root = Path(__file__).resolve().parent / "fixtures/documents"
    jobs = [(p.parent, json.loads(p.read_text())) for p in root.glob("*/job.json")]
    folder, _ = max(
        (j for j in jobs if j[1]["filename"] == filename and j[1]["state"] == "success"),
        key=lambda j: j[1]["created"],
    )
    doc = DoclingDocument.load_from_json(folder / "document.json")
    before = doc.model_dump_json()
    rendered = html.fromstring(viewer_html(doc, filename, "a" * 32))
    payload = json.loads(rendered.get_element_by_id("table-data").text)
    native = rendered.xpath("//table[@data-docling-ref]")
    assert {t.get("data-docling-ref") for t in native} == {t["ref"] for t in payload["tables"]}
    for table in native:
        data = next(t for t in payload["tables"] if t["ref"] == table.get("data-docling-ref"))
        boundary = table.xpath("ancestor::section[@data-page]")[0]
        assert int(boundary.get("data-page")) in data["pages"]
    assert [p.get("data-page") for p in rendered.xpath("//section[@data-page]")] == ["1", "2"]
    assert len(rendered.xpath('//img[starts-with(@src,"data:image/")]')) == 1
    assert doc.model_dump_json() == before


def test_document_active_content_cannot_enter_trusted_viewer():
    raw = """<html><body><section data-page="1"><script>alert(1)</script><style>body{}</style>
    <iframe src="https://example.com"></iframe><svg onload="alert(1)"></svg>
    <p onclick="alert(1)" style="background:url(https://example.com)">本文</p>
    <a href="javascript:alert(1)">リンク</a><img src="https://example.com/tracking.png" onerror="alert(1)">
    <img src="data:image/svg+xml;base64,AA=="><img src="data:image/png;base64,AA==">
    <table data-docling-ref="#/tables/0"><tr><td rowspan="2">値</td></tr></table>
    <!-- hidden --></section></body></html>"""
    body = html.fromstring(sanitize_body(raw))
    assert not body.xpath(
        ".//script|.//style|.//iframe|.//svg|.//@onclick|.//@onerror|.//@style|.//@href"
    )
    assert body.xpath(".//img/@src") == ["data:image/png;base64,AA=="]
    assert body.xpath(".//td/@rowspan") == ["2"]
    assert "本文" in body.text_content()


def test_view_endpoint_keeps_raw_files_inert_and_table_values_as_data(tmp_path, monkeypatch):
    import docling_desk.app as web

    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    client = TestClient(web.app)
    job_id = "c" * 32
    folder = tmp_path / job_id
    folder.mkdir()
    save_job(folder, Job(id=job_id, filename="test.pdf"))
    assert client.get(f"/view/{job_id}/extracted").status_code == 409
    doc = DoclingDocument(name="escaping")
    text = "</script><script>alert(1)</script>"
    doc.add_text(label=DocItemLabel.TEXT, text=text)
    doc.add_table(
        data=TableData(
            num_rows=1,
            num_cols=1,
            table_cells=[
                TableCell(
                    text=text,
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                )
            ],
        )
    )
    doc.save_as_json(folder / "document.json")
    (folder / "extracted.html").write_text("<script>alert(2)</script>")
    save_job(folder, Job(id=job_id, filename="test.pdf", state="success"))
    response = client.get(f"/view/{job_id}/extracted")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "allow-scripts allow-downloads" in csp
    assert "allow-same-origin" not in csp and "unsafe-eval" not in csp
    rendered = html.fromstring(response.text)
    assert rendered.xpath("//script[@src]/@src") == [
        "/static/vendor/ag-grid-community.min.js",
        "/static/tabulens-grid.js",
        "/static/table-common.js",
        "/static/inline-tables.js",
    ]
    assert len(rendered.xpath("//script[not(@src)]")) == 1  # Inert JSON only.
    payload = json.loads(rendered.get_element_by_id("table-data").text)
    assert payload["tables"][0]["rows"] == [[text]]
    assert "alert(2)" not in response.text
    raw = client.get(f"/files/{job_id}/extracted.html")
    assert "default-src 'none'" in raw.headers["content-security-policy"]
    assert client.get("/view/invalid/extracted").status_code == 404
