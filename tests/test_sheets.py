import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from lxml import html
from openpyxl import Workbook

import docling_desk.app as web
from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, save_job
from docling_desk.preview.sheets import column_name, preview_sheets, sheet_html, workbook_html

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests/fixtures/documents/sheet"


def test_existing_preview_sheet_names_and_source_values():
    sheets = preview_sheets(SAMPLE, "quicklook/original.xlsx.qlpreview/Preview.html")
    assert [(s["number"], s["name"]) for s in sheets] == [(1, "集計"), (2, "注記")]
    tree = html.fromstring(sheet_html(SAMPLE, SAMPLE.name, sheets[0]))
    assert tree.xpath('//tr[@class="sheet-column-axis"]/th/text()') == ["A", "B"]
    assert tree.xpath('//th[@class="sheet-row-axis"]/text()') == list(map(str, range(1, 9)))
    assert tree.xpath("//td/text()") == html.fromstring(sheets[0]["path"].read_text()).xpath(
        "//td/text()"
    )
    assert column_name(26) == "Z"
    assert column_name(27) == "AA"
    assert column_name(16384) == "XFD"


def test_single_sheet_hidden_row_merged_cells_and_inert_assets(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "単独<&シート"
    sheet.row_dimensions[2].hidden = True
    sheet.column_dimensions["B"].hidden = True
    book.save(tmp_path / "original.xlsx")
    (tmp_path / "local.css").write_text("td{color:red}")
    (tmp_path / "Preview.html").write_text(
        '<html><head><link href="local.css" rel="stylesheet"></head><body>'
        '<script>alert(1)</script><img src="https://example.com/x.png" onerror="alert(1)">'
        '<table class="worksheet" style="width:200;"><col><col>'
        '<tr><td colspan="2">結合</td></tr><tr><td>3行目</td><td>値</td></tr></table></body></html>'
    )
    sheets = preview_sheets(tmp_path, "Preview.html")
    tree = html.fromstring(sheet_html(tmp_path, "a" * 32, sheets[0]))
    assert tree.xpath('//th[@class="sheet-row-axis"]/text()') == ["1", "3"]
    assert tree.xpath('//tr[@class="sheet-column-axis"]/th/text()') == ["A", "C"]
    assert tree.xpath('//td[@colspan="2"]/text()') == ["結合"]
    assert not tree.xpath("//*[@onerror]|//img[@src]")
    assert tree.xpath("//script/@src") == ["/static/sheet-frame.js", "/static/file-drop.js"]
    assert not tree.xpath("//script/text()")
    assert tree.xpath("//link/@href") == [f"/files/{'a' * 32}/local.css", "/static/sheets.css"]
    wrapper = html.fromstring(workbook_html("a" * 32, "test.xlsx", sheets))
    payload = json.loads(wrapper.xpath('string(//script[@id="sheetData"])'))
    assert payload["sheets"] == [{"number": 1, "name": "単独<&シート"}]


def test_original_quicklook_tabs_and_unsafe_sheet_path(tmp_path):
    shutil.copy(SAMPLE / "original.xlsx", tmp_path / "original.xlsx")
    (tmp_path / "Sheet.html").write_text(
        '<html><body><table class="worksheet"></table></body></html>'
    )
    (tmp_path / "Preview.html").write_text(
        '<html><body><div class="TabHeader">集計</div><a href="Sheet.html">開く</a>'
        '<div class="TabHeader">注記</div><a href="../Outside.html">開く</a></body></html>'
    )
    assert [s["name"] for s in preview_sheets(tmp_path, "Preview.html")] == ["集計"]


def test_workbook_routes_and_source_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    job_id = "b" * 32
    folder = tmp_path / job_id
    shutil.copytree(SAMPLE, folder)
    save_job(
        folder,
        Job(
            id=job_id,
            filename="sample.xlsx",
            state="success",
            preview="quicklook/original.xlsx.qlpreview/Preview.html",
        ),
    )
    with TestClient(web.app) as client:
        response = client.get(f"/view/{job_id}/workbook")
        assert response.status_code == 200
        assert 'role="tablist"' in response.text
        assert "frame-src 'self'" in response.headers["content-security-policy"]
        sheet = client.get(f"/view/{job_id}/sheets/2")
        assert sheet.status_code == 200
        assert "合成テスト資料" in sheet.text
        assert "sandbox allow-scripts" in sheet.headers["content-security-policy"]
        assert "allow-same-origin" not in sheet.headers["content-security-policy"]
        assert '<script src="/static/sheet-frame.js"' in sheet.text
        assert client.get(f"/view/{job_id}/sheets/99").status_code == 404
        assert client.get(f"/view/{'c' * 32}/workbook").status_code == 404
        assert client.get("/static/sheets.js").status_code == 200
