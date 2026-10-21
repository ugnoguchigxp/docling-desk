import errno
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from docling_desk import atomic_files
from docling_desk.atomic_files import atomic_write_bytes, sync_directory


def residue(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_writes_new_file_and_creates_parent(tmp_path):
    path = tmp_path / "a" / "b" / "value.bin"
    atomic_write_bytes(path, b"\x00\xffdata")
    assert path.read_bytes() == b"\x00\xffdata"
    assert residue(path.parent) == []


def test_replaces_existing_file(tmp_path):
    path = tmp_path / "value"
    path.write_bytes(b"old")
    atomic_write_bytes(path, b"new")
    assert path.read_bytes() == b"new"
    assert residue(tmp_path) == []


def test_exact_mode_is_applied_regardless_of_umask(tmp_path):
    previous = os.umask(0)
    try:
        atomic_write_bytes(tmp_path / "private", b"x", mode=0o600)
        atomic_write_bytes(tmp_path / "plain", b"x")
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / "private").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "plain").stat().st_mode) == 0o666


def test_default_mode_follows_umask(tmp_path):
    previous = os.umask(0o077)
    try:
        atomic_write_bytes(tmp_path / "plain", b"x")
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / "plain").stat().st_mode) == 0o600


def test_temporary_is_unique_hidden_in_same_directory_and_ends_with_tmp(tmp_path, monkeypatch):
    seen = []
    real = os.replace

    def spy(source, destination):
        seen.append(Path(source))
        real(source, destination)

    monkeypatch.setattr(atomic_files.os, "replace", spy)
    path = tmp_path / "value.json"
    atomic_write_bytes(path, b"1")
    atomic_write_bytes(path, b"2")
    assert len(set(seen)) == 2
    for temporary in seen:
        assert temporary.parent == tmp_path
        assert temporary.name.startswith(".value.json.")
        assert temporary.name.endswith(".tmp")


def test_data_is_fsynced_before_replace_and_directory_after(tmp_path, monkeypatch):
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(
        atomic_files.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))
    )
    monkeypatch.setattr(
        atomic_files.os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))
    )
    atomic_write_bytes(tmp_path / "value", b"x")
    assert order == ["fsync", "replace", "fsync"]


def test_exception_before_replace_keeps_old_version_and_leaves_no_residue(tmp_path):
    path = tmp_path / "value"
    path.write_bytes(b"old")

    def fail():
        raise RuntimeError("killed before replace")

    with pytest.raises(RuntimeError):
        atomic_write_bytes(path, b"new", before_replace=fail)
    assert path.read_bytes() == b"old"
    assert residue(tmp_path) == []


def test_hook_sees_complete_temporary_but_nothing_published(tmp_path):
    path = tmp_path / "value"
    observed = {}

    def look():
        (temporary,) = residue(tmp_path)
        observed["temporary"] = temporary.read_bytes()
        observed["published"] = path.exists()

    atomic_write_bytes(path, b"complete", before_replace=look)
    assert observed == {"temporary": b"complete", "published": False}


def test_failed_replace_keeps_old_version_and_leaves_no_residue(tmp_path, monkeypatch):
    path = tmp_path / "value"
    path.write_bytes(b"old")

    def failed(source, destination):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(atomic_files.os, "replace", failed)
    with pytest.raises(OSError):
        atomic_write_bytes(path, b"new")
    assert path.read_bytes() == b"old"
    assert residue(tmp_path) == []


def test_failed_data_fsync_publishes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "value"
    path.write_bytes(b"old")

    def failed(fd):
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(atomic_files.os, "fsync", failed)
    with pytest.raises(OSError):
        atomic_write_bytes(path, b"new")
    assert path.read_bytes() == b"old"
    assert residue(tmp_path) == []


def test_directory_sync_error_after_replace_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    path = tmp_path / "value"
    path.write_bytes(b"old")

    def failed(directory):
        raise OSError(errno.EIO, "directory sync failed")

    monkeypatch.setattr(atomic_files, "sync_directory", failed)
    with caplog.at_level("WARNING", logger="docling_desk.atomic_files"):
        atomic_write_bytes(path, b"new")  # published, so the caller sees success
    assert path.read_bytes() == b"new"
    assert residue(tmp_path) == []
    assert any("directory sync" in record.getMessage() for record in caplog.records)


def test_missing_parent_raises_without_creating_it_when_asked(tmp_path):
    path = tmp_path / "gone" / "value"
    with pytest.raises(FileNotFoundError):
        atomic_write_bytes(path, b"x", create_parent=False)
    assert not path.parent.exists()


def test_directory_sync_unsupported_by_filesystem_is_skipped(tmp_path, monkeypatch):
    def unsupported(fd):
        raise OSError(errno.EINVAL, "not supported")

    monkeypatch.setattr(atomic_files.os, "fsync", unsupported)
    sync_directory(tmp_path)


def test_directory_that_cannot_be_opened_is_skipped(tmp_path, monkeypatch):
    real_open = os.open

    def cannot_open(target, flags, *args):
        if Path(target) == tmp_path:
            raise PermissionError(errno.EACCES, "cannot open directory")
        return real_open(target, flags, *args)

    monkeypatch.setattr(atomic_files.os, "open", cannot_open)
    sync_directory(tmp_path)
    atomic_write_bytes(tmp_path / "value", b"x")
    assert (tmp_path / "value").read_bytes() == b"x"


def test_real_directory_sync_error_is_not_swallowed(tmp_path, monkeypatch):
    real_fsync = os.fsync
    calls = []

    def failing_on_directory(fd):
        calls.append(fd)
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "io")
        real_fsync(fd)

    monkeypatch.setattr(atomic_files.os, "fsync", failing_on_directory)
    with pytest.raises(OSError):
        sync_directory(tmp_path)


def test_planted_temporary_symlink_is_never_followed(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    folder = tmp_path / "folder"
    folder.mkdir()
    monkeypatch.setattr(atomic_files, "uuid4", lambda: type("U", (), {"hex": "fixed"})())
    (folder / ".value.fixed.tmp").symlink_to(outside)
    with pytest.raises(FileExistsError):
        atomic_write_bytes(folder / "value", b"new")
    assert outside.read_bytes() == b"keep"
    assert not (folder / "value").exists()
    assert (folder / ".value.fixed.tmp").is_symlink()


def test_destination_symlink_is_replaced_not_written_through(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "value").symlink_to(outside)
    atomic_write_bytes(folder / "value", b"new")
    assert outside.read_bytes() == b"keep"
    assert not (folder / "value").is_symlink()
    assert (folder / "value").read_bytes() == b"new"


def test_concurrent_writers_never_publish_a_mixed_file(tmp_path):
    path = tmp_path / "value"
    values = [bytes([i]) * 4096 for i in range(8)]
    errors = []

    def write(value):
        try:
            for _ in range(20):
                atomic_write_bytes(path, value)
        except Exception as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=write, args=(v,)) for v in values]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert path.read_bytes() in values
    assert residue(tmp_path) == []


def named(name):
    from docling_desk.documents.library import Folder, Library

    return Library(folders={"f": Folder(id="f", name=name)})


def names(library):
    return [folder.name for folder in library.folders.values()]


@pytest.mark.parametrize("point", ["library_save_before_write", "library_save_before_replace"])
def test_library_save_fault_keeps_previous_manifest_and_no_residue(tmp_path, monkeypatch, point):
    from docling_desk.documents.library import load, save
    from docling_desk.operations.faults import Fault

    save(tmp_path, named("old"))
    manifests = tmp_path / "content" / "manifests"
    before = (manifests / "library.json").read_bytes()
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", point)
    with pytest.raises(Fault):
        save(tmp_path, named("new"))
    assert (manifests / "library.json").read_bytes() == before
    assert residue(manifests) == []
    assert names(load(tmp_path)) == ["old"]


def test_library_save_fault_after_replace_publishes_the_new_complete_manifest(
    tmp_path, monkeypatch
):
    from docling_desk.documents.library import load, save
    from docling_desk.operations.faults import Fault

    save(tmp_path, named("old"))
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "library_save_after_replace")
    with pytest.raises(Fault):
        save(tmp_path, named("new"))
    assert names(load(tmp_path)) == ["new"]
    assert residue(tmp_path / "content" / "manifests") == []


def test_record_artifacts_keeps_previous_manifest_when_replace_fails(tmp_path, monkeypatch):
    from docling_desk.storage import document_root, record_artifacts

    folder = document_root(tmp_path) / ("a" * 32)
    folder.mkdir(parents=True)
    record_artifacts(folder, {"state": "success", "id": "old"})
    before = (folder / "manifest.json").read_bytes()

    def failed(source, destination):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(atomic_files.os, "replace", failed)
    with pytest.raises(OSError):
        record_artifacts(folder, {"state": "success", "id": "new"})
    assert (folder / "manifest.json").read_bytes() == before
    assert residue(folder) == []


def test_transfer_receipt_is_private_complete_and_leaves_no_temporary(tmp_path):
    from types import SimpleNamespace

    from docling_desk.viewer.transfers import Transfers

    gateway = SimpleNamespace(
        config=lambda: {"transfer_dir": str(tmp_path / "transfers")},
        config_file=tmp_path / "gateway.json",
    )
    transfers = Transfers(gateway)
    record = {"transfer_id": "a" * 32, "user": "u", "fingerprint": "f", "retain_until": 1}
    transfers.save(record)
    path = transfers.root / ("a" * 32 + ".json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == record
    assert residue(transfers.root) == []


def test_wiki_batch_writers_are_private_and_skip_identical_content(tmp_path, monkeypatch):
    from docling_desk.wiki_batch import files, preparation

    target = tmp_path / "out" / "article.md"
    files.atomic_write(target, "日本語\n")
    assert target.read_text(encoding="utf-8") == "日本語\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    prepared = tmp_path / "prepared" / "a.json"
    preparation.atomic_write(prepared, "one")
    first = prepared.stat().st_ino
    preparation.atomic_write(prepared, "one")
    assert prepared.stat().st_ino == first  # identical content is not rewritten
    preparation.atomic_write(prepared, "two")
    assert prepared.read_text(encoding="utf-8") == "two"
    assert stat.S_IMODE(prepared.stat().st_mode) == 0o600
    assert residue(prepared.parent) == []

    def failed(source, destination):
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(atomic_files.os, "replace", failed)
    with pytest.raises(OSError):
        preparation.atomic_write(prepared, "three")
    assert prepared.read_text(encoding="utf-8") == "two"
    assert residue(prepared.parent) == []


def killed_writer(path: Path, where: str) -> int:
    """Run a writer in a child process that exits abruptly at the given point."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(
        """
        import os, sys
        from pathlib import Path
        from docling_desk import atomic_files
        path, where = Path(sys.argv[1]), sys.argv[2]
        if where == "after":
            atomic_files.sync_directory = lambda directory: os._exit(9)
        atomic_files.atomic_write_bytes(
            path, b"new" * 100000, before_replace=(lambda: os._exit(9)) if where == "before" else None
        )
        """
    )
    return subprocess.run([sys.executable, "-c", code, str(path), where], timeout=60).returncode


def test_process_killed_before_replace_leaves_only_the_old_complete_version(tmp_path):
    path = tmp_path / "value"
    path.write_bytes(b"old")
    assert killed_writer(path, "before") == 9
    assert path.read_bytes() == b"old"
    # The abandoned temporary is not the published name; stores' repair or mirror skip rules match it.
    assert all(p.name.startswith(".value.") and p.name.endswith(".tmp") for p in residue(tmp_path))


def test_process_killed_after_replace_shows_only_the_new_complete_version(tmp_path):
    path = tmp_path / "value"
    path.write_bytes(b"old")
    assert killed_writer(path, "after") == 9
    assert path.read_bytes() == b"new" * 100000
    assert residue(tmp_path) == []


def test_late_saves_into_a_deleted_document_do_not_recreate_its_folder(tmp_path):
    from docling_desk.documents.conversion import Job, save_job
    from docling_desk.storage import document_root, job_file, record_artifacts

    folder = document_root(tmp_path) / ("b" * 32)
    folder.mkdir(parents=True)
    job = Job(id=folder.name, filename="note.txt", state="success")
    save_job(folder, job)
    for child in folder.iterdir():
        child.unlink()
    folder.rmdir()
    with pytest.raises(FileNotFoundError):
        record_artifacts(folder, {"state": "success", "id": job.id})
    with pytest.raises(FileNotFoundError):
        save_job(folder, job)
    assert not folder.exists()
    assert job_file(folder).exists()  # runtime job.json was left by the first save only
