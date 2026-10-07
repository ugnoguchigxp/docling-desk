from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, save_job
from docling_desk.documents.library import load
from docling_desk.storage import document_folder, document_folders


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    with TestClient(web.app) as client:
        yield client


def file(client, name="report.pdf", parent=None, state="success"):
    import uuid

    id = uuid.uuid4().hex
    path = desk_config.DATA / id
    path.mkdir()
    save_job(path, Job(id=id, filename=name, state=state))
    (path / ("original" + Path(name).suffix)).write_bytes(b"%PDF-1.4 test")
    (path / "rag.jsonl").write_text('{"id":"' + id + ':pdf:1","source":"report.pdf"}\n')
    if parent:
        assert operation(client, "move", [("file", id)], destination=parent).status_code == 200
    return id


def folder(client, name, parent=None):
    response = client.post("/api/folders", json={"name": name, "parent_id": parent})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def operation(client, action, refs, **extra):
    return client.post(
        "/api/library/operations",
        json={"action": action, "items": [{"kind": kind, "id": id} for kind, id in refs], **extra},
    )


def test_existing_files_and_nested_navigation_persist(client):
    id = file(client)
    assert client.get("/api/jobs").json()[0]["folder_id"] is None
    parent = folder(client, "企画")
    child = folder(client, "2026", parent)
    assert operation(client, "move", [("file", id)], destination=child).status_code == 200
    assert client.get("/api/jobs/" + id).json()["folder_id"] == child
    assert load(desk_config.DATA).folders[child].parent_id == parent
    assert load(desk_config.DATA).files[id].parent_id == child
    assert client.get("/files/" + id + "/original.pdf").content == b"%PDF-1.4 test"


def test_library_responds_while_translation_source_is_busy(client):
    from concurrent.futures import ThreadPoolExecutor

    from docling_desk.translation.store import LOCK as TRANSLATION_LOCK

    id = file(client)
    translation = desk_config.DATA / id / "translations" / "en"
    translation.mkdir(parents=True)
    (translation / "page-1.json").write_text('{"state":"completed","result":{"text":"saved"}}')
    with ThreadPoolExecutor(max_workers=1) as worker:
        with TRANSLATION_LOCK:
            response = worker.submit(client.get, "/api/library").result(timeout=2)
            assert worker.submit(client.get, "/api/jobs").result(timeout=2).status_code == 200
            detail = worker.submit(client.get, "/api/jobs/" + id).result(timeout=2)
            assert detail.status_code == 200
            assert detail.json()["id"] == id
        assert response.status_code == 200
        assert response.json()["jobs"][0]["id"] == id
        assert response.json()["jobs"][0]["translations"]["en"] == {
            "saved": 1,
            "active": 0,
            "failed": 0,
        }
        assert client.get("/api/jobs").status_code == 200


def test_library_releases_organization_lock_before_reading_translation_records(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from docling_desk.api import library

    id = file(client)
    reading, release = Event(), Event()

    def slow_summary(folder):
        reading.set()
        assert release.wait(timeout=5)
        return {}

    monkeypatch.setattr(library, "summary", slow_summary)
    with ThreadPoolExecutor(max_workers=2) as worker:
        listing = worker.submit(client.get, "/api/library")
        try:
            assert reading.wait(timeout=2)
            detail = worker.submit(client.get, "/api/jobs/" + id).result(timeout=2)
            assert detail.status_code == 200
        finally:
            release.set()
        assert listing.result(timeout=2).status_code == 200


def test_rename_retains_source_and_downloads_with_display_name(client):
    id = file(client)
    assert operation(client, "rename", [("file", id)], name="新資料.pdf").status_code == 200
    job = client.get("/api/jobs/" + id).json()
    assert job["filename"] == "新資料.pdf"
    assert job["original_filename"] == "report.pdf"
    assert (
        Job.model_validate_json((desk_config.DATA / id / "job.json").read_text()).filename
        == "report.pdf"
    )
    download = client.get(f"/files/{id}/original.pdf?download=true")
    assert download.status_code == 200
    assert "filename*=utf-8" in download.headers["content-disposition"]
    assert operation(client, "rename", [("file", id)], name="新資料.xlsx").status_code == 422
    assert operation(client, "rename", [("file", id)], name="../新資料.pdf").status_code == 422


def test_bulk_conflict_has_no_partial_move(client):
    a, b = file(client, "a.pdf"), file(client, "b.pdf")
    target = folder(client, "target")
    file(client, "b.pdf", target)
    assert (
        operation(client, "move", [("file", a), ("file", b)], destination=target).status_code == 409
    )
    assert all(j["folder_id"] is None for j in client.get("/api/jobs").json() if j["id"] in {a, b})


def test_cycles_and_missing_destinations_rejected(client):
    root = folder(client, "root")
    child = folder(client, "child", root)
    for action in ("move", "copy"):
        assert operation(client, action, [("folder", root)], destination=child).status_code == 409
        assert operation(client, action, [("folder", root)], destination=root).status_code == 409
        assert (
            operation(client, action, [("folder", root)], destination="missing").status_code == 404
        )
    assert operation(client, "delete", [("file", "../../app.py")]).status_code == 404


def test_delete_folder_removes_descendants_artifacts_and_urls(client):
    root = folder(client, "親")
    child = folder(client, "子", root)
    id = file(client, parent=child)
    unrelated = file(client, "keep.pdf")
    translation = desk_config.DATA / id / "translations" / "en"
    translation.mkdir(parents=True)
    (translation / "page-1.json").write_text('{"state":"completed","result":{"text":"saved"}}')
    response = operation(client, "delete", [("folder", root), ("file", id)])
    assert response.status_code == 200
    assert response.json()["items"] == [{"kind": "folder", "id": root}]
    snapshot = client.get("/api/library").json()
    assert snapshot["folders"] == []
    assert "trash" not in snapshot
    assert {j["id"] for j in snapshot["jobs"]} == {unrelated}
    assert not (desk_config.DATA / id).exists()
    assert client.get("/api/jobs/" + id).status_code == 404
    assert client.get("/files/" + id + "/original.pdf").status_code == 404
    assert (desk_config.DATA / unrelated / "original.pdf").read_bytes() == b"%PDF-1.4 test"
    assert load(desk_config.DATA).files == {}


def test_delete_file_preserves_parent_and_releases_name(client):
    parent = folder(client, "parent")
    id = file(client, parent=parent)
    assert operation(client, "delete", [("file", id)]).status_code == 200
    assert not (desk_config.DATA / id).exists()
    assert parent in load(desk_config.DATA).folders
    assert id not in load(desk_config.DATA).files
    replacement = file(client, parent=parent)
    assert client.get("/api/jobs/" + replacement).json()["filename"] == "report.pdf"
    assert operation(client, "delete", [("file", id)]).status_code == 404


def test_copy_folder_preserves_files_and_remaps_rag_ids(client):
    root = folder(client, "source")
    child = folder(client, "child", root)
    id = file(client, parent=child)
    response = operation(client, "copy", [("folder", root), ("file", id)], destination=None)
    assert response.status_code == 200
    copied_root = response.json()["items"][0]["id"]
    snapshot = client.get("/api/library").json()
    assert (
        next(f for f in snapshot["folders"] if f["id"] == copied_root)["name"] == "source (コピー)"
    )
    copied_child = next(f["id"] for f in snapshot["folders"] if f["parent_id"] == copied_root)
    copied_file = next(j for j in snapshot["jobs"] if j["folder_id"] == copied_child)
    assert copied_file["id"] != id
    assert (
        client.get(f"/files/{copied_file['id']}/original.pdf").content
        == client.get(f"/files/{id}/original.pdf").content
    )
    assert (
        f'"id":"{id}:'
        not in (document_folder(desk_config.DATA, copied_file["id"]) / "rag.jsonl").read_text()
    )
    assert (
        copied_file["id"]
        in (document_folder(desk_config.DATA, copied_file["id"]) / "rag.jsonl").read_text()
    )


def test_copy_failure_rolls_back(client, monkeypatch):
    root = folder(client, "source")
    file(client, parent=root)
    before = client.get("/api/library").json()

    def broken(source, target, **kwargs):
        Path(target).mkdir()
        raise OSError("disk full")

    monkeypatch.setattr("docling_desk.documents.library.shutil.copytree", broken)
    assert operation(client, "copy", [("folder", root)]).status_code == 500
    assert client.get("/api/library").json() == before
    assert len(document_folders(desk_config.DATA)) == 1
    assert not list((desk_config.DATA / "derived/documents").glob("*"))


@pytest.mark.parametrize("action", ["delete", "copy"])
def test_busy_subtree_rejected(client, action):
    parent = folder(client, "processing")
    file(client, parent=parent, state="running")
    assert operation(client, action, [("folder", parent)]).status_code == 409
    assert parent in load(desk_config.DATA).folders
    assert len(client.get("/api/jobs").json()) == 1


def test_mutations_reject_cross_site_and_invalid_names(client):
    assert (
        client.post(
            "/api/folders", json={"name": "x"}, headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/library/operations",
            json={"action": "delete", "items": []},
            headers={"Origin": "https://other.example"},
        ).status_code
        == 403
    )
    assert client.post("/api/folders", json={"name": "../x"}).status_code == 422
    folder(client, "Plan")
    assert client.post("/api/folders", json={"name": "plan"}).status_code == 409


def test_upload_into_folder_and_duplicate_name(client, monkeypatch):
    target = folder(client, "upload")
    file(client, parent=target)

    def submit(fn, path, job, slots):
        job.state = "success"
        save_job(path, job)
        slots.release()

    monkeypatch.setattr(client.app.state, "worker", Mock(submit=submit))
    response = client.post(
        "/api/upload", files={"file": ("report.pdf", b"%PDF-1.4 test")}, data={"folder_id": target}
    )
    assert response.status_code == 202, response.text
    assert response.json()["folder_id"] == target
    assert response.json()["filename"] == "report (2).pdf"
    assert (
        client.post(
            "/api/upload",
            files={"file": ("x.pdf", b"%PDF-1.4 test")},
            data={"folder_id": "missing"},
        ).status_code
        == 404
    )


def test_worker_completion_does_not_overwrite_organization(client):
    id = file(client, state="running")
    parent = folder(client, "parent")
    assert operation(client, "move", [("file", id)], destination=parent).status_code == 200
    assert operation(client, "rename", [("file", id)], name="renamed.pdf").status_code == 200
    # Simulate the worker completing with its original job object.
    save_job(desk_config.DATA / id, Job(id=id, filename="report.pdf", state="success", pages=2))
    job = client.get("/api/jobs/" + id).json()
    assert job["state"] == "success"
    assert job["filename"] == "renamed.pdf"
    assert job["folder_id"] == parent


def test_delete_preflights_all_items_before_touching_files(client):
    id = file(client)
    assert operation(client, "delete", [("file", id), ("file", "missing")]).status_code == 404
    assert (desk_config.DATA / id / "original.pdf").exists()
    empty = folder(client, "empty")
    assert operation(client, "delete", [("folder", empty)]).status_code == 200
    assert empty not in load(desk_config.DATA).folders


def test_delete_partial_failure_keeps_remaining_items_visible(client, monkeypatch):
    parent = folder(client, "parent")
    ids = sorted([file(client, "a.pdf", parent), file(client, "b.pdf", parent)])
    from shutil import rmtree

    def fail_second(path):
        if path.name == ids[1]:
            raise PermissionError("busy disk")
        rmtree(path)

    monkeypatch.setattr("docling_desk.documents.library.shutil.rmtree", fail_second)
    response = operation(client, "delete", [("folder", parent)])
    assert response.status_code == 500
    assert "一部" in response.json()["detail"]
    assert not (desk_config.DATA / ids[0]).exists()
    assert (desk_config.DATA / ids[1] / "original.pdf").exists()
    assert {j["id"] for j in client.get("/api/jobs").json()} == {ids[1]}
    assert parent in load(desk_config.DATA).folders
    assert ids[0] not in load(desk_config.DATA).files


@pytest.mark.parametrize("action", ["delete", "copy"])
def test_active_translation_prevents_mutation(client, action):
    parent = folder(client, "parent")
    id = file(client, parent=parent)
    translation = desk_config.DATA / id / "translations" / "en"
    translation.mkdir(parents=True)
    (translation / "page-1.json").write_text('{"state":"running","result":null}')
    response = operation(client, action, [("folder", parent)])
    assert response.status_code == 409
    assert "翻訳中" in response.json()["detail"]
    assert (desk_config.DATA / id / "original.pdf").exists()


def test_delete_rejects_symlink_storage(client, tmp_path):
    id = "d" * 32
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    save_job(outside, Job(id=id, filename="outside.pdf", state="success"))
    (outside / "original.pdf").write_bytes(b"keep outside")
    (desk_config.DATA / id).symlink_to(outside, target_is_directory=True)
    assert operation(client, "delete", [("file", id)]).status_code == 409
    assert (outside / "original.pdf").read_bytes() == b"keep outside"


def test_no_trash_or_restore_api(client):
    id = file(client)
    for action in ("trash", "restore"):
        assert operation(client, action, [("file", id)]).status_code == 422
    assert (desk_config.DATA / id).exists()


def test_passive_recovery_page_inlines_only_matching_fonts_without_changing_saved_files(client):
    id = file(client, "deck.pptx")
    folder = desk_config.DATA / id / "progressive-preview"
    folder.mkdir()
    raw = '<html><head><link rel="stylesheet" href="fonts-2.css"></head><body><svg xmlns="http://www.w3.org/2000/svg"><text>page 2</text></svg></body></html>'
    (folder / "page-2.html").write_text(raw)
    css = "@font-face{src:url(data:font/ttf;base64,AA==)}/* </style><script> */"
    (folder / "fonts-2.css").write_text(css)
    response = client.get(f"/files/{id}/progressive-preview/page-2.html?inline_fonts=true")
    assert response.status_code == 200
    assert "<link " not in response.text
    assert "data:font/ttf" in response.text
    assert "<script>" not in response.text
    assert "sandbox" in response.headers["content-security-policy"]
    assert "allow-same-origin" not in response.headers["content-security-policy"]
    assert (folder / "page-2.html").read_text() == raw
    thumb = client.get(f"/files/{id}/progressive-preview/page-2.html?thumbnail=true")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/svg+xml")
    assert thumb.text.startswith("<svg ")
    assert "data:font/ttf" in thumb.text
    (folder / "fonts-2.css").unlink()
    assert (
        client.get(f"/files/{id}/progressive-preview/page-2.html?inline_fonts=true").status_code
        == 404
    )
    assert client.get(f"/files/{id}/progressive-preview/page-2.html").status_code == 200


@pytest.mark.parametrize("query", ["inline_fonts=true", "thumbnail=true"])
@pytest.mark.parametrize("broken", ["page-1.html", "fonts-1.css"])
def test_unreadable_recovery_asset_returns_a_reason_not_a_server_error(client, query, broken):
    id = file(client, "deck.pptx")
    folder = desk_config.DATA / id / "progressive-preview"
    folder.mkdir()
    (folder / "page-1.html").write_text(
        '<html><head><link rel="stylesheet" href="fonts-1.css"></head><body>'
        '<svg xmlns="http://www.w3.org/2000/svg"><text>saved</text></svg></body></html>'
    )
    (folder / "fonts-1.css").write_text("text{font-family:sans-serif}")
    (folder / broken).write_bytes(b"\xff\xfe")
    response = client.get(f"/files/{id}/progressive-preview/page-1.html?{query}")
    assert response.status_code == 409
    assert response.json()["detail"]


@pytest.mark.parametrize("query", ["inline_fonts=true", "thumbnail=true"])
def test_recovery_asset_removed_during_read_returns_not_found(client, monkeypatch, query):
    id = file(client, "deck.pptx")
    folder = desk_config.DATA / id / "progressive-preview"
    folder.mkdir()
    (folder / "page-1.html").write_text('<link rel="stylesheet" href="fonts-1.css">')
    (folder / "fonts-1.css").write_text("")
    read_text = Path.read_text

    def removed(path, *args, **kwargs):
        if path == folder / "fonts-1.css":
            raise FileNotFoundError("concurrent replacement")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", removed)
    response = client.get(f"/files/{id}/progressive-preview/page-1.html?{query}")
    assert response.status_code == 404
    assert response.json()["detail"]
