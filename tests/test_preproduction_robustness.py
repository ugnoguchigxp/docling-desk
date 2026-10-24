"""Pre-production robustness: failures that used to hang, crash or freeze the service."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pypdfium2 as pdfium
import pytest
from defusedxml.common import DefusedXmlException
from fastapi.testclient import TestClient

import docling_desk.app as desk_app
import docling_desk.documents.conversion as conversion
import docling_desk.operations.backup as backup
from docling_desk import config
from docling_desk.api import uploads
from docling_desk.documents.conversion import Job, read_job, save_job
from docling_desk.documents.library import LOCK
from docling_desk.execution import SlotPool
from docling_desk.operations.backup import exclusive_write
from docling_desk.preview import office_linux
from docling_desk.preview.sheets import workbook_sheets
from docling_desk.sqlite_writer import connect
from docling_desk.storage import document_folder, document_root, migrate_layout, original_file

# --- 1. a job must not stay "running" when everything after the work fails -------


def running_document(data: Path, identifier: str = "c" * 32) -> Path:
    folder = document_folder(data, identifier)
    folder.mkdir(parents=True)
    save_job(folder, Job(id=identifier, filename="note.txt", state="running"))
    return folder


def test_slot_lease_logs_what_the_work_raised_and_still_returns_the_slot(caplog):
    pool, executor = SlotPool(1), ThreadPoolExecutor(max_workers=1)
    try:
        lease = pool.claim()

        def fail():
            raise RuntimeError("disk full while saving")

        with caplog.at_level(logging.ERROR, logger="docling_desk.execution"):
            lease.submit(executor, fail).exception(timeout=5)
            executor.shutdown(wait=True)
        assert pool.in_use == 0
        assert any(
            r.exc_info and "disk full while saving" in str(r.exc_info[1]) for r in caplog.records
        )
    finally:
        executor.shutdown(wait=True)


def test_run_job_marks_the_job_failed_when_conversion_itself_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    folder = running_document(tmp_path)

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(uploads, "convert_job", boom)
    uploads.run_job(folder, read_job(folder))  # must not raise
    saved = read_job(folder)
    assert saved.state == "failed" and "No space" in saved.error


def test_last_resort_retries_the_save_and_never_overwrites_a_final_state(tmp_path, monkeypatch):
    folder = running_document(tmp_path)
    job = read_job(folder)
    real = conversion.save_job
    calls = []

    def flaky(target, record):
        calls.append(record.state)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        real(target, record)

    monkeypatch.setattr(conversion, "save_job", flaky)
    # The save fails: reported as False, never raised, job.json stays as it was.
    assert conversion.fail_job_last_resort(folder, job, RuntimeError("x")) is False
    assert read_job(folder).state == "running"
    assert conversion.fail_job_last_resort(folder, job, RuntimeError("x")) is True
    assert read_job(folder).state == "failed"

    done = running_document(tmp_path, "d" * 32)
    finished = read_job(done)
    finished.state = "success"
    real(done, finished)
    assert conversion.fail_job_last_resort(done, finished, RuntimeError("late")) is False
    assert read_job(done).state == "success"


# --- 2. startup survives an unreachable blob container / a damaged document ------


class UnreachableMirror:
    settle = 0

    def restore_incomplete(self):
        return False

    def has_local_content(self):
        return False

    def pull(self):
        raise ConnectionError("Azure Blob Storage is unreachable")

    def status(self):
        return {"backend": "azure-blob"}


class IdleWorker:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def close(self):
        pass


def test_startup_survives_unreachable_blob_and_stays_not_ready(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(desk_app, "create_mirror", lambda data: UnreachableMirror())
    monkeypatch.setattr(desk_app, "MirrorWorker", IdleWorker)
    with caplog.at_level(logging.ERROR, logger="docling_desk.blob"):
        with TestClient(desk_app.create_app()) as client:
            assert client.get("/health/live").status_code == 200
            ready = client.get("/health/ready")
            assert ready.status_code == 503
            assert "blob_restore_incomplete" in ready.json()["reasons"]
    assert any("restore failed" in r.getMessage() for r in caplog.records)


def make_legacy(data: Path, identifier: str, payload: bytes) -> Path:
    folder = data / identifier
    folder.mkdir(parents=True)
    save_job(folder, Job(id=identifier, filename="a.pdf", state="success"))
    (folder / "original.pdf").write_bytes(payload)
    return folder


def test_one_damaged_document_does_not_stop_the_migration_of_the_others(tmp_path):
    good = make_legacy(tmp_path, "1" * 32, b"%PDF-1.4 good")
    clash = make_legacy(tmp_path, "2" * 32, b"%PDF-1.4 old copy")
    # Same ID already present in the new layout with different bytes.
    existing = tmp_path / "content/documents" / clash.name / "original.pdf"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"different document")
    broken = make_legacy(tmp_path, "3" * 32, b"%PDF-1.4 with broken explanations")
    (broken / "explanations").mkdir()
    (broken / "explanations/index.sqlite").write_bytes(b"this is not a database" * 100)
    with exclusive_write(tmp_path, "migration"):
        skipped = migrate_layout(tmp_path)
    assert sorted(skipped) == sorted([broken.name, clash.name])
    assert original_file(document_folder(tmp_path, good.name), ".pdf").read_bytes() == (
        b"%PDF-1.4 good"
    )
    # Nothing of the skipped documents was deleted or overwritten.
    assert existing.read_bytes() == b"different document"
    assert (document_folder(tmp_path, clash.name) / "original.pdf").read_bytes() == (
        b"%PDF-1.4 old copy"
    )
    assert (document_folder(tmp_path, broken.name) / "original.pdf").read_bytes() == (
        b"%PDF-1.4 with broken explanations"
    )
    assert (document_folder(tmp_path, broken.name) / "explanations/index.sqlite").is_file()


def test_old_and_new_copy_with_the_same_id_are_skipped_not_fatal(tmp_path):
    clash = make_legacy(tmp_path, "4" * 32, b"%PDF-1.4 old")
    other = make_legacy(tmp_path, "5" * 32, b"%PDF-1.4 other")
    new = document_root(tmp_path) / clash.name
    new.mkdir(parents=True)
    save_job(new, Job(id=clash.name, filename="a.pdf", state="success"))
    with exclusive_write(tmp_path, "migration"):
        skipped = migrate_layout(tmp_path)
    assert skipped == [clash.name]
    assert (clash / "original.pdf").read_bytes() == b"%PDF-1.4 old"
    assert original_file(document_folder(tmp_path, other.name), ".pdf").is_file()


# --- 3. the event loop never waits for the library lock or the disk --------------


def test_upload_does_not_freeze_the_event_loop_while_the_library_lock_is_held(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "DATA", tmp_path)
    app = desk_app.create_app()
    ticks = []

    async def scenario():
        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            LOCK.acquire()
            beat = asyncio.create_task(heartbeat())
            try:
                pending = asyncio.create_task(
                    client.post("/api/upload", files={"file": ("note.txt", b"text")})
                )
                await asyncio.sleep(0.3)
                alive = await client.get("/health/live")
                assert alive.status_code == 200
                assert not pending.done()  # waiting for the lock, in a worker thread
            finally:
                LOCK.release()
            response = await asyncio.wait_for(pending, 10)
            beat.cancel()
            return response

    response = asyncio.run(scenario())
    assert response.status_code == 503  # lifespan did not run here: no worker is attached
    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    assert gaps and max(gaps) < 0.25  # the loop kept turning while the lock was held


# --- 4. page limit ---------------------------------------------------------------


def make_pdf(path: Path, pages: int) -> Path:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(100, 100)
    document.save(path)
    document.close()
    return path


def test_default_page_limit_is_bounded():
    assert config.DEFAULT_MAX_PAGES == 2000
    if "DOCLING_MAX_PAGES" not in os.environ:
        assert config.MAX_PAGES == 2000


def test_pdf_over_the_page_limit_is_refused_with_a_clear_message(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "three.pdf", 3)
    monkeypatch.setattr(conversion, "MAX_PAGES", 3)
    conversion.validate_input(pdf)
    monkeypatch.setattr(conversion, "MAX_PAGES", 2)
    with pytest.raises(ValueError, match="ページ数が上限（2ページ）"):
        conversion.validate_input(pdf)


def test_pdf_pdfium_cannot_open_is_left_to_the_conversion(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"%PDF-1.4 not really a pdf")
    conversion.validate_input(fake)


def test_upload_api_answers_422_for_too_many_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(conversion, "MAX_PAGES", 1)
    pdf = make_pdf(tmp_path / "two.pdf", 2).read_bytes()
    usage = uploads.shutil.disk_usage(tmp_path)
    monkeypatch.setattr(uploads.shutil, "disk_usage", lambda _: usage._replace(free=3 * 1024**3))
    monkeypatch.setattr(uploads, "load_profile", lambda name=None: _LocalProfile())
    with TestClient(desk_app.create_app()) as client:
        response = client.post("/api/upload", files={"file": ("two.pdf", pdf)})
        assert response.status_code == 422
        assert "ページ数が上限" in response.json()["detail"]
        assert client.app.state.slots.in_use == 0


class _LocalProfile:
    def check_credentials(self, key):
        pass

    def model_dump(self, mode="json"):
        return {"provider": "local"}


# --- 5. ZIP bombs and untrusted worksheet XML -------------------------------------


def office_zip(path: Path, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def test_zip_entry_size_and_compression_ratio_are_limited(tmp_path, monkeypatch):
    plain = office_zip(tmp_path / "plain.xlsx", {"xl/a.xml": b"<a>" + b"x" * 5000 + b"</a>"})
    conversion.validate_input(plain)
    bomb = office_zip(tmp_path / "bomb.xlsx", {"xl/big.xml": b"0" * 200_000})
    monkeypatch.setattr(conversion, "ZIP_RATIO_FLOOR", 100_000)
    with pytest.raises(ValueError, match="圧縮率"):
        conversion.validate_input(bomb)
    monkeypatch.setattr(conversion, "ZIP_RATIO_FLOOR", 10**9)  # ratio check off ...
    monkeypatch.setattr(conversion, "MAX_ZIP_ENTRY", 100_000)  # ... the entry limit still holds
    with pytest.raises(ValueError, match="1ファイル"):
        conversion.validate_input(bomb)


def test_worksheet_xml_with_entities_is_rejected(tmp_path):
    workbook = (
        '<?xml version="1.0"?><!DOCTYPE w [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/>'
        "<x>&b;</x></workbook>"
    )
    path = tmp_path / "evil.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", "<Relationships/>")
    with pytest.raises(DefusedXmlException):
        workbook_sheets(path)


# --- 6. LibreOffice process group --------------------------------------------------


def alive(pid: int) -> bool:
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def test_timeout_kills_the_whole_process_group(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    script = f"sleep 300 & echo $! > '{pidfile}'; wait"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        office_linux.run_in_group(["sh", "-c", script], timeout=1)
    assert time.monotonic() - started < 20
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(pid), "the grandchild survived the timeout"


def test_run_in_group_returns_output_and_status():
    result = office_linux.run_in_group(["sh", "-c", "echo out; echo err >&2; exit 3"], timeout=10)
    assert (result.returncode, result.stdout.strip(), result.stderr.strip()) == (3, "out", "err")


# --- 7. backup ---------------------------------------------------------------------


def test_backup_checksum_streams_the_file(tmp_path, monkeypatch):
    path = tmp_path / "big.bin"
    path.write_bytes(b"abc" * 100_000)

    def forbidden(self):
        raise AssertionError("the whole file must not be read into memory")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    import hashlib

    assert backup._sha256(path) == hashlib.sha256(b"abc" * 100_000).hexdigest()


@pytest.mark.parametrize(
    "error", [sqlite3.OperationalError("SQLite Writerとの通信が切れました"), OSError(28, "full")]
)
def test_backup_cli_reports_database_and_io_errors_with_an_exit_code(
    tmp_path, monkeypatch, capsys, error
):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(backup, "backup_local", fail)
    code = backup.main(["backup", "--data", str(tmp_path), "--output", str(tmp_path / "out")])
    assert code == 3
    assert "再実行" in capsys.readouterr().err


# --- 8. SQLite writer session ------------------------------------------------------


def test_writer_session_survives_a_value_sqlite_cannot_bind(tmp_path):
    with connect(tmp_path / "t.sqlite") as db:
        db.execute("CREATE TABLE t (v INTEGER)")
        db.commit()
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute("INSERT INTO t VALUES (?)", (2**70,))
        # The session thread is still alive and answers the next request.
        db.execute("INSERT INTO t VALUES (?)", (1,))
        db.commit()
        assert db.execute("SELECT v FROM t").fetchall() == [(1,)]


def test_writer_session_reports_rows_it_cannot_serialize(tmp_path):
    with connect(tmp_path / "b.sqlite") as db:
        db.execute("CREATE TABLE t (v BLOB)")
        db.execute("INSERT INTO t VALUES (x'00ff')")
        db.commit()
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute("SELECT v FROM t")
        assert db.execute("SELECT 1").fetchall() == [(1,)]
