"""Markdown Wiki, shared retrieval, and one persisted queue for Azure index/query calls."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

import tiktoken

from docling_desk.documents.library import LOCK, ancestors, load
from docling_desk.knowledge.embedding_azure import AzureEmbedding, EmbeddingError, paced_request
from docling_desk.knowledge.store import Store, digest, normalized
from docling_desk.knowledge.terms import expand_terms


class Knowledge:
    def __init__(self, data: Path, provider: AzureEmbedding | None = None):
        self.store = Store(data)
        self.provider = provider
        self.owner = uuid4().hex
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self.work, name="local-knowledge", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        self.thread.join(timeout=1)

    def refresh(self):
        self.store.sync_wiki()
        self.store.sync_documents()

    def create_task(self, kind: str, value: dict, output: dict | None = None) -> dict:
        value = dict(value)
        if kind in {"search", "index"} and self.provider:
            value["_embedding_profile"] = self.provider.profile
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Request IDs bind retries to the same normalized input, not to a new API attempt.
            request_id = value.get("client_request_id")
            if request_id:
                for row in db.execute(
                    "SELECT * FROM tasks WHERE kind IN ('text','search') AND json_extract(input,'$.client_request_id')=? LIMIT 1",
                    (request_id,),
                ):
                    previous = json.loads(row["input"])
                    if previous.get("client_request_id") == request_id:
                        if previous != value:
                            raise ValueError("同じ検索IDで異なる条件は指定できません。")
                        return task_value(row)
            active = db.execute(
                "SELECT count(*) FROM tasks WHERE state IN ('queued','running')"
            ).fetchone()[0]
            if active >= 10 and output is None:
                raise ValueError(
                    "検索・索引作成の処理が混み合っています。完了後に再度実行してください。"
                )
            now, task_id = time.time(), uuid4().hex
            db.execute(
                "INSERT INTO tasks(id,kind,state,input,output,created,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    task_id,
                    kind,
                    "completed" if output is not None else "queued",
                    json.dumps(value, ensure_ascii=False),
                    json.dumps(output, ensure_ascii=False) if output is not None else None,
                    now,
                    now,
                ),
            )
        self.wake.set()
        return self.task(task_id)

    def task(self, task_id: str) -> dict | None:
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return task_value(row) if row else None

    def cancel(self, task_id: str):
        with self.store.connection() as db:
            db.execute(
                "UPDATE tasks SET state='cancelled',updated=? WHERE id=? AND state IN ('queued','running')",
                (time.time(), task_id),
            )
        self.wake.set()

    def work(self):
        while not self.stop.is_set():
            try:
                # Crash recovery: never silently replay a potentially accepted network request.
                with self.store.connection() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute(
                        "UPDATE tasks SET state='failed',error=?,updated=? WHERE state='running' AND updated<?",
                        (
                            "前回の処理が中断しました。待ち時間後に再実行してください。",
                            time.time(),
                            time.time() - 240,
                        ),
                    )
                    row = db.execute(
                        "SELECT * FROM tasks WHERE state='queued' ORDER BY CASE WHEN kind='search' THEN 0 ELSE 1 END,created LIMIT 1"
                    ).fetchone()
                    if row:
                        db.execute(
                            "UPDATE tasks SET state='running',owner=?,updated=? WHERE id=?",
                            (self.owner, time.time(), row["id"]),
                        )
            except sqlite3.Error:
                logging.getLogger(__name__).warning(
                    "Knowledge queue storage temporarily unavailable"
                )
                self.stop.wait(1)
                continue
            if not row:
                self.wake.wait(1)
                self.wake.clear()
                continue
            task_id, value = row["id"], json.loads(row["input"])
            heartbeat_stop = threading.Event()

            def heartbeat():
                while not heartbeat_stop.wait(10):
                    try:
                        if not self.update(task_id):
                            return
                    except sqlite3.Error:
                        logging.getLogger(__name__).warning(
                            "Knowledge task heartbeat could not be saved"
                        )

            heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
            heartbeat_thread.start()
            try:
                if not self.provider or value.get("_embedding_profile") != self.provider.profile:
                    raise EmbeddingError(
                        "Azure embeddingの設定が変更されました。処理を再実行してください。"
                    )
                if row["kind"] == "index":
                    output = self.index(task_id)
                else:
                    self.validate_scope(value)
                    vector = self.embed([value["query"]], task_id)[0]
                    self.refresh()
                    output = self.search(value, vector)
                if output is None:
                    self.update(task_id, state="queued")
                else:
                    self.update(
                        task_id, state="completed", output=json.dumps(output, ensure_ascii=False)
                    )
            except (EmbeddingError, ValueError, OSError) as exc:
                self.fail(task_id, str(exc))
            except Exception:
                # Persist failure rather than killing the sole worker or leaking credentials/content.
                self.fail(task_id, "処理に失敗しました。再実行してください。")
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join()

    def fail(self, task_id, error):
        try:
            self.update(task_id, state="failed", error=error)
        except sqlite3.Error:
            logging.getLogger(__name__).warning("Knowledge task failure could not be saved")

    def update(self, task_id, **values):
        values["updated"] = time.time()
        with self.store.connection() as db:
            cursor = db.execute(
                "UPDATE tasks SET "
                + ",".join(f"{k}=?" for k in values)
                + " WHERE id=? AND state='running' AND owner=?",
                (*values.values(), task_id, self.owner),
            )
            return cursor.rowcount == 1

    def cancelled(self, task_id):
        if self.stop.is_set():
            return True
        with self.store.connection() as db:
            return not db.execute(
                "SELECT 1 FROM tasks WHERE id=? AND state='running' AND owner=?",
                (task_id, self.owner),
            ).fetchone()

    def embed(self, texts: list[str], task_id: str) -> list[list[float]]:
        if not self.provider:
            raise EmbeddingError("Azure embeddingが未設定です。全文検索は利用できます。")
        profile = self.provider.profile
        hashes = [digest(text) for text in texts]
        cached = self.vectors(hashes)
        missing = list(dict.fromkeys(h for h in hashes if h not in cached))
        if missing:
            inputs = [texts[hashes.index(h)] for h in missing]
            indexing = self.task(task_id)["kind"] == "index"

            def invalidated():
                if self.cancelled(task_id):
                    return True
                if self.provider.profile != self.task(task_id)["input"].get("_embedding_profile"):
                    raise EmbeddingError(
                        "Azure embeddingの設定が変更されました。処理を再実行してください。"
                    )
                if indexing:
                    self.refresh()
                    current = {c["text_hash"] for c in self.store.chunks()}
                    return any(h not in current for h in missing)
                self.validate_scope(self.task(task_id)["input"])
                return False

            vectors = None
            for attempt in range(3):
                self.update(task_id)  # Also renew task liveness while waiting for the gate.
                try:
                    vectors = paced_request(
                        self.store,
                        self.provider,
                        inputs,
                        self.stop,
                        invalidated,
                    )
                    break
                except EmbeddingError as exc:
                    if not exc.retryable or attempt == 2 or self.cancelled(task_id):
                        raise
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                known_dimensions = {
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT json_array_length(vector) FROM vectors WHERE profile=?",
                        (profile,),
                    )
                }
                if known_dimensions and {len(v) for v in vectors} != known_dimensions:
                    raise EmbeddingError(
                        "embeddingの次元が変更されました。モデル版の設定を更新してください。"
                    )
                for hash_, vector in zip(missing, vectors, strict=True):
                    db.execute(
                        "INSERT OR IGNORE INTO vectors VALUES(?,?,?)",
                        (profile, hash_, json.dumps(vector)),
                    )
                    cached[hash_] = vector
        return [cached[h] for h in hashes]

    def vectors(self, hashes: list[str] | None = None) -> dict:
        if not self.provider:
            return {}
        with self.store.connection() as db:
            result = {}
            if hashes is None:
                batches = [None]
            else:
                unique = list(dict.fromkeys(hashes))
                batches = [unique[i : i + 256] for i in range(0, len(unique), 256)]
            for batch in batches:
                sql = "SELECT hash,vector FROM vectors WHERE profile=?"
                parameters = [self.provider.profile]
                if batch is not None:
                    sql += " AND hash IN (" + ",".join("?" for _ in batch) + ")"
                    parameters.extend(batch)
                result.update({r[0]: json.loads(r[1]) for r in db.execute(sql, parameters)})
            return result

    def vector_hashes(self):
        if not self.provider:
            return set()
        with self.store.connection() as db:
            return {
                r[0]
                for r in db.execute(
                    "SELECT hash FROM vectors WHERE profile=?", (self.provider.profile,)
                )
            }

    def index(self, task_id):
        self.refresh()
        chunks = self.store.chunks()
        hashes, cached = set(), self.vector_hashes()
        texts = []
        for c in chunks:
            if c["text_hash"] not in hashes and c["text_hash"] not in cached:
                hashes.add(c["text_hash"])
                texts.append(c["text"])
        progress = self.task(task_id)["progress"]
        self.update(task_id, total=progress + len(texts))
        if self.cancelled(task_id):
            raise EmbeddingError("処理を中止しました。")
        # Yield after each <=6000-token batch so interactive searches can use the same queue.
        batch = texts[:6]
        if batch:
            self.embed(batch, task_id)
            progress += len(batch)
            self.update(task_id, progress=progress)
        return None if len(texts) > 6 else {"indexed": progress}

    def start_search(self, value: dict) -> dict:
        self.refresh()
        self.validate_scope(value)
        if value["mode"] == "text":
            result = self.search(value)
            return self.retrieval(self.create_task("text", value, result)["id"])
        if not self.provider:
            raise ValueError("Azure embeddingが未設定です。全文検索を選んでください。")
        cached = self.vectors([digest(value["query"])])
        if cached:
            output = self.search(value, cached[digest(value["query"])])
            return self.retrieval(self.create_task("search", value, output)["id"])
        return self.create_task("search", value)

    def validate_scope(self, value):
        source = value.get("source_id")
        if source is not None and (not source or not self.store.source(source)):
            raise ValueError("検索対象の資料・記事が見つかりません。")
        folder = value.get("folder_id")
        if folder is not None and (not folder or folder not in load(self.store.data).folders):
            raise ValueError("検索対象のフォルダーが見つかりません。")

    def selected_chunks(self, value):
        chunks = self.store.chunks()
        folder = value.get("folder_id")
        folders = load(self.store.data) if folder is not None else None
        if folder is not None and folder not in folders.folders:
            return []
        result = []
        for c in chunks:
            metadata = json.loads(c["metadata"])
            if value.get("kind", "all") != "all" and c["kind"] != value["kind"]:
                continue
            if value.get("source_id") and c["source_id"] != value["source_id"]:
                continue
            if value.get("namespace") and c["namespace"] != value["namespace"]:
                continue
            if folder is not None and (
                c["kind"] != "document"
                or folder
                not in [metadata.get("folder_id"), *ancestors(folders, metadata.get("folder_id"))]
            ):
                continue
            result.append(c)
        return result

    def search(self, value, query_vector=None):
        self.refresh()
        with LOCK:
            chunks = self.selected_chunks(value)
            query = normalized(value["query"])
            terms = query.split()
            expanded = expand_terms(value["query"], self.store.sources("wiki"))
            alternatives = [terms, *[normalized(phrase).split() for phrase in expanded]]
            text_scores = {}
            with self.store.connection() as db:
                # FTS5 trigram indexes substrings >=3 characters. Short terms need exact scanning.
                for words in alternatives:
                    long_terms = [t for t in words if len(t) >= 3]
                    if long_terms:
                        match = " AND ".join('"' + t.replace('"', '""') + '"' for t in long_terms)
                        rows = db.execute(
                            "SELECT id,bm25(chunk_fts,0,2,1) AS rank FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY rank",
                            (match,),
                        )
                        for row in rows:
                            text_scores[row[0]] = max(text_scores.get(row[0], 0), -row[1])
                    else:
                        text_scores.update({c["id"]: 1 for c in chunks})
            text = [
                c
                for c in chunks
                if c["id"] in text_scores
                and any(
                    all(t in normalized(c["text"] + " " + c["title"]) for t in words)
                    for words in alternatives
                )
                and not (
                    json.loads(c["metadata"]).get("collection", c["namespace"]) == "glossary"
                    and normalized(c["title"]) == query
                    and c["title"] != value["query"].strip()
                )
            ]
            text.sort(
                key=lambda c: (c["title"] != value["query"].strip(), -text_scores[c["id"]], c["id"])
            )
            semantic = []
            vectors = self.vectors([c["text_hash"] for c in chunks]) if query_vector else {}
            if query_vector:
                for c in chunks:
                    v = vectors.get(c["text_hash"])
                    if v and len(v) == len(query_vector):
                        vn, qn = math.hypot(*v), math.hypot(*query_vector)
                        if vn and qn and math.isfinite(vn) and math.isfinite(qn):
                            score = sum(
                                (a / vn) * (b / qn) for a, b in zip(v, query_vector, strict=True)
                            )
                            semantic.append((score, c))
                semantic.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
            scores = {}
            if value["mode"] in {"text", "hybrid"}:
                for rank, c in enumerate(text, 1):
                    scores[c["id"]] = scores.get(c["id"], 0) + 1 / (60 + rank)
            if value["mode"] in {"semantic", "hybrid"}:
                for rank, (_, c) in enumerate(semantic, 1):
                    scores[c["id"]] = scores.get(c["id"], 0) + 1 / (60 + rank)
            candidates, seen = [], set()
            for c in sorted(
                [c for c in chunks if c["id"] in scores],
                key=lambda c: (
                    -scores[c["id"]],
                    json.loads(c["metadata"]).get("language") != "ja",
                    c["id"],
                ),
            ):
                group = json.loads(c["metadata"]).get("translation_group")
                identity = (
                    (c["namespace"], group) if group and not value.get("source_id") else c["id"]
                )
                if identity not in seen:
                    seen.add(identity)
                    candidates.append(c)
            candidates = candidates[: value.get("limit", 20)]
            missing = sum(c["text_hash"] not in vectors for c in chunks) if query_vector else 0
            return {
                "results": [result_value(c, scores[c["id"]]) for c in candidates],
                "mode": value["mode"],
                "expanded_terms": expanded,
                "unembedded": missing,
                "notice": f"意味検索用の索引が未作成の断片が{missing}件あります。"
                if missing
                else "",
            }

    def retrieval(self, task_id: str) -> dict | None:
        self.refresh()
        with LOCK:
            task = self.task(task_id)
            if task and task.get("output", {}).get("results"):
                # Revalidate both revision and scope after a move/delete/reimport.
                allowed = {c["id"]: c for c in self.selected_chunks(task["input"])}
                original = task["output"]["results"]
                task["output"]["results"] = [
                    result_value(allowed[r["chunk_id"]], r["score"])
                    for r in original
                    if r["chunk_id"] in allowed
                ]
                task["output"]["stale"] = len(original) != len(task["output"]["results"])
            return task

    def context(self, task_id: str, chunk_ids: list[str], budget: int) -> dict:
        task = self.retrieval(task_id)
        if not task or task["state"] != "completed" or task["kind"] not in {"text", "search"}:
            raise ValueError("完了した検索結果を指定してください。")
        results = {r["chunk_id"]: r for r in task["output"]["results"]}
        if any(cid not in results for cid in chunk_ids):
            raise ValueError("検索結果に含まれない、または更新・削除された断片は取得できません。")
        self.refresh()
        with LOCK:
            chunks = {c["id"]: c for c in self.selected_chunks(task["input"])}
            used, seen, citations, text = 0, set(), [], []
            encoding = tiktoken.get_encoding("cl100k_base")
            for cid in dict.fromkeys(chunk_ids):
                if cid not in chunks:
                    raise ValueError("出典が更新されました。検索をやり直してください。")
                c = chunks[cid]
                key = c["context_id"]
                if key in seen:
                    continue
                seen.add(key)
                label = f"[S{len(citations) + 1}] {c['title']}\n"
                separator = "\n\n" if text else ""
                prefix = "\n\n".join(text) + separator
                remaining = budget - len(encoding.encode(prefix, disallowed_special=()))
                if remaining < len(encoding.encode(label, disallowed_special=())) + 10:
                    break
                tokens = encoding.encode(label + self.store.context(cid), disallowed_special=())
                content = encoding.decode(tokens[:remaining])
                while content.endswith("\ufffd"):
                    content = content[:-1]
                text.append(content)
                citations.append(
                    {
                        **results[cid],
                        "label": f"S{len(citations) + 1}",
                        "truncated": len(tokens) > remaining,
                    }
                )
            combined = "\n\n".join(text)
            combined_tokens = encoding.encode(combined, disallowed_special=())
            if len(combined_tokens) > budget:
                combined = encoding.decode(combined_tokens[:budget]).rstrip("\ufffd")
                citations[-1]["truncated"] = True
            used = len(encoding.encode(combined, disallowed_special=()))
            return {
                "text": combined,
                "citations": citations,
                "tokens": used,
                "budget": budget,
                "retrieval_id": task_id,
            }

    def status(self):
        self.refresh()
        chunks = self.store.chunks()
        cached = self.vector_hashes()
        with self.store.connection() as db:
            tasks = [
                task_value(row)
                for row in db.execute(
                    "SELECT * FROM tasks WHERE kind='index' ORDER BY created DESC LIMIT 5"
                )
            ]
            gate = (
                db.execute(
                    "SELECT until FROM gate WHERE profile=?", (self.provider.gate_key,)
                ).fetchone()
                if self.provider
                else None
            )
        return {
            "azure_configured": bool(self.provider),
            "chunks": len(chunks),
            "embedded": sum(c["text_hash"] in cached for c in chunks),
            "cooldown_until": gate[0] if gate else None,
            "interval_seconds": 15,
            "jobs": tasks,
        }


def task_value(row):
    result = dict(row)
    result["input"] = json.loads(result["input"])
    result["output"] = json.loads(result["output"]) if result["output"] else {}
    result.pop("owner", None)
    return result


def result_value(chunk, score):
    locator = json.loads(chunk["locator"])
    metadata = json.loads(chunk["metadata"])
    unit = locator.get("unit")
    if isinstance(unit, dict):
        unit = unit.get("number") or unit.get("index")
    pages = locator.get("pages", [])
    return {
        "chunk_id": chunk["id"],
        "source_id": chunk["source_id"],
        "kind": chunk["kind"],
        "title": chunk["title"],
        "revision": chunk["revision"],
        "text": chunk["text"],
        "score": score,
        "job_id": metadata.get("job_id"),
        "partial": metadata.get("state") == "partial",
        "unit": unit if isinstance(unit, int) else pages[0] if pages else 1,
        "locator": locator,
    }
