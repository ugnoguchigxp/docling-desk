"""Local UI's SQLite. Separate ownership and storage from the external knowledge API."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import tiktoken

from docling_desk.documents.library import LOCK, snapshot
from docling_desk.knowledge.catalog import (
    article_metadata,
    csv_rows,
    evidence_body,
    split_frontmatter,
)
from docling_desk.knowledge.content import parse
from docling_desk.knowledge.files import WikiFiles
from docling_desk.operations.backup import exclusive_write
from docling_desk.operations.faults import checkpoint
from docling_desk.sqlite_writer import connect
from docling_desk.storage import document_folder, knowledge_database, migrate_database


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def split_text(text: str, maximum: int = 1000) -> list[str]:
    # Split at Unicode character boundaries (slicing token IDs can corrupt Japanese).
    encoding = tiktoken.get_encoding("cl100k_base")
    if len(encoding.encode(text, disallowed_special=())) <= maximum:
        return [text]
    parts = []
    while text:
        lo, hi = 1, min(len(text), maximum * 4)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(encoding.encode(text[:mid], disallowed_special=())) <= maximum:
                lo = mid
            else:
                hi = mid - 1
        parts.append(text[:lo])
        text = text[lo:]
    return parts


class Store:
    def __init__(self, data: Path):
        self.data = data.resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        with exclusive_write(self.data, "migration"):
            migrate_database(
                self.data / "knowledge" / "local.sqlite", knowledge_database(self.data)
            )
        self.root = knowledge_database(self.data).parent
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "local.sqlite"
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, namespace TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                    revision TEXT NOT NULL, metadata TEXT NOT NULL, deleted INTEGER DEFAULT 0,
                    updated REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS wiki_path ON sources(namespace,path)
                    WHERE kind='wiki';
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, revision TEXT NOT NULL,
                    text TEXT NOT NULL, text_hash TEXT NOT NULL, context TEXT NOT NULL,
                    locator TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS contexts (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, text TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS context_source ON contexts(source_id);
                CREATE INDEX IF NOT EXISTS chunk_source ON chunks(source_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    id UNINDEXED, title, text, tokenize='trigram');
                CREATE TABLE IF NOT EXISTS vectors (
                    profile TEXT NOT NULL, hash TEXT NOT NULL, vector TEXT NOT NULL,
                    PRIMARY KEY(profile,hash));
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                    input TEXT NOT NULL, output TEXT, error TEXT, progress INTEGER DEFAULT 0,
                    total INTEGER DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
                    owner TEXT);
                CREATE TABLE IF NOT EXISTS gate (
                    profile TEXT PRIMARY KEY, owner TEXT, until REAL NOT NULL);
            """)
            # Upgrade derived storage without changing chunk IDs or saved retrievals.
            db.execute("BEGIN IMMEDIATE")
            if "context_id" not in {r[1] for r in db.execute("PRAGMA table_info(chunks)")}:
                db.execute("ALTER TABLE chunks ADD COLUMN context_id TEXT")
            for row in db.execute(
                "SELECT id,source_id,revision,context FROM chunks WHERE context_id IS NULL"
            ):
                context_id = digest(row["source_id"] + row["revision"] + digest(row["context"]))
                db.execute(
                    "INSERT OR IGNORE INTO contexts VALUES(?,?,?)",
                    (context_id, row["source_id"], row["context"]),
                )
                db.execute(
                    "UPDATE chunks SET context='',context_id=? WHERE id=?", (context_id, row["id"])
                )
        self.files = WikiFiles(self.data)
        self._wiki_signature = None
        with exclusive_write(self.data, "index"):
            if not self.files.manifest.exists():
                with self.connection() as db:
                    records = {}
                    for row in db.execute("SELECT * FROM sources WHERE kind='wiki'"):
                        source = dict(row)
                        records[source["id"]] = self.files.stage(source, source["body"])
                self.files.publish(records)
            self.sync_wiki()

    def sync_wiki(self):
        """Rebuild only Wiki index rows from the file catalog, preserving stable IDs."""
        with exclusive_write(self.data, "index"):
            signature = digest(self.files.manifest.read_text(encoding="utf-8"))
            if signature == self._wiki_signature:
                return
            records = self.files.load()
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                for sid, record in records.items():
                    source = {k: v for k, v in record.items() if k not in {"key", "sha256"}}
                    if isinstance(source["metadata"], dict):
                        source["metadata"] = json.dumps(source["metadata"], ensure_ascii=False)
                    raw = self.files.read(record)
                    source["body"] = (
                        raw
                        if source["path"].lower().endswith(".csv")
                        else split_frontmatter(raw)[0]
                    )
                    metadata = json.loads(source["metadata"])
                    sections = parse(evidence_body(source["body"]))[2]
                    if metadata.get("entry_kind") in {"csv", "navigation"} or (
                        metadata.get("language") == "ja"
                        and metadata.get("translation_status")
                        not in {None, "translated", "reviewed"}
                    ):
                        sections = []
                    self.replace(db, source, sections)
                    if record.get("deleted"):
                        db.execute("UPDATE sources SET deleted=1 WHERE id=?", (sid,))
                        self.remove_chunks(db, sid)
                for row in db.execute("SELECT id FROM sources WHERE kind='wiki'").fetchall():
                    if row[0] not in records:
                        db.execute("UPDATE sources SET deleted=1 WHERE id=?", (row[0],))
                        self.remove_chunks(db, row[0])
            self._wiki_signature = signature

    @contextmanager
    def connection(self, *, readonly=False):
        db = (
            connect(self.path.as_uri() + "?mode=ro", timeout=30, uri=True)
            if readonly
            else connect(self.path, timeout=30)
        )
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def sources(self, kind: str | None = None, *, include_body: bool = True) -> list[dict]:
        self.sync_wiki()
        columns = (
            "*"
            if include_body
            else "id,kind,namespace,path,title,revision,metadata,deleted,updated"
        )
        with self.connection(readonly=True) as db:
            return [
                self.decode(row)
                for row in db.execute(
                    f"SELECT {columns} FROM sources WHERE deleted=0 AND (? IS NULL OR kind=?) ORDER BY namespace,path",
                    (kind, kind),
                )
            ]

    @staticmethod
    def decode(row) -> dict:
        result = dict(row)
        result.update(json.loads(result.pop("metadata")))
        return result

    def source(self, source_id: str) -> dict | None:
        self.sync_wiki()
        with self.connection(readonly=True) as db:
            row = db.execute(
                "SELECT * FROM sources WHERE id=? AND deleted=0", (source_id,)
            ).fetchone()
            return self.decode(row) if row else None

    def replace(self, db, source: dict, sections: list[dict]):
        old = db.execute(
            "SELECT revision,deleted,title,metadata FROM sources WHERE id=?", (source["id"],)
        ).fetchone()
        if (
            old
            and not old["deleted"]
            and old["revision"] == source["revision"]
            and old["title"] == source["title"]
            and old["metadata"] == source["metadata"]
        ):
            return
        db.execute(
            """INSERT INTO sources(id,kind,namespace,path,title,body,revision,metadata,updated)
            VALUES(:id,:kind,:namespace,:path,:title,:body,:revision,:metadata,:updated)
            ON CONFLICT(id) DO UPDATE SET namespace=excluded.namespace,path=excluded.path,title=excluded.title,body=excluded.body,
            revision=excluded.revision,metadata=excluded.metadata,deleted=0,updated=excluded.updated""",
            source,
        )
        if old and old["revision"] == source["revision"] and not old["deleted"]:
            # Renaming also updates FTS titles without embedding the title again.
            if old["title"] != source["title"]:
                db.execute(
                    "UPDATE chunk_fts SET title=? WHERE id IN (SELECT id FROM chunks WHERE source_id=?)",
                    (normalized(source["title"]), source["id"]),
                )
            return
        self.remove_chunks(db, source["id"])
        checkpoint("index_after_unpublish")
        for number, section in enumerate(sections):
            context = section.get("context", section["text"])
            context_id = digest(source["id"] + source["revision"] + digest(context))
            db.execute(
                "INSERT OR IGNORE INTO contexts VALUES(?,?,?)", (context_id, source["id"], context)
            )
            for part, text in enumerate(split_text(section["text"])):
                if not text.strip():
                    continue
                cid = f"{source['id']}:{source['revision'][:12]}:{number}:{part}"
                locator = json.dumps(
                    {k: v for k, v in section.items() if k not in {"text", "context"}},
                    ensure_ascii=False,
                )
                db.execute(
                    "INSERT INTO chunks(id,source_id,revision,text,text_hash,context,locator,context_id) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        source["id"],
                        source["revision"],
                        text,
                        digest(text),
                        "",
                        locator,
                        context_id,
                    ),
                )
                db.execute(
                    "INSERT INTO chunk_fts VALUES(?,?,?)",
                    (cid, normalized(source["title"]), normalized(text)),
                )

    @staticmethod
    def remove_chunks(db, source_id):
        db.execute(
            "DELETE FROM chunk_fts WHERE id IN (SELECT id FROM chunks WHERE source_id=?)",
            (source_id,),
        )
        db.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
        db.execute("DELETE FROM contexts WHERE source_id=?", (source_id,))

    def import_wiki(self, namespace: str, path: str, body: str, title: str = "") -> dict:
        return self.import_many(namespace, [{"path": path, "body": body, "title": title}])[0]

    def import_many(self, namespace: str, articles: list[dict]) -> list[dict]:
        ids = []
        with exclusive_write(self.data, "index"):
            self.sync_wiki()
            return self._import_many(namespace, articles, ids)

    def _import_many(self, namespace: str, articles: list[dict], ids: list[str]) -> list[dict]:
        records = self.files.load()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for index, article in enumerate(articles):
                if index:
                    checkpoint("wiki_between_articles")
                path, raw = article["path"], article["body"]
                if path.lower().endswith(".csv"):
                    csv_rows(raw)
                    body, frontmatter = raw, {"entry_kind": "csv"}
                else:
                    body, frontmatter = split_frontmatter(raw)
                previous = db.execute(
                    "SELECT id,metadata FROM sources WHERE kind='wiki' AND namespace=? AND path=?",
                    (namespace, path),
                ).fetchone()
                sid = previous[0] if previous else "wiki-" + uuid4().hex
                explicit = article.get("metadata")
                metadata = {
                    **(json.loads(previous[1]) if previous and explicit is None else {}),
                    **article_metadata(frontmatter),
                    **(explicit or {}),
                }
                parsed_title, _, sections = parse(evidence_body(body))
                if metadata.get("entry_kind") in {"csv", "navigation"} or (
                    metadata.get("language") == "ja"
                    and metadata.get("translation_status") not in {None, "translated", "reviewed"}
                ):
                    sections = []
                source = {
                    "id": sid,
                    "kind": "wiki",
                    "namespace": namespace,
                    "path": path,
                    "title": article.get("title")
                    or metadata.get("title")
                    or parsed_title
                    or Path(path).stem,
                    "body": body,
                    "revision": digest(body + ("\nindex:excluded" if not sections else "")),
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "updated": time.time(),
                }
                self.replace(db, source, sections)
                records[sid] = self.files.stage({**source, "deleted": 0}, raw, records.get(sid))
                ids.append(sid)
            checkpoint("wiki_import_before_commit")
            self.files.publish(records)
        self._wiki_signature = digest(self.files.manifest.read_text(encoding="utf-8"))
        checkpoint("wiki_import_after_commit")
        return [self.source(sid) for sid in ids]

    def delete_wiki(self, source_id: str):
        with exclusive_write(self.data, "index"):
            self._delete_wiki(source_id)

    def _delete_wiki(self, source_id: str):
        self.sync_wiki()
        records = self.files.load()
        with self.connection() as db:
            db.execute("UPDATE sources SET deleted=1 WHERE id=? AND kind='wiki'", (source_id,))
            checkpoint("wiki_delete_before_chunks")
            self.remove_chunks(db, source_id)
            if source_id in records:
                records[source_id]["deleted"] = 1
                self.files.publish(records)
        self._wiki_signature = digest(self.files.manifest.read_text(encoding="utf-8"))

    def sync_documents(self):
        # Same lock as move/delete/copy prevents publishing partially changed library metadata.
        with exclusive_write(self.data, "index"):
            self._sync_documents()

    def _sync_documents(self):
        with LOCK:
            jobs = snapshot(self.data)["jobs"]
            with self.connection() as db:
                present = set()
                for job in jobs:
                    if job["state"] not in {"success", "partial"}:
                        continue
                    folder = document_folder(self.data, job["id"])
                    if folder.is_symlink():
                        continue
                    child = folder / "rag-index.jsonl"
                    parent = folder / "rag.jsonl"
                    try:
                        raw_parent = parent.read_text(encoding="utf-8")
                        raw_child = (
                            child.read_text(encoding="utf-8") if child.is_file() else raw_parent
                        )
                        contexts = {c["id"]: c for c in read_jsonl(raw_parent)}
                        children = read_jsonl(raw_child)
                        if any(
                            c.get("parent_id") and c["parent_id"] not in contexts for c in children
                        ):
                            raise ValueError("Missing parent")
                    except (OSError, ValueError, KeyError):
                        continue
                    sid = "doc-" + job["id"]
                    present.add(sid)
                    sections = []
                    for chunk in children:
                        if not chunk.get("text", "").strip():
                            continue
                        context = contexts.get(chunk.get("parent_id"), chunk)
                        sections.append(
                            {
                                "text": chunk["text"],
                                "context": context.get("text", chunk["text"]),
                                "parent_id": context.get("id"),
                                "refs": context.get("refs", []),
                                "context_refs": chunk.get("context_refs", []),
                                "provenance": context.get("provenance", []),
                                "relations": context.get("relations", []),
                                "row_range": chunk.get("row_range", []),
                                "pages": context.get("pages", []),
                                "unit": context.get("unit"),
                                "heading": " / ".join(chunk.get("headings", [])),
                            }
                        )
                    self.replace(
                        db,
                        {
                            "id": sid,
                            "kind": "document",
                            "namespace": "",
                            "path": job["id"],
                            "title": job["filename"],
                            "body": "",
                            "revision": digest(raw_child + raw_parent),
                            "metadata": json.dumps(
                                {
                                    "job_id": job["id"],
                                    "folder_id": job.get("folder_id"),
                                    "state": job["state"],
                                }
                            ),
                            "updated": time.time(),
                        },
                        sections,
                    )
                for row in db.execute(
                    "SELECT id FROM sources WHERE kind='document' AND deleted=0"
                ).fetchall():
                    if row[0] not in present:
                        db.execute("UPDATE sources SET deleted=1 WHERE id=?", (row[0],))
                        self.remove_chunks(db, row[0])

    def chunks(self) -> list[dict]:
        with self.connection(readonly=True) as db:
            return [
                dict(row)
                for row in db.execute("""SELECT c.id,c.source_id,c.revision,c.text,c.text_hash,c.context_id,c.locator,s.kind,s.title,s.namespace,s.metadata
                FROM chunks c JOIN sources s ON s.id=c.source_id WHERE s.deleted=0 AND c.revision=s.revision""")
            ]

    def context(self, chunk_id: str) -> str:
        with self.connection(readonly=True) as db:
            row = db.execute(
                "SELECT p.text FROM chunks c JOIN contexts p ON p.id=c.context_id WHERE c.id=?",
                (chunk_id,),
            ).fetchone()
            if not row:
                raise ValueError("出典が更新されました。検索をやり直してください。")
            return row[0]


def read_jsonl(raw: str) -> list[dict]:
    chunks = [json.loads(line) for line in raw.splitlines() if line.strip()]
    ids = set()
    for chunk in chunks:
        if (
            not isinstance(chunk, dict)
            or not isinstance(chunk.get("id"), str)
            or not chunk["id"]
            or chunk["id"] in ids
        ):
            raise ValueError("Invalid or duplicate chunk")
        ids.add(chunk["id"])
        if not isinstance(chunk.get("text", ""), str):
            raise ValueError("Invalid chunk text")
        for field in ("headings", "refs", "context_refs"):
            values = chunk.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError("Invalid chunk references")
        pages = chunk.get("pages", [])
        if not isinstance(pages, list) or any(type(v) is not int or v < 1 for v in pages):
            raise ValueError("Invalid chunk pages")
        if chunk.get("parent_id") is not None and not isinstance(chunk["parent_id"], str):
            raise ValueError("Invalid parent reference")
        if chunk.get("unit") is not None and not isinstance(chunk["unit"], (str, dict)):
            raise ValueError("Invalid chunk unit")
        for field in ("provenance", "relations", "row_range"):
            if not isinstance(chunk.get(field, []), list):
                raise ValueError("Invalid chunk locator")
    return chunks
