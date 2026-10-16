"""Bounded reads of accepted Docling artifacts; never extract or interpret intent."""

from __future__ import annotations

import base64
import csv
import json
from io import BytesIO, StringIO
from pathlib import Path
from typing import Literal

from docling_core.types.doc import (
    DocItem,
    DocItemLabel,
    DoclingDocument,
    SectionHeaderItem,
    TableItem,
    TextItem,
)
from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from docling_desk.documents.conversion import Job
from docling_desk.documents.pagination import sheet_names
from docling_desk.documents.tables import table_header_rows, table_rows
from docling_desk.preview.pdf_view import render_pdf_page
from docling_desk.preview.thumbnails import encode_thumbnail, thumbnail
from docling_desk.storage import job_file, original_file

MAX_SECTION_TABLE_CELLS = 5000


class ReadOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["outline", "section", "tables", "table", "page-image"]
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=100, ge=1, le=200)
    selector: dict | None = None
    max_chars: int = Field(default=40000, ge=1, le=80000)
    table_ref: str | None = Field(default=None, min_length=1, max_length=256)
    row_offset: int = Field(default=0, ge=0, le=1000000)
    row_limit: int = Field(default=100, ge=1, le=100)
    column_offset: int = Field(default=0, ge=0, le=1000000)
    column_limit: int = Field(default=50, ge=1, le=50)
    format: Literal["json", "csv"] = "json"
    unit: int | None = Field(default=None, ge=1, le=100000)

    @model_validator(mode="after")
    def required_options(self):
        if self.operation == "section" and self.selector is None:
            raise ValueError("selector required")
        if self.operation == "table" and self.table_ref is None:
            raise ValueError("table_ref required")
        if self.operation == "page-image" and self.unit is None:
            raise ValueError("unit required")
        return self


def bounded(result: dict) -> dict:
    if len(json.dumps(result, ensure_ascii=False).encode()) > 1900000:
        raise HTTPException(413, "document_read_too_large")
    return result


def heading_level(item: DocItem) -> int | None:
    if isinstance(item, TextItem) and item.label == DocItemLabel.TITLE:
        return 0
    return item.level if isinstance(item, SectionHeaderItem) else None


def read_document(folder: Path, options: ReadOptions) -> dict:
    job = Job.model_validate_json(job_file(folder).read_text())
    if job.state != "success":
        raise HTTPException(409, "document_read_not_ready")
    kind = {".pdf": "page", ".pptx": "slide", ".xlsx": "sheet"}.get(
        Path(job.filename).suffix.lower(), "document"
    )
    if options.operation == "page-image":
        if kind not in {"page", "slide"}:
            raise HTTPException(422, "page_image_unsupported")
        number = options.unit
        if number is None or not 1 <= number <= job.pages:
            raise HTTPException(422, "invalid_document_range")
        if kind == "page":
            data = render_pdf_page(original_file(folder, ".pdf"), number, 1.5)
        else:
            # Reuse the native slide preview and its existing cache/renderer.
            data = thumbnail(folder, number).read_bytes()
        with Image.open(BytesIO(data)) as image:
            data = encode_thumbnail(image, size=(1536, 1536), max_bytes=1024 * 1024)
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
        return {
            "kind": kind,
            "unit": number,
            "media_type": "image/webp",
            "width": width,
            "height": height,
            "body_base64": base64.b64encode(data).decode(),
        }

    if (folder / "document.json").is_symlink():
        raise HTTPException(409, "document_read_not_ready")
    doc = DoclingDocument.load_from_json(folder / "document.json")
    entries, items = [], []
    for item, level in doc.iterate_items():
        if not isinstance(item, DocItem):
            continue
        items.append(item)
        entries.append(
            {
                "ref": item.self_ref,
                "label": item.label.value,
                "level": heading_level(item) if heading_level(item) is not None else level,
                "parent": item.parent.cref if item.parent else None,
                "pages": sorted({p.page_no for p in item.prov}) if kind != "document" else [],
                "heading": item.text if heading_level(item) is not None else None,
            }
        )
    names = sheet_names(doc) if kind == "sheet" else {}
    units = (
        [{"number": n, "name": names.get(n)} for n in sorted(doc.pages)]
        if kind != "document"
        else []
    )
    if options.operation == "outline":
        end = options.offset + options.limit
        return bounded(
            {
                "kind": kind,
                "units": units,
                "entries": entries[options.offset : end],
                "total": len(entries),
                "next_offset": end if end < len(entries) else None,
            }
        )

    if options.operation == "section":
        selector = options.selector or {}
        refs = [entry["ref"] for entry in entries]
        selected = []
        try:
            match selector:
                case {"kind": "section", "ref": ref} if set(selector) == {"kind", "ref"}:
                    start = refs.index(ref)
                    start_level = heading_level(items[start])
                    if start_level is None:
                        raise ValueError()
                    end = next(
                        (
                            i
                            for i in range(start + 1, len(items))
                            if heading_level(items[i]) is not None
                            and heading_level(items[i]) <= start_level
                        ),
                        len(items),
                    )
                    selected = list(range(start, end))
                case {"kind": "elements", "start_ref": first, "end_ref": last} if set(selector) == {
                    "kind",
                    "start_ref",
                    "end_ref",
                }:
                    start, end = refs.index(first), refs.index(last)
                    if end < start:
                        raise ValueError()
                    selected = list(range(start, end + 1))
                case {"kind": "units", "start": start, "end": end} if set(selector) == {
                    "kind",
                    "start",
                    "end",
                }:
                    known = {u["number"] for u in units}
                    if (
                        type(start) is not int
                        or type(end) is not int
                        or start > end
                        or start < 1
                        or end - start >= 100
                        or not set(range(start, end + 1)) <= known
                    ):
                        raise ValueError()
                    selected = [
                        i
                        for i, e in enumerate(entries)
                        if any(start <= n <= end for n in e["pages"])
                    ]
                case _:
                    raise ValueError()
        except ValueError:
            raise HTTPException(422, "invalid_document_range") from None
        result = []
        chars = 0
        for i in selected:
            item = items[i]
            # Docling's Markdown serializer expands the entire dense grid before
            # producing text. Bound that work before allocation, like table reads.
            if (
                isinstance(item, TableItem)
                and item.data.num_rows * item.data.num_cols > MAX_SECTION_TABLE_CELLS
            ):
                raise HTTPException(413, "document_read_too_large")
            text = (
                item.export_to_markdown(doc=doc)
                if isinstance(item, TableItem)
                else item.text
                if isinstance(item, TextItem)
                else ""
            )
            chars += len(text)
            if chars > options.max_chars or len(result) >= 1000:
                raise HTTPException(413, "document_read_too_large")
            result.append(
                {
                    **entries[i],
                    "text": text,
                    "provenance": [p.model_dump(mode="json") for p in item.prov],
                }
            )
        return bounded({"kind": kind, "selector": selector, "elements": result, "chars": chars})

    def table_info(table):
        return {
            "table_ref": table.self_ref,
            "caption": table.caption_text(doc),
            "pages": sorted({p.page_no for p in table.prov}) if kind != "document" else [],
            "rows": table.data.num_rows,
            "columns": table.data.num_cols,
            "header_rows": table_header_rows(table),
            "merged": any(c.row_span > 1 or c.col_span > 1 for c in table.data.table_cells),
            "provenance": [p.model_dump(mode="json") for p in table.prov],
        }

    if options.operation == "tables":
        end = options.offset + options.limit
        return bounded(
            {
                "tables": [table_info(t) for t in doc.tables[options.offset : end]],
                "total": len(doc.tables),
                "next_offset": end if end < len(doc.tables) else None,
            }
        )
    table = next((t for t in doc.tables if t.self_ref == options.table_ref), None)
    if table is None:
        raise HTTPException(404, "document_table_not_found")
    r, c = options.row_offset, options.column_offset
    if r >= table.data.num_rows or c >= table.data.num_cols:
        raise HTTPException(422, "invalid_document_range")
    rend, cend = (
        min(r + options.row_limit, table.data.num_rows),
        min(c + options.column_limit, table.data.num_cols),
    )
    rows = table_rows(table, r, rend, c, cend)
    cells = [
        cell.model_dump(mode="json")
        for cell in table.data.table_cells
        if cell.start_row_offset_idx < rend
        and cell.end_row_offset_idx > r
        and cell.start_col_offset_idx < cend
        and cell.end_col_offset_idx > c
    ]
    result = {
        **table_info(table),
        "row_offset": r,
        "column_offset": c,
        "next_row_offset": rend if rend < table.data.num_rows else None,
        "next_column_offset": cend if cend < table.data.num_cols else None,
        "format": options.format,
        "cells": cells,
        "data": rows,
    }
    if options.format == "csv":
        stream = StringIO(newline="")
        csv.writer(stream).writerows(rows)
        result["csv"] = stream.getvalue()
    return bounded(result)
