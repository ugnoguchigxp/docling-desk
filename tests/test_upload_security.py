"""Request and backup boundaries from the security review, using synthetic inputs."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import httpx
import pytest
import starlette.formparsers
from fastapi.testclient import TestClient

from docling_desk import config
from docling_desk.api import uploads
from docling_desk.app import create_app
from docling_desk.documents.library import Library, load, save
from docling_desk.operations import backup
from docling_desk.storage import document_folder, job_file


@pytest.mark.parametrize("multipart", [True, False])
def test_unknown_length_body_stops_before_full_receive_and_closes_spool(
    tmp_path, monkeypatch, multipart
):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "MAX_BYTES", 1024 * 1024)
    files = []
    spool = starlette.formparsers.SpooledTemporaryFile

    def tracked_spool(*args, **kwargs):
        file = spool(*args, **kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", tracked_spool)
    consumed = 0
    size = 4 * 1024 * 1024

    async def chunks():
        nonlocal consumed
        if multipart:
            yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="note.txt"\r\n\r\n'
        else:
            yield b'{"name":"'
        for _ in range(size // 65536):
            consumed += 65536
            yield b"x" * 65536
        yield b"\r\n--test--\r\n" if multipart else b'"}'

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost"
        ) as client:
            response = await client.post(
                "/api/upload" if multipart else "/api/folders",
                headers={
                    "content-type": "multipart/form-data; boundary=test"
                    if multipart
                    else "application/json"
                },
                content=chunks(),
            )
            assert response.status_code == 413

    asyncio.run(run())
    assert consumed < size
    assert consumed <= 2 * 1024 * 1024 + 65536
    if multipart:
        assert files and all(file.closed for file in files)
    assert not list(tmp_path.rglob("original.*"))


def test_authentication_rejects_before_receiving_unknown_length_body(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "AUTH_MODE", "jwt")
    consumed = False

    async def chunks():
        nonlocal consumed
        consumed = True
        yield b"untrusted"

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost"
        ) as client:
            assert (await client.post("/api/upload", content=chunks())).status_code == 401

    asyncio.run(run())
    assert not consumed


def test_backup_and_upload_publish_one_consistent_snapshot(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA", data)
    save(data, Library())
    enumerated, proceed, publishing = threading.Event(), threading.Event(), threading.Event()
    payload_files, publish_upload = backup._payload_files, uploads.publish_upload

    def paused(root):
        iterator = payload_files(root)
        first = next(iterator)
        enumerated.set()
        assert proceed.wait(10)
        yield first
        yield from iterator

    def publishing_upload(*args):
        publishing.set()
        return publish_upload(*args)

    monkeypatch.setattr(backup, "_payload_files", paused)
    monkeypatch.setattr(uploads, "publish_upload", publishing_upload)
    archive = tmp_path / "archive"
    with TestClient(create_app()) as client, ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(client.app.state, "worker", Mock())
        copying = pool.submit(backup.backup_local, data, archive)
        assert enumerated.wait(5)
        upload = pool.submit(
            client.post, "/api/upload", files={"file": ("review.txt", b"review bytes")}
        )
        try:
            assert publishing.wait(5)
            assert not upload.done()
            assert client.get("/api/jobs").json() == []
            assert not list(data.rglob("original.*"))
        finally:
            proceed.set()
        copying.result(timeout=10)
        response = upload.result(timeout=10)
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert job_file(document_folder(data, job_id)).is_file()
        restored = tmp_path / "restored"
        backup.restore_local(archive, restored)
        assert load(restored).files == {}
        assert not list(restored.rglob("original.*"))
        # A subsequent snapshot includes both the reference and the exact bytes.
        complete = tmp_path / "complete"
        backup.backup_local(data, complete)
        after = tmp_path / "after"
        backup.restore_local(complete, after)
        assert job_id in load(after).files
        assert (
            after / "content/documents" / job_id / "original.txt"
        ).read_bytes() == b"review bytes"
        assert job_file(document_folder(after, job_id)).is_file()
        manifest = json.loads((complete / "manifest.json").read_text())
        assert any(job_id in item["path"] for item in manifest["files"])


@pytest.mark.parametrize("action", ["rename", "move", "copy", "delete", "folder"])
def test_library_mutations_wait_for_the_backup_lock(tmp_path, monkeypatch, action):
    from docling_desk.documents.conversion import Job, save_job
    from docling_desk.documents.library import FolderCreate, Operation, create_folder, operate

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA", data)
    job = Job(id="a" * 32, filename="note.txt", state="success")
    folder = document_folder(data, job.id)
    folder.mkdir(parents=True)
    original = data / "content/documents" / job.id / "original.txt"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original bytes")
    save_job(folder, job)
    save(data, Library())
    entered = threading.Event()

    def mutate():
        entered.set()
        if action == "folder":
            return create_folder(data, FolderCreate(name="new folder"))
        return operate(
            data,
            Operation(action=action, items=[{"kind": "file", "id": job.id}], name="renamed.txt"),
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with backup.exclusive_write(data, "backup"):
            result = pool.submit(mutate)
            assert entered.wait(5)
            # The mutation cannot finish while the separate backup thread owns the lock.
            with pytest.raises(TimeoutError):
                result.result(timeout=0.1)
            assert original.read_bytes() == b"original bytes"
            assert load(data).files == {}
        assert result.result(timeout=5)
