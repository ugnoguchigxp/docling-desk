"""Read-only grid projection of official Docling tables with source coordinates."""

from __future__ import annotations

from pathlib import Path

from docling_core.types.doc import DoclingDocument, TableItem
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from docling_desk.documents.pagination import sheet_names
from docling_desk.documents.text import DOCUMENT_SUFFIXES

TOO_LARGE = "document_too_large"


def load_bounded(path: Path, detail: str = TOO_LARGE) -> DoclingDocument:
    """Load a saved document; tables beyond docling-core's cell limit become 413.

    docling-core refuses tables over DOCLINGCORE_MAX_TABLE_CELLS (1,000,000 by default)
    at validation, before any grid is built. Documents saved by an older release may
    hold such a table; they stay on disk but cannot be opened. Other invalid data
    still raises, so a broken file is not mistaken for a large one.
    """
    try:
        return DoclingDocument.load_from_json(path)
    except ValidationError as error:
        if any("exceeds the limit" in str(item.get("msg", "")) for item in error.errors()):
            raise HTTPException(413, detail) from error
        raise


class TableView(BaseModel):
    ref: str
    label: str
    source_label: str
    pages: list[int]
    rows: list[list[str]]
    columns: int
    header_rows: int
    merged: bool


def table_rows(table: TableItem, row_start=0, row_end=None, col_start=0, col_end=None):
    """Project a rectangle without allocating the rest of a large table."""
    data = table.data
    row_end = data.num_rows if row_end is None else row_end
    col_end = data.num_cols if col_end is None else col_end
    rows = [[""] * (col_end - col_start) for _ in range(row_end - row_start)]
    for cell in data.table_cells:
        row, col = cell.start_row_offset_idx, cell.start_col_offset_idx
        if row_start <= row < row_end and col_start <= col < col_end:
            rows[row - row_start][col - col_start] = cell.text
    return rows


def table_header_rows(table: TableItem) -> int:
    marked = set()
    for cell in table.data.table_cells:
        if cell.column_header:
            marked.update(range(cell.start_row_offset_idx, cell.end_row_offset_idx))
    headers = 0
    while headers < table.data.num_rows and headers in marked:
        headers += 1
    return headers


def project_tables(doc: DoclingDocument, filename: str) -> list[TableView]:
    suffix = Path(filename).suffix.lower()
    unit = {".xlsx": "シート", ".pptx": "スライド"}.get(suffix, "ページ")
    names = sheet_names(doc) if suffix == ".xlsx" else {}
    result: list[TableView] = []
    for index, table in enumerate(doc.tables, start=1):
        data = table.data
        rows = table_rows(table)
        headers = table_header_rows(table)
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
