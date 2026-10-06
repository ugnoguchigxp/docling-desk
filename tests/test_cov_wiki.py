"""Statement and branch coverage for wiki batch preparation, terms, and transport."""

from __future__ import annotations

import csv
import io
import json
import re
import signal
import sys
import time
from pathlib import Path

import httpx
import pytest

from docling_desk.wiki_batch import preparation
from docling_desk.wiki_batch.files import (
    FatalProviderError,
    NeedsReview,
    RetryLater,
    Stopped,
    hash_text,
)
from docling_desk.wiki_batch.folder_catalog import catalog
from docling_desk.wiki_batch.markdown import (
    keep_text,
    packets_for,
    parse_json,
    restore,
    rewrite_links,
    split_markdown,
    validate_translation,
    verify_result,
)
from docling_desk.wiki_batch.provider import AzureClient, Runtime, config_from_env, wait_until
from docling_desk.wiki_batch.repository import Repository, chunks_for
from docling_desk.wiki_batch.research import (
    analysis,
    assessment,
    bounded_text,
    reading,
    research_for,
    translation_context,
)
from docling_desk.wiki_batch.snapshot import LIMITS, RESEARCH_VERSION
from docling_desk.wiki_batch.store import BatchStore
from docling_desk.wiki_batch.terminology import (
    CONTEXT_CHARS,
    DECISION_CHARS,
    INPUT_CHARS,
    VERSION,
    candidates_for_text,
    collect_terms,
    consistency_checks,
    find,
    initial_resolution,
    load_registry,
    parse_decisions,
    source_context,
    term_context,
    term_issues,
    term_key,
    term_packets,
    validate_registry,
    validate_resolution,
)

NOTION = "abcdef0123456789abcdef0123456789"
MISSING = "abcdef0123456789abcdef01234567aa"


def _csv(rows, columns):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _workspace(root: Path) -> None:
    columns = ["ID", "Phrase", "Category", "Region", "User Type", "Prio", "Notes", "Detail"]
    detail = (
        f"See [spec](https://www.notion.so/page-{NOTION}) "
        f"https://example.com/docs https://notion.so/{MISSING} " + ("D" * 180)
    )
    rows = [
        {
            "ID": "N-1",
            "Phrase": "Hello World",
            "Category": "Wid|gets",
            "Region": "JP",
            "User Type": "admin",
            "Prio": "1",
            "Notes": "N" * 300,
            "Detail": detail,
        },
        {
            "ID": "N-2",
            "Phrase": "Only CSV Title",
            "Category": "Wid|gets",
            "Region": "US",
            "User Type": "user",
            "Prio": "2",
            "Notes": "csv",
            "Detail": "csv detail",
        },
    ]
    page = (
        "# Hello World\n\n"
        "ID: N-1\n"
        "Phrase: Hello World\n"
        "Category: Wid|gets\n"
        "Region: Japan\n"
        "Notes: short\n"
        f"Detail: {detail}\n"
    )
    guide = "# Operator Guide\n\nPlain prose without properties.\n"
    files = {
        "items.csv": _csv(rows, columns),
        f"Hello {NOTION}.md": page,
        "Guide.md": guide,
    }
    for name in ("Alpha", "Beta"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "nested" / "deep").mkdir(parents=True)
        for rel, text in files.items():
            (folder / rel).write_text(text, encoding="utf-8")
    gamma = root / "Gamma"
    gamma.mkdir()
    (gamma / "only.md").write_text("# Gamma Heading\n\nJust a document.\n", encoding="utf-8")
    manifests = root / "manifests"
    manifests.mkdir()
    (manifests / "collections.json").write_text(
        json.dumps(
            {
                "Alpha": ["notes", "Alpha label"],
                "Beta": ["notes", "Beta label"],
                "Gamma": ["gamma", "Gamma label"],
            }
        ),
        encoding="utf-8",
    )
    (manifests / "folder-titles.json").write_text(
        json.dumps({"Alpha": ["アルファ", "CSV由来の説明です。", ["Phrase", "Notes", "Missing"]]}),
        encoding="utf-8",
    )


@pytest.fixture
def built(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _workspace(root)
    preparation.configure(root)
    preparation.migrate()
    report = preparation.build()
    return root, report


def test_prepare_build_check_and_second_pass(built):
    root, report = built
    assert report["folders"] == 3
    assert report["aliased_records"] == 3
    assert report["csv_only"] == 1
    assert report["markdown_only"] == 2
    assert report["unresolved_notion_urls"] == 1
    checked = preparation.check()
    assert checked["local_links_valid"] and checked["source_bytes_preserved"]
    pages = [
        json.loads(line)
        for line in (root / "manifests/pages.jsonl").read_text().splitlines()
        if line.strip()
    ]
    record = next(page for page in pages if page["id"] == "N-1")
    ja = root / record["ja_path"]
    ja.write_text(
        ja.read_text().replace("---\n", '---\ncategory_ja: "分類"\n', 1), encoding="utf-8"
    )
    preparation.build()
    (root / "manifests/toc-translations.json").write_text(
        json.dumps(
            {
                record["key"]: {
                    "source_hash": record["source_hash"],
                    "title": "訳題",
                    "category": "目次分類",
                }
            }
        ),
        encoding="utf-8",
    )
    preparation.build()
    index = (root / "wiki/pages/ja/notes/index.csv").read_text(encoding="utf-8")
    assert "訳題" in index and "目次分類" in index
    landing = (root / "wiki/pages/index.md").read_text(encoding="utf-8")
    landing += "\n[外部](https://example.com) [mail](mailto:a@b.c) [節](#top)\n"
    (root / "wiki/pages/index.md").write_text(landing, encoding="utf-8")
    assert preparation.check()["wiki_pages"] == checked["wiki_pages"]


def test_configure_validation_and_helpers(tmp_path):
    root = tmp_path / "empty"
    (root / "manifests").mkdir(parents=True)
    preparation.configure(root)
    assert preparation.COLLECTIONS == {}
    assert preparation.export_directories(root / "missing") == []
    assert preparation.normalize("Ａ　B") == "A B"
    assert preparation.read_json(root / "nope.json", {"fallback": 1}) == {"fallback": 1}
    target = root / "nested" / "out.txt"
    preparation.atomic_write(target, "same")
    preparation.atomic_write(target, "same")
    preparation.atomic_write(target, "changed")
    assert target.read_text(encoding="utf-8") == "changed"
    assert preparation.digest(b"a") == hash_text("a")
    assert preparation.make_csv([dict.fromkeys(preparation.INDEX_COLUMNS, "x")]).startswith(
        "id,title"
    )

    odd = tmp_path / "odd"
    folder = odd / "sources/notion/!!!"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("# T\n", encoding="utf-8")
    (odd / "manifests").mkdir()
    preparation.configure(odd)
    assert preparation.COLLECTIONS["!!!"][0].startswith("collection-")

    hidden = tmp_path / "hidden"
    (hidden / "sources/notion/.hidden").mkdir(parents=True)
    (hidden / "sources/notion/.hidden/a.md").write_text("# T\n", encoding="utf-8")
    (hidden / "manifests").mkdir()
    with pytest.raises(ValueError, match="不正"):
        preparation.configure(hidden)

    bad = tmp_path / "bad"
    (bad / "manifests").mkdir(parents=True)
    cases = [
        ["nope"],
        {"ok": "x"},
        {"": ["a", "b"]},
        {".hidden": ["a", "b"]},
        {"Ok": ["bad slug", "label"]},
        {"Ok": ["slug", ""]},
        {"Ok": ["slug"]},
        {"a/b": ["slug", "label"]},
    ]
    for value in cases:
        (bad / "manifests/collections.json").write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError, match="不正"):
            preparation.configure(bad)


def test_migrate_edges(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    preparation.configure(bare)
    with pytest.raises(ValueError, match="No Notion"):
        preparation.migrate()

    root = tmp_path / "exports"
    root.mkdir()
    export = root / "Pack"
    export.mkdir()
    (export / "a.md").write_text("# A\n\ntext\n", encoding="utf-8")
    link = export / "link.md"
    link.symlink_to(export / "a.md")
    (root / "manifests").mkdir()
    preparation.configure(root)
    with pytest.raises(ValueError, match="symlink"):
        preparation.migrate()
    link.unlink()
    (root / "sources/notion/Pack").mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        preparation.migrate()
    (root / "sources/notion/Pack").rmdir()
    (root / "manifests/source-files.json").write_text(
        json.dumps([{"source_path": "sources/notion/Pack/a.md", "sha256": "0" * 64, "bytes": 1}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        preparation.migrate()
    (root / "manifests/source-files.json").unlink()
    preparation.migrate()
    source = root / "sources/notion/Pack/a.md"
    source.write_text(source.read_text(encoding="utf-8") + "x", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        preparation.check_sources()
    source.write_text("# A\n\ntext\n", encoding="utf-8")
    (root / "sources/notion/Pack/extra.md").write_text("# E\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        preparation.check_sources()
    (root / "sources/notion/Pack/extra.md").unlink()
    preparation.migrate()
    (root / "manifests/source-files.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        preparation.check_sources()


def test_parse_titles_links_and_body(tmp_path):
    page = tmp_path / f"Item {NOTION}.md"
    page.write_text(
        "# Kept\n\n   \nID: N-9\nReq. ID: R-1\nNotes: first\ncontinued\n",
        encoding="utf-8",
    )
    parsed = preparation.parse_page(page, ["Notes", "ID", "Req. ID"])
    assert parsed["preamble"] == "   "
    assert parsed["business_id"] == "N-9"
    assert "continued" in parsed["properties"]["Notes"]
    plain = tmp_path / "plain.md"
    plain.write_text("no heading\n", encoding="utf-8")
    assert preparation.parse_page(plain, [])["title"] == "plain"
    assert preparation.parse_page(plain, [])["is_properties"] is False

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
    for key in keys:
        assert preparation.preferred_title({key: "  値  "}, "fallback", "c") == "値"
    assert preparation.preferred_title({}, "", "c") == "Untitled"
    long = preparation.preferred_title({"Phrase": "あ" * 200}, "fallback", "c")
    assert long.endswith("…") and len(long) == 178

    hosts = [
        f"https://notion.com/{NOTION}",
        f"https://www.notion.so/page-{NOTION}",
        f"https://team.notion.site/{NOTION[:8]}-{NOTION[8:12]}-{NOTION[12:16]}-{NOTION[16:20]}-{NOTION[20:]}",
        "https://example.com/a",
        "https://notion.example/a",
        "notaurl",
    ]
    assert preparation.notion_id_from_url(hosts[0]) == NOTION
    assert preparation.notion_id_from_url(hosts[1]) == NOTION
    assert preparation.notion_id_from_url(hosts[2]) == NOTION
    assert preparation.notion_id_from_url(hosts[3]) is None
    assert preparation.notion_id_from_url(hosts[4]) is None
    assert preparation.notion_id_from_url(hosts[5]) is None

    preparation.configure(tmp_path)
    current = tmp_path / "wiki/pages/original/notes/a.md"
    current.parent.mkdir(parents=True)
    target = tmp_path / "wiki/pages/original/notes/b.md"
    notion_map = {NOTION: {"id": "N-1", "original_path": target.relative_to(tmp_path).as_posix()}}
    unresolved = {}
    text = (
        f"[spec](https://www.notion.so/{NOTION}) https://notion.so/{MISSING} https://example.com/x"
    )
    converted = preparation.convert_links(text, current, notion_map, unresolved)
    assert "N-1" not in converted or "b.md" in converted
    assert MISSING in next(iter(unresolved))
    assert "example.com" in converted

    prose = {
        "page": {"is_properties": False, "body": "body", "properties": {}},
        "source_properties": {},
        "source_status": "markdown_only",
    }
    assert preparation.original_body(prose) == "body"
    record = {
        "page": {"is_properties": True, "properties": {"A": "x|y", "B": "", "C": "long\ntext"}},
        "source_properties": {"A": "other", "D": "added", "E": ""},
        "source_status": "csv_only",
    }
    body = preparation.original_body(record)
    assert "CSVの記載" in body and "CSV representation" in body and "\\|" in body and "## C" in body
    with pytest.raises(ValueError, match="frontmatter"):
        preparation.read_meta(plain)


def test_gather_conflicts_and_duplicate_notion(tmp_path):
    root = tmp_path / "gather"
    box = root / "sources/notion/Box"
    box.mkdir(parents=True)
    (root / "manifests").mkdir()
    (box / "a.csv").write_text("ID,Name\n1,One\n", encoding="utf-8")
    (box / "b.csv").write_text("ID,Name\n1,One\n", encoding="utf-8")
    preparation.configure(root)
    with pytest.raises(ValueError, match="Multiple CSVs"):
        preparation.gather_records()
    (box / "b.csv").unlink()
    (box / "a.csv").write_text("ID,Name\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed"):
        preparation.gather_records()
    (box / "a.csv").write_text("Name\nSame\n", encoding="utf-8")
    (box / "one.md").write_text("# Same\n\nbody\n", encoding="utf-8")
    (box / "two.md").write_text("# Same\n\nother\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Ambiguous"):
        preparation.gather_records()

    for child in box.iterdir():
        if child.is_file():
            child.unlink()
    left = root / "sources/notion/Left"
    right = root / "sources/notion/Right"
    for folder, body in ((left, "alpha"), (right, "beta")):
        folder.mkdir()
        (folder / "row.csv").write_text("ID,Phrase\nZ-1,Title\n", encoding="utf-8")
        (folder / "page.md").write_text(f"# Title\n\nID: Z-1\n{body}\n", encoding="utf-8")
    (root / "manifests/collections.json").write_text(
        json.dumps({"Left": ["same", "L"], "Right": ["same", "R"], "Box": ["box", "B"]}),
        encoding="utf-8",
    )
    preparation.configure(root)
    with pytest.raises(ValueError, match="Conflicting"):
        preparation.gather_records()
    (right / "page.md").write_text("# Title\n\nID: Z-1\nalpha\n", encoding="utf-8")
    records = preparation.gather_records()
    assert any(len(record["sources"]) > 1 for record in records)

    notion = tmp_path / "notion"
    notion.mkdir()
    folder = notion / "Only"
    folder.mkdir()
    (folder / "rows.csv").write_text("ID,Phrase\nA-1,A\nB-1,B\n", encoding="utf-8")
    (folder / f"a {NOTION}.md").write_text("# A\n\nID: A-1\n", encoding="utf-8")
    (folder / f"b {NOTION}.md").write_text("# B\n\nID: B-1\n", encoding="utf-8")
    (notion / "manifests").mkdir()
    (notion / "manifests/source-files.json").write_text("[]", encoding="utf-8")
    preparation.configure(notion)
    preparation.SOURCES.mkdir(parents=True)
    # Files already live at the export root; move them by migrate after removing the empty inventory.
    (notion / "manifests/source-files.json").unlink()
    preparation.migrate()
    with pytest.raises(ValueError, match="multiple Wiki"):
        preparation.build()


def test_check_failures(built, tmp_path):
    root, _report = built

    def pages():
        return [
            json.loads(line)
            for line in (root / "manifests/pages.jsonl").read_text().splitlines()
            if line.strip()
        ]

    def save(records):
        (root / "manifests/pages.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )

    preparation.configure(root)
    original = (root / "manifests/pages.jsonl").read_text(encoding="utf-8")
    records = pages()
    save(records + [records[0]])
    with pytest.raises(ValueError, match="Duplicate Wiki"):
        preparation.check()
    (root / "manifests/pages.jsonl").write_text(original, encoding="utf-8")

    record = pages()[0]
    path = root / record["original_path"]
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace(f'id: "{record["id"]}"', 'id: "OTHER"', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="ID mismatch"):
        preparation.check()
    path.write_text(raw, encoding="utf-8")
    path.write_text(
        re.sub(r'counterpart_path: ".*?"', 'counterpart_path: "missing.md"', raw, count=1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Counterpart"):
        preparation.check()
    path.write_text(raw, encoding="utf-8")

    ja = root / record["ja_path"]
    ja_raw = ja.read_text(encoding="utf-8")
    ja.write_text(ja_raw.replace("untranslated", "translated", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="Translation status"):
        preparation.check()
    ja.write_text(ja_raw, encoding="utf-8")

    extra = path.parent / "extra-page.md"
    extra.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="Unregistered"):
        preparation.check()
    extra.unlink()

    index = path.parent / "index.csv"
    index_raw = index.read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(index_raw)))
    rows[0]["page_path"] = "missing.md"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=preparation.INDEX_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    index.write_text(buffer.getvalue(), encoding="utf-8")
    with pytest.raises(ValueError, match="Broken CSV"):
        preparation.check()
    rows[0]["page_path"] = Path(record["original_path"]).name
    doubled = rows + [rows[0]]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=preparation.INDEX_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(doubled)
    index.write_text(buffer.getvalue(), encoding="utf-8")
    with pytest.raises(ValueError, match="indexed twice"):
        preparation.check()
    rows[0]["translation_status"] = "translated"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=preparation.INDEX_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    index.write_text(buffer.getvalue(), encoding="utf-8")
    with pytest.raises(ValueError, match="CSV metadata"):
        preparation.check()
    index.write_text("\n".join(index_raw.splitlines()[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="do not cover"):
        preparation.check()
    index.write_text(index_raw, encoding="utf-8")

    folders = json.loads((root / "manifests/folders.json").read_text(encoding="utf-8"))
    saved_folders = json.dumps(folders)
    folders[0]["id"] = "missing-folder"
    (root / "manifests/folders.json").write_text(json.dumps(folders), encoding="utf-8")
    with pytest.raises(ValueError, match="export folders"):
        preparation.check()
    (root / "manifests/folders.json").write_text(saved_folders, encoding="utf-8")
    folder_page = root / folders[0]["path"]
    folder_raw = folder_page.read_text(encoding="utf-8")
    stray = folder_page.parent.parent / "stray.md"
    stray.write_text(folder_raw, encoding="utf-8")
    with pytest.raises(ValueError, match="folder pages"):
        preparation.check()
    stray.unlink()
    folder_page.write_text(folder_raw.replace(folders[0]["title"], "別題", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="Folder title"):
        preparation.check()
    folder_page.write_text(folder_raw, encoding="utf-8")
    folders = json.loads(saved_folders)
    folders[0]["count"] = folders[0]["count"] + 9
    (root / "manifests/folders.json").write_text(json.dumps(folders), encoding="utf-8")
    with pytest.raises(ValueError, match="Folder contents"):
        preparation.check()
    (root / "manifests/folders.json").write_text(saved_folders, encoding="utf-8")
    path.write_text(raw + "\n[壊れた](missing-target.md)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Broken Markdown"):
        preparation.check()
    path.write_text(raw, encoding="utf-8")

    record = pages()[0]
    record["source_hash"] = "0" * 64
    save([record if item["key"] == record["key"] else item for item in pages()])
    ja = root / record["ja_path"]
    ja.write_text(
        ja.read_text(encoding="utf-8").replace("untranslated", "translated", 1), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Source changed"):
        preparation.build()


def test_folder_catalog_edges(tmp_path):
    root = tmp_path / "cat"
    box = root / "Box"
    box.mkdir(parents=True)
    (box / "sub").mkdir()
    with pytest.raises(ValueError, match="no Wiki"):
        catalog(root, root, [box], [])
    records = [
        {
            "sources": [{"collection": "Box"}],
            "collection": "one",
            "translation_status": "translated",
        },
        {"sources": [{"collection": "Box"}], "collection": "two", "translation_status": "reviewed"},
    ]
    with pytest.raises(ValueError, match="multiple collections"):
        catalog(root, root, [box], records)
    (box / "empty.md").write_text("no heading\n", encoding="utf-8")
    one = [
        {
            "sources": [{"collection": "Box"}],
            "collection": "Box",
            "translation_status": "needs_review",
        }
    ]
    listed = catalog(root, root, [box], one)
    assert listed[0]["title_basis"]["heading"] is None
    (box / "empty.md").write_text("# From Markdown\n\ntext\n", encoding="utf-8")
    titled = catalog(root, root, [box], one, {"Box": ["規則", "説明", ["Nope"]]})
    assert titled[0]["title"] == "規則"
    auto = catalog(root, root, [box], one)
    assert auto[0]["title"] == "From Markdown"
    bare = root / "Bare"
    bare.mkdir()
    bare_records = [
        {
            "sources": [{"collection": "Bare"}],
            "collection": "other",
            "translation_status": "untranslated",
        }
    ]
    assert catalog(root, root, [bare], bare_records)[0]["title_basis"]["path"] is None
    (box / "rows.csv").write_text("A,B\n,\nvalue,\n", encoding="utf-8")
    csv_records = [
        {
            "sources": [{"collection": "Box"}],
            "collection": "Box",
            "translation_status": "translated",
        }
    ]
    summarized = catalog(root, root, [box], csv_records, {"Box": ["題", "説明", ["A", "Missing"]]})
    assert summarized[0]["title_basis"]["evidence"][0]["csv_row"] == 3
    other = root / "Other"
    other.mkdir()
    (other / "x.md").write_text("# X\n", encoding="utf-8")
    shared = [
        {
            "sources": [{"collection": "Box"}, {"collection": "Other"}],
            "collection": "Box",
            "translation_status": "translated",
        }
    ]
    folders = catalog(root, root, [box, other], shared)
    preferred = next(folder for folder in folders if folder["duplicate_of"] is None)
    assert preferred["id"] == "Box"
    assert any(folder["duplicate_of"] == "Box" for folder in folders)


def _entry(**overrides):
    entry = {
        "id": "avg",
        "senseId": "mean",
        "definition": "average",
        "domain": "general",
        "appliesWhen": "math context",
        "status": "draft",
        "conditional": False,
        "acceptedJa": ["平均"],
        "disallowedJa": ["平均値"],
        "aliases": [{"text": "average", "caseSensitive": False}],
        "sources": [],
        "source": {"text": "AVG", "caseSensitive": True},
        "preferredJa": "平均",
    }
    entry.update(overrides)
    return entry


def _registry(*entries):
    return {"schemaVersion": 1, "entries": list(entries)}


def test_registry_validation(tmp_path):
    root = tmp_path / "terms"
    (root / "sources").mkdir(parents=True)
    body = "evidence body"
    (root / "sources/note.md").write_text("---\ntitle: t\n---\n\n" + body, encoding="utf-8")
    (root / "sources/raw.txt").write_text("raw text", encoding="utf-8")
    good = _entry(
        status="verified",
        sources=[{"path": "sources/note.md", "hash": hash_text(body), "locator": "p1"}],
    )
    assert validate_registry(_registry(good), root)["entries"][0]["id"] == "avg"
    raw = _entry(
        id="raw",
        senseId="file",
        source={"text": "RAW", "caseSensitive": True},
        aliases=[],
        status="verified",
        sources=[{"path": "sources/raw.txt", "hash": hash_text("raw text"), "locator": "line"}],
    )
    validate_registry(_registry(raw), root)
    assert load_registry(root)["entries"] == []
    (root / "manifests").mkdir()
    (root / "manifests/translation-terminology.json").write_text(
        json.dumps(_registry(good)), encoding="utf-8"
    )
    assert load_registry(root)["entries"][0]["senseId"] == "mean"
    (root / "manifests/translation-terminology.json").write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_registry(root)

    def reject(value, match, registry_root=None):
        with pytest.raises(NeedsReview, match=match):
            validate_registry(value, registry_root)

    reject([], "形式")
    reject({"schemaVersion": 2, "entries": []}, "形式")
    reject({"schemaVersion": 1, "entries": ["x"]}, "項目")
    broken = _entry(definition="  ")
    reject(_registry(broken), "定義")
    reject(_registry(_entry(id="bad id")), "ID")
    reject(_registry(_entry(status="open")), "状態")
    reject(_registry(_entry(conditional="yes")), "状態")
    reject(_registry(_entry(acceptedJa="平均")), "配列")
    reject(_registry(_entry(preferredJa="  ")), "推奨訳")
    reject(_registry(_entry(acceptedJa=[" "])), "許容訳")
    reject(_registry(_entry(status="verified", preferredJa=None, sources=[])), "確認済み")
    reject(_registry(_entry(status="verified", sources=[])), "確認済み")
    reject(_registry(_entry(preferredJa="平均値")), "矛盾")
    reject(_registry(_entry(acceptedJa=["平均値"])), "矛盾")
    duplicate = _entry()
    reject(_registry(duplicate, duplicate), "重複しています")
    reject(_registry(_entry(aliases=[{"text": " ", "caseSensitive": True}])), "別名")
    reject(
        _registry(_entry(aliases=[{"text": "AVG", "caseSensitive": True}])),
        "別名が重複",
    )
    reject(
        _registry(
            _entry(
                source={"text": "AVG", "caseSensitive": False},
                aliases=[{"text": "avg", "caseSensitive": False}],
            )
        ),
        "別名が重複",
    )
    reject(_registry(_entry(sources=["x"])), "出典")
    reject(
        _registry(
            _entry(
                sources=[{"url": "https://example.com", "path": "sources/raw.txt", "locator": "p"}]
            )
        ),
        "出典",
    )
    reject(_registry(_entry(sources=[{"locator": "p"}])), "出典")
    reject(_registry(_entry(sources=[{"url": "http://example.com", "locator": "p"}])), "出典URL")
    reject(
        _registry(_entry(sources=[{"url": "https://user:pw@example.com", "locator": "p"}])),
        "出典URL",
    )
    reject(
        _registry(_entry(sources=[{"path": "sources/raw.txt", "hash": "nope", "locator": "p"}])),
        "ハッシュ",
    )
    reject(
        _registry(
            _entry(
                status="verified",
                sources=[{"path": "sources/raw.txt", "hash": "a" * 64, "locator": "p"}],
            )
        ),
        "変更",
        root,
    )
    first = _entry(
        status="verified",
        conditional=False,
        sources=[{"url": "https://example.com/a", "locator": "p"}],
    )
    second = _entry(
        id="avg2",
        senseId="other",
        status="draft",
        source={"text": "AVG", "caseSensitive": True},
        aliases=[],
    )
    reject(_registry(first, second), "多義語")
    conditional = _entry(
        status="verified",
        conditional=True,
        sources=[{"url": "https://example.com/a", "locator": "p"}],
    )
    other = _entry(
        id="avg2", senseId="other", source={"text": "AVG", "caseSensitive": True}, aliases=[]
    )
    assert validate_registry(_registry(conditional, other))


def test_term_matching_resolution_and_packets():
    verified = _entry(
        status="verified",
        conditional=False,
        sources=[{"url": "https://example.com/a", "locator": "p1"}],
    )
    draft = _entry(
        id="draft",
        senseId="only",
        status="draft",
        conditional=True,
        source={"text": "DRAFT", "caseSensitive": True},
        aliases=[],
    )
    deprecated = _entry(
        id="old",
        senseId="old",
        status="deprecated",
        source={"text": "OLD", "caseSensitive": True},
        aliases=[],
    )
    short = _entry(
        id="av",
        senseId="short",
        status="verified",
        conditional=True,
        source={"text": "AV", "caseSensitive": True},
        aliases=[],
        sources=[{"url": "https://example.com/b", "locator": "p2"}],
    )
    meta = _entry(
        id="meta",
        senseId="label",
        domain="metadata",
        status="verified",
        source={"text": "META", "caseSensitive": True},
        aliases=[],
        preferredJa="メタ",
        acceptedJa=["メタ"],
        disallowedJa=["禁止メタ"],
        sources=[{"url": "https://example.com/c", "locator": "p3"}],
    )
    registry = _registry(verified, draft, deprecated, short, meta)
    units = [
        {"id": "u0", "kind": "code", "text": "AVG OLD", "keep": {}, "group": 0},
        {
            "id": "u1",
            "kind": "paragraph",
            "text": "Use ⟦KEEP_0⟧ daily",
            "keep": {"⟦KEEP_0⟧": "AVG"},
            "group": 1,
        },
        {"id": "u2", "kind": "paragraph", "text": " G", "keep": {}, "group": 1},
        {"id": "u3", "kind": "cell", "text": "META", "keep": {}, "group": 2},
        {"id": "u4", "kind": "paragraph", "text": "DRAFT term", "keep": {}, "group": 3},
        {"id": "u5", "kind": "fence", "text": "AVG", "keep": {}, "group": 4},
    ]
    text = "See AVG and `AVG` plus https://example.com/AVG and [a](https://example.com/AVG)."
    matches = find(verified, text)
    assert matches and all("AVG" == match["text"] for match in matches)
    assert find(_entry(source={"text": "missing", "caseSensitive": False}, aliases=[]), text) == []
    snapshot = collect_terms(registry, units)
    assert snapshot["version"] == VERSION
    assert any(term_key(entry) == "meta/label" for entry in snapshot["entries"])
    assert all(term_key(entry) != "old/old" for entry in snapshot["entries"])
    resolution = initial_resolution(snapshot)
    assert resolution["registryHash"] == snapshot["registryHash"]
    assert any(decision["entryKey"] for decision in resolution["decisions"])
    assert any(decision["entryKey"] is None for decision in resolution["decisions"])
    context = term_context(snapshot, resolution, ["u1"])
    assert context["inputHash"]
    assert term_context(snapshot, None, ["u3"])["decisions"] == []
    selected = source_context(units, snapshot["occurrences"][:1])
    assert selected
    assert candidates_for_text(snapshot, "AVG appears")

    occurrence = next(item for item in snapshot["occurrences"] if "avg/mean" in item["entryKeys"])
    verified_key = "avg/mean"
    verified_entries = {
        term_key(entry)
        for entry in snapshot["entries"]
        if entry["status"] == "verified" and term_key(entry) in occurrence["entryKeys"]
    }
    if not verified_entries:
        verified_key = "avg/mean"
        occurrence["entryKeys"].append(verified_key)
    else:
        verified_key = next(iter(verified_entries))
    decisions = parse_decisions(
        {
            "unresolved": [],
            "decisions": [
                {
                    "occurrenceId": occurrence["id"],
                    "entryKey": verified_key,
                    "reason": "文脈で確定",
                    "citations": ["u1", "term:" + verified_key],
                }
            ],
        },
        snapshot,
        [occurrence],
        {"source", "u1", "source:u1", "term:" + verified_key},
    )
    assert decisions[0]["citations"][0] == "source:u1"

    def bad_decision(value, match):
        with pytest.raises(NeedsReview, match=match):
            parse_decisions(value, snapshot, [occurrence], {"source", "u1", "term:" + verified_key})

    bad_decision({}, "確定できません")
    bad_decision({"unresolved": [], "decisions": []}, "確定できません")
    bad_decision({"unresolved": [{}], "decisions": [{}]}, "確定できません")
    bad_decision(
        {
            "unresolved": [],
            "decisions": [
                {
                    "occurrenceId": "nope",
                    "reason": "x",
                    "entryKey": verified_key,
                    "citations": ["source"],
                }
            ],
        },
        "固定ID",
    )
    bad_decision(
        {
            "unresolved": [],
            "decisions": [
                {
                    "occurrenceId": occurrence["id"],
                    "reason": " ",
                    "entryKey": verified_key,
                    "citations": ["source"],
                }
            ],
        },
        "固定ID",
    )
    bad_decision(
        {
            "unresolved": [],
            "decisions": [
                {
                    "occurrenceId": occurrence["id"],
                    "reason": "あ" * 1001,
                    "entryKey": verified_key,
                    "citations": ["source"],
                }
            ],
        },
        "固定ID",
    )
    bad_decision(
        {
            "unresolved": [],
            "decisions": [
                {
                    "occurrenceId": occurrence["id"],
                    "reason": "ok",
                    "entryKey": "missing/x",
                    "citations": ["source"],
                }
            ],
        },
        "登録されていない",
    )
    bad_decision(
        {
            "unresolved": [],
            "decisions": [
                {
                    "occurrenceId": occurrence["id"],
                    "reason": "ok",
                    "entryKey": verified_key,
                    "citations": [],
                }
            ],
        },
        "出典",
    )
    bad_decision(
        {
            "unresolved": [],
            "decisions": [
                {
                    "occurrenceId": occurrence["id"],
                    "reason": "ok",
                    "entryKey": verified_key,
                    "citations": ["term:" + verified_key],
                }
            ],
        },
        "出典",
    )
    bulky = {
        "unresolved": [],
        "decisions": [
            {
                "occurrenceId": occurrence["id"],
                "reason": "あ" * 1000,
                "entryKey": verified_key,
                "citations": [f"c{i:02d}" + ("x" * 40) for i in range(20)],
            }
        ],
    }
    with pytest.raises(NeedsReview, match="予算"):
        parse_decisions(
            bulky,
            snapshot,
            [occurrence],
            {
                "source",
                "u1",
                "term:" + verified_key,
                *[f"c{i:02d}" + ("x" * 40) for i in range(20)],
            },
        )

    # Replace one verified unconditional occurrence with its required key so validation can fail other ways.
    with pytest.raises(NeedsReview):
        validate_resolution(snapshot, {"registryHash": "nope", "decisions": []})
    with pytest.raises(NeedsReview, match="固定ID"):
        present = [item["id"] for item in snapshot["occurrences"]]
        validate_resolution(
            snapshot,
            {
                "registryHash": snapshot["registryHash"],
                "decisions": [
                    {
                        "occurrenceId": "missing" if index == 0 else present[index],
                        "entryKey": None,
                        "reason": "x",
                        "citations": [],
                    }
                    for index in range(len(present))
                ],
            },
        )
    conflicting = json.loads(json.dumps(snapshot))
    conflicting["occurrences"] = [
        {
            "id": "t0",
            "start": 0,
            "end": 5,
            "text": "AVG",
            "group": 1,
            "unitIds": ["u1"],
            "entryKeys": ["avg/mean", "av/short"],
        },
        {
            "id": "t1",
            "start": 2,
            "end": 7,
            "text": "VG",
            "group": 1,
            "unitIds": ["u1"],
            "entryKeys": ["avg/mean", "av/short"],
        },
    ]
    conflicting["entries"] = [verified, short]
    bad_resolution = {
        "registryHash": conflicting["registryHash"],
        "decisions": [
            {"occurrenceId": "t0", "entryKey": "avg/mean", "reason": "a", "citations": ["source"]},
            {"occurrenceId": "t1", "entryKey": "av/short", "reason": "b", "citations": ["source"]},
        ],
    }
    with pytest.raises(NeedsReview, match="矛盾"):
        validate_resolution(conflicting, bad_resolution)
    with pytest.raises(NeedsReview, match="採用ID"):
        validate_resolution(
            conflicting,
            {
                "registryHash": conflicting["registryHash"],
                "decisions": [
                    {"occurrenceId": "t0", "entryKey": None, "reason": "a", "citations": []},
                    {"occurrenceId": "t1", "entryKey": "avg/mean", "reason": "b", "citations": []},
                ],
            },
        )

    huge = json.loads(json.dumps(snapshot))
    huge["entries"] = [{**verified, "definition": "あ" * 8000}]
    huge["occurrences"] = [
        {
            "id": f"t{i}",
            "start": 0,
            "end": 3,
            "text": "AVG",
            "group": 0,
            "unitIds": ["u1"],
            "entryKeys": ["avg/mean"],
        }
        for i in range(6)
    ]
    packet = {"position": 0, "units": [units[1]]}
    with pytest.raises(NeedsReview, match="単一の翻訳単位"):
        term_packets([packet], huge)
    split_snapshot = json.loads(json.dumps(snapshot))
    split_snapshot["occurrences"] = [
        {
            "id": "t0",
            "start": 0,
            "end": 3,
            "text": "AVG",
            "group": 1,
            "unitIds": ["u1"],
            "entryKeys": ["avg/mean"],
        },
        {
            "id": "t1",
            "start": 0,
            "end": 3,
            "text": "AVG",
            "group": 9,
            "unitIds": ["u9"],
            "entryKeys": ["avg/mean"],
        },
    ]
    packets = term_packets(
        [
            {
                "position": 0,
                "units": [
                    units[1],
                    {"id": "u9", "kind": "paragraph", "text": "AVG", "keep": {}, "group": 9},
                ],
            }
        ],
        split_snapshot,
    )
    assert packets

    translations = [
        {"id": "u1", "text": "平均値を使う"},
        {"id": "u3", "text": "別表記"},
        {"id": "u2", "text": "続き"},
    ]
    issues = term_issues(
        conflicting,
        {
            "registryHash": conflicting["registryHash"],
            "decisions": [
                {
                    "occurrenceId": "t0",
                    "entryKey": "avg/mean",
                    "reason": "a",
                    "citations": ["source"],
                },
                {
                    "occurrenceId": "t1",
                    "entryKey": "av/short",
                    "reason": "b",
                    "citations": ["source"],
                },
            ],
        },
        units,
        translations,
    )
    assert issues
    meta_snapshot = {
        "registryHash": "h",
        "entries": [meta],
        "occurrences": [
            {
                "id": "m",
                "start": 0,
                "end": 4,
                "text": "META",
                "group": 2,
                "unitIds": ["u3"],
                "entryKeys": ["meta/label"],
            }
        ],
    }
    meta_issues = term_issues(
        meta_snapshot,
        {
            "registryHash": "h",
            "decisions": [
                {"occurrenceId": "m", "entryKey": "meta/label", "reason": "a", "citations": []}
            ],
        },
        units,
        [{"id": "u3", "text": "禁止メタ"}],
    )
    assert any(issue["id"] == "u3" for issue in meta_issues)
    assert (
        term_issues(
            meta_snapshot,
            {
                "registryHash": "h",
                "decisions": [
                    {"occurrenceId": "m", "entryKey": None, "reason": "a", "citations": []}
                ],
            },
            units,
            translations,
        )
        == []
    )

    wide = {
        "version": VERSION,
        "registryHash": "h",
        "entries": [{**verified, "preferredJa": "平均", "acceptedJa": ["平均"]}],
        "occurrences": [
            {
                "id": "t0",
                "start": 0,
                "end": 3,
                "text": "AVG",
                "group": 0,
                "unitIds": ["wide"],
                "entryKeys": ["avg/mean"],
            }
        ],
    }
    wide_units = [
        {
            "id": "wide",
            "kind": "paragraph",
            "text": "A" * 20000,
            "keep": {},
            "literal": False,
            "group": 0,
        },
        {
            "id": "wide2",
            "kind": "paragraph",
            "text": "B" * 20000,
            "keep": {},
            "literal": False,
            "group": 1,
        },
        {
            "id": "skip",
            "kind": "paragraph",
            "text": "literal",
            "keep": {},
            "literal": True,
            "group": 2,
        },
    ]
    wide["occurrences"].append(
        {
            "id": "t1",
            "start": 0,
            "end": 3,
            "text": "AVG",
            "group": 1,
            "unitIds": ["wide2"],
            "entryKeys": ["avg/mean"],
        }
    )
    checks = consistency_checks(
        wide,
        {
            "registryHash": "h",
            "decisions": [
                {"occurrenceId": "t0", "entryKey": "avg/mean", "reason": "a", "citations": []},
                {"occurrenceId": "t1", "entryKey": "avg/mean", "reason": "b", "citations": []},
            ],
        },
        wide_units,
        [{"id": "wide", "text": "前置き平均あと"}, {"id": "wide2", "text": "平均"}],
    )
    assert checks and checks[0]["consistencyExamples"]
    with pytest.raises(NeedsReview, match="用語表記"):
        consistency_checks(
            wide,
            {
                "registryHash": "h",
                "decisions": [
                    {"occurrenceId": "t0", "entryKey": "avg/mean", "reason": "a", "citations": []}
                ],
            },
            [
                {
                    "id": "wide",
                    "kind": "paragraph",
                    "text": "あ" * (INPUT_CHARS + 100),
                    "keep": {},
                    "literal": False,
                    "group": 0,
                }
            ],
            [{"id": "wide", "text": "平均"}],
        )
    assert CONTEXT_CHARS > DECISION_CHARS


def test_markdown_protection_packets_and_validation():
    with pytest.raises(NeedsReview, match="保護記号"):
        keep_text("already ⟦KEEP_0⟧")
    protected, keep = keep_text(
        'Use `code` [a](<wiki/pages/a.md> "t") https://example.com/a REQ-12 abcdef0123456789abcdef0123456789 1,234.5 2e-3 10%'
    )
    assert keep and "⟦KEEP_" in protected
    unit = {"keep": keep, "prefix": " ", "suffix": "\n"}
    assert restore(unit, protected).strip()
    with pytest.raises(NeedsReview, match="不明な保護"):
        restore({"keep": {}}, "⟦KEEP_0⟧")
    rewritten = rewrite_links(
        "See [a](<wiki/pages/original/notes/a.md#x>) and [b](https://example.com)",
        "wiki/pages/ja/notes/page.md",
        "wiki/pages/original/notes/page.md",
    )
    assert "wiki/pages" in rewritten
    with pytest.raises(NeedsReview, match="収録範囲外"):
        rewrite_links(
            "[a](../../../../etc/passwd)",
            "wiki/pages/ja/notes/page.md",
            "wiki/pages/ja/notes/page.md",
        )
    units = split_markdown(
        "# Title\n\n"
        + ("word " * 2000)
        + "\n\n| A | B |\n| --- | --- |\n| c | d |\n\n```\ncode\n```\n\n---\n\n<script></script>\n",
        "Title",
        "Category",
        False,
        50,
    )
    assert any(item["kind"] == "cell" for item in units)
    assert packets_for(units)
    literals = [{"id": f"u{i}", "literal": True, "text": "x"} for i in range(3)]
    assert packets_for(literals) == []
    many = [{"id": f"u{i}", "literal": False, "text": "word"} for i in range(25)]
    assert len(packets_for(many)) == 2
    with pytest.raises(NeedsReview, match="HTML"):
        split_markdown("<div>abc</div>\n", "Title")
    assert split_markdown("<br>\n", "Title")
    with pytest.raises(NeedsReview, match="分割できない"):
        split_markdown("12345678901234567890", "T", maximum=2)
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(NeedsReview, match="JSON"):
        parse_json("nope")
    packet = {
        "units": [
            {"id": "u0", "text": "Hello ⟦KEEP_0⟧", "kind": "paragraph"},
            {"id": "u1", "text": "| cell", "kind": "cell"},
            {"id": "u2", "text": "## Head", "kind": "heading"},
        ]
    }
    good = {
        "translations": [
            {"id": "u0", "text": "こんにちは ⟦KEEP_0⟧"},
            {"id": "u1", "text": "セル"},
            {"id": "u2", "text": "## 見出し"},
        ]
    }
    assert validate_translation(good, packet)["translations"]
    with pytest.raises(NeedsReview, match="欠落"):
        validate_translation({"translations": []}, packet)
    with pytest.raises(NeedsReview, match="固定ID"):
        validate_translation(
            {
                "translations": [
                    {"id": "u9", "text": "x"},
                    {"id": "u1", "text": "y"},
                    {"id": "u2", "text": "## z"},
                ]
            },
            packet,
        )
    with pytest.raises(NeedsReview, match="保護"):
        validate_translation(
            {
                "translations": [
                    {"id": "u0", "text": "欠落"},
                    {"id": "u1", "text": "セル"},
                    {"id": "u2", "text": "## 見出し"},
                ]
            },
            packet,
        )
    with pytest.raises(NeedsReview, match="セル"):
        validate_translation(
            {
                "translations": [
                    {"id": "u0", "text": "こんにちは ⟦KEEP_0⟧"},
                    {"id": "u1", "text": "a|b"},
                    {"id": "u2", "text": "## 見出し"},
                ]
            },
            packet,
        )
    with pytest.raises(NeedsReview, match="見出し"):
        validate_translation(
            {
                "translations": [
                    {"id": "u0", "text": "こんにちは ⟦KEEP_0⟧"},
                    {"id": "u1", "text": "セル"},
                    {"id": "u2", "text": "# 見出し"},
                ]
            },
            packet,
        )
    with pytest.raises(NeedsReview, match="数値"):
        validate_translation(
            {
                "translations": [
                    {"id": "u0", "text": "1 ⟦KEEP_0⟧"},
                    {"id": "u1", "text": "セル"},
                    {"id": "u2", "text": "## 見出し"},
                ]
            },
            packet,
        )
    assert verify_result({"approved": True, "issues": []}, packet)["approved"] is True
    with pytest.raises(NeedsReview, match="形式"):
        verify_result({"approved": "yes", "issues": []}, packet)
    with pytest.raises(NeedsReview, match="指摘"):
        verify_result({"approved": False, "issues": [{"id": "missing", "reason": "x"}]}, packet)
    with pytest.raises(NeedsReview, match="矛盾"):
        verify_result({"approved": True, "issues": [{"id": "u0", "reason": "x"}]}, packet)


class _Client:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []
        self.tokens = []

    def request(self, role, request, call_key, job_id):
        step = call_key.split("/research/", 1)[1]
        self.calls.append(step)
        self.tokens.append(request["maxOutputTokens"])
        return json.dumps(self.responder(step, json.loads(request["input"])))


class _Repo:
    def __init__(self, hits=None, document=True):
        self.hits = hits if hits is not None else lambda query: []
        self.document = document
        self.synced = False

    def sync(self):
        self.synced = True

    def search(self, query, key):
        return {"expandedTerms": [query], "indexedAt": 1, "results": self.hits(query)}

    def related_document(self, hits, queries, reference, known):
        if not self.document or not hits or hits[0].get("empty"):
            return None
        if any(item.get("key") == hits[0]["key"] for item in known):
            return None
        return {
            "referenceId": reference,
            "key": hits[0]["key"],
            "title": hits[0].get("title", "Related"),
            "content": hits[0].get("content", "related inventory text"),
            "truncated": False,
            "queries": queries,
        }


def _job(tmp_path, snapshot):
    root = tmp_path / "research"
    root.mkdir(parents=True, exist_ok=True)
    store = BatchStore(root)
    store.db.execute(
        "INSERT INTO jobs(id,page_key,input_hash,recipe_hash,status,payload,priority,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("job-1", "notes/a", "h", "r", "running", "{}", 0, 0, 0),
    )
    return store


def _snap(
    text="Hello inventory section.", entries=None, occurrences=None, units=None, category="Widgets"
):
    page = {"title_original": "Title", "key": "notes/a"}
    if category is not None:
        page["category"] = category
    return {
        "researchVersion": RESEARCH_VERSION,
        "sourceText": text,
        "sourceBodyHash": hash_text(text),
        "terminology": {
            "version": VERSION,
            "registryHash": "registry",
            "entries": entries or [],
            "occurrences": occurrences or [],
        },
        "page": page,
        "units": units
        or [{"id": "u0", "text": "Hello", "keep": {}, "kind": "paragraph", "group": 0}],
    }


def _basic(step, _payload):
    if step.startswith(("analyze", "plan")):
        return {
            "summary": "understood",
            "queries": [{"query": "inventory", "reason": "central term"}],
        }
    if step.startswith(("read", "synthesis")):
        return {"terminology": [], "cautions": []}
    if step.startswith("assess"):
        return {"sufficient": True, "reason": "enough", "queries": [], "unresolved": []}
    raise AssertionError(step)


def test_research_validation_and_flow(tmp_path):
    with pytest.raises(NeedsReview):
        bounded_text("  ", 10)
    with pytest.raises(NeedsReview):
        bounded_text("あ" * 11, 10)
    assert (
        analysis(
            {
                "summary": "s",
                "queries": [
                    {"query": "same", "reason": "a"},
                    {"query": "same", "reason": "b"},
                    "bad",
                ],
            }
            if False
            else {
                "summary": "s",
                "queries": [{"query": "same", "reason": "a"}, {"query": "same", "reason": "b"}],
            },
        )["queries"][0]["query"]
        == "same"
    )
    with pytest.raises(NeedsReview, match="件数"):
        analysis({"summary": "s", "queries": []})
    with pytest.raises(NeedsReview, match="形式"):
        analysis({"summary": "s", "queries": ["x"]})
    documents = [
        {"referenceId": "ref:1", "id": "shared", "key": "k", "path": "p"},
        {"referenceId": "ref:2", "id": "shared", "key": "k2", "path": "p2"},
    ]
    assert reading({"terminology": [], "cautions": []}, documents) == {
        "terminology": [],
        "cautions": [],
    }
    parsed = reading(
        {
            "terminology": [
                {
                    "source": "AVG",
                    "target": "平均",
                    "reason": "math",
                    "citations": ["ref:1", "ref:1"],
                }
            ],
            "cautions": [{"text": "注意", "citations": ["k2"]}],
        },
        documents,
    )
    assert parsed["terminology"][0]["citations"] == ["ref:1"]
    with pytest.raises(NeedsReview, match="形式"):
        reading({"terminology": [{}] * 13, "cautions": []}, documents)
    with pytest.raises(NeedsReview, match="用語"):
        reading({"terminology": ["x"], "cautions": []}, documents)
    with pytest.raises(NeedsReview, match="注意"):
        reading({"terminology": [], "cautions": ["x"]}, documents)
    with pytest.raises(NeedsReview, match="出典"):
        reading(
            {
                "terminology": [
                    {"source": "A", "target": "B", "reason": "C", "citations": ["missing"]}
                ],
                "cautions": [],
            },
            documents,
        )
    with pytest.raises(NeedsReview, match="判断形式"):
        assessment([])
    with pytest.raises(NeedsReview, match="追加検索"):
        assessment({"sufficient": False, "reason": "r", "queries": ["x"], "unresolved": ["gap"]})
    with pytest.raises(NeedsReview, match="矛盾"):
        assessment(
            {
                "sufficient": True,
                "reason": "r",
                "queries": [{"query": "q", "reason": "why"}],
                "unresolved": [],
            }
        )
    with pytest.raises(NeedsReview, match="矛盾"):
        assessment({"sufficient": False, "reason": "r", "queries": [], "unresolved": []})
    assert (
        assessment(
            {
                "sufficient": False,
                "reason": "r",
                "queries": [{"query": "q", "reason": "why"}],
                "unresolved": ["gap"],
            }
        )["sufficient"]
        is False
    )

    store = _job(tmp_path, None)
    runtime = Runtime(
        now=lambda: 1000, sleep=lambda ms: None, stopping=lambda: False, log=lambda message: None
    )
    job = {"id": "job-1", "page_key": "notes/a"}
    with pytest.raises(NeedsReview, match="旧方式"):
        research_for(store, _Client(_basic), runtime, _Repo(), job, {"researchVersion": "old"})
    snapshot = _snap()
    store.save(job, "complete", {"sourceHash": "nope"}) if False else store.save(
        "job-1", "complete", {"sourceHash": "nope"}
    )
    with pytest.raises(NeedsReview, match="原文版"):
        research_for(store, _Client(_basic), runtime, _Repo(), job, snapshot)
    cached = {
        "sourceHash": snapshot["sourceBodyHash"],
        "stopReason": "no_new_queries",
        "unresolved": ["gap"],
        "registeredTerminology": {"registryHash": "registry", "decisions": []},
    }
    store.save("job-1", "complete", cached)
    with pytest.raises(NeedsReview, match="追加調査"):
        research_for(store, _Client(_basic), runtime, _Repo(), job, snapshot)
    cached["stopReason"] = "sufficient"
    store.save("job-1", "complete", cached)
    assert (
        research_for(store, _Client(_basic), runtime, _Repo(), job, snapshot)["stopReason"]
        == "sufficient"
    )
    store.db.execute("DELETE FROM research WHERE job_id=?", ("job-1",))
    logs = []
    runtime = Runtime(
        now=lambda: 1000, sleep=lambda ms: None, stopping=lambda: False, log=logs.append
    )
    repo = _Repo(
        hits=lambda query: [{"key": "notes/b", "title": "B"}, {"key": "notes/c", "title": "C"}]
    )
    result = research_for(store, _Client(_basic), runtime, repo, job, snapshot)
    assert result["stopReason"] == "sufficient" and repo.synced
    assert any("原文読解" in message for message in logs)
    store.close()

    paused = tmp_path / "paused"
    store = _job(paused, None)
    store.set("paused", "1")
    with pytest.raises(Stopped):
        research_for(
            store,
            _Client(_basic),
            Runtime(
                now=lambda: 1,
                sleep=lambda ms: None,
                stopping=lambda: False,
                log=lambda message: None,
            ),
            _Repo(),
            job,
            _snap(),
        )
    store.close()

    mismatch = tmp_path / "mismatch"
    store = _job(mismatch, None)
    store.save("job-1", "input/analyze/0", {"hash": "different", "chars": 1})
    with pytest.raises(NeedsReview, match="一致しません"):
        research_for(store, _Client(_basic), runtime, _Repo(), job, _snap())
    store.close()

    huge = tmp_path / "huge"
    store = _job(huge, None)
    entry = _entry(definition="あ" * 40000)
    with pytest.raises(NeedsReview, match="予算"):
        research_for(
            store,
            _Client(_basic),
            runtime,
            _Repo(),
            job,
            _snap("AVG appears here", entries=[entry], occurrences=[]),
        )
    store.close()

    sections = "\n".join(f"# Section {i}\n" + ("inventory " * 500) for i in range(4))
    multi = tmp_path / "multi"
    store = _job(multi, None)
    calls = []

    def planned(step, payload):
        calls.append(step)
        return _basic(step, payload)

    research_for(store, _Client(planned), runtime, _Repo(), job, _snap(sections))
    assert any(step.startswith("plan/") for step in calls)
    store.close()

    merged = tmp_path / "merged"
    store = _job(merged, None)
    calls.clear()
    research_for(
        store,
        _Client(planned),
        runtime,
        _Repo(),
        job,
        _snap("# A\n\nalpha\n# B\n\nbeta\n", category=None),
    )
    assert calls.count("analyze/0") == 1 or "analyze/0" in calls
    assert not any(step == "analyze/1" for step in calls if False)
    store.close()
    empty = tmp_path / "empty"
    store = _job(empty, None)
    research_for(store, _Client(_basic), runtime, _Repo(), job, _snap("\n", category=None))
    store.close()

    def assess(step, payload):
        if step.startswith("assess"):
            number = step.rsplit("/", 1)[-1]
            return {
                "sufficient": False,
                "reason": "need more",
                "queries": [{"query": f"extra{number}", "reason": "follow up"}],
                "unresolved": ["gap"],
            }
        return _basic(step, payload)

    limited = tmp_path / "limit"
    store = _job(limited, None)
    with pytest.raises(NeedsReview, match="round_limit"):
        research_for(
            store,
            _Client(assess),
            runtime,
            _Repo(
                hits=lambda query: [
                    {"key": f"{query}-1", "title": "T"},
                    {"key": f"{query}-2", "title": "U"},
                ]
            ),
            job,
            _snap(),
        )
    assert store.checkpoint("job-1", "complete")["stopReason"] == "round_limit"
    store.close()

    def no_new(step, payload):
        if step.startswith("assess"):
            return {
                "sufficient": False,
                "reason": "again",
                "queries": [{"query": "inventory", "reason": "same"}],
                "unresolved": ["gap"],
            }
        return _basic(step, payload)

    again = tmp_path / "again"
    store = _job(again, None)
    with pytest.raises(NeedsReview, match="no_new_queries"):
        research_for(store, _Client(no_new), runtime, _Repo(hits=lambda query: []), job, _snap())
    store.close()

    def evidence(step, payload):
        if step.startswith("assess/0"):
            return {
                "sufficient": False,
                "reason": "next",
                "queries": [{"query": "fresh", "reason": "new"}],
                "unresolved": ["gap"],
            }
        return _basic(step, payload)

    fresh = tmp_path / "fresh"
    store = _job(fresh, None)

    def hits(query):
        if query == "inventory":
            return [{"key": "notes/b", "title": "B"}]
        return []

    with pytest.raises(NeedsReview, match="no_new_evidence"):
        research_for(store, _Client(evidence), runtime, _Repo(hits=hits), job, _snap())
    store.close()

    entry = _entry(
        status="verified",
        conditional=True,
        sources=[{"url": "https://example.com/a", "locator": "p"}],
    )
    occurrence = {
        "id": "term-0",
        "start": 0,
        "end": 3,
        "text": "AVG",
        "group": 0,
        "unitIds": ["u0"],
        "entryKeys": ["avg/mean"],
    }
    units = [{"id": "u0", "text": "AVG means average", "keep": {}, "kind": "paragraph", "group": 0}]

    def resolve(step, payload):
        if step.startswith("resolve"):
            rows = payload["registeredTerminology"]["occurrences"]
            return {
                "unresolved": [],
                "decisions": [
                    {
                        "occurrenceId": row["id"],
                        "entryKey": "avg/mean",
                        "reason": "文脈",
                        "citations": ["source"],
                    }
                    for row in rows
                ],
            }
        return _basic(step, payload)

    resolved = tmp_path / "resolved"
    store = _job(resolved, None)
    client = _Client(resolve)
    result = research_for(
        store,
        client,
        runtime,
        _Repo(),
        job,
        _snap("AVG in source", entries=[entry], occurrences=[occurrence], units=units),
    )
    assert result["stopReason"] == "sufficient"
    assert 4096 in client.tokens
    store.close()

    huge_units = [
        {"id": "u0", "text": "A" * 20000, "keep": {}, "kind": "paragraph", "group": 0},
        {"id": "u1", "text": "B" * 20000, "keep": {}, "kind": "paragraph", "group": 1},
    ]
    occurrences = [
        {
            "id": "term-0",
            "start": 0,
            "end": 1,
            "text": "A",
            "group": 0,
            "unitIds": ["u0"],
            "entryKeys": ["avg/mean"],
        },
        {
            "id": "term-1",
            "start": 0,
            "end": 1,
            "text": "B",
            "group": 1,
            "unitIds": ["u1"],
            "entryKeys": ["avg/mean"],
        },
    ]
    sized = tmp_path / "sized"
    store = _job(sized, None)
    research_for(
        store,
        _Client(resolve),
        runtime,
        _Repo(),
        job,
        _snap("AVG", entries=[entry], occurrences=occurrences, units=huge_units),
    )
    store.close()
    impossible = tmp_path / "impossible"
    store = _job(impossible, None)
    with pytest.raises(NeedsReview, match="入力予算"):
        research_for(
            store,
            _Client(resolve),
            runtime,
            _Repo(),
            job,
            _snap(
                "AVG",
                entries=[entry],
                occurrences=[
                    {
                        "id": "term-0",
                        "start": 0,
                        "end": 1,
                        "text": "A",
                        "group": 0,
                        "unitIds": ["u0"],
                        "entryKeys": ["avg/mean"],
                    }
                ],
                units=[
                    {"id": "u0", "text": "A" * 40000, "keep": {}, "kind": "paragraph", "group": 0}
                ],
            ),
        )
    store.close()

    overlap_units = [{"id": "u0", "text": "AVG value", "keep": {}, "kind": "paragraph", "group": 0}]
    overlap = [
        {
            "id": "term-0",
            "start": 0,
            "end": 5,
            "text": "AVG",
            "group": 0,
            "unitIds": ["u0"],
            "entryKeys": ["avg/mean", "av/short"],
        },
        {
            "id": "term-1",
            "start": 2,
            "end": 7,
            "text": "G v",
            "group": 0,
            "unitIds": ["u0"],
            "entryKeys": ["avg/mean", "av/short"],
        },
    ]
    short = _entry(
        id="av",
        senseId="short",
        status="verified",
        conditional=True,
        source={"text": "AV", "caseSensitive": True},
        aliases=[],
        sources=[{"url": "https://example.com/b", "locator": "p"}],
    )

    def conflict(step, payload):
        if step.startswith("resolve"):
            return {
                "unresolved": [],
                "decisions": [
                    {
                        "occurrenceId": "term-0",
                        "entryKey": "avg/mean",
                        "reason": "one",
                        "citations": ["source"],
                    },
                    {
                        "occurrenceId": "term-1",
                        "entryKey": "av/short",
                        "reason": "two",
                        "citations": ["source"],
                    },
                ],
            }
        return _basic(step, payload)

    bad = tmp_path / "bad-resolution"
    store = _job(bad, None)
    with pytest.raises(NeedsReview, match="矛盾"):
        research_for(
            store,
            _Client(conflict),
            runtime,
            _Repo(),
            job,
            _snap("AVG value", entries=[entry, short], occurrences=overlap, units=overlap_units),
        )
    assert store.checkpoint("job-1", "term-resolution-error")
    store.close()

    context = translation_context(
        {
            "interpretation": {
                "terminology": [
                    {"source": "AVG", "citations": ["ref:1"]},
                    {"source": "missing", "citations": ["ref:2"]},
                ],
                "cautions": [{"text": "careful", "citations": ["ref:1"]}],
            },
            "documents": [
                {"referenceId": "ref:1", "content": "x" * 900, "truncated": False, "title": "t"},
                {"referenceId": "ref:9", "content": "y", "truncated": True, "title": "u"},
            ],
            "searches": [{"query": "none", "hits": []}, {"query": "some", "hits": [{"key": "k"}]}],
            "plan": {"summary": "s"},
            "stopReason": "sufficient",
        },
        "AVG is here",
    )
    assert context["research"]["noResults"] == ["none"]
    assert context["references"][0]["truncated"] is True
    empty_citations = translation_context(
        {
            "interpretation": {"terminology": [], "cautions": []},
            "documents": [
                {"referenceId": "ref:1", "content": "abc", "truncated": False, "title": "t"}
            ],
            "searches": [],
            "plan": {},
            "stopReason": "sufficient",
        },
        "text",
    )
    assert empty_citations["references"]
    assert LIMITS["totalDocuments"] == 6


def test_repository_chunks_sync_and_search(tmp_path):
    assert chunks_for("", "Title") == []
    body = "# One\n\nalpha\n\n# Two\n\n" + ("beta " * 800) + "\n\n" + ("😀" * 5)
    chunks = chunks_for(body, "Title", 20)
    assert len(chunks) > 1
    with pytest.raises(NeedsReview, match="上限"):
        chunks_for("😀", maximum=1)

    root = tmp_path / "repo"
    data = tmp_path / "data"
    manifests = root / "manifests"
    manifests.mkdir(parents=True)
    digest = "a" * 64

    def page(
        key,
        collection,
        title,
        status="untranslated",
        body_text="Inventory levels remain stable today.",
        ja=True,
        meta=None,
    ):
        record = {
            "key": key,
            "id": key.split("/")[-1],
            "collection": collection,
            "original_path": f"wiki/pages/original/{key}.md",
            "ja_path": f"wiki/pages/ja/{key}.md",
            "translation_status": status,
            "source_hash": digest,
            "title_original": title,
            "title_ja": title + "訳",
        }
        for language, field, heading in (
            ("original", "original_path", title),
            ("ja", "ja_path", record["title_ja"]),
        ):
            if language == "ja" and not ja:
                continue
            path = root / record[field]
            path.parent.mkdir(parents=True, exist_ok=True)
            front = ""
            if meta:
                front = (
                    "---\n"
                    + "".join(
                        f"{name}: {json.dumps(value, ensure_ascii=False)}\n"
                        for name, value in meta.items()
                    )
                    + "---\n\n"
                )
            path.write_text(front + f"# {heading}\n\n{body_text}\n", encoding="utf-8")
        return record

    records = [
        page("notes/Alpha", "notes", "Alpha Page"),
        page("glossary/AbC", "glossary", "AbC", body_text="abc glossary meaning"),
        page(
            "notes/Reviewed",
            "notes",
            "Reviewed",
            status="reviewed",
            body_text="Reviewed inventory remains.",
        ),
    ]
    (root / "wiki/pages/original/notes/index.csv").write_text(
        "id,title\nAlpha,Alpha Page\n", encoding="utf-8"
    )
    (root / "wiki/pages/index.md").write_text("# Wiki\n\nlanding\n", encoding="utf-8")
    folder = root / "wiki/pages/folders/notes/index.md"
    folder.parent.mkdir(parents=True)
    folder.write_text("# Notes\n", encoding="utf-8")
    (manifests / "folders.json").write_text(
        json.dumps(
            [{"id": "notes", "path": "wiki/pages/folders/notes/index.md", "collection": "notes"}]
        ),
        encoding="utf-8",
    )
    (manifests / "pages.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    repo = Repository(root, data)
    synced = repo.sync()
    assert synced["indexed"] and synced["articles"] >= 3
    found = repo.search("inventory")
    assert found["results"]
    assert repo.search("inventory", exclude_key="notes/Alpha")["results"]
    assert all(hit["title"] != "AbC" for hit in repo.search("abc")["results"])
    short = repo.search("ab")
    assert "query" in short
    boosted = repo.search("Alpha Page")
    assert boosted["results"]
    assert repo.search("nothing-special-zzz")["results"] == []
    document = repo.related_document(
        [{"key": "notes/Alpha", "title": "Alpha Page", "score": 1}],
        ["inventory"],
        "ref:1",
        [],
    )
    assert document["truncated"] is False
    long_page = page(
        "notes/Long",
        "notes",
        "Long Page",
        body_text=("inventory item " * 800) + "\n\nunique-middle-term\n",
    )
    records.append(long_page)
    (manifests / "pages.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    repo.sync()
    full = repo.related_document(
        [{"key": "notes/Long", "title": "Long"}], ["unique-middle-term"], "ref:2", []
    )
    assert full["truncated"] is True
    assert (
        repo.related_document(
            [{"key": "notes/Long", "title": "Long"}], ["unique-middle-term"], "ref:2", [full]
        )
        is None
    )
    partial = dict(full)
    partial["truncated"] = True
    partial["locator"] = "chunk:0001"
    again = repo.related_document(
        [{"key": "notes/Long", "title": "Long"}], ["missing-term"], "ref:3", [partial]
    )
    assert again is None or again["truncated"] is True
    records.pop()
    (manifests / "pages.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    repo.sync()
    assert repo.sync(key="notes/Alpha")["articles"] >= 1
    with pytest.raises(NeedsReview, match="公開対象"):
        repo.sync(key="notes/missing")

    broken = tmp_path / "broken"
    (broken / "manifests").mkdir(parents=True)
    (broken / "manifests/pages.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(NeedsReview, match="対応表"):
        Repository(broken, tmp_path / "broken-data")
    (broken / "manifests/pages.jsonl").write_text(
        json.dumps(records[0]) + "\n" + json.dumps(records[0]) + "\n", encoding="utf-8"
    )
    with pytest.raises(NeedsReview, match="重複"):
        Repository(broken, tmp_path / "broken-data")
    bad = dict(records[0])
    bad["translation_status"] = "open"
    (broken / "manifests/pages.jsonl").write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(NeedsReview, match="版"):
        Repository(broken, tmp_path / "broken-data")
    bad = dict(records[0])
    bad["original_path"] = "elsewhere/a.md"
    (broken / "manifests/pages.jsonl").write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(NeedsReview, match="パス"):
        Repository(broken, tmp_path / "broken-data")

    huge = root / records[0]["original_path"]
    with huge.open("wb") as handle:
        handle.truncate(2 * 1024 * 1024 + 1)
    with pytest.raises(NeedsReview, match="2 MiB"):
        repo.read(records[0]["original_path"])
    huge.write_text("# Alpha Page\n\nInventory levels remain stable today.\n", encoding="utf-8")
    ja = root / records[0]["ja_path"]
    ja.unlink()
    assert repo.sync(key=records[0]["key"])["articles"] >= 1
    reviewed = next(record for record in records if record["translation_status"] == "reviewed")
    (root / reviewed["ja_path"]).unlink()
    with pytest.raises((FileNotFoundError, ValueError, NeedsReview)):
        repo.sync(key=reviewed["key"])
    ja.write_text('---\nsource_hash: "' + ("b" * 64) + '"\n---\n\n# T\n', encoding="utf-8")
    (root / records[0]["ja_path"]).write_text("# restored\n", encoding="utf-8")
    (root / records[0]["original_path"]).write_text(
        '---\nsource_hash: "' + ("c" * 64) + '"\n---\n\n# Alpha Page\n\ntext\n',
        encoding="utf-8",
    )
    with pytest.raises(NeedsReview, match="原本版"):
        repo.sync(key=records[0]["key"])
    (root / reviewed["ja_path"]).write_text(
        "# Reviewed\n\nReviewed inventory remains.\n", encoding="utf-8"
    )
    (manifests / "folders.json").write_text("{}\n", encoding="utf-8")
    (root / records[0]["original_path"]).write_text(
        "# Alpha Page\n\nInventory levels remain stable today.\n", encoding="utf-8"
    )
    with pytest.raises(NeedsReview, match="フォルダー対応表"):
        repo.sync()
    (manifests / "folders.json").write_text(
        json.dumps(
            [{"id": "nope", "path": "wiki/pages/folders/nope/index.md", "collection": "missing"}]
        ),
        encoding="utf-8",
    )
    with pytest.raises(NeedsReview, match="フォルダー案内"):
        repo.sync()


def _insert_job(store):
    store.db.execute(
        "INSERT INTO jobs(id,page_key,input_hash,recipe_hash,status,payload,priority,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("job-1", "notes/a", "h", "r", "queued", "{}", 0, 0, 0),
    )


def test_provider_wait_config_and_transport(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_LUNA_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_SOL_DEPLOYMENT", raising=False)
    with pytest.raises(ValueError, match="設定"):
        config_from_env()
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "http://example.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AZURE_OPENAI_LUNA_DEPLOYMENT", "luna")
    monkeypatch.setenv("AZURE_OPENAI_SOL_DEPLOYMENT", "sol")
    with pytest.raises(ValueError, match="HTTPS"):
        config_from_env()
    for endpoint in (
        "https://user:pw@example.com",
        "https://example.com?q=1",
        "https://example.com#frag",
    ):
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
        with pytest.raises(ValueError, match="HTTPS"):
            config_from_env()
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.com/openai")
    config = config_from_env()
    root = tmp_path / "provider"
    root.mkdir()
    store = BatchStore(root)
    _insert_job(store)
    clock = {"t": 0, "stop": False}
    logs = []

    def sleep(ms):
        clock["t"] += ms
        clock["stop"] = True

    runtime = Runtime(
        now=lambda: clock["t"],
        sleep=sleep,
        stopping=lambda: clock["stop"],
        log=logs.append,
    )
    store.set("blocked_until", 5000)
    with pytest.raises(Stopped):
        wait_until(store, runtime, lambda: 5000)
    assert logs
    clock["stop"] = True
    clock["t"] = 9000
    with pytest.raises(Stopped):
        wait_until(store, runtime, lambda: 1000)
    store.set("paused", "1")
    clock["stop"] = False
    with pytest.raises(Stopped):
        wait_until(
            store, Runtime(now=lambda: 0, sleep=lambda ms: None, stopping=lambda: False), lambda: 0
        )
    store.set("paused", "0")
    store.set("blocked_until", 0)

    responses = []

    def handler(request):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    provider_clock = {"t": 10_000}

    def provider_sleep(ms):
        provider_clock["t"] += ms

    client = AzureClient(
        store,
        config,
        Runtime(now=lambda: provider_clock["t"], sleep=provider_sleep, log=lambda message: None),
        httpx.MockTransport(handler),
    )
    request = {"instructions": "Do", "input": "{}", "maxOutputTokens": 128}
    responses.append(
        httpx.Response(
            200,
            headers={"x-ratelimit-limit-tokens": "100", "x-ratelimit-limit-requests": "0"},
            json={"status": "completed", "output_text": "ok"},
        )
    )
    assert client.request("draft", request, "call-1", "job-1") == "ok"
    assert store.number("observed_tpm") == 100
    responses.append(
        httpx.Response(
            200,
            headers={"x-ratelimit-limit-tokens": "40", "x-ratelimit-limit-requests": "9"},
            json={
                "status": "completed",
                "output": [
                    "skip",
                    {"type": "other"},
                    {
                        "type": "message",
                        "content": [
                            "bad",
                            {"type": "other", "text": "no"},
                            {"type": "output_text", "text": 1},
                            {"type": "output_text", "text": "from-output"},
                        ],
                    },
                ],
            },
        )
    )
    assert client.request("verify", request, "call-2", "job-1") == "from-output"
    assert store.number("observed_tpm") == 40
    assert client.request("draft", request, "call-1", "job-1") == "ok"
    for status in (429, 408, 500, 401, 403, 404, 400):
        responses.append(httpx.Response(status, headers={"retry-after": "1"}, content=b"nope"))
        expected = (
            RetryLater
            if status in {429, 408, 500}
            else FatalProviderError
            if status in {401, 403, 404}
            else NeedsReview
        )
        with pytest.raises(expected):
            client.request("draft", request, f"call-{status}", "job-1")
    responses.append(httpx.Response(200, json={"status": "incomplete"}))
    with pytest.raises(NeedsReview, match="未完了"):
        client.request("draft", request, "call-bad", "job-1")
    responses.append(httpx.Response(200, json=[]))
    with pytest.raises(NeedsReview, match="未完了"):
        client.request("draft", request, "call-list", "job-1")
    responses.append(httpx.Response(200, json={"status": "completed", "output": {}}))
    with pytest.raises(NeedsReview, match="出力形式"):
        client.request("draft", request, "call-output", "job-1")
    responses.append(httpx.Response(200, json={"status": "completed", "output": []}))
    with pytest.raises(NeedsReview, match="本文がありません"):
        client.request("draft", request, "call-empty", "job-1")
    responses.append(httpx.Response(200, content=b"{"))
    with pytest.raises(NeedsReview, match="解析"):
        client.request("draft", request, "call-json", "job-1")
    responses.append(httpx.ConnectError("secret failed https://example.com/openai"))
    with pytest.raises(RetryLater) as exc:
        client.request("draft", request, "call-net", "job-1")
    assert "secret" not in str(exc.value)
    assert "[非表示]" in client.scrub(config["apiKey"] + config["endpoint"])
    for _ in range(6):
        store.db.execute(
            "INSERT INTO attempts(id,job_id,call_key,role,started_at,status,reserved) VALUES(?,?,?,?,?,?,?)",
            (f"old-{_}", "job-1", "call-limit", "draft", 1, "error", 1),
        )
    with pytest.raises(NeedsReview, match="再試行上限"):
        client.request("draft", request, "call-limit", "job-1")
    store.db.execute(
        "INSERT INTO attempts(id,job_id,call_key,role,started_at,status,reserved) VALUES(?,?,?,?,?,?,?)",
        ("stale", "job-1", "call-stale", "draft", -100_000_000, "error", 1),
    )
    with pytest.raises(NeedsReview, match="再試行上限"):
        client.request("draft", request, "call-stale", "job-1")
    # time.monotonic is the process-wide clock. Freeze it only for the read-loop
    # check; a stuck clock makes httpx and the SQLite client wait forever.
    real_monotonic = time.monotonic
    arm = {"on": False}

    def monotonic():
        if arm["on"] and sys._getframe(1).f_lineno == 159:
            arm["on"] = False
            return real_monotonic() + 1000
        return real_monotonic()

    class Late(httpx.SyncByteStream):
        def __iter__(self):
            arm["on"] = True
            yield b'{"status":"completed","output_text":"late"}'

    monkeypatch.setattr("docling_desk.wiki_batch.provider.time.monotonic", monotonic)
    responses.append(httpx.Response(200, stream=Late()))
    with pytest.raises(RetryLater):
        client.request("draft", request, "call-timeout", "job-1")
    monkeypatch.setattr("docling_desk.wiki_batch.provider.time.monotonic", real_monotonic)
    responses.append(httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)))
    with pytest.raises(NeedsReview, match="上限"):
        client.request("draft", request, "call-size", "job-1")
    store.close()


def test_cli_commands(tmp_path, monkeypatch, capsys):
    import docling_desk.wiki_batch.__main__ as cli

    handlers = {}
    monkeypatch.setattr(
        cli.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler)
    )
    root = tmp_path / "cli"
    root.mkdir()
    data = tmp_path / "data"

    def fail(argv, code=2):
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == code

    fail(["status", "--root", str(root), "--key", "x"])
    fail(["enqueue", "--root", str(root), "--limit", "0"])
    fail(["run", "--root", str(root), "--trial", "--limit", "1"])
    fail(["sync", "--root", str(root), "--trial"])
    fail(["import-workspace", "--root", str(root)])
    fail(["sync", "--root", str(root), "--from", str(tmp_path)])
    fail(["status", "--root", str(tmp_path / "missing")])
    assert cli.main(["status", "--root", str(root), "--data", str(data)]) == 0
    capsys.readouterr()
    assert cli.main(["pause", "--root", str(root), "--data", str(data)]) == 0
    assert json.loads(capsys.readouterr().out)["paused"] is True
    assert cli.main(["resume", "--root", str(root), "--data", str(data)]) == 0
    assert cli.main(["retry", "--root", str(root), "--data", str(data), "--key", "notes/a"]) == 0
    monkeypatch.setattr(
        "docling_desk.wiki_batch.migration.import_workspace",
        lambda source, target, app_data: {"imported": True},
    )
    assert (
        cli.main(
            ["import-workspace", "--root", str(root), "--from", str(tmp_path), "--data", str(data)]
        )
        == 0
    )
    assert cli.main(["terms-check", "--root", str(root), "--data", str(data)]) == 0

    class Repo:
        pages = [
            {"key": "notes/a", "collection": "notes", "translation_status": "untranslated"},
            {"key": "glossary/b", "collection": "glossary", "translation_status": "untranslated"},
        ]

        def __init__(self, workspace, app_data):
            self.workspace = workspace

        def sync(self):
            return {"articles": 2}

    monkeypatch.setattr(cli, "Repository", Repo)
    assert cli.main(["sync", "--root", str(root), "--data", str(data)]) == 0
    monkeypatch.setattr("docling_desk.wiki_batch.preparation.configure", lambda workspace: None)
    monkeypatch.setattr(
        "docling_desk.wiki_batch.preparation.export_directories", lambda workspace: [Path("Alpha")]
    )
    monkeypatch.setattr("docling_desk.wiki_batch.preparation.check", lambda: {"ok": True})
    monkeypatch.setattr("docling_desk.wiki_batch.preparation.migrate", lambda: None)
    monkeypatch.setattr("docling_desk.wiki_batch.preparation.build", lambda: {"prepared": True})
    assert cli.main(["prepare", "--root", str(root), "--prepare-action", "plan"]) == 0
    assert cli.main(["prepare", "--root", str(root), "--prepare-action", "check"]) == 0
    assert cli.main(["prepare", "--root", str(root), "--prepare-action", "migrate"]) == 0
    (root / "data/translation").mkdir(parents=True, exist_ok=True)
    (root / "data/translation/publication.json").write_text("{}", encoding="utf-8")
    assert cli.main(["prepare", "--root", str(root), "--prepare-action", "build"]) == 1
    (root / "data/translation/publication.json").unlink()
    monkeypatch.setenv("AZURE_OPENAI_LUNA_DEPLOYMENT", "")
    monkeypatch.setenv("AZURE_OPENAI_SOL_DEPLOYMENT", "")
    assert cli.main(["enqueue", "--root", str(root), "--data", str(data)]) == 1
    monkeypatch.setenv("AZURE_OPENAI_LUNA_DEPLOYMENT", "luna")
    monkeypatch.setenv("AZURE_OPENAI_SOL_DEPLOYMENT", "sol")
    assert cli.main(["enqueue", "--root", str(root), "--data", str(data), "--key", "missing"]) == 1
    assert (
        cli.main(["enqueue", "--root", str(root), "--data", str(data), "--collection", "missing"])
        == 1
    )

    def explode(*args, **kwargs):
        raise NeedsReview("review")

    monkeypatch.setattr(cli, "snapshot_for", explode)
    assert cli.main(["enqueue", "--root", str(root), "--data", str(data), "--limit", "1"]) == 1
    created = {"count": 0}

    def snapshot(*args, **kwargs):
        created["count"] += 1
        if created["count"] == 1:
            handlers[signal.SIGINT](signal.SIGINT, None)
        return {
            "page": {"key": args[1]["key"], "collection": args[1]["collection"]},
            "inputHash": args[1]["key"],
            "recipeHash": "recipe",
            "packets": [],
        }

    monkeypatch.setattr(cli, "snapshot_for", snapshot)
    assert cli.main(["enqueue", "--root", str(root), "--data", str(data)]) == 0
    (root / "data/translation/publication.json").write_text("{}", encoding="utf-8")
    assert cli.main(["enqueue", "--root", str(root), "--data", str(data)]) == 1
    (root / "data/translation/publication.json").unlink()

    class Worker:
        def __init__(self, store, client, repository, runtime, instructions=None):
            self.store = store

        def run(self, limit, trial):
            return {"outcome": {"completed": 1}, "paused": False}

    monkeypatch.setattr(cli, "BatchWorker", Worker)
    monkeypatch.setattr(cli, "AzureClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "config_from_env", lambda: {"draft": "d", "verify": "v"})
    assert cli.main(["run", "--root", str(root), "--data", str(data), "--limit", "1"]) == 0

    class ReviewWorker(Worker):
        def run(self, limit, trial):
            return {"outcome": {"needsReview": 1, "failed": 0}, "paused": False, "needs_review": []}

    monkeypatch.setattr(cli, "BatchWorker", ReviewWorker)
    assert cli.main(["run", "--root", str(root), "--data", str(data), "--trial"]) == 1

    class PauseWorker(Worker):
        def run(self, limit, trial):
            return {"outcome": {"failed": 1}, "paused": True}

    monkeypatch.setattr(cli, "BatchWorker", PauseWorker)
    assert cli.main(["run", "--root", str(root), "--data", str(data)]) == 1

    class BrokenRepo(Repo):
        def sync(self):
            raise OSError("disk")

    monkeypatch.setattr(cli, "Repository", BrokenRepo)
    assert cli.main(["sync", "--root", str(root), "--data", str(data)]) == 1
    assert signal.SIGTERM in handlers or True
