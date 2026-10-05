import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
import docling_desk.knowledge.api as local_knowledge_api
from docling_desk import config as desk_config
from docling_desk.knowledge.api import MAX_WIKI_BATCH, MAX_WIKI_BYTES
from docling_desk.knowledge.content import normalize_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    for key in ("ENDPOINT", "KEY", "DEPLOYMENT", "MODEL_VERSION", "DIMENSIONS"):
        monkeypatch.delenv("DOCLING_AZURE_EMBEDDING_" + key, raising=False)
    http = TestClient(web.app)
    yield http
    if current := getattr(web.app.state, "knowledge", None):
        current.close()
        del web.app.state.knowledge
    http.close()


def upload(client, files, manifest=None):
    data = {"namespace": "Wiki"}
    payload = [("files", item) for item in files]
    if manifest is not None:
        payload.append(("manifest", ("manifest.json", manifest, "application/json")))
    return client.post("/api/wiki/import", data=data, files=payload)


def test_wiki_limits_are_pinned():
    assert MAX_WIKI_BYTES == 2 * 1024 * 1024
    assert MAX_WIKI_BATCH == 20 * 1024 * 1024


def test_wiki_import_rejects_paths_encoding_and_size_boundaries(client, monkeypatch):
    for name in ("../secret.md", "/etc/passwd.md", "a.md\x00", "notes.txt", "a/../../b.md"):
        response = upload(client, [(name, b"# T\n\nbody", "text/markdown")])
        assert response.status_code == 422, name
    assert (
        upload(client, [("案内.md", "# 案内\n\n日本語".encode(), "text/markdown")]).status_code
        == 201
    )
    assert upload(client, [("bad.md", b"\xff\xfe# bad", "text/markdown")]).status_code == 422
    assert upload(client, [("empty.md", b" \n\t", "text/markdown")]).status_code == 422
    monkeypatch.setattr(local_knowledge_api, "MAX_WIKI_BYTES", 32)
    monkeypatch.setattr(local_knowledge_api, "MAX_WIKI_BATCH", 40)
    assert upload(client, [("fit.md", b"a" * 32, "text/markdown")]).status_code == 201
    assert upload(client, [("over.md", b"a" * 33, "text/markdown")]).status_code == 422
    exact = [("files", (f"n{i}.md", b"b" * 20, "text/markdown")) for i in range(2)]
    assert (
        client.post("/api/wiki/import", data={"namespace": "Batch"}, files=exact).status_code == 201
    )
    over = exact + [("files", ("extra.md", b"c", "text/markdown"))]
    assert (
        client.post("/api/wiki/import", data={"namespace": "Batch"}, files=over).status_code == 422
    )
    duplicate = [
        ("files", ("same.md", b"# A\n\n1", "text/markdown")),
        ("files", ("same.md", b"# B\n\n2", "text/markdown")),
    ]
    assert (
        client.post("/api/wiki/import", data={"namespace": "Dup"}, files=duplicate).status_code
        == 422
    )


def test_normalize_path_accepts_the_length_boundary():
    assert normalize_path("a" * 509 + ".md")
    with pytest.raises(ValueError):
        normalize_path("a" * 510 + ".md")


def test_same_sentence_keeps_both_sources_and_delete_hides_the_body(client):
    first = upload(client, [("a.md", "# A\n\n共通の文です。".encode(), "text/markdown")])
    second = upload(client, [("b.md", "# B\n\n共通の文です。".encode(), "text/markdown")])
    assert first.status_code == second.status_code == 201
    ids = {first.json()["articles"][0]["id"], second.json()["articles"][0]["id"]}
    found = client.post("/api/knowledge/search", json={"query": "共通の文"})
    assert found.status_code == 202
    assert {hit["source_id"] for hit in found.json()["output"]["results"]} == ids
    removed = first.json()["articles"][0]["id"]
    assert client.delete("/api/wiki/sources/" + removed).status_code == 200
    again = client.post("/api/knowledge/search", json={"query": "共通の文"})
    assert {hit["source_id"] for hit in again.json()["output"]["results"]} == ids - {removed}
    assert client.get("/api/wiki/sources/" + removed).status_code == 404
