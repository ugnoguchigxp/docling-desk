"""Immutable document inputs; copied document IDs do not change content identity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from docling_core.types.doc import DoclingDocument, TableItem, TextItem

from docling_desk.documents.pagination import sheet_names
from docling_desk.documents.rag import picture_refs
from docling_desk.documents.tables import project_tables
from docling_desk.documents.text import DOCUMENT_SUFFIXES
from docling_desk.storage import job_file, original_file

HASH_VERSION = 1


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_json(path: Path) -> dict:
    if path.stat().st_size > 64 * 1024**2:
        raise ValueError("原文データが読込上限を超えています。")
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value: object, document_id: str, path: tuple[str, ...] = ()) -> object:
    if isinstance(value, list):
        return [canonical(item, document_id, path) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            rag_row = not path and "id" in value and "source_sha256" in value
            if (key == "source" and rag_row) or (key == "filename" and path == ("origin",)):
                continue  # Only known provenance fields are excluded.
            if key == "name" and not path and value.get("schema_name") == "DoclingDocument":
                continue
            if rag_row and key in {"id", "parent_id"} and isinstance(item, str):
                # Test fixtures and legacy copies may contain a previous job prefix.
                parts = item.split(":", 1)
                if len(parts) == 2 and len(parts[0]) == 32:
                    try:
                        int(parts[0], 16)
                        item = "document:" + parts[1]
                    except ValueError:
                        pass
            result[key] = canonical(item, document_id, (*path, key))
        return result
    return value


def snapshot(folder: Path) -> dict:
    job = read_json(job_file(folder))
    suffix = Path(job.get("original_filename") or job["filename"]).suffix.lower()
    paths = [
        original_file(folder, suffix),
        folder / "document.json",
        folder / "rag.jsonl",
        folder / "rag-index.jsonl",
    ]
    for _ in range(2):
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        raw = read_json(paths[1])
        parents = [json.loads(line) for line in paths[2].read_text().splitlines() if line.strip()]
        children = [json.loads(line) for line in paths[3].read_text().splitlines() if line.strip()]
        after = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        if before == after:
            break
    else:
        raise ValueError("原文が更新中です。完了後に実行してください。")
    known = {row["id"] for row in parents}
    if len(known) != len(parents) or len({row["id"] for row in children}) != len(children):
        raise ValueError("検索データのIDが重複しています。")
    for row in parents + children:
        if row.get("source_sha256") != before[0]:
            raise ValueError("原本と検索データが一致しません。")
        if row.get("parent_id") and row["parent_id"] not in known:
            raise ValueError("検索データの親参照が不正です。")
    doc = DoclingDocument.model_validate(raw)
    excluded = picture_refs(doc)
    kind = (
        "document"
        if suffix in DOCUMENT_SUFFIXES
        else {".pdf": "page", ".pptx": "slide", ".xlsx": "sheet"}[suffix]
    )
    count = 1 if kind == "document" else job.get("pages", 0)
    names = sheet_names(doc) if kind == "sheet" else {}
    table_views = {t.ref: t.model_dump() for t in project_tables(doc, job["filename"])}
    units: dict[int, dict] = {
        n: {
            "id": f"{kind}-{n}",
            "kind": kind,
            "number": n,
            "name": names.get(n, ""),
            "blocks": [],
            "tables": [],
            "excluded_pictures": 0,
        }
        for n in range(1, count + 1)
    }
    unlocated = 0
    for picture in doc.pictures:
        if kind == "document":
            units[1]["excluded_pictures"] += 1
        elif picture.prov and picture.prov[0].page_no in units:
            units[picture.prov[0].page_no]["excluded_pictures"] += 1
    for item, _ in doc.iterate_items():
        if not isinstance(item, (TextItem, TableItem)) or item.self_ref in excluded:
            continue
        if not item.prov and kind != "document":
            unlocated += 1
            continue
        number = 1 if kind == "document" else item.prov[0].page_no
        if number not in units:
            unlocated += 1
            continue
        pages = sorted({p.page_no for p in item.prov})
        if isinstance(item, TableItem):
            table = dict(table_views[item.self_ref])
            table["cells"] = [c.model_dump(mode="json") for c in item.data.table_cells]
            units[number]["tables"].append(table)
            if not any(cell.strip() for row in table["rows"] for cell in row):
                continue
            text = json.dumps(
                {"header_rows": table["header_rows"], "rows": table["rows"]}, ensure_ascii=False
            )
            block_kind = "table"
        else:
            text = item.text
            block_kind = item.label.value
        if text.strip():
            units[number]["blocks"].append(
                {"id": item.self_ref, "text": text, "kind": block_kind, "pages": pages}
            )
    checksum = fingerprint(
        [
            HASH_VERSION,
            before[0],
            canonical(raw, folder.name),
            canonical(parents, folder.name),
            canonical(children, folder.name),
        ]
    )
    return {
        "document_id": folder.name,
        "source_hash": checksum,
        "hash_version": HASH_VERSION,
        "original_sha256": before[0],
        "source_name": job.get("original_filename") or job["filename"],
        "extraction_state": job["state"],
        "unlocated_count": unlocated,
        "units": list(units.values()),
        "parents": parents,
        "children": children,
    }


def current_hash(folder: Path) -> str | None:
    try:
        return snapshot(folder)["source_hash"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def parts(unit: dict, max_chars: int = 8000, max_parts: int = 8) -> list[list[dict]]:
    """Preserve full text; split tables at rows, exceptional long rows at characters."""
    pieces: list[dict] = []
    tables = {t["ref"]: t for t in unit["tables"]}
    for block in unit["blocks"]:
        table = tables.get(block["id"])
        if table and len(block["text"]) > max_chars:
            texts = [
                (row, json.dumps(cells, ensure_ascii=False))
                for row, cells in enumerate(table["rows"])
            ]
            header = table["rows"][: table["header_rows"]]
        else:
            texts, header = [(None, block["text"])], None
        for row, text in texts:
            for start in range(0, len(text), max_chars):
                end = min(start + max_chars, len(text))
                piece = {
                    **block,
                    "text": text[start:end],
                    "start": start,
                    "end": end,
                    "part_id": f"{block['id']}:{row if row is not None else 'text'}:{start}",
                }
                if row is not None and table is not None:
                    piece.update(
                        row_start=row,
                        row_end=row + 1,
                        column_headers=header,
                        header_rows=table["header_rows"],
                    )
                if start or end < len(text):
                    piece.update(
                        context_before=text[max(0, start - 200) : start],
                        context_after=text[end : end + 200],
                    )
                piece["original_ref"] = piece.pop("id")
                piece["numbers"] = [
                    {
                        "value": m.group(),
                        "start": start + m.start(),
                        "end": start + m.end(),
                        "context": text[max(0, start + m.start() - 30) : start + m.end() + 30],
                    }
                    for m in re.finditer(
                        r"[+-]?\d+(?:[.,]\d+)*(?:\s*(?:%|％|円|件|人|秒|分|時間|日|年|kg|km|MiB|GB|倍))?",
                        piece["text"],
                    )
                ]
                pieces.append(piece)
    groups, current, size = [], [], 0
    for piece in pieces:
        length = len(piece["text"])
        if current and size + length > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(piece)
        size += length
    if current:
        groups.append(current)
    if len(groups) > max_parts:
        raise ValueError("解説対象が上限（8部分）を超えています。")
    return groups
