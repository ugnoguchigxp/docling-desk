import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
import docling_desk.knowledge.embedding_azure as embedding_azure
import docling_desk.knowledge.service as local_knowledge
from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, save_job
from docling_desk.knowledge.embedding_azure import (
    AzureEmbedding,
    EmbeddingError,
    paced_request,
    retry_delay,
)
from docling_desk.knowledge.service import Knowledge
from docling_desk.knowledge.store import Store, split_text


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    for key in ("ENDPOINT", "KEY", "DEPLOYMENT", "MODEL_VERSION", "DIMENSIONS"):
        monkeypatch.delenv("DOCLING_AZURE_EMBEDDING_" + key, raising=False)
    client = TestClient(web.app)
    yield client
    if current := getattr(web.app.state, "knowledge", None):
        current.close()
        del web.app.state.knowledge
    client.close()


def article(
    client, name="guide.md", body="# 利用ガイド\n\n日本語の検索本文です。", namespace="テスト"
):
    response = client.post(
        "/api/wiki/import",
        data={"namespace": namespace},
        files=[("files", (name, body.encode(), "text/markdown"))],
    )
    assert response.status_code == 201, response.text
    return response.json()["articles"][0]["id"]


def search(client, query, **values):
    response = client.post("/api/knowledge/search", json={"query": query, **values})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["state"] == "completed"
    return result


def document(data: Path, filename="資料.pdf", text="日本語の資料本文です。", job_id="a" * 32):
    folder = data / job_id
    folder.mkdir()
    save_job(folder, Job(id=job_id, filename=filename, state="success", pages=3))
    (folder / "original.pdf").write_bytes(b"%PDF-test")
    parent = {
        "id": job_id + ":pdf:2",
        "text": text + "\n関連する根拠本文",
        "pages": [2],
        "refs": ["#/texts/2"],
        "headings": ["第二章"],
        "unit": "ページ 2",
    }
    (folder / "rag.jsonl").write_text(json.dumps(parent, ensure_ascii=False), encoding="utf-8")
    (folder / "rag-index.jsonl").write_text(
        json.dumps(
            {**parent, "text": text, "parent_id": parent["id"], "id": parent["id"] + ":search:0"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job_id


def test_wiki_is_explicit_markdown_only_and_persists(client):
    document(desk_config.DATA)
    assert client.get("/api/wiki/catalog").json()["articles"] == []
    sid = article(client)
    assert client.get("/api/wiki/sources/" + sid).json()["body"].startswith("# 利用")
    for filename in ("unsafe.txt", "a.pdf", "../outside.md", "/absolute.md", "sub/../a.md"):
        assert (
            client.post("/api/wiki/import", files={"files": (filename, b"test")}).status_code == 422
        )
    assert client.get("/api/wiki/sources/doc-" + "a" * 32).status_code == 404
    current = web.app.state.knowledge
    current.close()
    del web.app.state.knowledge
    assert client.get("/api/wiki/catalog").json()["articles"][0]["id"] == sid


def test_safe_markdown_links_tables_and_stable_heading_anchors(client):
    job = document(desk_config.DATA)
    target = article(client, "guide/other.md", "# 相手の記事\n\n## 詳細\n本文")
    sid = article(
        client,
        "guide/main.md",
        f"""# 最初

[記事](other.md#詳細) [原本](doc:{job}#page=2) [外部](https://example.com)
[外側](../../private.md) [危険](javascript:alert(1)) ![画像](https://example.com/private.png)
<script>alert('bad')</script><img src=x onerror=alert(1)>

| 名前 | 値 |
| --- | --- |
| A | 123 |

## 同じ見出し
本文
## 同じ見出し
次の本文
""",
    )
    value = client.get("/api/wiki/sources/" + sid).json()
    rendered = value["html"]
    assert "<script>" not in rendered and "<img" not in rendered
    assert "<table>" in rendered and "123" in rendered
    assert target in rendered and f"job={job}" in rendered and "unit=2" in rendered
    assert 'target="_blank" rel="noopener noreferrer"' in rendered
    assert 'aria-disabled="true"' in rendered
    assert len({h["anchor"] for h in value["outline"]}) == 3
    assert value["outline"] == client.get("/api/wiki/sources/" + sid).json()["outline"]
    assert client.get("/api/wiki/sources/" + sid + "/markdown").text.startswith("# 最初")


def test_shared_search_short_japanese_terms_and_context_citations(client):
    job = document(desk_config.DATA)
    sid = article(client)
    hits = search(client, "日本語")["output"]["results"]
    assert {h["kind"] for h in hits} == {"wiki", "document"}
    assert {h["source_id"] for h in hits} == {sid, "doc-" + job}
    assert len(search(client, "本文")["output"]["results"]) == 2
    assert {h["kind"] for h in search(client, "本文", kind="document")["output"]["results"]} == {
        "document"
    }
    retrieved = search(client, "本文")
    value = client.post(
        "/api/knowledge/context",
        json={
            "retrieval_id": retrieved["id"],
            "chunk_ids": [h["chunk_id"] for h in retrieved["output"]["results"]],
            "budget": 1000,
        },
    )
    assert value.status_code == 200, value.text
    assert "[S1]" in value.json()["text"] and "関連する根拠本文" in value.json()["text"]
    doc = next(c for c in value.json()["citations"] if c["kind"] == "document")
    assert doc["unit"] == 2 and doc["locator"]["refs"] == ["#/texts/2"]
    assert value.json()["tokens"] <= 1000
    import tiktoken

    assert (
        len(tiktoken.get_encoding("cl100k_base").encode(value.json()["text"]))
        == value.json()["tokens"]
    )
    assert (
        client.post(
            "/api/knowledge/context",
            json={"retrieval_id": retrieved["id"], "chunk_ids": ["forged"], "budget": 100},
        ).status_code
        == 409
    )


def test_reimport_invalidates_old_retrieval_and_keeps_article_identity(client):
    sid = article(client)
    previous = search(client, "日本語")
    chunk = previous["output"]["results"][0]["chunk_id"]
    assert article(client, body="# 更新\n\n別の内容。") == sid
    assert (
        client.get("/api/knowledge/retrievals/" + previous["id"]).json()["output"]["results"] == []
    )
    assert (
        client.post(
            "/api/knowledge/context", json={"retrieval_id": previous["id"], "chunk_ids": [chunk]}
        ).status_code
        == 409
    )
    assert not search(client, "日本語")["output"]["results"]
    assert search(client, "別の内容")["output"]["results"][0]["source_id"] == sid
    assert client.delete("/api/wiki/sources/" + sid).status_code == 200
    assert not search(client, "別の内容")["output"]["results"]


def test_move_rename_delete_revalidate_scope_and_original_position(client):
    job = document(desk_config.DATA)
    root = client.post("/api/folders", json={"name": "検索範囲"}).json()["id"]

    def operation(action, **extra):
        return client.post(
            "/api/library/operations",
            json={"action": action, "items": [{"kind": "file", "id": job}], **extra},
        )

    assert operation("move", destination=root).status_code == 200
    previous = search(client, "本文", folder_id=root)
    assert previous["output"]["results"]
    assert operation("rename", name="新しい名前.pdf").status_code == 200
    assert (
        client.get("/api/knowledge/retrievals/" + previous["id"]).json()["output"]["results"][0][
            "title"
        ]
        == "新しい名前.pdf"
    )
    assert operation("move", destination=None).status_code == 200
    assert not client.get("/api/knowledge/retrievals/" + previous["id"]).json()["output"]["results"]
    assert operation("delete").status_code == 200
    assert not search(client, "本文")["output"]["results"]


def test_without_azure_text_search_and_idempotency_remain_available(client):
    article(client)
    first = search(client, "検索", client_request_id="same")
    assert search(client, "検索", client_request_id="same")["id"] == first["id"]
    assert (
        client.post(
            "/api/knowledge/search", json={"query": "別", "client_request_id": "same"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/knowledge/search", json={"query": "検索", "mode": "semantic"}).status_code
        == 422
    )
    assert client.post("/api/knowledge/index-jobs", json={}).status_code == 422
    assert not client.get("/api/knowledge/status").json()["azure_configured"]
    assert client.post("/api/knowledge/search", json={"query": "   "}).status_code == 422


def test_oversized_multibyte_chunks_preserve_text_and_fit_embedding_limit():
    original = "日本語の断片😃" * 1800
    parts = split_text(original)
    import tiktoken

    encoding = tiktoken.get_encoding("cl100k_base")
    assert "".join(parts) == original
    assert all(len(encoding.encode(p, disallowed_special=())) <= 1000 for p in parts)


def test_durable_cooldown_after_success_failure_retry_after_and_restart(tmp_path, monkeypatch):
    store = Store(tmp_path)
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1", 2)
    now, calls = [1000.0], []
    monkeypatch.setattr(embedding_azure.time, "time", lambda: now[0])

    class Stop:
        def is_set(self):
            return False

        def wait(self, delay):
            now[0] += delay

    def request(self, texts):
        calls.append(now[0])
        now[0] += 3
        if len(calls) == 2:
            error = EmbeddingError("429", True)
            error.delay = 30
            raise error
        return [[1, 0]], 0

    monkeypatch.setattr(AzureEmbedding, "request", request)
    assert paced_request(store, provider, ["one"], Stop()) == [[1, 0]]
    restarted_store = Store(tmp_path)
    with pytest.raises(EmbeddingError):
        paced_request(restarted_store, provider, ["two"], Stop())
    assert paced_request(restarted_store, provider, ["three"], Stop()) == [[1, 0]]
    assert calls == [1000, 1018, 1051]
    assert retry_delay({"retry-after-ms": "25000", "retry-after": "30"}) == 30


def test_azure_request_contract_order_and_errors(monkeypatch):
    provider = AzureEmbedding(
        "https://example.openai.azure.com", "secret", "actual-deployment", "v1", 2
    )
    captured = []

    def handler(req):
        captured.append(req)
        return httpx.Response(
            200,
            json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]},
        )

    original = httpx.Client
    monkeypatch.setattr(
        embedding_azure.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler)),
    )
    assert provider.request(["one", "two"])[0] == [[1, 0], [0, 1]]
    assert str(captured[0].url).endswith("/openai/v1/embeddings")
    assert captured[0].headers["api-key"] == "secret"
    assert json.loads(captured[0].content) == {
        "model": "actual-deployment",
        "input": ["one", "two"],
        "encoding_format": "float",
        "dimensions": 2,
    }


def wait_task(knowledge, task_id):
    end = time.monotonic() + 4
    while time.monotonic() < end:
        value = knowledge.task(task_id)
        if value["state"] not in {"queued", "running"}:
            return value
        time.sleep(0.02)
    pytest.fail("queue did not finish")


def test_index_semantic_hybrid_query_cache_and_profile_isolation(tmp_path, monkeypatch):
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1", 2)
    calls = []

    def request(store, provider, texts, stop, cancelled):
        calls.append(texts)
        return [[1.0, 0.1] if "apple" in t or "fruit" in t else [0.1, 1.0] for t in texts]

    monkeypatch.setattr(local_knowledge, "paced_request", request)
    knowledge = Knowledge(tmp_path, provider)
    try:
        fruit = knowledge.store.import_wiki("Wiki", "fruit.md", "# Fruit\n\napple")
        knowledge.store.import_wiki("Wiki", "vehicle.md", "# Vehicle\n\ncar")
        job = knowledge.create_task("index", {})
        assert wait_task(knowledge, job["id"])["state"] == "completed"
        value = {"query": "fruit", "mode": "semantic", "kind": "all", "limit": 10}
        search = knowledge.start_search(value)
        completed = wait_task(knowledge, search["id"])
        assert completed["state"] == "completed", completed
        assert completed["output"]["results"][0]["source_id"] == fruit["id"]
        count = len(calls)
        repeat = knowledge.start_search({**value, "mode": "hybrid"})
        assert repeat["state"] == "completed"
        assert wait_task(knowledge, repeat["id"])["state"] == "completed"
        assert len(calls) == count
        assert knowledge.status()["embedded"] == knowledge.status()["chunks"]
        knowledge.provider = AzureEmbedding(
            provider.endpoint, provider.key, provider.deployment, "v2", 2
        )
        assert knowledge.status()["embedded"] == 0
    finally:
        knowledge.close()


def test_manifest_registers_language_variants_and_existing_document_links(client):
    job = document(desk_config.DATA)
    manifest = {
        "articles": {
            "original.md": {
                "language": "en",
                "translation_group": "guide",
                "source_job_id": job,
                "source_unit": 2,
            },
            "ja.md": {"language": "ja", "translation_group": "guide"},
        }
    }
    response = client.post(
        "/api/wiki/import",
        data={"namespace": "翻訳"},
        files=[
            ("files", ("original.md", b"# Original\n\nOriginal text")),
            ("files", ("ja.md", "# 日本語版\n\n日本語の記事".encode())),
            ("manifest", ("wiki-manifest.json", json.dumps(manifest).encode())),
        ],
    )
    assert response.status_code == 201, response.text
    articles = response.json()["articles"]
    assert {a["language"] for a in articles} == {"en", "ja"}
    result = client.get("/api/wiki/sources/" + articles[0]["id"]).json()
    assert result["related_document"]["job_id"] == job and result["source_unit"] == 2
    assert article(client, "original.md", "# Updated\n\nUpdated text", "翻訳") == articles[0]["id"]
    assert client.get("/api/wiki/sources/" + articles[0]["id"]).json()["language"] == "en"
    bad = client.post(
        "/api/wiki/import",
        files=[
            ("files", ("new.md", b"# New")),
            (
                "manifest",
                (
                    "wiki-manifest.json",
                    b'{"articles":{"new.md":{"source_job_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}}',
                ),
            ),
        ],
    )
    assert bad.status_code == 422
    assert all(a["path"] != "new.md" for a in client.get("/api/wiki/catalog").json()["articles"])


def test_batch_import_rolls_back_every_article_on_storage_failure(tmp_path, monkeypatch):
    store = Store(tmp_path)
    replace = store.replace
    calls = []

    def fail_second(db, source, sections):
        calls.append(source["path"])
        if len(calls) == 2:
            raise OSError("simulated storage failure")
        replace(db, source, sections)

    monkeypatch.setattr(store, "replace", fail_second)
    with pytest.raises(OSError):
        store.import_many(
            "Wiki", [{"path": "a.md", "body": "# A"}, {"path": "b.md", "body": "# B"}]
        )
    assert store.sources() == [] and store.chunks() == []


def test_search_gets_queue_priority_between_index_batches_and_cancel_is_terminal(
    tmp_path, monkeypatch
):
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1", 2)
    started, release, calls = threading.Event(), threading.Event(), []

    def request(store, provider, texts, stop, cancelled):
        calls.append(texts)
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
        return [[1, 0] for _ in texts]

    monkeypatch.setattr(local_knowledge, "paced_request", request)
    knowledge = Knowledge(tmp_path, provider)
    try:
        for i in range(12):
            knowledge.store.import_wiki("Wiki", f"{i}.md", f"# Article {i}\n\nBody {i}")
        index = knowledge.create_task("index", {})
        assert started.wait(3)
        query = knowledge.start_search({"query": "interactive", "mode": "semantic", "kind": "all"})
        cancelled = knowledge.create_task(
            "search", {"query": "cancelled", "mode": "semantic", "kind": "all"}
        )
        knowledge.cancel(cancelled["id"])
        release.set()
        assert wait_task(knowledge, query["id"])["state"] == "completed"
        assert wait_task(knowledge, index["id"])["progress"] == 12
        assert calls[1] == ["interactive"]
        assert all("cancelled" not in call for call in calls)
        assert knowledge.task(cancelled["id"])["state"] == "cancelled"
    finally:
        release.set()
        knowledge.close()


def test_concurrent_services_cannot_send_during_another_request(tmp_path, monkeypatch):
    first, second = Store(tmp_path), Store(tmp_path)
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1", 2)
    active, release, stop = threading.Event(), threading.Event(), threading.Event()
    calls, errors = [], []

    def request(self, texts):
        calls.append(texts)
        active.set()
        assert release.wait(3)
        return [[1, 0]], 0

    monkeypatch.setattr(AzureEmbedding, "request", request)

    def run(store, text):
        try:
            paced_request(store, provider, [text], stop)
        except EmbeddingError as exc:
            errors.append(str(exc))

    one = threading.Thread(target=run, args=(first, "first"))
    two = threading.Thread(target=run, args=(second, "second"))
    one.start()
    assert active.wait(3)
    two.start()
    stop.set()
    release.set()
    one.join(3)
    two.join(3)
    assert calls == [["first"]] and len(errors) == 1


def test_large_section_shares_parent_storage(tmp_path):
    store = Store(tmp_path)
    body = "# Large\n\n" + "unique body paragraph with information. " * 3000
    source = store.import_wiki("Wiki", "large.md", body)
    assert len(store.chunks()) > 10
    with store.connection() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert store.path.stat().st_size < len(body.encode()) * 10
    assert store.source(source["id"])["body"] == body


def test_invalid_semantic_scope_is_rejected_before_queueing(tmp_path):
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1", 2)
    knowledge = Knowledge(tmp_path, provider)
    try:
        for field in ("source_id", "folder_id"):
            with pytest.raises(ValueError):
                knowledge.start_search({"query": "test", "mode": "semantic", field: "missing"})
        with knowledge.store.connection() as db:
            assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    finally:
        knowledge.close()


def test_request_id_cannot_change_search_mode(tmp_path):
    knowledge = Knowledge(tmp_path)
    try:
        value = {"query": "test", "mode": "text", "client_request_id": "same"}
        knowledge.create_task("text", value, {"results": []})
        with pytest.raises(ValueError):
            knowledge.create_task("search", {**value, "mode": "semantic"})
    finally:
        knowledge.close()


def test_malformed_document_does_not_break_healthy_search(client):
    healthy = document(desk_config.DATA)
    bad = document(desk_config.DATA, job_id="b" * 32)
    (desk_config.DATA / bad / "rag-index.jsonl").write_text("[1, 2]")
    hits = search(client, "資料本文")["output"]["results"]
    assert [hit["job_id"] for hit in hits] == [healthy]


def test_empty_scope_cannot_silently_search_everything(client):
    for field in ("source_id", "folder_id", "namespace", "client_request_id"):
        assert (
            client.post("/api/knowledge/search", json={"query": "test", field: ""}).status_code
            == 422
        )


def test_malformed_azure_response_preserves_retry_after(monkeypatch):
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1", 2)
    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda *a, **kw: httpx.Response(200, json={"data": []}, headers={"retry-after": "30"}),
    )
    with pytest.raises(EmbeddingError) as caught:
        provider.request(["test"])
    assert caught.value.delay == 30


def test_legacy_context_migration_preserves_saved_retrieval_ids(tmp_path):
    import sqlite3

    root = tmp_path / "knowledge"
    root.mkdir()
    with sqlite3.connect(root / "local.sqlite") as db:
        db.execute(
            "CREATE TABLE chunks (id TEXT PRIMARY KEY,source_id TEXT,revision TEXT,text TEXT,text_hash TEXT,context TEXT,locator TEXT)"
        )
        for cid in ("first", "second"):
            db.execute(
                "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
                (cid, "wiki-old", "rev", "part", "hash", "shared parent", "{}"),
            )
    store = Store(tmp_path)
    assert store.context("first") == store.context("second") == "shared parent"
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM contexts").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT count(*) FROM chunks WHERE context='' AND context_id IS NOT NULL"
            ).fetchone()[0]
            == 2
        )
    Store(tmp_path)  # Reopening is idempotent.
    assert store.context("second") == "shared parent"


def test_dimension_change_between_network_and_commit_fails(tmp_path, monkeypatch):
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1")

    def concurrent_vector(store, *args):
        with store.connection() as db:
            db.execute("INSERT INTO vectors VALUES(?,?,?)", (provider.profile, "other", "[1,0]"))
        return [[1, 0, 0]]

    monkeypatch.setattr(local_knowledge, "paced_request", concurrent_vector)
    knowledge = Knowledge(tmp_path, provider)
    try:
        job = knowledge.start_search({"query": "test", "mode": "semantic"})
        result = wait_task(knowledge, job["id"])
        assert result["state"] == "failed" and "次元" in result["error"]
        assert set(knowledge.vectors()) == {"other"}
    finally:
        knowledge.close()


def test_lost_task_ownership_stops_network_and_progress(tmp_path):
    knowledge = Knowledge(tmp_path)
    knowledge.close()
    job = knowledge.create_task("search", {"query": "test", "mode": "semantic"})
    knowledge.stop.clear()
    with knowledge.store.connection() as db:
        db.execute(
            "UPDATE tasks SET state='running',owner=? WHERE id=?", ("other-worker", job["id"])
        )
    assert knowledge.cancelled(job["id"])
    assert not knowledge.update(job["id"], state="completed")
    assert knowledge.task(job["id"])["state"] == "running"


def test_cancellation_after_gate_acquisition_does_not_send(tmp_path, monkeypatch):
    store = Store(tmp_path)
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1")
    checks, calls = [], []

    def cancelled():
        checks.append(True)
        return len(checks) == 2

    monkeypatch.setattr(AzureEmbedding, "request", lambda *args: calls.append(args))
    with pytest.raises(EmbeddingError):
        paced_request(store, provider, ["test"], threading.Event(), cancelled)
    assert calls == []
    with store.connection() as db:
        row = db.execute("SELECT owner,until FROM gate").fetchone()
        assert row["owner"] is None and row["until"] > time.time() + 14


def test_old_profile_queue_does_not_replay_with_new_credentials(tmp_path, monkeypatch):
    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1")
    knowledge = Knowledge(tmp_path, provider)
    knowledge.close()
    job = knowledge.create_task("search", {"query": "test", "mode": "semantic"})
    knowledge.provider = AzureEmbedding(provider.endpoint, provider.key, provider.deployment, "v2")
    calls = []
    monkeypatch.setattr(local_knowledge, "paced_request", lambda *args: calls.append(args))
    knowledge.stop.clear()
    knowledge.thread = threading.Thread(target=knowledge.work)
    knowledge.thread.start()
    try:
        result = wait_task(knowledge, job["id"])
        assert result["state"] == "failed" and "設定" in result["error"]
        assert calls == []
    finally:
        knowledge.close()


def test_worker_recovers_from_temporary_sqlite_failure(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import contextmanager

    provider = AzureEmbedding("https://example.openai.azure.com", "secret", "deployment", "v1", 2)
    knowledge = Knowledge(tmp_path, provider)
    knowledge.close()
    job = knowledge.create_task("search", {"query": "test", "mode": "semantic"})
    real_connection = knowledge.store.connection
    attempts = []

    @contextmanager
    def flaky_connection(**options):
        if threading.current_thread() is knowledge.thread and not attempts:
            attempts.append(True)
            raise sqlite3.OperationalError("temporary failure")
        with real_connection(**options) as db:
            yield db

    monkeypatch.setattr(knowledge.store, "connection", flaky_connection)
    monkeypatch.setattr(local_knowledge, "paced_request", lambda *args: [[1, 0]])
    knowledge.stop.clear()
    knowledge.thread = threading.Thread(target=knowledge.work)
    knowledge.thread.start()
    try:
        assert wait_task(knowledge, job["id"])["state"] == "completed"
        assert attempts and knowledge.thread.is_alive()
    finally:
        knowledge.close()


def test_removed_folder_invalidates_retrieval_without_error(client):
    job = document(desk_config.DATA)
    folder = client.post("/api/folders", json={"name": "Temp"}).json()["id"]

    def operation(action, items, **extra):
        return client.post(
            "/api/library/operations", json={"action": action, "items": items, **extra}
        )

    file = [{"kind": "file", "id": job}]
    assert operation("move", file, destination=folder).status_code == 200
    result = search(client, "本文", folder_id=folder)
    assert result["output"]["results"]
    assert operation("move", file, destination=None).status_code == 200
    assert operation("delete", [{"kind": "folder", "id": folder}]).status_code == 200
    current = client.get("/api/knowledge/retrievals/" + result["id"])
    assert current.status_code == 200 and current.json()["output"]["stale"]
    assert current.json()["output"]["results"] == []


@pytest.mark.parametrize(
    "endpoint,dimensions",
    [("https://[broken", ""), ("https://example.com:bad", ""), ("https://example.com", "²")],
)
def test_invalid_azure_configuration_keeps_wiki_available(
    client, monkeypatch, endpoint, dimensions
):
    for key, value in {
        "ENDPOINT": endpoint,
        "KEY": "secret",
        "DEPLOYMENT": "deployment",
        "MODEL_VERSION": "v1",
        "DIMENSIONS": dimensions,
    }.items():
        monkeypatch.setenv("DOCLING_AZURE_EMBEDDING_" + key, value)
    assert article(client)
    status = client.get("/api/knowledge/status").json()
    assert not status["azure_configured"] and status["configuration_error"]
    assert search(client, "日本語")["output"]["results"]
