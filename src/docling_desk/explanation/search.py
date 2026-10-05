"""Per-document, replaceable FTS5 index with literal queries and parent expansion."""

from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from docling_desk.sqlite_writer import connect
from docling_desk.storage import explanation_database


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def source_offset(text: str, normalized_offset: int) -> int:
    """Map a normalized match to an original-text boundary for excerpting."""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high) // 2
        if len(normalize(text[:middle])) < normalized_offset:
            low = middle + 1
        else:
            high = middle
    return low


def retrieve(folder: Path, source: dict, terms: list[str], *, exclude_pages=()) -> dict:
    path = explanation_database(folder)
    if path.parent.is_symlink() or (path.exists() and path.is_symlink()):
        raise ValueError("検索索引の保存先が不正です。")
    path.parent.mkdir(parents=True, exist_ok=True)
    rebuild = True
    if path.exists():
        try:
            with closing(connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
                rebuild = conn.execute(
                    "SELECT source_hash, index_version FROM metadata"
                ).fetchone() != (
                    source["source_hash"],
                    1,
                )
                rebuild = rebuild or conn.execute("PRAGMA quick_check").fetchone() != ("ok",)
        except sqlite3.Error:
            pass
    if rebuild:
        temporary = path.with_name(f"index-{uuid4().hex}.tmp")
        try:
            temporary.touch(mode=0o600, exist_ok=False)
            with closing(connect(temporary)) as conn, conn:
                conn.execute("CREATE TABLE metadata(source_hash TEXT, index_version INTEGER)")
                conn.execute("INSERT INTO metadata VALUES (?, ?)", (source["source_hash"], 1))
                conn.execute(
                    "CREATE VIRTUAL TABLE chunks USING fts5(text, chunk_id UNINDEXED, tokenize='trigram')"
                )
                conn.executemany(
                    "INSERT INTO chunks VALUES (?, ?)",
                    [(normalize(r["text"]), r["id"]) for r in source["children"]],
                )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    queries = list(
        dict.fromkeys(normalize(t.strip()) for t in terms if isinstance(t, str) and t.strip())
    )[:12]
    queries = [q[:100] for q in queries]
    children = {r["id"]: r for r in source["children"]}
    parents = {r["id"]: r for r in source["parents"]}
    selections = []
    with closing(connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
        for term in queries:
            if len(term) < 3:
                rows = conn.execute(
                    "SELECT chunk_id FROM chunks WHERE instr(text, ?) > 0 LIMIT 50", (term,)
                )
            else:
                literal = '"' + term.replace('"', '""') + '"'
                rows = conn.execute(
                    "SELECT chunk_id FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT 50",
                    (literal,),
                )
            selections.append(
                [
                    row[0]
                    for row in rows
                    if not (
                        children[row[0]].get("pages")
                        and set(children[row[0]]["pages"]) <= set(exclude_pages)
                    )
                ]
            )
    # Round robin prevents the first query monopolizing the evidence budget.
    selected = [keys[index] for index in range(50) for keys in selections if index < len(keys)]
    evidence = []
    seen = set()
    for key in dict.fromkeys(selected):
        child = children[key]
        parent = parents.get(child.get("parent_id")) or child
        if parent["id"] in seen:
            continue
        if parent.get("pages") and set(parent["pages"]) <= set(exclude_pages):
            continue
        seen.add(parent["id"])
        # Bound supplemental context, while the target unit itself stays complete.
        text = parent["text"]
        normalized = normalize(text)
        positions = [normalized.find(q) for q in queries if q in normalized]
        position = source_offset(text, min(positions, default=0))
        start = max(0, position - 1000) if len(text) > 4000 else 0
        excerpt = text[start : start + 4000]
        if len(text) > start + 4000:
            boundary = excerpt.rfind("\n")
            if boundary > 0:
                excerpt = excerpt[:boundary]
        evidence.append(
            {
                "id": f"local-{len(evidence) + 1}",
                "kind": "document",
                "chunk_id": parent["id"],
                "pages": parent.get("pages", []),
                "title": " / ".join(parent.get("headings", [])),
                "text": excerpt,
                "source_start": start,
                "source_end": start + len(excerpt),
                "truncated": len(text) > len(excerpt),
            }
        )
        if len(evidence) == 8:
            break
    return {
        "status": "success" if evidence else "no_results" if queries else "no_terms",
        "queries": queries,
        "evidence": evidence,
    }
