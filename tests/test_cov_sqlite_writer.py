"""In-process coverage for the SQLite writer client, session, and listener."""

import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from docling_desk import sqlite_writer


def short_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="covw-", dir="/tmp"))


class Node:
    def __init__(self, *, symlink=False, uid=None, mode=0o700):
        self.symlink = symlink
        self.uid = os.getuid() if uid is None else uid
        self.mode = mode

    def __truediv__(self, _other):
        return self

    def mkdir(self, **_kwargs):
        return None

    def is_symlink(self):
        return self.symlink

    def lstat(self):
        return os.stat_result((self.mode, 0, 0, 0, self.uid, 0, 0, 0, 0, 0))


def test_runtime_rejects_and_accepts(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_writer, "Path", lambda *_args, **_kwargs: Node(symlink=True))
    with pytest.raises(sqlite3.OperationalError, match="接続先"):
        sqlite_writer._runtime()
    monkeypatch.setattr(sqlite_writer, "Path", lambda *_args, **_kwargs: Node(uid=os.getuid() + 1))
    with pytest.raises(sqlite3.OperationalError, match="接続先"):
        sqlite_writer._runtime()
    monkeypatch.setattr(sqlite_writer, "Path", lambda *_args, **_kwargs: Node(mode=0o755))
    with pytest.raises(sqlite3.OperationalError, match="接続先"):
        sqlite_writer._runtime()

    real = Path

    def routed(value, *args):
        if value == "/tmp":
            return real(tmp_path)
        return real(value, *args)

    monkeypatch.setattr(sqlite_writer, "Path", routed)
    root = sqlite_writer._runtime()
    assert root.is_dir()
    assert root.parent == tmp_path


def test_dial_closes_on_failure():
    root = short_dir()
    try:
        with pytest.raises(FileNotFoundError):
            sqlite_writer._dial(root)
    finally:
        shutil.rmtree(root)


def test_socket_dials_or_reports_startup_failures(monkeypatch):
    root = short_dir()
    os.chmod(root, 0o700)
    monkeypatch.setattr(sqlite_writer, "_runtime", lambda: root)
    monkeypatch.setattr(
        sqlite_writer, "_dial", lambda _root: (_ for _ in ()).throw(OSError("perm"))
    )
    with pytest.raises(OSError, match="perm"):
        sqlite_writer._socket()

    client, server = socket.socketpair()
    server.close()
    monkeypatch.setattr(sqlite_writer, "_dial", lambda _root: client)
    assert sqlite_writer._socket() is client
    client.close()

    class Dead:
        def __init__(self, code):
            self.returncode = code
            self.started = 0

        def poll(self):
            return self.returncode

    dead = Dead(1)
    monkeypatch.setattr(sqlite_writer.subprocess, "Popen", lambda *_args, **_kwargs: dead)
    monkeypatch.setattr(
        sqlite_writer, "_dial", lambda _root: (_ for _ in ()).throw(ConnectionRefusedError())
    )
    with pytest.raises(sqlite3.OperationalError, match="起動できません"):
        sqlite_writer._socket()

    state = {"n": 0, "sock": None}

    class Restart:
        def __init__(self):
            state["n"] += 1
            self.n = state["n"]
            self.returncode = 0

        def poll(self):
            return 0 if self.n == 1 else None

    def dial(_root):
        if state["n"] >= 2:
            left, right = socket.socketpair()
            right.close()
            state["sock"] = left
            return left
        raise FileNotFoundError(str(root / "writer.sock"))

    monkeypatch.setattr(sqlite_writer.subprocess, "Popen", lambda *_args, **_kwargs: Restart())
    monkeypatch.setattr(sqlite_writer, "_dial", dial)
    assert sqlite_writer._socket() is state["sock"]
    state["sock"].close()

    clock = {"t": 1000.0}

    def monotonic():
        clock["t"] += 3
        return clock["t"]

    monkeypatch.setattr(sqlite_writer.time, "monotonic", monotonic)
    monkeypatch.setattr(sqlite_writer.subprocess, "Popen", lambda *_args, **_kwargs: Dead(None))
    monkeypatch.setattr(
        sqlite_writer, "_dial", lambda _root: (_ for _ in ()).throw(ConnectionRefusedError())
    )
    try:
        with pytest.raises(sqlite3.OperationalError, match="起動できません"):
            sqlite_writer._socket()
    finally:
        shutil.rmtree(root, ignore_errors=True)


class Session:
    def __init__(self, lock=None):
        self.lock = lock or threading.Lock()
        self.client, server = socket.socketpair()
        self.thread = threading.Thread(
            target=sqlite_writer._session, args=(server, self.lock), daemon=True
        )
        self.thread.start()
        self.stream = self.client.makefile("rwb")

    def call(self, **request):
        self.stream.write((json.dumps(request) + "\n").encode())
        self.stream.flush()
        line = self.stream.readline()
        if not line:
            return None
        return json.loads(line)

    def raw(self, payload: bytes):
        self.stream.write(payload)
        self.stream.flush()

    def close(self):
        self.stream.close()
        self.client.close()
        self.thread.join(timeout=2)


def test_session_statements_backup_and_errors(tmp_path):
    source = tmp_path / "source.sqlite"
    target = tmp_path / "target.sqlite"
    db = sqlite3.connect(source)
    db.execute("CREATE TABLE records(value INTEGER PRIMARY KEY, detail TEXT)")
    db.execute("INSERT INTO records VALUES (1, '既存')")
    db.commit()
    db.close()
    sqlite3.connect(target).close()

    session = Session()
    opened = session.call(
        op="open", path=str(source), timeout=1, isolation_level="DEFERRED", uri=False
    )
    assert opened["pid"] == os.getpid()
    created = session.call(op="execute", sql="CREATE TABLE extra(value)", parameters=[])
    assert created["description"] is None
    session.call(op="executescript", sql="INSERT INTO extra VALUES (1);")
    many = session.call(
        op="executemany",
        sql="INSERT INTO records(value, detail) VALUES (?, ?)",
        parameters=[[2, "二"], [3, "三"]],
    )
    assert many["rowcount"]
    session.call(op="commit")
    session.call(op="execute", sql="BEGIN IMMEDIATE", parameters=[])
    session.call(op="execute", sql="UPDATE records SET detail='変更' WHERE value=1", parameters=[])
    denied = session.call(op="backup", target=target.as_uri())
    assert denied["error"] == "OperationalError"
    session.call(op="rollback")
    copied = session.call(op="backup", target=target.as_uri())
    assert copied == {}
    same = session.call(op="backup", target=source.as_uri())
    assert "同じDB" in same["message"]
    bad = session.call(op="execute", sql="SELECT missing FROM records", parameters=[])
    assert bad["error"] == "OperationalError"
    unsupported = session.call(op="nope")
    assert unsupported["error"] == "ProgrammingError"
    session.close()

    check = sqlite3.connect(source)
    assert check.execute("SELECT detail FROM records WHERE value=1").fetchone()[0] == "既存"
    check.close()
    copied_db = sqlite3.connect(target)
    assert copied_db.execute("SELECT count(*) FROM records").fetchone()[0] == 3
    copied_db.close()

    readonly = Session()
    opened = readonly.call(
        op="open",
        path=f"{source.as_uri()}?mode=rw",
        timeout=1,
        isolation_level=None,
        uri=True,
    )
    assert "error" not in opened
    readonly.close()

    held = threading.Lock()
    assert held.acquire(blocking=False)
    blocked = Session(held)
    locked = blocked.call(
        op="open",
        path=f"{source.as_uri()}?mode=ro",
        timeout=0,
        isolation_level="",
        uri=True,
    )
    assert "error" not in locked
    selected = blocked.call(op="execute", sql="SELECT value FROM records", parameters=[])
    assert selected["rows"]
    failed = blocked.call(op="backup", target=target.as_uri())
    assert failed["message"] == "database is locked"
    held.release()
    copied = blocked.call(op="backup", target=(tmp_path / "ro-copy.sqlite").as_uri())
    assert copied == {}
    blocked.close()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(threading, "excepthook", lambda _args: None)
    try:
        crashing = Session()
        crashing.raw(b"not-json\n")
        crashing.thread.join(timeout=2)
        assert not crashing.thread.is_alive()

        lock = threading.Lock()
        early = Session(lock)
        early.raw(b'{"op":"execute","sql":"SELECT 1","parameters":[]}\n')
        early.thread.join(timeout=2)
        assert not early.thread.is_alive()
        assert lock.acquire(blocking=False)
        lock.release()
    finally:
        monkeypatch.undo()


def test_session_lock_timeout_and_rollback(tmp_path):
    path = tmp_path / "tx.sqlite"
    lock = threading.Lock()
    first = Session(lock)
    assert "error" not in first.call(
        op="open", path=str(path), timeout=1, isolation_level="DEFERRED", uri=False
    )
    first.call(op="execute", sql="CREATE TABLE records(value)", parameters=[])
    first.call(op="execute", sql="BEGIN IMMEDIATE", parameters=[])
    first.call(op="execute", sql="INSERT INTO records VALUES (1)", parameters=[])
    second = Session(lock)
    refused = second.call(
        op="open", path=str(path), timeout=0, isolation_level="DEFERRED", uri=False
    )
    assert refused["message"] == "database is locked"
    first.call(op="rollback")
    first.close()
    opened = second.call(
        op="open", path=str(path), timeout=1, isolation_level="DEFERRED", uri=False
    )
    assert opened["pid"]
    rows = second.call(op="execute", sql="SELECT count(*) FROM records", parameters=[])
    assert rows["rows"] == [[0]]
    second.close()


def responses(items, monkeypatch):
    client, server = socket.socketpair()

    def run():
        with server, server.makefile("rwb") as stream:
            for item in items:
                if not stream.readline():
                    return
                if item is None:
                    return
                if isinstance(item, bytes):
                    stream.write(item)
                else:
                    stream.write((json.dumps(item) + "\n").encode())
                stream.flush()

    threading.Thread(target=run, daemon=True).start()
    monkeypatch.setattr(sqlite_writer, "_socket", lambda: client)
    return sqlite_writer.connect(":memory:")


def test_client_cursor_errors_and_close(tmp_path, monkeypatch):
    db = responses([{"pid": 42}], monkeypatch)
    assert db.writer_pid == 42
    monkeypatch.undo()

    rows = [
        {"pid": 7},
        {
            "rows": [[1, "日本語"]],
            "description": [["value", None, None, None, None, None, None]],
            "rowcount": 1,
            "lastrowid": 1,
        },
        {"rows": [], "description": None, "rowcount": -1, "lastrowid": None},
        {"rows": [], "description": None, "rowcount": 2, "lastrowid": 2},
        {},
        {},
        {},
    ]
    db = responses(rows, monkeypatch)
    db.row_factory = sqlite3.Row
    cursor = db.execute("SELECT value, detail FROM records")
    row = cursor.fetchone()
    assert row["VALUE"] == 1 and row[0] == 1
    assert list(row) == [1, "日本語"] and len(row) == 2
    with pytest.raises(IndexError, match="No item"):
        row["missing"]
    assert dict(row)["value"] == 1
    assert cursor.fetchone() is None
    empty = db.execute("SELECT 1")
    assert empty.fetchall() == []
    db.executemany("INSERT INTO records VALUES (?, ?)", [(2, "b")])
    db.executescript("SELECT 1;")
    db.commit()
    db.rollback()
    monkeypatch.undo()

    db = responses([{"pid": 1}, {"error": "IntegrityError", "message": "constraint"}], monkeypatch)
    with pytest.raises(sqlite3.IntegrityError, match="constraint"):
        db.execute("INSERT")
    monkeypatch.undo()

    db = responses([{"pid": 1}, {"error": "sqlite_version", "message": "nope"}], monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="nope"):
        db.execute("SELECT 1")
    monkeypatch.undo()

    db = responses([{"pid": 1}, {"error": "Row", "message": "row"}], monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="row"):
        db.execute("SELECT 1")
    monkeypatch.undo()

    db = responses([{"pid": 1}, {"error": "NotARealError", "message": "missing"}], monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="missing"):
        db.execute("SELECT 1")
    monkeypatch.undo()

    db = responses([{"pid": 1}, b"not-json\n"], monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="通信"):
        db.execute("SELECT 1")
    monkeypatch.undo()

    db = responses([{"pid": 1}, None], monkeypatch)
    with pytest.raises(sqlite3.OperationalError, match="通信"):
        db.execute("SELECT 1")
    monkeypatch.undo()

    client, server = socket.socketpair()

    def drop():
        with server, server.makefile("rb") as stream:
            stream.readline()

    threading.Thread(target=drop, daemon=True).start()
    monkeypatch.setattr(sqlite_writer, "_socket", lambda: client)
    with pytest.raises(sqlite3.OperationalError, match="通信"):
        sqlite_writer.connect(tmp_path / "dead.sqlite")

    def fail(self, *_args, **_kwargs):
        raise RuntimeError("open failed")

    left, right = socket.socketpair()
    right.close()
    monkeypatch.setattr(sqlite_writer, "_socket", lambda: left)
    monkeypatch.setattr(sqlite_writer.Connection, "_request", fail)
    with pytest.raises(RuntimeError, match="open failed"):
        sqlite_writer.connect(tmp_path / "boom.sqlite")
    monkeypatch.undo()

    path = tmp_path / "live.sqlite"
    root = short_dir()
    os.chmod(root, 0o700)
    monkeypatch.setattr(sqlite_writer, "_runtime", lambda: root)
    server_thread = threading.Thread(target=sqlite_writer.serve, args=(root,), daemon=True)
    server_thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            probe = sqlite_writer._dial(root)
            probe.close()
            break
        except OSError:
            time.sleep(0.01)
    else:
        raise AssertionError("writer did not start")

    with sqlite_writer.connect(path) as database:
        database.execute("CREATE TABLE records(value)")
        database.execute("INSERT INTO records VALUES (1)")
    with pytest.raises(RuntimeError, match="boom"):
        with sqlite_writer.connect(path) as database:
            database.execute("INSERT INTO records VALUES (2)")
            raise RuntimeError("boom")
    with sqlite_writer.connect(path) as database:
        assert database.execute("SELECT value FROM records").fetchall() == [(1,)]
        other = sqlite_writer.connect(tmp_path / "other.sqlite")
        database.backup(other)
        other._pid = -1
        with pytest.raises(sqlite3.ProgrammingError, match="閉じて"):
            database.backup(other)
        other._pid = os.getpid()
        other.close()
        with pytest.raises(sqlite3.ProgrammingError, match="閉じて"):
            database.backup(other)
        database.close()
        with pytest.raises(sqlite3.ProgrammingError, match="閉じている"):
            database.execute("SELECT 1")
        database.close()

    fresh = sqlite_writer.connect(path, uri=False)
    fresh._pid = -1
    with pytest.raises(sqlite3.ProgrammingError):
        fresh.execute("SELECT 1")
    fresh.close()

    shutdown = sqlite_writer.connect(path)
    raw_sock = shutdown._sock

    class Sock:
        def shutdown(self, *_args, **_kwargs):
            raise OSError("shutdown")

        def close(self):
            raw_sock.close()

    shutdown._sock = Sock()
    shutdown.close()
    sqlite_writer.Connection.__del__(object())
    lingering = sqlite_writer.connect(path)
    sqlite_writer.Connection.__del__(lingering)
    sqlite_writer.Connection.__del__(lingering)

    uri = sqlite_writer.connect(path.as_uri() + "?mode=ro", uri=True)
    assert uri.path.startswith("file:")
    uri.close()
    plain = sqlite_writer.connect(path, uri=False)
    assert plain.path == str(path.resolve())
    plain.close()


def test_serve_accepts_resets_idle_and_stops(monkeypatch):
    root = short_dir()
    (root / "owner.lock").touch()
    fd = os.open(root / "owner.lock", os.O_RDWR)
    fcntl_lock = __import__("fcntl")
    fcntl_lock.flock(fd, fcntl_lock.LOCK_EX | fcntl_lock.LOCK_NB)
    try:
        sqlite_writer.serve(root)
    finally:
        fcntl_lock.flock(fd, fcntl_lock.LOCK_UN)
        os.close(fd)

    clock = {"t": 0.0}
    monkeypatch.setattr(sqlite_writer.time, "monotonic", lambda: clock["t"])
    real_socket = socket.socket
    plan = {"n": 0, "nb": 0, "client": None}

    class Listener(real_socket):
        def accept(self):
            if self.gettimeout() == 0:
                plan["nb"] += 1
                if plan["nb"] == 1:
                    left, right = socket.socketpair()
                    right.close()
                    return left, None
                raise BlockingIOError()
            plan["n"] += 1
            step = plan["n"]
            if step == 2:
                left, right = socket.socketpair()
                plan["client"] = left
                return right, None
            if step == 4 and plan["client"] is not None:
                plan["client"].close()
                time.sleep(0.1)
            if step >= 5:
                clock["t"] += 50
            raise TimeoutError()

    monkeypatch.setattr(sqlite_writer.socket, "socket", Listener)
    sqlite_writer.serve(root)
    assert plan["n"] >= 5
    assert plan["nb"] >= 2
    assert not (root / "writer.sock").exists()


def test_module_main_serves_until_idle(monkeypatch):
    import runpy

    root = short_dir()
    monkeypatch.setattr(sys, "argv", ["sqlite_writer", str(root)])
    real = time.monotonic
    started = real()
    monkeypatch.setattr(time, "monotonic", lambda: started + (real() - started) * 40)
    box = {}

    def run():
        try:
            runpy.run_module("docling_desk.sqlite_writer", run_name="__main__")
        except BaseException as exc:  # pragma: no cover - failure signal
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "error" not in box
