"""Read-only grid projection of official Docling tables with source coordinates."""

from __future__ import annotations

from pathlib import Path

from docling_core.types.doc import DoclingDocument
from pydantic import BaseModel

from docling_desk.documents.pagination import sheet_names
from docling_desk.documents.text import DOCUMENT_SUFFIXES


class TableView(BaseModel):
    ref: str
    label: str
    source_label: str
    pages: list[int]
    rows: list[list[str]]
    columns: int
    header_rows: int
    merged: bool


def project_tables(doc: DoclingDocument, filename: str) -> list[TableView]:
    suffix = Path(filename).suffix.lower()
    unit = {".xlsx": "シート", ".pptx": "スライド"}.get(suffix, "ページ")
    names = sheet_names(doc) if suffix == ".xlsx" else {}
    result: list[TableView] = []
    for index, table in enumerate(doc.tables, start=1):
        data = table.data
        rows = [[""] * data.num_cols for _ in range(data.num_rows)]
        marked_headers: set[int] = set()
        for cell in data.table_cells:
            row, col = cell.start_row_offset_idx, cell.start_col_offset_idx
            if 0 <= row < data.num_rows and 0 <= col < data.num_cols:
                rows[row][col] = cell.text
            if cell.column_header:
                marked_headers.update(range(row, cell.end_row_offset_idx))
        headers = 0
        while headers < data.num_rows and headers in marked_headers:
            headers += 1
        pages = sorted({p.page_no for p in table.prov})
        source = " / ".join(
            f"{unit} {page}" + (f" · {names[page]}" if page in names else "") for page in pages
        ) or ("文書全体" if suffix in DOCUMENT_SUFFIXES else "ページ位置未取得")
        result.append(
            TableView(
                ref=table.self_ref,
                label=f"表 {index}",
                source_label=source,
                pages=pages,
                rows=rows,
                columns=data.num_cols,
                header_rows=headers,
                merged=any(c.row_span > 1 or c.col_span > 1 for c in data.table_cells),
            )
        )
    return result
