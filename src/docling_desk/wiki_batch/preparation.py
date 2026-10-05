#!/usr/bin/env python3
"""Preserve Notion exports and prepare linked Wiki pages and CSV contents tables."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from docling_desk.knowledge.catalog import safe_file

from .folder_catalog import catalog

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources" / "notion"
PAGES = ROOT / "wiki" / "pages"
MANIFESTS = ROOT / "manifests"
COLLECTIONS = {}


def configure(root: Path):
    global ROOT, SOURCES, PAGES, MANIFESTS, COLLECTIONS
    ROOT = root.resolve()
    SOURCES, PAGES, MANIFESTS = ROOT / "sources/notion", ROOT / "wiki/pages", ROOT / "manifests"
    config = MANIFESTS / "collections.json"
    COLLECTIONS = (
        json.loads(config.read_text())
        if config.exists()
        else {
            p.name: (
                re.sub(r"[^A-Za-z0-9_-]", "_", p.name).strip("_")
                or "collection-" + digest(p.name.encode())[:12],
                p.name,
            )
            for p in SOURCES.iterdir()
            if p.is_dir()
        }
        if SOURCES.exists()
        else {}
    )
    if not isinstance(COLLECTIONS, dict) or any(
        not isinstance(name, str)
        or not re.fullmatch(r"[^/\\\x00-\x1f.][^/\\\x00-\x1f]*", name)
        or not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(v, str) and v for v in value)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", value[0])
        for name, value in COLLECTIONS.items()
    ):
        raise ValueError("資料群の設定が不正です。")


# Export folder spellings are preserved in sources/notion. Only Wiki names change.
INDEX_COLUMNS = [
    "id",
    "title",
    "category",
    "page_path",
    "counterpart_path",
    "translation_status",
    "source_status",
    "region",
    "user_type",
    "priority",
]
NOTION_ID = re.compile(r"([a-f0-9]{32})(?:\.md)?$", re.I)
URL_PATTERN = re.compile(
    r"\[(?P<label>[^\]\n]*)\]\((?P<link>https?://[^\s)]+)\)"
    r"|(?P<bare>https?://[^\s)<>]+)"
)
INFRASTRUCTURE_DIRECTORIES = {
    "sources",
    "wiki",
    "manifests",
    "pipelines",
    "app",
    "data",
    "spec",
    "tests",
    "node_modules",
    ".git",
    "__pycache__",
    "api",
    "web",
    "shared",
    "scripts",
    "third_party",
    "dist-web",
    "test-results",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(value: str) -> str:
    # Deliberately case-sensitive: avg and AVG are different glossary entries.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def relative(target: Path, current: Path) -> str:
    return Path(os.path.relpath(target, current.parent)).as_posix()


def link_path(target: Path, current: Path) -> str:
    return "<" + relative(target, current) + ">"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == text.encode("utf-8"):
        return
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as temp:
        temp.write(text)
        temp.flush()
        os.fsync(temp.fileno())
        temporary = Path(temp.name)
    temporary.replace(path)


def read_json(path: Path, fallback):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def export_directories(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(
        p
        for p in base.iterdir()
        if p.is_dir()
        and not (base == ROOT and p.name in INFRASTRUCTURE_DIRECTORIES)
        and (list(p.glob("*.csv")) or list(p.glob("*.md")))
    )


def migrate() -> None:
    directories = export_directories(ROOT)
    if not directories:
        if (MANIFESTS / "source-files.json").exists():
            check_sources()
            return
        raise ValueError("No Notion export directories found")
    previous = read_json(MANIFESTS / "source-files.json", [])
    known = {item["source_path"] for item in previous}
    additions = []
    for directory in directories:
        if (SOURCES / directory.name).exists():
            raise ValueError(f"Archive destination already exists: {directory.name}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Source symlink requires explicit handling: {path}")
            if path.is_file():
                source_path = (Path("sources/notion") / path.relative_to(ROOT)).as_posix()
                if source_path in known:
                    raise ValueError(f"Duplicate archived source: {source_path}")
                additions.append(
                    {
                        "original_path": path.relative_to(ROOT).as_posix(),
                        "source_path": source_path,
                        "sha256": digest(path.read_bytes()),
                        "bytes": path.stat().st_size,
                    }
                )
    # Keep the recovery inventory before moving any folder. Source bytes are not rewritten.
    atomic_write(MANIFESTS / "source-files.json", dump(previous + additions) + "\n")
    SOURCES.mkdir(parents=True, exist_ok=True)
    for directory in directories:
        shutil.move(str(directory), str(SOURCES / directory.name))
    check_sources()


def check_sources() -> None:
    inventory = read_json(MANIFESTS / "source-files.json", [])
    if not inventory:
        raise ValueError("Source inventory is missing")
    expected = {item["source_path"] for item in inventory}
    actual = {p.relative_to(ROOT).as_posix() for p in SOURCES.rglob("*") if p.is_file()}
    if expected != actual:
        raise ValueError(
            f"Source inventory mismatch: missing={len(expected - actual)}, "
            f"unregistered={len(actual - expected)}"
        )
    for item in inventory:
        path = safe_file(ROOT, item["source_path"])
        if digest(path.read_bytes()) != item["sha256"]:
            raise ValueError(f"Original source was changed: {item['source_path']}")


def parse_page(path: Path, columns: list[str]) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    heading = re.search(r"^# (.*)$", text, re.M)
    title = heading[1].strip() if heading else path.stem
    body = text[heading.end() :].lstrip("\n") if heading else text
    fields, preamble, current = {}, [], None
    keys = sorted(set(columns + ["ID", "Req. ID", "Design ID"]), key=len, reverse=True)
    # Only the leading Notion property block is parsed. Prose documents are kept whole.
    first_line = next((line for line in body.splitlines() if line.strip()), "")
    is_properties = any(first_line.startswith(key + ":") for key in keys)
    if is_properties:
        for line in body.splitlines():
            key = next((key for key in keys if line.startswith(key + ":")), None)
            if key:
                current = key
                fields[key] = line[len(key) + 1 :].lstrip()
            elif current:
                fields[current] += "\n" + line
            else:
                preamble.append(line)
        fields = {key: value.rstrip() for key, value in fields.items()}
    page_id_match = NOTION_ID.search(path.stem)
    return {
        "path": path,
        "title": title,
        "body": body,
        "properties": fields,
        "preamble": "\n".join(preamble),
        "is_properties": is_properties,
        "notion_page_id": page_id_match[1].lower() if page_id_match else None,
        "business_id": next(
            (
                fields.get(key, "").splitlines()[0].strip()
                for key in ["ID", "Req. ID", "Design ID"]
                if fields.get(key)
            ),
            None,
        ),
    }


def preferred_title(row: dict, fallback: str, collection: str) -> str:
    keys = [
        "User Input",
        "Phrase",
        "Parameter Name",
        "Backlog Item",
        "Requirement Short Title",
        "User Need Short Title",
        "Need",
        "Design Decision / Constraint",
        "Proposed Design Decision / Constraint",
        "Risk Control Description",
        "User Need",
    ]
    title = next((normalize(row[key]) for key in keys if row.get(key, "").strip()), fallback)
    if len(title) > 180:
        title = title[:177] + "…"
    return title or "Untitled"


def gather_records() -> list[dict]:
    records = {}
    for directory in export_directories(SOURCES):
        collection, label = COLLECTIONS.get(
            directory.name, (directory.name.lower(), directory.name)
        )
        csvs = sorted(directory.glob("*.csv"))
        if len(csvs) > 1:
            raise ValueError(f"Multiple CSVs need an explicit mapping: {directory}")
        rows, columns = [], []
        if csvs:
            with csvs[0].open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = reader.fieldnames or []
                rows = list(reader)
            if any(None in row or any(value is None for value in row.values()) for row in rows):
                raise ValueError(f"Malformed CSV: {csvs[0]}")
        id_column = next((key for key in ["ID", "Req. ID", "Design ID"] if key in columns), None)
        pages = [parse_page(path, columns) for path in sorted(directory.rglob("*.md"))]
        used = set()

        def add(row: dict, page: dict | None, row_number: int | None) -> None:
            business_id = (row.get(id_column, "").strip() if id_column else None) or (
                page["business_id"] if page else None
            )
            notion_id = page["notion_page_id"] if page else None
            title = preferred_title(
                row, page["title"] if page else business_id or "Untitled", collection
            )
            identifier = (
                business_id
                or notion_id
                or "doc-"
                + digest((collection + ":" + (page["path"].name if page else title)).encode())[:24]
            )
            filename = (
                identifier
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", identifier)
                else notion_id or digest(identifier.encode())[:24]
            )
            key = collection + "/" + filename
            source = {
                "collection": directory.name,
                "markdown": page["path"].relative_to(ROOT).as_posix() if page else None,
                "csv": csvs[0].relative_to(ROOT).as_posix() if csvs and row_number else None,
                "csv_row": row_number,
                "markdown_sha256": digest(page["path"].read_bytes()) if page else None,
            }
            record = {
                "key": key,
                "id": identifier,
                "collection": collection,
                "collection_label": label,
                "title_original": title,
                "notion_page_id": notion_id,
                "sources": [source],
                "source_status": "complete"
                if page and row_number
                else "csv_only"
                if row_number
                else "markdown_only",
                "source_properties": row,
                "category": next(
                    (
                        row.get(c, "")
                        for c in [
                            "Primary Group",
                            "Need Category",
                            "User Need Category",
                            "Parameter Category",
                            "Category",
                            "Group",
                            "Requirement Type",
                            "Design Type",
                        ]
                        if row.get(c)
                    ),
                    "",
                ),
                "region": row.get("Region", ""),
                "user_type": row.get("User Type", row.get("Impacted Users", "")),
                "priority": row.get("Prio", ""),
                "original_path": f"wiki/pages/original/{key}.md",
                "ja_path": f"wiki/pages/ja/{key}.md",
                "page": page,
            }
            if key in records:
                existing = records[key]
                old_page = existing["page"]
                if existing["source_properties"] != row or (
                    (old_page["body"] if old_page else "") != (page["body"] if page else "")
                ):
                    raise ValueError(f"Conflicting records with the same ID: {key}")
                existing["sources"].append(source)
            else:
                records[key] = record

        for number, row in enumerate(rows, 2):
            if id_column:
                candidates = [p for p in pages if p["business_id"] == row[id_column].strip()]
            else:
                candidates = [
                    p for p in pages if normalize(p["title"]) == normalize(row[columns[0]])
                ]
            if len(candidates) > 1:
                raise ValueError(
                    f"Ambiguous CSV row/page correspondence: {directory.name}:{number}"
                )
            page = candidates[0] if candidates else None
            if page:
                used.add(page["path"])
            add(row, page, number)
        for page in pages:
            if page["path"] not in used:
                add({}, page, None)
    return sorted(records.values(), key=lambda record: record["key"])


def frontmatter(meta: dict) -> str:
    # JSON scalar/flow values are valid YAML; source strings keep their exact meaning.
    return (
        "---\n"
        + "".join(key + ": " + dump(value) + "\n" for key, value in meta.items())
        + "---\n\n"
    )


def read_meta(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Wiki page needs frontmatter: {path}")
    block = text.split("---\n", 2)[1]
    return {
        key: json.loads(value)
        for key, value in (line.split(": ", 1) for line in block.splitlines() if line)
    }


def notion_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (
        host == "notion.com"
        or host.endswith(".notion.com")
        or host == "notion.so"
        or host.endswith(".notion.so")
        or host.endswith(".notion.site")
    ):
        return None
    match = re.search(
        r"([a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$", parsed.path, re.I
    )
    return match[1].replace("-", "").lower() if match else None


def convert_links(text: str, path: Path, notion_map: dict, unresolved: dict) -> str:
    def replace(match):
        url = match["link"] or match["bare"]
        page_id = notion_id_from_url(url)
        if not page_id:
            return match[0]
        target = notion_map.get(page_id)
        if target:
            label = match["label"] if match["link"] else target["id"]
            return f"[{label}]({relative(ROOT / target['original_path'], path)})"
        unresolved.setdefault(url, set()).add(path.relative_to(ROOT).as_posix())
        label = (match["label"] or "Notion参照") + "（未収録）"
        return f"[{label}]({url})"

    return URL_PATTERN.sub(replace, text)


def original_body(record: dict) -> str:
    page = record["page"]
    if page and not page["is_properties"]:
        return page["body"]
    fields = dict(page["properties"] if page else record["source_properties"])
    # CSV-only attributes remain visible; conflicting representations are both retained.
    conflicts = []
    for key, value in record["source_properties"].items():
        if value and key not in fields:
            fields[key] = value
        elif value and key in fields and normalize(value) != normalize(fields[key]):
            conflicts.append((key, value))
    small, long = [], []
    for key, value in fields.items():
        if not value:
            continue
        if "\n" in value or len(value) > 150:
            long.append((key, value))
        else:
            small.append((key, value))
    output = ""
    if record["source_status"] == "csv_only":
        output += (
            "> 個別のMarkdown原本が未収録のため、CSVの記載内容から構成した原文ページです。\n\n"
        )
    if small:
        output += "| Property | Value |\n|---|---|\n"
        output += (
            "".join(
                "| " + key.replace("|", "\\|") + " | " + value.replace("|", "\\|") + " |\n"
                for key, value in small
            )
            + "\n"
        )
    output += "".join(f"## {key}\n\n{value}\n\n" for key, value in long)
    for key, value in conflicts:
        output += f"## {key} — CSV representation\n\n{value}\n\n"
    return output


def make_csv(rows: list[dict]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=INDEX_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def build() -> dict:
    check_sources()
    records = gather_records()
    prior = (
        {
            record["key"]: record
            for record in (
                json.loads(line)
                for line in (MANIFESTS / "pages.jsonl").read_text().splitlines()
                if line.strip()
            )
        }
        if (MANIFESTS / "pages.jsonl").exists()
        else {}
    )
    notion_map = {}
    for record in records:
        if record["notion_page_id"]:
            if record["notion_page_id"] in notion_map:
                raise ValueError(f"Notion page belongs to multiple Wiki records: {record['key']}")
            notion_map[record["notion_page_id"]] = record
    unresolved = {}
    indexes = {}
    toc_translations = read_json(MANIFESTS / "toc-translations.json", {})
    for record in records:
        original, translated = ROOT / record["original_path"], ROOT / record["ja_path"]
        source_hash = digest(
            dump(
                {
                    "properties": record["source_properties"],
                    "body": record["page"]["body"] if record["page"] else "",
                }
            ).encode()
        )
        status = "untranslated"
        ja_title = "【未翻訳】" + record["title_original"]
        if translated.exists():
            meta = read_meta(translated)
            status = meta["translation_status"]
            ja_title = meta["title"]
            if "category_ja" in meta:
                record["category_ja"] = meta["category_ja"]
            old = prior.get(record["key"])
            if old and old.get("source_hash") != source_hash and status != "untranslated":
                raise ValueError(
                    f"Source changed; retain translation and reconcile explicitly: {record['key']}"
                )
        record.update(
            source_hash=source_hash,
            translation_status=status,
            title_ja=ja_title if status != "untranslated" else None,
        )
        toc = toc_translations.get(record["key"])
        if status == "untranslated" and toc and toc.get("source_hash") == source_hash:
            ja_title = toc["title"]
            record["category_ja"] = toc["category"]
            record["title_ja"] = ja_title
        meta = {
            "id": record["id"],
            "title": record["title_original"],
            "language": "original",
            "collection": record["collection"],
            "notion_page_id": record["notion_page_id"],
            "counterpart_path": relative(translated, original),
            "translation_status": status,
            "source_status": record["source_status"],
            "source_hash": source_hash,
            "source_properties": record["source_properties"],
        }
        nav = f"[目次](index.csv) · [日本語版]({link_path(translated, original)})"
        sources = []
        for source in record["sources"]:
            for field in ["markdown", "csv"]:
                if source[field]:
                    sources.append(
                        f"[{source['collection']} / {field}]({link_path(ROOT / source[field], original)})"
                    )
        body = convert_links(original_body(record), original, notion_map, unresolved)
        atomic_write(
            original,
            frontmatter(meta)
            + nav
            + "\n\n# "
            + record["title_original"]
            + "\n\n"
            + body.rstrip()
            + "\n\n## 書き出し原本\n\n"
            + "\n\n".join(sources)
            + "\n",
        )
        if not translated.exists():
            ja_meta = {
                **meta,
                "title": ja_title,
                "language": "ja",
                "counterpart_path": relative(original, translated),
            }
            atomic_write(
                translated,
                frontmatter(ja_meta)
                + f"[目次](index.csv) · [原文を見る]({link_path(original, translated)})\n\n"
                + "# "
                + ja_title
                + "\n\n"
                + "このページの本文はまだ日本語に翻訳されていません。\n\n"
                + f"内容は[原文ページ]({link_path(original, translated)})で確認できます。\n",
            )
        for language, path, counterpart, title in [
            ("original", original, translated, record["title_original"]),
            ("ja", translated, original, ja_title),
        ]:
            index = path.parent / "index.csv"
            indexes.setdefault(index, []).append(
                {
                    "id": record["id"],
                    "title": title,
                    "category": record.get("category_ja", record["category"])
                    if language == "ja"
                    else record["category"],
                    "page_path": path.name,
                    "counterpart_path": relative(counterpart, index),
                    "translation_status": status,
                    "source_status": record["source_status"],
                    "region": record["region"],
                    "user_type": record["user_type"],
                    "priority": record["priority"],
                }
            )
    source_order = {
        (r["collection"], r["id"]): min(
            (source["csv_row"] for source in r["sources"] if source["csv_row"]),
            default=float("inf"),
        )
        for r in records
    }
    for path, rows in indexes.items():
        # Preserve source CSV order; Markdown-only documents follow the CSV records.
        rows.sort(key=lambda row: source_order[(path.parent.name, row["id"])])
        atomic_write(path, make_csv(rows))
    manifest_records = [
        {key: value for key, value in record.items() if key != "page"} for record in records
    ]
    atomic_write(
        MANIFESTS / "pages.jsonl", "".join(dump(record) + "\n" for record in manifest_records)
    )
    folders = catalog(
        ROOT,
        SOURCES,
        export_directories(SOURCES),
        records,
        read_json(MANIFESTS / "folder-titles.json", {}),
    )
    atomic_write(
        MANIFESTS / "folders.json", json.dumps(folders, ensure_ascii=False, indent=2) + "\n"
    )
    for folder in folders:
        path = ROOT / folder["path"]
        meta = {"title": folder["title"], "kind": "folder", "source_folder": folder["id"]}
        body = (
            f"# {folder['title']}\n\n{folder['description']}\n\n"
            f"元のフォルダー：`{folder['id']}`\n\n収録文書：{folder['count']}件\n\n"
        )
        if folder["duplicate_of"]:
            other = next(f for f in folders if f["id"] == folder["duplicate_of"])
            body += (
                f"[{other['id']}]({link_path(ROOT / other['path'], path)})と同じ内容の書き出しです。"
                "フォルダーは別々に残し、本文と検索索引を共有しています。\n\n"
            )
        body += (
            "## 目次\n\n"
            f"- [日本語版の目次（CSV）]({link_path(ROOT / folder['ja_index_path'], path)})\n"
            f"- [原文の目次（CSV）]({link_path(ROOT / folder['original_index_path'], path)})\n\n"
        )
        basis = folder["title_basis"]
        body += "## タイトルの根拠\n\n"
        if basis["kind"] == "csv_content_summary":
            body += (
                "CSVの"
                + "、".join(f"`{field}`" for field in basis["columns"])
                + "の列と記載内容をもとに、日本語のフォルダータイトルを付けています。\n\n"
                f"[書き出し原本のCSV]({link_path(ROOT / basis['path'], path)})\n\n"
            )
            for example in basis["evidence"][:2]:
                text = example["text"].replace("\n", "\n> ")
                body += (
                    f"{example['field']}（CSVの{example['csv_row']}行目）\n\n> {text}"
                    + (" …（一部省略）" if example["truncated"] else "")
                    + "\n\n"
                )
        elif basis["path"]:
            body += (
                f"CSVがないため、原文の見出し「{basis['heading']}」をもとにしています。\n\n"
                f"[書き出し原本のMarkdown]({link_path(ROOT / basis['path'], path)})\n"
            )
        atomic_write(path, frontmatter(meta) + body)
    summary = {
        "pages_per_language": len(records),
        "csv_indexes": len(indexes),
        "folders": len(folders),
        "collections": {
            key: {
                "label": next(r["collection_label"] for r in records if r["collection"] == key),
                "pages": sum(r["collection"] == key for r in records),
            }
            for key in sorted({r["collection"] for r in records})
        },
        "csv_only": [r["key"] for r in records if r["source_status"] == "csv_only"],
        "markdown_only": [r["key"] for r in records if r["source_status"] == "markdown_only"],
        "aliased_exports": {
            r["key"]: [s["collection"] for s in r["sources"]]
            for r in records
            if len(r["sources"]) > 1
        },
        "unresolved_notion_references": [
            {"url": url, "from_pages": sorted(paths)} for url, paths in sorted(unresolved.items())
        ],
    }
    atomic_write(MANIFESTS / "import-report.json", dump(summary) + "\n")
    landing = (
        "# Wiki\n\n原文と日本語版を資料別に閲覧するための入口です。"
        "日本語本文は現在、翻訳準備中です。\n\n"
        f"元の{len(folders)}フォルダーをそれぞれ保持しています。"
        f"重複をまとめた本文は{len(records):,}ページです。\n\n"
        "| フォルダー | 元のフォルダー名 | 件数 | 日本語版の目次 | 原文の目次 |\n|---|---|---:|---|---|\n"
    )
    for folder in folders:
        landing += (
            f"| [{folder['title']}](folders/{folder['id']}/index.md) | {folder['id']} | "
            f"{folder['count']} | [開く]({relative(ROOT / folder['ja_index_path'], PAGES / 'index.md')}) | "
            f"[開く]({relative(ROOT / folder['original_index_path'], PAGES / 'index.md')}) |\n"
        )
    landing += (
        "\n各目次のページリンクと原文・日本語版のリンクは相対パスです。"
        "Wiki閲覧画面ではCSVを目次表として表示し、タイトルから本文を開けます。\n"
    )
    atomic_write(PAGES / "index.md", landing)
    check()
    return {
        "pages_per_language": len(records),
        "collections": len(summary["collections"]),
        "folders": len(folders),
        "csv_indexes": len(indexes),
        "csv_only": len(summary["csv_only"]),
        "markdown_only": len(summary["markdown_only"]),
        "aliased_records": len(summary["aliased_exports"]),
        "unresolved_notion_urls": len(unresolved),
    }


def check() -> dict:
    check_sources()
    records = [
        json.loads(line)
        for line in (MANIFESTS / "pages.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if len({r["key"] for r in records}) != len(records):
        raise ValueError("Duplicate Wiki identity")
    known_paths = set()
    for record in records:
        for kind, other in [("original_path", "ja_path"), ("ja_path", "original_path")]:
            path = ROOT / record[kind]
            meta = read_meta(path)
            if meta["id"] != record["id"]:
                raise ValueError(f"ID mismatch: {path}")
            if (path.parent / meta["counterpart_path"]).resolve() != (
                ROOT / record[other]
            ).resolve():
                raise ValueError(f"Counterpart mismatch: {path}")
            known_paths.add(path.resolve())
        if (
            read_meta(ROOT / record["ja_path"])["translation_status"]
            != record["translation_status"]
        ):
            raise ValueError(f"Translation status mismatch: {record['key']}")
    actual_pages = {
        p.resolve() for language in ["original", "ja"] for p in (PAGES / language).rglob("*.md")
    }
    if actual_pages != known_paths:
        raise ValueError("Unregistered or missing Wiki pages")
    indexed = set()
    for index in PAGES.rglob("index.csv"):
        with index.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            page = (index.parent / row["page_path"]).resolve()
            counterpart = (index.parent / row["counterpart_path"]).resolve()
            if page not in known_paths or counterpart not in known_paths:
                raise ValueError(f"Broken CSV page link: {index}:{row['id']}")
            if page in indexed:
                raise ValueError(f"Page indexed twice: {page}")
            indexed.add(page)
            meta = read_meta(page)
            if meta["id"] != row["id"] or meta["translation_status"] != row["translation_status"]:
                raise ValueError(f"CSV metadata mismatch: {index}:{row['id']}")
    if indexed != known_paths:
        raise ValueError("CSV indexes do not cover every Wiki page")
    folders = read_json(MANIFESTS / "folders.json", [])
    if {f["id"] for f in folders} != {p.name for p in export_directories(SOURCES)}:
        raise ValueError("Original export folders are missing from the Wiki catalog")
    folder_paths = {ROOT / f["path"] for f in folders}
    if folder_paths != set((PAGES / "folders").rglob("*.md")):
        raise ValueError("Unregistered or missing folder pages")
    for folder in folders:
        if read_meta(ROOT / folder["path"])["title"] != folder["title"]:
            raise ValueError(f"Folder title mismatch: {folder['id']}")
        for kind in ["original_index_path", "ja_index_path"]:
            with (ROOT / folder[kind]).open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            if len(rows) != folder["count"]:
                raise ValueError(f"Folder contents mismatch: {folder['id']}")
    # Check actual Markdown links, including original CSVs and folder descriptions.
    for path in (
        [PAGES / "index.md"] + sorted(folder_paths) + [Path(p) for p in sorted(known_paths)]
    ):
        text = path.read_text(encoding="utf-8").split("---\n", 2)[-1]
        for match in re.finditer(r"\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)\)", text):
            target = match[1].strip("<>")
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if not (path.parent / target.split("#", 1)[0]).exists():
                raise ValueError(f"Broken Markdown link: {path}:{target}")
    return {
        "source_files": len(read_json(MANIFESTS / "source-files.json", [])),
        "wiki_pages": len(known_paths),
        "indexed_pages": len(indexed),
        "folder_pages": len(folders),
        "source_bytes_preserved": True,
        "local_links_valid": True,
    }
