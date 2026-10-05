"""Real process boundaries: one writer, atomic transactions and crash recovery."""

import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from docling_desk import sqlite_writer
from docling_desk.knowledge.store import Store
from docling_desk.wiki_batch.store import BatchStore


@pytest.fixture
def writer(monkeypatch):
    # Short socket path, separate from any running UI or another test run.
    with tempfile.TemporaryDirectory(prefix="writer-test-", dir="/tmp") as directory:
        root = Path(directory)
        monkeypatch.setattr(sqlite_writer, "_runtime", lambda: root)
        children = []
        start = subprocess.Popen

        def tracked_start(*args, **kwargs):
            child = start(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(subprocess, "Popen", tracked_start)
        try:
            yield root
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=10)


def child_code(root, path, statements):
    return (
        "from pathlib import Path\n"
        "from docling_desk import sqlite_writer as w\n"
        f"w._runtime = lambda: Path({str(root)!r})\n"
        f"db = w.connect({str(path)!r}, isolation_level=None)\n" + statements
    )


def run_child(code):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    return subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=20
    )


def test_ui_and_batch_share_one_process_across_workspaces(writer, tmp_path):
    store = Store(tmp_path / "ui")
    batch = BatchStore(tmp_path / "workspace")
    try:
        article = store.import_wiki("wiki", "example.md", "# Example\nAtomic content")
        batch.set("paused", 1)
        with store.connection() as db:
            assert db.writer_pid == batch.db.writer_pid != os.getpid()
            result = run_child(child_code(writer, db.path, "print(db.writer_pid)\ndb.close()\n"))
            assert result.returncode == 0, result.stderr
            assert int(result.stdout) == db.writer_pid
        assert store.source(article["id"])["body"] == "# Example\nAtomic content"
        assert batch.setting("paused") == "1"
    finally:
        batch.close()


def test_simultaneous_first_connections_start_one_writer(writer, tmp_path):
    code = child_code(writer, tmp_path / "startup.sqlite", "print(db.writer_pid)\ndb.close()\n")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run_child, [code] * 4))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert len({r.stdout.strip() for r in results}) == 1
    # The daemon was started by a child rather than this fixture's tracked Popen.
    with sqlite_writer.connect(tmp_path / "startup.sqlite") as db:
        pid = db.writer_pid
    os.kill(pid, signal.SIGTERM)


def test_readonly_connection_sees_committed_snapshot_while_writer_is_busy(writer, tmp_path):
    path = tmp_path / "readers.sqlite"
    with sqlite_writer.connect(path) as db:
        db.executescript("PRAGMA journal_mode=WAL; CREATE TABLE records(value);")
        db.execute("INSERT INTO records VALUES(1)")
    with sqlite_writer.connect(path) as db:
        db.execute("UPDATE records SET value=2")
        with sqlite_writer.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.05) as reader:
            assert reader.writer_pid == db.writer_pid
            assert reader.execute("SELECT value FROM records").fetchone() == (1,)
    with sqlite_writer.connect(path) as db:
        assert db.execute("SELECT value FROM records").fetchone() == (2,)


def test_simultaneous_process_start_and_read_modify_write_are_serialized(writer, tmp_path):
    path = tmp_path / "counter.sqlite"
    with sqlite_writer.connect(path) as db:
        db.executescript("CREATE TABLE counter(value); INSERT INTO counter VALUES(0);")
    code = child_code(
        writer,
        path,
        "for _ in range(20):\n"
        " db.execute('BEGIN IMMEDIATE')\n"
        " value = db.execute('SELECT value FROM counter').fetchone()[0]\n"
        " db.execute('UPDATE counter SET value=?', (value + 1,))\n"
        " db.execute('COMMIT')\n"
        "print(db.writer_pid)\ndb.close()\n",
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run_child, [code] * 4))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert len({r.stdout.strip() for r in results}) == 1
    with sqlite_writer.connect(path) as db:
        assert db.execute("SELECT value FROM counter").fetchone()[0] == 80
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_killed_client_rolls_back_and_releases_writer(writer, tmp_path):
    path = tmp_path / "crash.sqlite"
    with sqlite_writer.connect(path) as db:
        db.execute("CREATE TABLE records(value)")
    result = run_child(
        child_code(
            writer,
            path,
            "import os\ndb.execute('BEGIN IMMEDIATE')\n"
            "db.execute('INSERT INTO records VALUES(1)')\nos._exit(86)\n",
        )
    )
    assert result.returncode == 86
    with sqlite_writer.connect(path) as db:
        assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 0
        db.execute("INSERT INTO records VALUES(2)")
    with sqlite_writer.connect(path) as db:
        assert db.execute("SELECT value FROM records").fetchall() == [(2,)]


def test_exception_rolls_back_and_preserves_sqlite_error_types(writer, tmp_path):
    path = tmp_path / "rollback.sqlite"
    with sqlite_writer.connect(path) as db:
        db.execute("CREATE TABLE records(value UNIQUE)")
        db.execute("INSERT INTO records VALUES(1)")
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite_writer.connect(path) as db:
            db.execute("INSERT INTO records VALUES(2)")
            db.execute("INSERT INTO records VALUES(1)")
    with sqlite_writer.connect(path) as db:
        assert db.execute("SELECT value FROM records").fetchall() == [(1,)]


def test_forked_child_cannot_use_or_disconnect_parent_session(writer, tmp_path):
    with sqlite_writer.connect(tmp_path / "fork.sqlite") as db:
        db.execute("CREATE TABLE records(value)")
        pid = os.fork()
        if pid == 0:
            try:
                db.execute("INSERT INTO records VALUES(1)")
            except sqlite3.ProgrammingError:
                db.close()
                os._exit(0)
            os._exit(1)
        assert os.waitpid(pid, 0)[1] == 0
        db.execute("INSERT INTO records VALUES(2)")
        assert db.execute("SELECT value FROM records").fetchall() == [(2,)]


def test_backup_refuses_same_file_instead_of_waiting_forever(writer, tmp_path):
    path = tmp_path / "same.sqlite"
    with sqlite_writer.connect(path) as db, sqlite_writer.connect(path) as target:
        db.execute("CREATE TABLE records(value)")
        with pytest.raises(sqlite3.OperationalError, match="同じDB"):
            db.backup(target)


def test_writer_death_is_not_replayed_and_new_session_recovers(writer, tmp_path):
    path = tmp_path / "restart.sqlite"
    with sqlite_writer.connect(path) as db:
        db.execute("CREATE TABLE records(value)")
    db = sqlite_writer.connect(path)
    db.execute("INSERT INTO records VALUES(1)")
    old_pid = db.writer_pid
    os.kill(old_pid, signal.SIGKILL)
    # Wait before attempting commit: delivery of SIGKILL is asynchronous, so an
    # immediate request could legitimately commit before the process exits.
    os.waitpid(old_pid, 0)
    with pytest.raises(sqlite3.OperationalError, match="通信"):
        db.commit()
    with sqlite_writer.connect(path) as fresh:
        assert fresh.writer_pid != old_pid
        assert fresh.execute("SELECT count(*) FROM records").fetchone()[0] == 0
        fresh.execute("INSERT INTO records VALUES(2)")


def test_wait_timeout_and_close_release_other_sessions(writer, tmp_path):
    path = tmp_path / "timeout.sqlite"
    first = sqlite_writer.connect(path)
    second = sqlite_writer.connect(path, timeout=0.05)
    try:
        first.execute("CREATE TABLE records(value)")
        first.execute("INSERT INTO records VALUES(1)")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.execute("INSERT INTO records VALUES(2)")
        first.close()
        second.execute("INSERT INTO records VALUES(2)")
        second.commit()
        assert second.execute("SELECT value FROM records").fetchall() == [(2,)]
    finally:
        first.close()
        second.close()


def test_rows_named_parameters_and_backup_are_preserved(writer, tmp_path):
    path, copy = tmp_path / "source.sqlite", tmp_path / "copy.sqlite"
    with sqlite_writer.connect(path) as db:
        db.execute("CREATE TABLE records(value PRIMARY KEY, detail)")
        db.executemany("INSERT INTO records VALUES(?,?)", [(1, "日本語"), (2, "\nline\n")])
    with sqlite_writer.connect(path) as db, sqlite_writer.connect(copy) as target:
        db.backup(target)
        target.row_factory = sqlite3.Row
        row = target.execute("SELECT * FROM records WHERE value=:value", {"value": 1}).fetchone()
        assert row[0] == row["VALUE"] == 1
        assert dict(row) == {"value": 1, "detail": "日本語"}
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite_writer.connect(copy.as_uri() + "?mode=ro", uri=True) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM records")


def test_sqlite_opens_are_centralized():
    root = Path(__file__).parents[1] / "src/docling_desk"
    direct = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if "sqlite3.connect(" in p.read_text() and p.name != "sqlite_writer.py"
    ]
    assert direct == []
