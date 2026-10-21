"""Conversion computes outside the write lock and publishes one complete generation.

Covers docs/refactoring/R13-design.md: short lock sections, the attempt/original
re-check at publication, activity-based backup refusal that a crash cannot make
permanent, interrupted-publication recovery and the Blob notification order.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest

import docling_desk.documents.conversion as service
from docling_desk import activity
from docling_desk.blob_mirror import Conflict, Mirror, Missing, Remote
from docling_desk.documents import diagram_store as diagrams
from docling_desk.documents.conversion import Job, save_job
from docling_desk.knowledge.store import Store
from docling_desk.operations.backup import (
    backup_local,
    exclusive_write,
    restore_local,
    writer_status,
)
from docling_desk.storage import job_file, migrate_layout, record_original, remove_document

ROOT = Path(__file__).resolve().parent.parent
TEXT = "資料の本文\n\n二つ目の段落。\n"
SCENE = json.dumps(
    {
        "type": "excalidraw",
        "version": 2,
        "elements": [{"id": "a", "type": "rectangle", "x": 0, "y": 0, "width": 1, "height": 1}],
        "appState": {},
    }
).encode()


def document(data: Path, text: str = TEXT) -> tuple[Path, Job]:
    """A queued document exactly as the upload leaves it for the worker."""
    data.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    folder = data / "derived" / "documents" / identifier
    original = data / "content" / "documents" / identifier / "original.txt"
    original.parent.mkdir(parents=True)
    original.write_text(text, encoding="utf-8")
    folder.mkdir(parents=True)
    job = Job(id=identifier, filename="note.txt")
    record_original(folder, job.filename, job.created)
    save_job(folder, job)
    return folder, job


def pdf_document(data: Path) -> tuple[Path, Job]:
    from reportlab.pdfgen import canvas

    folder, _ = document(data)
    identifier = folder.name
    (data / "content" / "documents" / identifier / "original.txt").unlink()
    original = data / "content" / "documents" / identifier / "original.pdf"
    page = canvas.Canvas(str(original), pagesize=(300, 400))
    page.drawString(30, 350, "Fresh upload")
    page.save()
    job = Job(id=identifier, filename="new.pdf")
    record_original(folder, job.filename, job.created)
    save_job(folder, job)
    return folder, job


def saved(folder: Path) -> Job:
    return Job.model_validate_json(job_file(folder).read_text(encoding="utf-8"))


def leftovers(data: Path) -> list[str]:
    found = []
    for root in (activity.directory(data), activity.work_root(data)):
        found += [p.name for p in root.glob("*")] if root.is_dir() else []
    return found


class Pause:
    """Stop the conversion at a named checkpoint until the test lets it go."""

    def __init__(self, monkeypatch, at: str = "convert_after_document"):
        self.reached, self.release = threading.Event(), threading.Event()
        real = service.checkpoint

        def hook(name):
            if name == at:
                self.reached.set()
                if not self.release.wait(30):
                    raise TimeoutError(name)
            real(name)

        monkeypatch.setattr(service, "checkpoint", hook)

    def run(self, folder: Path, job: Job) -> threading.Thread:
        thread = threading.Thread(target=service.convert_job, args=(folder, job))
        thread.start()
        assert self.reached.wait(30), "the conversion never reached the wait point"
        return thread

    def finish(self, thread: threading.Thread) -> None:
        self.release.set()
        thread.join(30)
        assert not thread.is_alive()


def save_diagram(data: Path) -> float:
    started = time.monotonic()
    status, _ = diagrams.create(data, uuid4().hex, "図", None, SCENE, uuid4().hex)
    assert status == 201
    return time.monotonic() - started


def test_other_saves_do_not_wait_for_the_computation(tmp_path, monkeypatch):
    data = tmp_path / "data"
    waits = {}
    for hold in (0.2, 2.5):
        folder, job = document(data)
        pause = Pause(monkeypatch)
        thread = pause.run(folder, job)
        time.sleep(hold)
        waits[hold] = save_diagram(data)
        assert thread.is_alive() and saved(folder).state == "running"
        assert not (folder / "document.json").exists()
        pause.finish(thread)
        assert job.state == "success", job.error
        assert saved(folder).state == "success"
    # The wait does not follow the conversion time (it was 12 times longer).
    assert waits[0.2] < 0.5 and waits[2.5] < 0.5
    assert leftovers(data) == []


def test_publication_is_one_generation_and_notifies_afterwards(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    seen = {}
    blocked = threading.Event()
    other = threading.Thread(target=lambda: (save_diagram(data), blocked.set()))
    real = service.checkpoint

    def hook(name):
        if name == "convert_during_publish" and "during" not in seen:
            # Part of the artifacts has moved but readers still see an unpublished job.
            seen["during"] = (saved(folder).state, sorted(p.name for p in folder.iterdir()))
            other.start()
            time.sleep(0.3)
            seen["writer_waited"] = not blocked.is_set()
        if name == "convert_after_publish":
            seen["after_files"] = (saved(folder).state, (folder / "manifest.json").exists())
        real(name)

    monkeypatch.setattr(service, "checkpoint", hook)
    monkeypatch.setattr(service, "notify", lambda: seen.setdefault("notified", saved(folder).state))
    service.convert_job(folder, job)
    other.join(10)
    assert seen["during"][0] == "running" and "document.json" in seen["during"][1]
    assert seen["writer_waited"] is True and blocked.is_set()
    assert seen["after_files"] == ("running", False)
    assert seen["notified"] == "success"  # published first, then announced
    assert job.state == "success" and (folder / "manifest.json").is_file()
    for name in ("document.json", "rag.jsonl", "rag-index.jsonl", "extracted.html"):
        assert (folder / name).is_file()
    assert not (folder / "job.json").exists() and not list(folder.glob("*.tmp"))
    assert not activity.attempt_record(data, folder.name).exists()
    assert leftovers(data) == []


def test_preview_built_in_the_work_area_is_valid_where_it_is_published(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from docling.datamodel.base_models import ConversionStatus
    from docling_core.types.doc import DoclingDocument

    from docling_desk.preview.editable_preview import editable_preview
    from docling_desk.storage import original_file

    data = tmp_path / "data"
    folder, job = pdf_document(data)
    fixture = DoclingDocument.load_from_json(ROOT / "tests/fixtures/documents/page/document.json")
    monkeypatch.setattr(
        service,
        "converter",
        lambda: SimpleNamespace(
            convert=lambda *a, **k: SimpleNamespace(
                status=ConversionStatus.SUCCESS, document=fixture, errors=[]
            )
        ),
    )
    service.convert_job(folder, job)
    assert job.state == "success", job.error
    assert job.preview == "editable-preview/Preview.html"
    source = original_file(folder, ".pdf")
    assert source.is_file() and not (folder / "original.pdf").exists()
    assert editable_preview(source, folder) == job.preview
    # No artifact remembers the temporary work path.
    work = str(activity.work_root(data))
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".html", ".txt", ".md", ".css"}:
            assert work not in path.read_text(encoding="utf-8", errors="ignore"), path


def test_failed_conversion_publishes_no_artifacts(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "convert_after_rag")
    service.convert_job(folder, job)
    assert saved(folder).state == "failed" and job.state == "failed"
    assert sorted(p.name for p in folder.iterdir()) == []
    assert leftovers(data) == []


def test_failure_while_publishing_rolls_the_artifacts_back(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    monkeypatch.setenv("DOCLING_FAULT", "raise:convert_after_publish")
    service.convert_job(folder, job)
    assert saved(folder).state == "failed"
    assert sorted(p.name for p in folder.iterdir()) == []
    assert leftovers(data) == [] and not activity.attempt_record(data, folder.name).exists()


def test_a_refused_start_leaves_a_failed_job_on_disk_and_delete_works(tmp_path):
    from fastapi import HTTPException

    from docling_desk.documents.library import ItemRef, Operation, operate, register_upload

    data = tmp_path / "data"
    folder, job = document(data)
    register_upload(data, job, None)
    item = Operation(action="delete", items=[ItemRef(kind="file", id=job.id)])
    with pytest.raises(HTTPException) as refused:  # queued: still refused
        operate(data, item)
    assert refused.value.status_code == 409
    (data / "content" / "documents" / job.id / "original.txt").unlink()
    service.convert_job(folder, job)
    assert job.state == "failed"
    assert saved(folder).state == "failed" and "原本" in (saved(folder).error or "")
    assert leftovers(data) == [] and not activity.attempt_record(data, folder.name).exists()
    operate(data, item)  # the failed document can now be deleted
    assert not folder.exists()


def test_a_refusal_never_overwrites_a_finished_or_live_job(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    service.convert_job(folder, job)
    assert saved(folder).state == "success"
    before = job_file(folder).read_bytes()
    service.convert_job(folder, Job(id=job.id, filename="note.txt"))  # already converted
    assert job_file(folder).read_bytes() == before

    live_folder, live_job = document(data)
    pause = Pause(monkeypatch)
    thread = pause.run(live_folder, live_job)
    (data / "content" / "documents" / live_folder.name / "original.txt").unlink()
    other = Job(id=live_job.id, filename="note.txt")
    service.convert_job(live_folder, other)  # another attempt is live: no touch
    assert other.state == "failed" and saved(live_folder).state == "running"
    pause.finish(thread)


def test_a_failure_after_the_activity_began_does_not_leave_the_job_queued(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    real = Path.mkdir

    def failing(self, *args, **kwargs):
        if self.parent == activity.work_root(data):
            raise OSError("work area unavailable")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing)
    service.convert_job(folder, job)
    monkeypatch.undo()
    assert job.state == "failed" and saved(folder).state == "failed"
    assert leftovers(data) == [] and not activity.attempt_record(data, folder.name).exists()


def test_a_deleted_document_is_not_resurrected(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    pause = Pause(monkeypatch)
    thread = pause.run(folder, job)
    remove_document(folder)
    pause.finish(thread)
    assert job.state == "failed"
    assert not folder.exists() and not job_file(folder).parent.exists()
    assert not (data / "content" / "documents" / folder.name).exists()
    assert leftovers(data) == []


def test_a_changed_original_is_not_published(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    pause = Pause(monkeypatch)
    thread = pause.run(folder, job)
    original = data / "content" / "documents" / folder.name / "original.txt"
    original.write_text("差し替えた別の本文です。" * 5, encoding="utf-8")
    pause.finish(thread)
    assert saved(folder).state == "failed" and "原本" in (saved(folder).error or "")
    assert not (folder / "document.json").exists()
    assert not activity.attempt_record(data, folder.name).exists()
    assert leftovers(data) == []


def test_a_stale_attempt_cannot_publish_late(tmp_path, monkeypatch):
    data = tmp_path / "data"
    # Recovery (or a cancel) already ended the run: its failure stays the truth.
    folder, job = document(data)
    pause = Pause(monkeypatch)
    thread = pause.run(folder, job)
    recovered = saved(folder).model_copy(update={"state": "failed", "error": "再起動で中断"})
    save_job(folder, recovered)
    pause.finish(thread)
    assert saved(folder).error == "再起動で中断" and not (folder / "document.json").exists()
    assert job.state == "failed"
    # A newer attempt took the document over: the old one writes nothing.
    folder, job = document(data)
    pause = Pause(monkeypatch)
    thread = pause.run(folder, job)
    record = activity.attempt_record(data, folder.name)
    record.write_text(json.dumps({"attempt": "newer", "sha256": "0" * 64}), encoding="utf-8")
    pause.finish(thread)
    assert saved(folder).state == "running" and not (folder / "document.json").exists()
    assert json.loads(record.read_text())["attempt"] == "newer"
    assert leftovers(data) == []


def test_only_one_live_attempt_per_document_and_a_dead_one_is_taken_over(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    pause = Pause(monkeypatch)
    thread = pause.run(folder, job)
    second = Job(id=job.id, filename=job.filename)
    service.convert_job(folder, second)
    assert second.state == "failed" and "実行中" in (second.error or "")
    assert len(list(activity.work_root(data).glob("*"))) == 1
    pause.finish(thread)
    assert saved(folder).state == "success"
    # A run that died leaves "running" and its record, but no lock: take over.
    folder, job = document(data)
    save_job(folder, job.model_copy(update={"state": "running"}))
    activity.attempt_record(data, folder.name).write_text(
        json.dumps({"attempt": "dead", "sha256": "0" * 64}), encoding="utf-8"
    )
    service.convert_job(folder, job)
    assert saved(folder).state == "success"
    # A published document is never converted over.
    again = Job(id=job.id, filename=job.filename)
    service.convert_job(folder, again)
    assert again.state == "failed" and saved(folder).state == "success"


def test_backup_is_refused_while_computing_and_restores_one_generation(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    pause = Pause(monkeypatch)
    thread = pause.run(folder, job)
    assert writer_status(data)["state"] == "active"
    with pytest.raises(ValueError, match="拒否"):
        backup_local(data, tmp_path / "during")
    assert not (tmp_path / "during").exists()
    pause.finish(thread)
    assert writer_status(data)["state"] == "idle"
    Store(data).sync_documents()
    archive = tmp_path / "archive"
    backup_local(data, archive)
    packed = {item["path"] for item in json.loads((archive / "manifest.json").read_text())["files"]}
    assert not any(path.startswith(("runtime/activity", "runtime/conversions")) for path in packed)
    destination = tmp_path / "restored"
    restore_local(archive, destination)
    restored = Store(destination)
    assert saved(destination / "derived" / "documents" / folder.name).state == "success"
    chunk = next(item for item in restored.chunks() if "資料の本文" in item["text"])
    assert "資料の本文" in restored.context(chunk["id"])
    assert leftovers(destination) == []


def test_activity_is_the_lock_not_the_file(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    first = activity.Activity(data, "a" * 32, "convert", job="x")
    with exclusive_write(data, "convert"):
        first.begin()
    with pytest.raises(activity.ActivityConflict):
        activity.Activity(data, "a" * 32, "convert").begin()
    assert [item["attempt"] for item in activity.live(data)] == ["a" * 32]
    assert activity.is_live(data, "a" * 32) and not activity.is_live(data, "b" * 32)
    assert first.end() is True and first.end() is False
    assert not first.path.exists()
    # A file nobody holds is a leftover and never refuses anything.
    stale = activity.directory(data) / ("c" * 32 + ".lock")
    stale.write_text("{}", encoding="utf-8")
    assert activity.live(data) == [] and stale.exists()
    assert activity.sweep(data) == [] and not stale.exists()
    backup_local(data, tmp_path / "ok")


def test_startup_cleanup_keeps_live_and_removes_dead(tmp_path):
    data = tmp_path / "data"
    folder, job = document(data)
    live = activity.Activity(data, "1" * 32, "convert")
    live.begin()
    (activity.work_root(data) / ("1" * 32)).mkdir(parents=True)
    (activity.work_root(data) / ("2" * 32)).mkdir(parents=True)
    (activity.work_root(data) / ("2" * 32) / "x").write_text("x")
    (activity.directory(data) / ("2" * 32 + ".lock")).write_text("{}")
    other = activity.attempt_record(data, folder.name)
    other.write_text(json.dumps({"attempt": "2" * 32, "publishing": ["half.json"]}))
    (folder / "half.json").write_text("{}")
    try:
        migrate_layout(data)
        assert (activity.work_root(data) / ("1" * 32)).is_dir() and live.path.exists()
        assert not (activity.work_root(data) / ("2" * 32)).exists()
        assert not other.exists() and not (folder / "half.json").exists()
    finally:
        live.end()
    # A publication that did reach its job.json is kept, only the record goes.
    folder, job = document(data)
    service.convert_job(folder, job)
    activity.attempt_record(data, folder.name).write_text(
        json.dumps({"attempt": "3" * 32, "publishing": ["document.json", "manifest.json"]})
    )
    migrate_layout(data)
    assert (folder / "document.json").is_file() and (folder / "manifest.json").is_file()
    assert not activity.attempt_record(data, folder.name).exists()


def test_private_folders_keep_the_whole_run_under_the_write_lock(tmp_path, monkeypatch):
    private = tmp_path / ("p" * 32)
    private.mkdir()
    (private / "original.txt").write_text(TEXT, encoding="utf-8")
    states = []
    real = service.checkpoint

    def hook(name):
        states.append(writer_status(private.parent)["purpose"])
        real(name)

    monkeypatch.setattr(service, "checkpoint", hook)
    job = Job(id=private.name, filename="note.txt")
    service.convert_job(private, job)
    assert job.state == "success" and set(states) == {"convert"}
    assert not (private.parent / "runtime").exists()


class MemoryStore:
    def __init__(self):
        self.blobs: dict[str, tuple[bytes, str, str]] = {}
        self.version = 0

    def list(self):
        return {k: Remote(e, len(b), s) for k, (b, e, s) in self.blobs.items()}

    def put(self, key, path, sha256, etag):
        current = self.blobs.get(key)
        if (etag is None) != (current is None) or (current and current[1] != etag):
            raise Conflict(key)
        self.version += 1
        self.blobs[key] = (path.read_bytes(), f'"v{self.version}"', sha256)
        return f'"v{self.version}"'

    def get(self, key, target):
        if key not in self.blobs:
            raise Missing(key)
        target.write_bytes(self.blobs[key][0])
        return self.blobs[key][1]

    def delete(self, key, etag):
        self.blobs.pop(key, None)


def test_blob_sync_never_shows_a_published_job_without_its_artifacts(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder, job = document(data)
    remote = MemoryStore()
    mirror = Mirror(data, remote, derived=True, settle=0.0)
    key = f"runtime/documents/{folder.name}/job.json"
    states = []

    def remote_state():
        return json.loads(remote.blobs[key][0])["state"] if key in remote.blobs else None

    real = service.checkpoint

    def hook(name):
        if name in {"convert_during_publish", "convert_after_publish"}:
            mirror.push()  # a pass lands inside the publication window
            states.append(remote_state())
        real(name)

    monkeypatch.setattr(service, "checkpoint", hook)
    service.convert_job(folder, job)
    assert "success" not in states
    mirror.push()
    assert remote_state() == "success"
    local = {p.relative_to(data).as_posix() for p in (data / "derived").rglob("*") if p.is_file()}
    assert local and local <= set(remote.blobs)


# -- real processes ---------------------------------------------------------

CHILD = """
import sys, time
from pathlib import Path
from docling_desk.documents import conversion as service
from docling_desk.documents.conversion import Job

folder, name, ready, go = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
real = service.checkpoint

def hook(point):
    if point == "convert_after_document" and sys.argv[4] != "-":
        ready.write_text(point)
        deadline = time.time() + 120
        while not go.exists() and time.time() < deadline:
            time.sleep(0.05)
    real(point)

service.checkpoint = hook
service.convert_job(folder, Job(id=folder.name, filename=name))
"""


def child(tmp_path: Path, folder: Path, ready: Path, go: Path, **env) -> subprocess.Popen:
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    environment = {"DOCLING_ENV_FILE": "/dev/null", "PATH": "/usr/bin:/bin", **env}
    return subprocess.Popen(
        [sys.executable, str(script), str(folder), "note.txt", str(ready), str(go)],
        cwd=ROOT,
        env=environment,
        stderr=subprocess.PIPE,
    )


def wait_for(path: Path, process: subprocess.Popen, seconds: float = 180) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert process.poll() is None, process.stderr.read().decode()[-2000:]
        assert time.monotonic() < deadline, "the child never reached the wait point"
        time.sleep(0.1)


def test_another_process_computing_refuses_backup_and_a_kill_releases_it(tmp_path):
    data = tmp_path / "data"
    folder, _ = document(data)
    ready, go = tmp_path / "ready", tmp_path / "go"
    process = child(tmp_path, folder, ready, go)
    try:
        wait_for(ready, process)
        with pytest.raises(ValueError, match="拒否"):
            backup_local(data, tmp_path / "during")
        assert writer_status(data)["state"] == "active"
        assert save_diagram(data) < 0.5  # the child holds no write lock while computing
        assert saved(folder).state == "running" and len(leftovers(data)) == 2
        process.kill()
        process.wait(30)
        # Nothing was published, and the dead run does not refuse the backup.
        assert writer_status(data)["state"] == "idle"
        backup_local(data, tmp_path / "after")
        assert activity.sweep(data) == []
        assert not (folder / "document.json").exists()
        with exclusive_write(data, "migration"):
            migrate_layout(data)
        assert leftovers(data) == [] and not activity.attempt_record(data, folder.name).exists()
        assert saved(folder).state == "running"  # startup recovery marks it failed
    finally:
        process.kill()
        process.wait(30)
        process.stderr.close()


@pytest.mark.parametrize("point", ["convert_during_publish", "convert_after_publish"])
def test_kill_at_the_publication_boundary_leaves_no_half_generation(tmp_path, point):
    data = tmp_path / "data"
    folder, _ = document(data)
    process = child(tmp_path, folder, tmp_path / "r", Path("-"), DOCLING_FAULT=f"exit:{point}")
    try:
        assert process.wait(240) == 86, process.stderr.read().decode()[-2000:]
    finally:
        process.stderr.close()
    # The unpublished job still reads "running" while part of the files moved.
    assert saved(folder).state == "running"
    assert (folder / "document.json").exists()
    assert not (folder / "manifest.json").exists()
    backup_local(data, tmp_path / "after-crash")  # never refused for good
    with exclusive_write(data, "migration"):
        migrate_layout(data)
    assert sorted(p.name for p in folder.iterdir()) == []
    assert leftovers(data) == [] and not activity.attempt_record(data, folder.name).exists()
    # Startup recovery then fails the job; nothing of the attempt is readable.
    failed = saved(folder).model_copy(update={"state": "failed", "error": "中断"})
    save_job(folder, failed)
    assert saved(folder).state == "failed" and not (folder / "document.json").exists()
