"""HTTP contract of standalone Excalidraw diagrams."""

import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from docling_desk import config
from docling_desk.api.diagrams import router
from docling_desk.app import create_app
from docling_desk.documents import diagram_store as store
from docling_desk.documents.conversion import Job, save_job
from docling_desk.storage import document_folder

FIXTURE = Path(__file__).parent / "fixtures" / "diagrams" / "basic.excalidraw"
SCENE = "application/vnd.excalidraw+json"


def scene(label="a") -> bytes:
    element = {"id": label, "type": "rectangle", "x": 0, "y": 0, "width": 1, "height": 1}
    return json.dumps(
        {"type": "excalidraw", "version": 2, "elements": [element], "appState": {}}
    ).encode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    app = create_app()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def new_id() -> str:
    return uuid4().hex


def headers(**extra):
    return {"Idempotency-Key": uuid4().hex, **extra}


def put(client, raw=None, identifier=None, name="図", folder_id=None, **extra):
    identifier = identifier or new_id()
    body = {"name": name, "folder_id": folder_id, "scene": json.loads(raw or scene())}
    response = client.put(
        f"/api/diagrams/{identifier}",
        json=body,
        headers=headers(**{"If-None-Match": "*", **extra}),
    )
    return identifier, response


def make(client, raw=None, **kwargs):
    identifier, response = put(client, raw, **kwargs)
    assert response.status_code == 201, response.text
    return identifier, response.json()


def save(client, identifier, etag, raw=None, **extra):
    return client.post(
        f"/api/diagrams/{identifier}/revisions",
        content=raw or scene("n"),
        headers=headers(**{"If-Match": etag, "Content-Type": SCENE, **extra}),
    )


def test_create_returns_info_with_etag_and_is_listed(client):
    identifier, info = make(client, name="設計")
    assert info["name"] == "設計.excalidraw" and info["revision_number"] == 1
    assert info["operation_id"] and info["draft"] is None and info["folder_id"] is None
    response = client.get(f"/api/diagrams/{identifier}")
    assert response.status_code == 200 and response.headers["etag"] == info["etag"]
    assert response.json() == {**info, "operation_id": None}
    jobs = client.get("/api/library").json()["jobs"]
    listed = next(j for j in jobs if j["id"] == identifier)
    assert listed["excalidraw"] is True and listed["filename"] == "設計.excalidraw"
    assert client.get(f"/api/jobs/{identifier}").json()["preview"] == "excalidraw"


def test_create_preconditions_and_validation(client):
    body = {"name": "a", "folder_id": None, "scene": json.loads(scene())}
    url = f"/api/diagrams/{new_id()}"
    assert client.put(url, json=body, headers={"Idempotency-Key": "k"}).status_code == 428
    assert client.put(url, json=body, headers={"If-None-Match": "*"}).status_code == 428
    wrong = {"If-None-Match": '"x"', "Idempotency-Key": "k"}
    assert client.put(url, json=body, headers=wrong).status_code == 412
    good = {"If-None-Match": "*", "Idempotency-Key": "k"}
    assert client.put("/api/diagrams/short", json=body, headers=good).status_code == 422
    assert (
        client.put(url, content="x", headers={**good, "Content-Type": "text/plain"}).status_code
        == 415
    )
    assert client.put(url, json={**body, "scene": []}, headers=good).status_code == 422
    assert client.put(url, json={**body, "name": ""}, headers=good).status_code == 422
    assert client.put(url, json={**body, "folder_id": 3}, headers=good).status_code == 422
    assert client.put(url, json={**body, "folder_id": "nope"}, headers=good).status_code == 404
    bad = {**body, "scene": {**body["scene"], "type": "other"}}
    response = client.put(url, json=bad, headers=good)
    assert response.status_code == 422 and isinstance(response.json()["detail"], str)
    assert client.get("/api/library").json()["jobs"] == []


def test_create_idempotency_replay_and_conflicts(client):
    identifier = new_id()
    body = {"name": "a", "folder_id": None, "scene": json.loads(scene())}
    first = client.put(
        f"/api/diagrams/{identifier}",
        json=body,
        headers={"If-None-Match": "*", "Idempotency-Key": "same"},
    )
    assert first.status_code == 201
    again = client.put(
        f"/api/diagrams/{identifier}",
        json=body,
        headers={"If-None-Match": "*", "Idempotency-Key": "same"},
    )
    assert again.status_code == 200 and again.json() == first.json()
    other_body = {**body, "name": "b"}
    conflict = client.put(
        f"/api/diagrams/{identifier}",
        json=other_body,
        headers={"If-None-Match": "*", "Idempotency-Key": "same"},
    )
    assert conflict.status_code == 409
    other_key = client.put(
        f"/api/diagrams/{identifier}",
        json=body,
        headers={"If-None-Match": "*", "Idempotency-Key": "different"},
    )
    assert other_key.status_code == 412
    assert len(client.get("/api/library").json()["jobs"]) == 1


def test_content_roundtrip_keeps_bytes_and_download(client):
    raw = FIXTURE.read_bytes()
    identifier, info = make(client, raw)
    # PUT re-serialises the embedded scene, so exact bytes are checked through /revisions.
    response = save(client, identifier, info["etag"], raw)
    assert response.status_code == 201
    saved = response.json()
    content = client.get(f"/api/diagrams/{identifier}/content")
    assert content.status_code == 200 and content.content == raw
    assert content.headers["content-type"] == SCENE
    assert content.headers["etag"] == saved["etag"]
    assert content.headers["x-content-type-options"] == "nosniff"
    old = client.get(
        f"/api/diagrams/{identifier}/content", params={"revision": info["revision_id"]}
    )
    assert old.status_code == 200 and old.headers["etag"] == info["etag"]
    assert json.loads(old.content) == json.loads(raw)
    download = client.get(f"/api/diagrams/{identifier}/content", params={"download": "true"})
    assert download.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert (
        client.get(f"/api/diagrams/{identifier}/content", params={"revision": new_id()}).status_code
        == 404
    )
    assert (
        client.get(f"/api/diagrams/{identifier}/content", params={"revision": "../x"}).status_code
        == 404
    )


def test_original_url_serves_the_current_revision(client):
    identifier, info = make(client)
    first = client.get(f"/files/{identifier}/original.excalidraw")
    assert first.status_code == 200 and json.loads(first.content) == json.loads(scene())
    assert first.headers["content-type"].startswith(SCENE)
    save(client, identifier, info["etag"], scene("newer"))
    second = client.get(f"/files/{identifier}/original.excalidraw")
    assert json.loads(second.content)["elements"][0]["id"] == "newer"
    download = client.get(f"/files/{identifier}/original.excalidraw", params={"download": True})
    assert "attachment" in download.headers["content-disposition"]
    assert document_folder(config.DATA, identifier) / "original.excalidraw" not in list(
        (config.DATA / "derived" / "documents" / identifier).rglob("*")
    )


def test_save_headers_status_codes_and_idempotency(client):
    identifier, info = make(client)
    url = f"/api/diagrams/{identifier}/revisions"
    base = {"Content-Type": SCENE}
    assert (
        client.post(url, content=scene(), headers={**base, "Idempotency-Key": "k"}).status_code
        == 428
    )
    assert (
        client.post(url, content=scene(), headers={**base, "If-Match": info["etag"]}).status_code
        == 428
    )
    assert save(client, identifier, '"r-stale"').status_code == 412
    assert save(client, identifier, info["etag"], b"[1]").status_code == 422
    wrong = client.post(
        url,
        content=b"x",
        headers=headers(**{"If-Match": info["etag"], "Content-Type": "text/plain"}),
    )
    assert wrong.status_code == 415
    ok = {"If-Match": info["etag"], "Idempotency-Key": "save-1", "Content-Type": "application/json"}
    first = client.post(url, content=scene("b"), headers=ok)
    assert first.status_code == 201 and first.headers["etag"] == first.json()["etag"]
    assert first.json()["revision_number"] == 2
    replay = client.post(url, content=scene("b"), headers=ok)
    assert replay.status_code == 201 and replay.json() == first.json()
    changed = client.post(url, content=scene("c"), headers=ok)
    assert changed.status_code == 409
    assert save(client, identifier, info["etag"]).status_code == 412


def test_save_over_the_limit_is_413(client):
    identifier, info = make(client)
    huge = (
        b'{"type":"excalidraw","version":2,"elements":[],"x":"' + b"a" * (16 * 1024 * 1024) + b'"}'
    )
    assert save(client, identifier, info["etag"], huge).status_code == 413
    assert client.get(f"/api/diagrams/{identifier}").json()["revision_number"] == 1


def test_revisions_paging_and_restore(client):
    identifier, info = make(client, scene("first"))
    etag = info["etag"]
    for index in range(3):
        etag = save(client, identifier, etag, scene(str(index))).json()["etag"]
    page = client.get(f"/api/diagrams/{identifier}/revisions", params={"limit": 3}).json()
    assert [i["number"] for i in page["items"]] == [4, 3, 2] and page["next"] == 2
    assert page["items"][0]["current"] is True
    rest = client.get(
        f"/api/diagrams/{identifier}/revisions", params={"limit": 3, "before": page["next"]}
    ).json()
    assert [i["number"] for i in rest["items"]] == [1] and rest["next"] is None
    for limit in (0, 101):
        response = client.get(f"/api/diagrams/{identifier}/revisions", params={"limit": limit})
        assert response.status_code == 422
    assert len(client.get(f"/api/diagrams/{identifier}/revisions").json()["items"]) == 4
    first = rest["items"][0]["id"]
    url = f"/api/diagrams/{identifier}/restore"
    body = {"revision_id": first}
    assert client.post(url, json=body, headers={"Idempotency-Key": "r"}).status_code == 428
    stale = client.post(url, json=body, headers=headers(**{"If-Match": info["etag"]}))
    assert stale.status_code == 412
    ok = {"If-Match": etag, "Idempotency-Key": "restore-1"}
    restored = client.post(url, json=body, headers=ok)
    assert restored.status_code == 201 and restored.json()["revision_number"] == 5
    assert client.post(url, json=body, headers=ok).json() == restored.json()
    newest = client.get(f"/api/diagrams/{identifier}/revisions", params={"limit": 1}).json()
    assert newest["items"][0]["restored_from"] == first
    content = client.get(f"/api/diagrams/{identifier}/content")
    assert json.loads(content.content)["elements"][0]["id"] == "first"
    missing = client.post(
        url,
        json={"revision_id": new_id()},
        headers=headers(**{"If-Match": restored.json()["etag"]}),
    )
    assert missing.status_code == 404
    assert client.post(url, json={}, headers=headers(**{"If-Match": etag})).status_code == 422


def test_draft_flow(client):
    identifier, info = make(client, scene("base"))
    url = f"/api/diagrams/{identifier}/drafts"
    assert client.post(url).status_code == 428
    assert client.post(url, headers={"If-Match": '"r-x"'}).status_code == 412
    created = client.post(url, headers={"If-Match": info["etag"]})
    assert created.status_code == 201
    draft = created.json()
    assert created.headers["etag"] == draft["etag"] == f'"d-{draft["id"]}-1"'
    assert draft["base_revision"] == info["revision_id"]
    duplicate = client.post(url, headers={"If-Match": info["etag"]})
    assert duplicate.status_code == 409 and draft["id"] in duplicate.json()["detail"]
    assert client.get(f"/api/diagrams/{identifier}").json()["draft"] == draft
    item = f"{url}/{draft['id']}"
    assert client.get(item).json() == draft
    content = client.get(f"{item}/content")
    assert content.headers["content-type"] == SCENE and content.headers["etag"] == draft["etag"]
    assert json.loads(content.content)["elements"][0]["id"] == "base"
    assert client.put(item, content=scene("v2")).status_code == 428
    assert (
        client.put(
            item, content=scene("v2"), headers={"If-Match": '"d-x-1"', "Content-Type": SCENE}
        ).status_code
        == 412
    )
    assert (
        client.put(
            item, content=b"{}", headers={"If-Match": draft["etag"], "Content-Type": SCENE}
        ).status_code
        == 422
    )
    updated = client.put(
        item, content=scene("v2"), headers={"If-Match": draft["etag"], "Content-Type": SCENE}
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert updated.headers["etag"] == updated.json()["etag"]
    for number in (3, 4, 5):
        updated = client.put(
            item,
            content=scene(f"v{number}"),
            headers={"If-Match": updated.json()["etag"], "Content-Type": SCENE},
        )
    directory = (
        config.DATA / "content" / "documents" / identifier / "drafts" / draft["id"] / "versions"
    )
    assert sorted(int(p.name) for p in directory.iterdir()) == [3, 4, 5]
    latest = updated.json()
    commit = f"{item}/commit"
    body = {"source_etag": info["etag"]}
    assert client.post(commit, json=body, headers={"If-Match": latest["etag"]}).status_code == 428
    assert (
        client.post(commit, json=body, headers=headers(**{"If-Match": draft["etag"]})).status_code
        == 412
    )
    assert (
        client.post(commit, json={}, headers=headers(**{"If-Match": latest["etag"]})).status_code
        == 422
    )
    ok = {"If-Match": latest["etag"], "Idempotency-Key": "commit-1"}
    done = client.post(commit, json=body, headers=ok)
    assert done.status_code == 201 and done.json()["revision_number"] == 2
    assert done.json()["draft"] is None
    assert client.post(commit, json=body, headers=ok).json() == done.json()
    assert client.get(item).status_code == 404
    current = client.get(f"/api/diagrams/{identifier}/content")
    assert json.loads(current.content)["elements"][0]["id"] == "v5"


def test_commit_against_a_newer_revision_is_412_and_keeps_the_draft(client):
    identifier, info = make(client)
    url = f"/api/diagrams/{identifier}/drafts"
    draft = client.post(
        url, content=scene("d"), headers={"If-Match": info["etag"], "Content-Type": SCENE}
    ).json()
    newer = save(client, identifier, info["etag"], scene("newer")).json()
    commit = f"{url}/{draft['id']}/commit"
    for source in (info["etag"], newer["etag"]):
        response = client.post(
            commit, json={"source_etag": source}, headers=headers(**{"If-Match": draft["etag"]})
        )
        assert response.status_code == 412
    assert client.get(f"{url}/{draft['id']}").json() == draft
    assert client.get(f"/api/diagrams/{identifier}").json()["revision_number"] == 2


def test_discard_draft(client):
    identifier, info = make(client)
    url = f"/api/diagrams/{identifier}/drafts"
    draft = client.post(url, headers={"If-Match": info["etag"]}).json()
    item = f"{url}/{draft['id']}"
    assert client.delete(item).status_code == 428
    assert client.delete(item, headers={"If-Match": '"d-x-1"'}).status_code == 412
    assert client.delete(item, headers={"If-Match": draft["etag"]}).status_code == 204
    assert client.get(item).status_code == 404
    assert client.get(f"/api/diagrams/{identifier}").json()["draft"] is None
    assert client.delete(item, headers={"If-Match": draft["etag"]}).status_code == 404


def test_operation_lookup_and_expiry(client):
    identifier, info = make(client)
    url = f"/api/diagrams/{identifier}/operations/{info['operation_id']}"
    response = client.get(url)
    assert response.status_code == 200 and response.json() == info
    assert client.get(f"/api/diagrams/{identifier}/operations/{new_id()}").status_code == 404
    path = config.DATA / "content" / "documents" / identifier / "manifest.json"
    manifest = json.loads(path.read_text())
    for entry in manifest["operations"].values():
        entry["created"] = time.time() - 8 * 86400
    path.write_text(json.dumps(manifest))
    assert client.get(url).status_code == 404


def test_other_documents_and_bad_ids_are_not_found(client):
    foreign = new_id()
    folder = document_folder(config.DATA, foreign)
    folder.mkdir(parents=True)
    save_job(folder, Job(id=foreign, filename="a.pdf", state="success"))
    for target in (foreign, new_id(), "nope"):
        base = f"/api/diagrams/{target}"
        assert client.get(base).status_code == 404
        assert client.get(f"{base}/content").status_code == 404
        assert client.get(f"{base}/revisions").status_code == 404
        assert save(client, target, '"r-x"').status_code == 404
        assert client.post(f"{base}/drafts", headers={"If-Match": '"r-x"'}).status_code == 404
        assert client.get(f"{base}/drafts/{new_id()}").status_code == 404
        assert client.get(f"{base}/operations/{new_id()}").status_code == 404
    existing = put(client, identifier=foreign)[1]
    assert existing.status_code == 412


def test_upload_accepts_excalidraw_and_keeps_bytes(client):
    raw = FIXTURE.read_bytes()
    response = client.post(
        "/api/upload", files={"file": ("設計.excalidraw", raw, "application/octet-stream")}
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["excalidraw"] and job["state"] == "success" and job["filename"] == "設計.excalidraw"
    content = client.get(f"/api/diagrams/{job['id']}/content")
    assert content.content == raw
    again = client.post("/api/upload", files={"file": ("設計.excalidraw", raw, "text/plain")})
    assert again.json()["filename"] == "設計 (2).excalidraw" and again.json()["id"] != job["id"]
    assert len(client.get("/api/library").json()["jobs"]) == 2


def test_upload_rejects_invalid_scenes_without_leaving_documents(client):
    bad = [
        b"not json",
        b'{"type":"excalidraw"}',
        b'{"type":"excalidraw","version":1,"elements":[{"id":"a","type":"embeddable"}]}',
    ]
    for raw in bad:
        response = client.post("/api/upload", files={"file": ("a.excalidraw", raw, "text/plain")})
        assert response.status_code == 422, response.text
    assert client.get("/api/library").json()["jobs"] == []
    assert not list((config.DATA / "content" / "documents").glob("*"))


def test_library_delete_and_copy_refusal_over_http(client):
    identifier, _ = make(client)
    copied = client.post(
        "/api/library/operations",
        json={"action": "copy", "items": [{"kind": "file", "id": identifier}]},
    )
    assert (
        copied.status_code == 409 and copied.json()["detail"] == "図表の複製には対応していません。"
    )
    removed = client.post(
        "/api/library/operations",
        json={"action": "delete", "items": [{"kind": "file", "id": identifier}]},
    )
    assert removed.status_code == 200
    assert client.get(f"/api/diagrams/{identifier}").status_code == 404
    for root in ("content", "derived", "runtime", "cache"):
        assert not (config.DATA / root / "documents" / identifier).exists()
    assert store.repair_diagrams(config.DATA)["repaired"] == []
