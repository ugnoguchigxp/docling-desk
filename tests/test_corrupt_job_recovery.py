import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from docling_desk import config
from docling_desk.app import create_app
from docling_desk.atomic_files import atomic_write_bytes
from docling_desk.documents.conversion import Job, read_job, save_job
from docling_desk.documents.library import register_upload
from docling_desk.storage import document_folder, job_file, original_file, record_original


def test_corrupt_legacy_record_without_manifest_keeps_original_extension(tmp_path):
    folder = tmp_path / ("d" * 32)
    folder.mkdir()
    (folder / "job.json").write_bytes(b"{broken")
    (folder / "original.pdf").write_bytes(b"synthetic original")
    job = read_job(folder)
    assert job.filename == folder.name + ".pdf"
    assert original_file(folder, ".pdf").read_bytes() == b"synthetic original"
    assert (folder / "job.json").read_bytes() == b"{broken"


@pytest.mark.parametrize("bad_record", [b"{broken", b"{}", b'{"id":"other","filename":"test.txt"}'])
def test_one_corrupt_job_preserves_other_documents_and_is_recoverable(
    tmp_path, monkeypatch, bad_record
):
    monkeypatch.setattr(config, "DATA", tmp_path)
    identifiers = ["a" * 32, "b" * 32, "c" * 32]
    saved = {}
    originals = {}
    for identifier in identifiers:
        folder = document_folder(tmp_path, identifier)
        folder.mkdir(parents=True)
        source = original_file(folder, ".txt")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("synthetic " + identifier)
        job = Job(id=identifier, filename=identifier + ".txt", state="success")
        record_original(folder, job.filename, job.created)
        register_upload(tmp_path, job, None)
        save_job(folder, job)
        saved[identifier] = job_file(folder).read_bytes()
        originals[identifier] = hashlib.sha256(source.read_bytes()).hexdigest()
    broken = document_folder(tmp_path, identifiers[1])
    job_file(broken).write_bytes(bad_record)
    for _ in range(2):
        with TestClient(create_app()) as client:
            assert client.get("/health/live").status_code == 200
            response = client.get("/api/library")
            assert response.status_code == 200, response.text
            jobs = {job["id"]: job for job in response.json()["jobs"]}
            assert len(jobs) == 3
            assert jobs[identifiers[0]]["state"] == jobs[identifiers[2]]["state"] == "success"
            assert jobs[identifiers[1]]["error_code"] == "metadata_corrupt"
            assert "原本は保持" in jobs[identifiers[1]]["error"]
            for identifier in identifiers:
                downloaded = client.get(f"/files/{identifier}/original.txt")
                assert downloaded.status_code == 200, downloaded.text
                assert hashlib.sha256(downloaded.content).hexdigest() == originals[identifier]
            assert job_file(broken).read_bytes() == bad_record
            assert client.get("/api/jobs").status_code == 200
    # Restore a known-good backup; no extra authoritative quarantine state needs clearing.
    atomic_write_bytes(job_file(broken), saved[identifiers[1]])
    with TestClient(create_app()) as client:
        restored = client.get(f"/api/jobs/{identifiers[1]}").json()
        assert restored["state"] == "success" and restored["error_code"] is None
        assert json.loads(job_file(broken).read_bytes())["id"] == identifiers[1]
