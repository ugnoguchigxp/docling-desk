"""File-backed Wiki workspaces and explicit synchronization to the local app."""

from __future__ import annotations

import json
import re
from pathlib import Path

from docling_desk.knowledge.catalog import (
    article_metadata,
    evidence_body,
    safe_file,
    split_frontmatter,
)
from docling_desk.knowledge.store import Store
from docling_desk.knowledge.terms import expand_terms

from .files import NeedsReview, hash_text, utf16_length


def chunks_for(body, title=None, maximum=2500):
    chunks, buffer, heading = [], "", title

    def flush():
        nonlocal buffer
        if buffer.strip():
            chunks.append(
                {
                    "locator": f"chunk:{len(chunks) + 1:04d}",
                    "heading": heading,
                    "content": buffer.strip(),
                }
            )
        buffer = ""

    for line in body.split("\n"):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            flush()
            heading = match[2]
        if buffer and utf16_length(buffer + "\n" + line) > maximum:
            flush()
        remaining = line
        while utf16_length(remaining) > maximum:
            flush()
            end, size = 0, 0
            for char in remaining:
                width = 2 if ord(char) > 0xFFFF else 1
                if size + width > maximum:
                    break
                end += 1
                size += width
            if not end:
                raise NeedsReview("本文分割の上限が小さすぎます。")
            buffer, remaining = remaining[:end], remaining[end:]
            flush()
        buffer += ("\n" if buffer else "") + remaining
    flush()
    return chunks


class Repository:
    def __init__(self, root: Path, data: Path):
        self.root, self.data = root.resolve(), data.resolve()
        self.refresh()

    def refresh(self):
        raw = safe_file(self.root, "manifests/pages.jsonl").read_text(encoding="utf-8")
        self.pages = [json.loads(line) for line in raw.splitlines() if line.strip()]
        required = {
            "key",
            "id",
            "collection",
            "original_path",
            "ja_path",
            "translation_status",
            "source_hash",
            "title_original",
        }
        if any(not isinstance(p, dict) or not required <= p.keys() for p in self.pages) or len(
            {p["key"] for p in self.pages}
        ) != len(self.pages):
            raise NeedsReview("Wikiの対応表が不正またはIDが重複しています。")
        for page in self.pages:
            if (
                not all(isinstance(page[k], str) for k in required)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", page["collection"])
                or not re.fullmatch(
                    re.escape(page["collection"]) + r"/[A-Za-z0-9][A-Za-z0-9._-]*", page["key"]
                )
                or not re.fullmatch(r"[a-f0-9]{64}", page["source_hash"])
                or page["translation_status"]
                not in {"untranslated", "translated", "reviewed", "needs_review"}
            ):
                raise NeedsReview("Wikiの版・翻訳状態が不正です。")
            for language, field in (("original", "original_path"), ("ja", "ja_path")):
                if not page[field].startswith(
                    f"wiki/pages/{language}/{page['collection']}/"
                ) or not page[field].endswith(".md"):
                    raise NeedsReview("Wiki記事の対応パスが不正です。")

    def read(self, path):
        file = safe_file(self.root, path)
        if file.stat().st_size > 2 * 1024 * 1024:
            raise NeedsReview("Wiki記事は2 MiB以内にしてください。")
        raw = file.read_bytes().decode("utf-8")
        body, meta = split_frontmatter(raw)
        return {"raw": raw, "body": body, "meta": meta}

    def sync(self, key=None):
        self.refresh()
        store = Store(self.data)
        from docling_desk.knowledge.originals import Originals

        workspace = Originals(self.data).register(self.root)
        grouped = {}
        for page in self.pages:
            if key is not None and key != page["key"]:
                continue
            for language, field, title in (
                ("original", "original_path", "title_original"),
                ("ja", "ja_path", "title_ja"),
            ):
                path = page[field]
                try:
                    document = self.read(path)
                except (FileNotFoundError, ValueError):
                    if language == "ja" and page["translation_status"] not in {
                        "translated",
                        "reviewed",
                    }:
                        continue
                    raise
                meta = article_metadata(
                    {
                        **document["meta"],
                        "title": page.get(title) or page["title_original"],
                        "external_key": page["key"],
                        "external_id": page["id"],
                        "translation_group": page["key"],
                        "language": language,
                        "translation_status": page["translation_status"],
                        "source_hash": page["source_hash"],
                        "collection": page["collection"],
                        "workspace": workspace,
                    }
                )
                if document["meta"].get("source_hash") not in {None, page["source_hash"]}:
                    raise NeedsReview("Wiki本文と対応表の原本版が一致しません。")
                grouped.setdefault(page["collection"], []).append(
                    {"path": path, "body": document["raw"], "metadata": meta}
                )
        if key is not None and not grouped:
            raise NeedsReview("公開対象の記事が対応表にありません。")
        for collection in grouped:
            for language in ("original", "ja"):
                path = f"wiki/pages/{language}/{collection}/index.csv"
                file = self.root / path
                if file.exists():
                    grouped[collection].append(
                        {
                            "path": path,
                            "body": safe_file(self.root, path).read_text(encoding="utf-8"),
                            "metadata": {
                                "entry_kind": "csv",
                                "language": language,
                                "workspace": workspace,
                                "title": f"{collection}の目次",
                            },
                        }
                    )
        if key is None:
            navigation = []
            landing = self.root / "wiki/pages/index.md"
            if landing.exists():
                navigation.append(("Wiki", "wiki/pages/index.md"))
            folders_file = self.root / "manifests/folders.json"
            if folders_file.exists():
                folders = json.loads(
                    safe_file(self.root, "manifests/folders.json").read_text(encoding="utf-8")
                )
                if not isinstance(folders, list):
                    raise NeedsReview("フォルダー対応表が不正です。")
                for folder in folders:
                    if (
                        not isinstance(folder, dict)
                        or folder.get("path") != f"wiki/pages/folders/{folder.get('id')}/index.md"
                        or folder.get("collection") not in {p["collection"] for p in self.pages}
                    ):
                        raise NeedsReview("フォルダー案内のパスが不正です。")
                    navigation.append((folder["collection"], folder["path"]))
            for collection, path in navigation:
                grouped.setdefault(collection, []).append(
                    {
                        "path": path,
                        "body": self.read(path)["raw"],
                        "metadata": {"entry_kind": "navigation", "workspace": workspace},
                    }
                )
        count = 0
        present = set()
        for collection, articles in grouped.items():
            imported = store.import_many(collection, articles)
            present.update(a["id"] for a in imported)
            count += len(imported)
        if key is None:
            for source in store.sources("wiki", include_body=False):
                if source.get("workspace") == workspace and source["id"] not in present:
                    store.delete_wiki(source["id"])
        return {"articles": count, "pages": len(self.pages), "indexed": True}

    def search(self, query, exclude_key=None, limit=4):
        store = Store(self.data)
        sources = [
            s
            for s in store.sources("wiki")
            if s.get("external_key")
            and s.get("workspace") == hash_text(str(self.root))
            and s.get("external_key") != exclude_key
            and s.get("entry_kind") != "csv"
            and not (
                s.get("language") == "ja"
                and s.get("translation_status") not in {"translated", "reviewed"}
            )
        ]
        expanded = expand_terms(query, sources)
        phrases = [query, *expanded]
        # Read the same FTS index as the app; keep glossary case rules before ranking.
        scores = {}
        with store.connection() as db:
            for phrase in phrases:
                from docling_desk.knowledge.store import normalized

                term = normalized(phrase)
                if len(term) >= 3:
                    rows = db.execute(
                        "SELECT id,bm25(chunk_fts,0,2,1) rank FROM chunk_fts WHERE chunk_fts MATCH ?",
                        ('"' + term.replace('"', '""') + '"',),
                    ).fetchall()
                    for row in rows:
                        scores[row[0]] = max(scores.get(row[0], 0), -row[1])
                else:
                    for chunk in store.chunks():
                        if term in normalized(chunk["text"] + " " + chunk["title"]):
                            scores[chunk["id"]] = 1
        source_map = {s["id"]: s for s in sources}
        hits = []
        for chunk in store.chunks():
            source = source_map.get(chunk["source_id"])
            if source is None or chunk["id"] not in scores:
                continue
            if (
                source.get("collection") == "glossary"
                and source["title"].lower() == query.lower()
                and source["title"] != query
            ):
                continue
            locator = json.loads(chunk["locator"])
            score = scores[chunk["id"]] + (
                100 if source["title"] == query or source.get("external_id") == query else 0
            )
            hits.append(
                {
                    "key": source["external_key"],
                    "id": source["external_id"],
                    "collection": source["collection"],
                    "language": source["language"],
                    "title": source["title"],
                    "path": source["path"],
                    "locator": locator.get("anchor") or chunk["id"],
                    "heading": locator.get("heading"),
                    "content": chunk["text"],
                    "sourceHash": source["source_hash"],
                    "sources": [],
                    "translationStatus": source["translation_status"],
                    "score": score,
                }
            )
        seen, result = set(), []
        for hit in sorted(hits, key=lambda h: (-h["score"], h["language"] != "ja", h["key"])):
            if hit["key"] not in seen:
                seen.add(hit["key"])
                result.append(hit)
        return {
            "query": query,
            "expandedTerms": expanded,
            "results": result[:limit],
            "indexedAt": None,
        }

    def related_document(self, hits, queries, reference_id, known):
        hit = hits[0]
        page = next(p for p in self.pages if p["key"] == hit["key"])
        original = self.read(page["original_path"])
        body = evidence_body(original["body"])
        previous = [
            d for d in known if d["key"] == page["key"] and d["bodyHash"] == hash_text(body)
        ]
        if any(not d["truncated"] for d in previous):
            return None
        content, locator = body, "document:full"
        if utf16_length(body) > 6000:
            chunks = chunks_for(body, page["title_original"])
            selected = set()
            for i, chunk in enumerate(chunks):
                if any(q.lower() in chunk["content"].lower() for q in queries):
                    selected.update(j for j in (i - 1, i, i + 1) if 0 <= j < len(chunks))
            if not selected:
                selected.add(0)
            read = {loc for d in previous for loc in d["locator"].split(",")}
            parts = []
            for i in sorted(selected):
                chunk = chunks[i]
                candidate = "\n\n".join(parts + [f"[{chunk['locator']}]\n{chunk['content']}"])
                if chunk["locator"] not in read and utf16_length(candidate) <= 6000:
                    parts.append(f"[{chunk['locator']}]\n{chunk['content']}")
            if not parts:
                return None
            content = "\n\n".join(parts)
            locator = ",".join(re.findall(r"\[(chunk:\d+)\]", content))
        return {
            **hit,
            "language": "original",
            "path": page["original_path"],
            "title": page["title_original"],
            "content": content,
            "locator": locator,
            "sourceHash": page["source_hash"],
            "referenceId": reference_id,
            "bodyHash": hash_text(body),
            "truncated": utf16_length(body) > 6000,
            "queries": queries,
        }
