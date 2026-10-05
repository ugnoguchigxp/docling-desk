import json
from pathlib import Path

from docling_core.types.doc import DoclingDocument, TableCell, TableData

from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, save_job
from docling_desk.documents.tables import project_tables


def test_real_synthetic_tables_keep_values_headers_and_source():
    root = Path(__file__).resolve().parent / "fixtures/documents"
    jobs = [(p.parent, json.loads(p.read_text())) for p in root.glob("*/job.json")]
    folder, _ = max(
        (j for j in jobs if j[1]["filename"] == "synthetic-sheet.xlsx"),
        key=lambda j: j[1]["created"],
    )
    doc = DoclingDocument.load_from_json(folder / "document.json")
    before = doc.model_dump_json()
    views = project_tables(doc, "sample.xlsx")
    table = views[1]
    assert table.source_label == "シート 1 · 集計"
    assert table.header_rows == 1 and table.pages == [1]
    assert table.rows == [
        ["区分", "件数"],
        ["A", "120"],
        ["B", "180"],
        ["C", "150"],
        ["合計", "450"],
    ]
    assert views[2].source_label == "シート 2 · 注記"
    assert doc.model_dump_json() == before


def test_merged_cells_and_untrusted_text_are_preserved_as_data():
    doc = DoclingDocument(name="grid-test")
    doc.add_table(
        data=TableData(
            num_rows=2,
            num_cols=2,
            table_cells=[
                TableCell(
                    text="結合見出し",
                    column_header=True,
                    row_span=1,
                    col_span=2,
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=0,
                    end_col_offset_idx=2,
                ),
                TableCell(
                    text='<img src=x onerror="alert(1)">',
                    start_row_offset_idx=1,
                    end_row_offset_idx=2,
                    start_col_offset_idx=0,
                    end_col_offset_idx=1,
                ),
                TableCell(
                    text="10",
                    start_row_offset_idx=1,
                    end_row_offset_idx=2,
                    start_col_offset_idx=1,
                    end_col_offset_idx=2,
                ),
            ],
        )
    )
    view = project_tables(doc, "test.pdf")[0]
    assert view.merged and view.header_rows == 1
    assert view.rows[0] == ["結合見出し", ""]
    assert view.rows[1] == ['<img src=x onerror="alert(1)">', "10"]
    assert view.pages == [] and view.source_label == "ページ位置未取得"


def test_table_api_and_vendor_routes_are_confined(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import docling_desk.app as web

    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    client = TestClient(web.app)

    folder = desk_config.DATA / ("b" * 32)
    folder.mkdir()
    save_job(folder, Job(id="b" * 32, filename="sample.xlsx"))
    assert client.get("/api/jobs/" + "b" * 32 + "/tables").status_code == 409
    doc = DoclingDocument(name="table-api")
    doc.add_table(data=TableData(num_rows=0, num_cols=0, table_cells=[]))
    doc.save_as_json(folder / "document.json")
    save_job(folder, Job(id="b" * 32, filename="sample.xlsx", state="success"))
    result = client.get("/api/jobs/" + "b" * 32 + "/tables")
    assert result.status_code == 200 and result.json()[0]["rows"] == []
    assert client.get("/api/jobs/invalid/tables").status_code == 404
    assert client.get("/static/vendor/manifest.json").status_code == 404
    assert client.get("/static/vendor/ag-grid-community.min.js").status_code == 200
    assert client.get("/static/tables.js").status_code == 200
    assert client.get("/api/jobs/" + "b" * 32 + "/tables/0/csv").status_code == 404
