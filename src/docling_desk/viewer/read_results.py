"""Typed MCP read results; HTTP page-image bytes become native MCP image content."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class VersionedRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    source_revision: UUID
    evidence_revision: UUID


class OutlineResult(VersionedRead):
    kind: Literal["page", "slide", "sheet", "document"]
    units: list[dict]
    entries: list[dict]
    total: int
    next_offset: int | None


class SectionResult(VersionedRead):
    kind: Literal["page", "slide", "sheet", "document"]
    selector: dict
    elements: list[dict]
    chars: int


class TableInfo(BaseModel):
    table_ref: str
    caption: str
    pages: list[int]
    rows: int
    columns: int
    header_rows: int
    merged: bool
    provenance: list[dict]


class TablesResult(VersionedRead):
    tables: list[TableInfo]
    total: int
    next_offset: int | None


class TableResult(VersionedRead, TableInfo):
    row_offset: int
    column_offset: int
    next_row_offset: int | None
    next_column_offset: int | None
    format: Literal["json", "csv"]
    cells: list[dict]
    data: list[list[str]]
    csv: str | None = None


class PageImageResult(VersionedRead):
    kind: Literal["page", "slide"]
    unit: int
    media_type: Literal["image/webp"]
    width: int
    height: int
