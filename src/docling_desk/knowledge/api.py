"""Local-only Wiki/retrieval routes; no access to arbitrary server filesystem paths."""

from __future__ import annotations

import html
import re
import threading
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode

import tiktoken
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from docling_desk.documents.library import LOCK
from docling_desk.knowledge.catalog import evidence_body, manifest_metadata
from docling_desk.knowledge.content import normalize_path, parse, render
from docling_desk.knowledge.embedding_azure import AzureEmbedding, EmbeddingError
from docling_desk.knowledge.originals import Originals
from docling_desk.knowledge.service import Knowledge
from docling_desk.operations.backup import exclusive_write

MANAGER_LOCK = threading.RLock()
MAX_WIKI_BYTES = 2 * 1024 * 1024
MAX_WIKI_BATCH = 20 * 1024 * 1024


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["text", "semantic", "hybrid"] = "text"
    kind: Literal["all", "document", "wiki"] = "all"
    source_id: str | None = Field(default=None, min_length=1, max_length=128)
    folder_id: str | None = Field(default=None, min_length=1, max_length=128)
    namespace: str | None = Field(default=None, min_length=1, max_length=100)
    limit: int = Field(default=20, ge=1, le=50)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=100)


class ContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retrieval_id: str = Field(min_length=1, max_length=100)
    chunk_ids: list[str] = Field(min_length=1, max_length=50)
    budget: int = Field(default=4000, ge=100, le=16000)


class WikiMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    language: str | None = Field(default=None, pattern=r"^[a-zA-Z][a-zA-Z0-9-]{0,31}$")
    translation_group: str | None = Field(default=None, min_length=1, max_length=100)
    source_job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    source_unit: int = Field(default=1, ge=1, le=10000)
    external_key: str | None = Field(default=None, min_length=1, max_length=512)
    external_id: str | None = Field(default=None, min_length=1, max_length=200)
    collection: str | None = Field(default=None, min_length=1, max_length=200)
    source_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    translation_status: Literal["untranslated", "translated", "reviewed", "needs_review"] | None = (
        None
    )
    category: str | None = Field(default=None, max_length=200)


def service(request: Request, data: Path) -> Knowledge:
    with MANAGER_LOCK:
        current = getattr(request.app.state, "knowledge", None)
        if current and current.store.data != data.resolve():
            current.close()
            current = None
        if not current:
            error = ""
            try:
                provider = AzureEmbedding.from_env()
            except EmbeddingError as exc:
                provider, error = None, str(exc)
            current = Knowledge(data, provider)
            current.config_error = error
            request.app.state.knowledge = current
        return current


def create_router(data: Callable[[], Path]) -> APIRouter:
    router = APIRouter()

    def manager(request: Request):
        return service(request, data())

    def displayed_source(knowledge, source):
        if (
            source.get("language") == "ja"
            and source.get("translation_status") in {"untranslated", "needs_review"}
            and source.get("translation_group")
        ):
            return next(
                (
                    s
                    for s in knowledge.store.sources("wiki")
                    if s["namespace"] == source["namespace"]
                    and s.get("workspace") == source.get("workspace")
                    and s.get("translation_group") == source["translation_group"]
                    and s.get("language") == "original"
                ),
                source,
            )
        return source

    @router.get("/api/wiki/catalog")
    def catalog(request: Request):
        articles = manager(request).store.sources("wiki", include_body=False)
        return {
            "articles": [{k: v for k, v in s.items() if k != "body"} for s in articles],
            "namespaces": sorted({s["namespace"] for s in articles}),
        }

    @router.post("/api/wiki/import", status_code=201)
    async def import_articles(
        request: Request,
        files: list[UploadFile] = File(...),
        namespace: str = Form(default="Wiki"),
        manifest: UploadFile | None = File(default=None),
    ):
        namespace = namespace.strip()
        if not namespace or len(namespace) > 100 or re.search(r"[/\\\x00-\x1f]", namespace):
            raise HTTPException(422, "Wikiの分類名は100文字以内で指定してください。")
        if not 1 <= len(files) <= 100:
            raise HTTPException(422, "一度に取り込める記事は100件までです。")
        parsed, paths, size, metadata = [], set(), 0, {}
        try:
            if manifest:
                raw_manifest = await manifest.read(256 * 1024 + 1)
                if len(raw_manifest) > 256 * 1024:
                    raise ValueError("記事情報のmanifestは256 KiB以内にしてください。")
                try:
                    metadata = {
                        normalize_path(path): WikiMetadata.model_validate(m).model_dump(
                            exclude_none=True
                        )
                        for path, m in manifest_metadata(
                            raw_manifest.decode("utf-8-sig"), manifest.filename or ""
                        ).items()
                    }
                except (ValueError, TypeError):
                    raise ValueError("manifestの記事情報が不正です。") from None
            for file in files:
                path = normalize_path(file.filename or "")
                if path in paths:
                    raise ValueError("同じパスの記事が重複しています。")
                paths.add(path)
                raw = await file.read(MAX_WIKI_BYTES + 1)
                size += len(raw)
                if len(raw) > MAX_WIKI_BYTES or size > MAX_WIKI_BATCH:
                    raise ValueError("記事は1件2 MiB、一度の取り込みは20 MiBまでです。")
                try:
                    body = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise ValueError("Wiki記事はUTF-8で保存してください。") from None
                if not body.strip() or any(ord(c) < 32 and c not in "\r\n\t" for c in body):
                    raise ValueError("空の記事やバイナリデータは取り込めません。")
                parsed.append({"path": path, "body": body, "metadata": metadata.get(path)})
            if manifest and (manifest.filename or "").endswith(".jsonl"):
                metadata = {p: m for p, m in metadata.items() if p in paths}
                for article in parsed:
                    article["metadata"] = metadata.get(article["path"])
            if set(metadata) - paths:
                raise ValueError(
                    "manifestには今回取り込むMarkdown記事のパスだけを指定してください。"
                )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            for file in [*files, *([manifest] if manifest else [])]:
                await file.close()
        knowledge = manager(request)

        def commit():
            with exclusive_write(knowledge.store.data, "index"):
                with LOCK:
                    knowledge.refresh()
                    registered = {
                        s["job_id"] for s in knowledge.store.sources("document", include_body=False)
                    }
                    if any(
                        m.get("source_job_id") and m["source_job_id"] not in registered
                        for m in metadata.values()
                    ):
                        raise HTTPException(422, "manifestの関連資料が登録されていません。")
                    return knowledge.store.import_many(namespace, parsed)

        try:
            articles = await run_in_threadpool(commit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"articles": [{k: v for k, v in s.items() if k != "body"} for s in articles]}

    @router.get("/api/wiki/sources/{source_id}")
    def article(request: Request, source_id: str):
        knowledge = manager(request)
        knowledge.refresh()
        source = knowledge.store.source(source_id)
        if not source or source["kind"] != "wiki":
            raise HTTPException(404, "Wiki記事が見つかりません。")
        displayed = displayed_source(knowledge, source)
        body = displayed["body"]
        return {
            **source,
            "body": body,
            "fallback_original": displayed["id"] if displayed["id"] != source["id"] else None,
            "related_document": next(
                (
                    s
                    for s in knowledge.store.sources("document", include_body=False)
                    if s["job_id"] == source.get("source_job_id")
                ),
                None,
            ),
            "outline": [] if source.get("entry_kind") == "csv" else parse(evidence_body(body))[1],
            "html": render(
                body,
                displayed["namespace"],
                displayed["path"],
                knowledge.store.sources("wiki", include_body=False),
                knowledge.store.sources("document", include_body=False),
                knowledge.store.source,
                lambda path, fragment: Originals(knowledge.store.data).link(
                    displayed, path, fragment
                ),
            ),
        }

    @router.get("/api/wiki/sources/{source_id}/original")
    def original(request: Request, source_id: str, path: str, download: bool = False):
        knowledge = manager(request)
        source = knowledge.store.source(source_id)
        if not source or source["kind"] != "wiki":
            raise HTTPException(404, "Wiki記事が見つかりません。")
        originals = Originals(knowledge.store.data)
        stack = ExitStack()
        try:
            stream = stack.enter_context(originals.open(source, path))
        except (OSError, ValueError):
            stack.close()
            return HTMLResponse(
                '<!doctype html><html lang="ja"><meta charset="utf-8">'
                "<title>原本を参照できません</title><h1>原本を参照できません</h1>"
                "<p>ファイルが見つからないか、登録した原本領域の外、またはシンボリックリンクです。"
                "ワークスペースの同期と原本の保存場所を確認してください。</p></html>",
                status_code=404,
                headers={
                    "Content-Security-Policy": "default-src 'none'",
                    "Cache-Control": "no-store",
                },
            )
        name = path.rsplit("/", 1)[-1]
        if download:

            def chunks():
                try:
                    while block := stream.read(64 * 1024):
                        yield block
                finally:
                    stack.close()

            return StreamingResponse(
                chunks(),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe=""),
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-store",
                },
                background=BackgroundTask(stack.close),
            )
        with stack:
            raw = stream.read(MAX_WIKI_BYTES + 1)
        content = "<p>この添付ファイルは取得して開いてください。</p>"
        if path.lower().endswith((".md", ".markdown", ".csv")):
            try:
                if len(raw) > MAX_WIKI_BYTES:
                    raise ValueError
                body = raw.decode("utf-8-sig")
                content = render(
                    body,
                    source["namespace"],
                    path,
                    knowledge.store.sources("wiki", include_body=False),
                    knowledge.store.sources("document", include_body=False),
                    knowledge.store.source,
                    lambda target, fragment: originals.link(source, target, fragment),
                    workspace=source.get("workspace"),
                )
            except ValueError:
                content = "<p>この原本は画面で表示できません。取得して内容を確認してください。</p>"
        url = request.url.path + "?" + urlencode({"path": path, "download": "true"})
        return HTMLResponse(
            '<!doctype html><html lang="ja"><meta charset="utf-8">'
            f"<title>{html.escape(name)} — 原本</title>"
            "<style>body{max-width:70rem;margin:2rem auto;padding:0 1rem;overflow-wrap:anywhere;"
            "font:16px/1.6 system-ui}table{border-collapse:collapse}td,th{border:1px solid #aaa;"
            "padding:.4rem}pre{overflow:auto}</style>"
            f"<header><h1>{html.escape(name)}</h1><p>書き出し原本（読み取り専用）</p>"
            f'<a href="{html.escape(url, quote=True)}">原本を取得</a></header><hr><main>{content}</main></html>',
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @router.get("/api/wiki/sources/{source_id}/markdown")
    def markdown(request: Request, source_id: str):
        knowledge = manager(request)
        source = knowledge.store.source(source_id)
        if not source or source["kind"] != "wiki":
            raise HTTPException(404, "Wiki記事が見つかりません。")
        return Response(
            displayed_source(knowledge, source)["body"],
            media_type="text/csv; charset=utf-8"
            if source.get("entry_kind") == "csv"
            else "text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment"},
        )

    @router.delete("/api/wiki/sources/{source_id}")
    def delete(request: Request, source_id: str):
        knowledge = manager(request)
        source = knowledge.store.source(source_id)
        if not source or source["kind"] != "wiki":
            raise HTTPException(404, "Wiki記事が見つかりません。")
        with exclusive_write(knowledge.store.data, "index"):
            with LOCK:
                knowledge.store.delete_wiki(source_id)
        return {"deleted": source_id}

    @router.get("/api/knowledge/status")
    def status(request: Request):
        knowledge = manager(request)
        return {**knowledge.status(), "configuration_error": knowledge.config_error}

    @router.post("/api/knowledge/search", status_code=202)
    def search(request: Request, value: SearchInput):
        value.query = value.query.strip()
        if any(ord(c) < 32 and c not in "\n\r\t" for c in value.query) or (
            not value.query
            or len(tiktoken.get_encoding("cl100k_base").encode(value.query, disallowed_special=()))
            > 1000
        ):
            raise HTTPException(422, "検索文は空にせず、1000トークン以内で入力してください。")
        try:
            return manager(request).start_search(value.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/knowledge/retrievals/{retrieval_id}")
    def retrieval(request: Request, retrieval_id: str):
        try:
            result = manager(request).retrieval(retrieval_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not result or result["kind"] not in {"text", "search"}:
            raise HTTPException(404, "検索が見つかりません。")
        return result

    @router.post("/api/knowledge/context")
    def context(request: Request, value: ContextInput):
        try:
            return manager(request).context(value.retrieval_id, value.chunk_ids, value.budget)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/knowledge/index-jobs", status_code=202)
    def index(request: Request):
        knowledge = manager(request)
        if not knowledge.provider:
            raise HTTPException(
                422, knowledge.config_error or "Azure embeddingを設定してください。"
            )
        try:
            return knowledge.create_task("index", {})
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc

    @router.get("/api/knowledge/index-jobs/{job_id}")
    def index_job(request: Request, job_id: str):
        result = manager(request).task(job_id)
        if not result or result["kind"] != "index":
            raise HTTPException(404, "索引作成が見つかりません。")
        return result

    @router.post("/api/knowledge/tasks/{task_id}/cancel")
    def cancel(request: Request, task_id: str):
        knowledge = manager(request)
        if not knowledge.task(task_id):
            raise HTTPException(404, "処理が見つかりません。")
        knowledge.cancel(task_id)
        return knowledge.task(task_id)

    return router
