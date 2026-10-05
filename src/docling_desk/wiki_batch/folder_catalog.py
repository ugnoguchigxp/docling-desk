"""Describe each original export folder without duplicating the Wiki corpus."""

import csv
import re


def catalog(root, sources, directories, records, title_rules=None):
    title_rules = title_rules or {}
    folders = []
    for directory in directories:
        pages = [r for r in records if any(s["collection"] == directory.name for s in r["sources"])]
        if not pages:
            raise ValueError(f"Export folder has no Wiki records: {directory.name}")
        collections = {r["collection"] for r in pages}
        if len(collections) != 1:
            raise ValueError(f"Export folder has multiple collections: {directory.name}")
        collection = next(iter(collections))
        csvs = sorted(directory.glob("*.csv"))
        csv_path = csvs[0] if csvs else None
        columns, rows = [], []
        if csv_path:
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                columns, rows = reader.fieldnames or [], list(reader)
        title, description, requested_fields = title_rules.get(
            directory.name, (directory.name, "元のフォルダーに収録された資料です。", columns[:3])
        )
        fields = [field for field in requested_fields if field in columns]
        evidence = []
        for field in fields:
            for row_number, row in enumerate(rows, 2):
                if row.get(field, "").strip():
                    value = row[field].strip()
                    evidence.append(
                        {
                            "field": field,
                            "csv_row": row_number,
                            "text": value[:240],
                            "truncated": len(value) > 240,
                        }
                    )
                    break
        heading = None
        if not csv_path:
            markdown = next(iter(sorted(directory.glob("*.md"))), None)
            match = (
                re.search(r"^#\s+(.+)$", markdown.read_text(encoding="utf-8-sig"), re.M)
                if markdown
                else None
            )
            heading = (
                {"path": markdown.relative_to(root).as_posix(), "heading": match[1]}
                if match
                else None
            )
            if directory.name not in title_rules and heading:
                title = heading["heading"]
        folders.append(
            {
                "id": directory.name,
                "title": title,
                "description": description,
                "source_path": directory.relative_to(root).as_posix(),
                "source_subfolders": [
                    p.relative_to(directory).as_posix()
                    for p in sorted(directory.rglob("*"))
                    if p.is_dir()
                ],
                "collection": collection,
                "count": len(pages),
                "translated": sum(
                    r["translation_status"] in {"translated", "reviewed"} for r in pages
                ),
                "path": f"wiki/pages/folders/{directory.name}/index.md",
                "original_index_path": f"wiki/pages/original/{collection}/index.csv",
                "ja_index_path": f"wiki/pages/ja/{collection}/index.csv",
                "title_basis": {
                    "kind": "csv_content_summary" if csv_path else "markdown_heading",
                    "path": csv_path.relative_to(root).as_posix()
                    if csv_path
                    else heading["path"]
                    if heading
                    else None,
                    "columns": fields,
                    "evidence": evidence,
                    "heading": heading["heading"] if heading else None,
                },
                "duplicate_of": None,
            }
        )
    for folder in folders:
        others = [f for f in folders if f["collection"] == folder["collection"]]
        preferred = next((f for f in others if f["id"] == folder["collection"]), others[0])
        if folder != preferred:
            folder["duplicate_of"] = preferred["id"]
    return folders
