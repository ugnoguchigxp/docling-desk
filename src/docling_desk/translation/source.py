"""Bind visible text to its existing HTML node; never recreate document layout."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from docling_core.types.doc import DoclingDocument, TableItem, TextItem
from lxml import html
from openpyxl import load_workbook

from docling_desk.documents.text import DOCUMENT_SUFFIXES
from docling_desk.preview.editable_preview import ensure_pdf_preview
from docling_desk.preview.sheets import preview_sheets, sheet_html
from docling_desk.storage import job_file, original_file
from docling_desk.translation.store import LOCK, atomic_json, fingerprint

BINDING_VERSION = 1


def text_candidate(value: str) -> bool:
    return any(c.isalpha() for c in value)


def serialize(tree, raw: str) -> str:
    # libxml supplies a default HTML4 doctype even when the source has none.
    # Keep the source's rendering mode rather than emitting that default.
    declaration = re.match(r"\s*(<!doctype[^>]*>)", raw, re.I)
    return html.tostring(tree, encoding="unicode", doctype=declaration[1] if declaration else None)


def slide_template(folder: Path, relative: str) -> str:
    folder = folder.resolve()
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError("スライドのプレビューが不正です。")
    raw = path.read_text(encoding="utf-8")
    tree = html.fromstring(raw)
    for node in tree.xpath("//script|//iframe|//object|//embed|//base|//form|//meta[@http-equiv]"):
        node.drop_tree()
    for node in tree.iter():
        for attribute in list(node.attrib):
            if attribute.lower().startswith("on"):
                del node.attrib[attribute]
        for attribute in ("src", "href"):
            value = node.get(attribute)
            if value is None or value.startswith(("data:image/", "#")):
                continue
            target = (path.parent / value).resolve()
            if target.is_relative_to(folder.resolve()) and target.is_file():
                node.set(attribute, f"/files/{folder.name}/{target.relative_to(folder).as_posix()}")
            else:
                del node.attrib[attribute]
    return serialize(tree, raw)


def template_for(folder: Path, job: dict, number: int) -> str:
    suffix = Path(job.get("original_filename") or job["filename"]).suffix.lower()
    if suffix == ".pptx":
        slides = json.loads((folder / "slides.json").read_text())["slides"]
        slide = next(s for s in slides if s["number"] == number)
        return slide_template(folder, slide["preview"])
    if suffix == ".pdf":
        ensure_pdf_preview(original_file(folder, ".pdf"), folder)
        return slide_template(folder, f"editable-preview/page-{number}.html")
    if suffix in DOCUMENT_SUFFIXES:
        if number != 1:
            raise ValueError("この形式は文書全体を表示します。")
        return slide_template(folder, job["preview"])
    sheets = preview_sheets(folder, job["preview"])
    sheet = next(s for s in sheets if s["number"] == number)
    return sheet_html(folder, folder.name, sheet)


def html_segments(raw: str, roots: list) -> list[dict]:
    tree = html.fromstring(raw)
    scope = roots or [tree.find("body")]
    segments = []
    # Callers use roots parsed from this exact string, with paths into this tree.
    for root in scope:
        for node in root.iter():
            if not isinstance(node.tag, str) or node.tag in {"script", "style"}:
                continue
            for slot in ("text", "tail"):
                value = getattr(node, slot)
                if node is root and slot == "tail":
                    continue
                if value and text_candidate(value):
                    segments.append(
                        {
                            "id": f"t{len(segments) + 1:05d}",
                            "source_text": value,
                            "locator": {
                                "node_path": node.getroottree().getpath(node),
                                "slot": slot,
                            },
                        }
                    )
    return segments


def worksheet_segments(raw: str, workbook, number: int) -> tuple[list[dict], int]:
    tree = html.fromstring(raw)
    worksheet = workbook.worksheets[number - 1]
    segments, excluded = [], 0
    hidden_columns = set()
    for dimension in worksheet.column_dimensions.values():
        if dimension.hidden:
            hidden_columns.update(range(dimension.min, dimension.max + 1))
    visible_columns = [
        n for n in range(1, max(worksheet.max_column, 1) + 1) if n not in hidden_columns
    ]
    occupied = set()
    for table in tree.xpath('//table[contains(concat(" ", @class, " "), " worksheet ")]'):
        rows = table.xpath("./tr|./tbody/tr")
        visible_rows = [row for row in rows if row.xpath('./th[@class="sheet-row-axis"]')]
        row_numbers = [
            int(row.xpath('string(./th[@class="sheet-row-axis"])')) for row in visible_rows
        ]
        for row_index, row in enumerate(visible_rows):
            axis = row.xpath('./th[@class="sheet-row-axis"]')
            if not axis:
                continue
            row_no = int(axis[0].text)
            col_index = 0
            for cell in row.xpath("./td"):
                while (row_no, col_index) in occupied:
                    col_index += 1
                colspan, rowspan = int(cell.get("colspan", "1")), int(cell.get("rowspan", "1"))
                for r in row_numbers[row_index : row_index + rowspan]:
                    for c in range(col_index, col_index + colspan):
                        occupied.add((r, c))
                candidate = None
                if col_index < len(visible_columns):
                    candidate = worksheet.cell(row_no, visible_columns[col_index])
                value = "".join(cell.itertext()).strip()
                # Require exact source text as well as position and type. Uncertain cells are never guessed.
                if (
                    candidate is not None
                    and candidate.data_type in {"s", "inlineStr"}
                    and isinstance(candidate.value, str)
                    and value == candidate.value.strip()
                ):
                    bound = html_segments(raw, [cell])
                    for segment in bound:
                        segment["id"] = f"t{len(segments) + 1:05d}"
                        segment["cell_ref"] = candidate.coordinate
                        segments.append(segment)
                elif text_candidate(value) and (
                    candidate is None or candidate.data_type in {"s", "inlineStr"}
                ):
                    excluded += 1
                col_index += colspan
    return segments, excluded


def document_segments(doc: DoclingDocument, number: int | None) -> list[dict]:
    segments = []
    for item, _ in doc.iterate_items():
        if not isinstance(item, (TextItem, TableItem)):
            continue
        if number is not None and (not item.prov or item.prov[0].page_no != number):
            continue
        parent = item.parent.resolve(doc) if item.parent else None
        if parent is not None and parent.label.value == "picture":
            continue
        values = (
            [(item.text, {"ref": item.self_ref})]
            if isinstance(item, TextItem)
            else [
                (
                    cell.text,
                    {
                        "ref": item.self_ref,
                        "row": cell.start_row_offset_idx,
                        "col": cell.start_col_offset_idx,
                    },
                )
                for cell in item.data.table_cells
            ]
        )
        for value, locator in values:
            if value and text_candidate(value):
                segments.append(
                    {"id": f"t{len(segments) + 1:05d}", "source_text": value, "locator": locator}
                )
    return segments


def build_source(folder: Path) -> dict:
    job = json.loads((job_file(folder)).read_text())
    suffix = Path(job.get("original_filename") or job["filename"]).suffix.lower()
    original = original_file(folder, suffix)
    kind = (
        "document"
        if suffix in DOCUMENT_SUFFIXES
        else {".pptx": "slide", ".xlsx": "sheet", ".pdf": "page"}[suffix]
    )
    doc = DoclingDocument.load_from_json(folder / "document.json")
    units = []
    workbook = load_workbook(original, data_only=False) if kind == "sheet" else None
    numbers = sorted(doc.pages)
    if kind == "document":
        numbers = [1]
    if kind == "slide":
        numbers = [s["number"] for s in json.loads((folder / "slides.json").read_text())["slides"]]
    if kind == "sheet":
        numbers = [s["number"] for s in preview_sheets(folder, job["preview"])]
    try:
        for number in numbers:
            unit = {
                "id": f"{kind}-{number}",
                "kind": kind,
                "number": number,
                "mode": "panel",
                "excluded_count": 0,
            }
            raw = ""
            if kind != "document" or job.get("preview"):
                raw = template_for(folder, job, number)
                tree = html.fromstring(raw)
                if kind == "sheet":
                    segments, excluded = worksheet_segments(raw, workbook, number)
                    unit.update(segments=segments, excluded_count=excluded, mode="replace")
                else:
                    roots = tree.xpath(
                        '//div[contains(concat(" ",normalize-space(@class)," ")," slide ")]'
                    )
                    segments = html_segments(raw, roots)
                    if segments:
                        unit.update(segments=segments, mode="replace")
                assets = {}
                for url in tree.xpath('//link[@rel="stylesheet"]/@href'):
                    prefix = f"/files/{folder.name}/"
                    if url.startswith(prefix):
                        path = (folder / url[len(prefix) :]).resolve()
                        if path.is_relative_to(folder.resolve()) and path.is_file():
                            assets[url] = hashlib.sha256(path.read_bytes()).hexdigest()
                unit["template_hash"] = fingerprint([raw, assets])
            if unit["mode"] == "panel":
                unit["segments"] = document_segments(doc, None if kind == "document" else number)
            unit["source_hash"] = fingerprint([BINDING_VERSION, unit])
            units.append(unit)
    finally:
        if workbook:
            workbook.close()
    return {
        "schema_version": 1,
        "binding_version": BINDING_VERSION,
        "document_id": folder.name,
        "original_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "unlocated_count": sum(
            isinstance(i, (TextItem, TableItem)) and not i.prov for i, _ in doc.iterate_items()
        )
        if kind != "document"
        else 0,
        "units": units,
    }


def source_map(folder: Path) -> dict:
    folder = folder.resolve()
    with LOCK:
        source = build_source(folder)
        # A source edit invalidates every stored result, even if one unit's visible text happens to match.
        for unit in source["units"]:
            unit["source_hash"] = fingerprint([unit["source_hash"], source["original_sha256"]])
        path = folder / "translation-source.json"
        if not path.exists() or json.loads(path.read_text()) != source:
            atomic_json(path, source)
        return source
