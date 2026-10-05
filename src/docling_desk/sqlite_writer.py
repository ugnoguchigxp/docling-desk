"""SQLite connections owned by one local process, shared by UI and batch clients.

The private Unix socket carries JSON, never executable Python objects. A session
keeps its transaction until commit/rollback; losing the client rolls it back.
Requests are never replayed after a transport failure (a commit may have landed).
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def _runtime() -> Path:
    # Stable across installations, data roots, workspaces, Python versions and TMPDIR.
    root = Path("/tmp") / f"docling-desk-writer-{os.getuid()}-v1"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if root.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise sqlite3.OperationalError("SQLite Writerの接続先が不正です。")
    return root


def _dial(root: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.connect(str(root / "writer.sock"))
        return sock
    except OSError:
        sock.close()
        raise


def _socket() -> socket.socket:
    root = _runtime()
    # Serialize discovery/startup, including simultaneous UI and CLI starts.
    with (root / "startup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _dial(root)
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(str(Path(p or ".").resolve()) for p in sys.path)

        def start():
            return subprocess.Popen(
                [sys.executable, "-m", "docling_desk.sqlite_writer", str(root)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        process = start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                return _dial(root)
            except (FileNotFoundError, ConnectionRefusedError):
                if process.poll() is not None:
                    if process.returncode != 0:
                        break
                    # An idle predecessor may still be releasing its lifetime lock.
                    process = start()
                time.sleep(0.02)
        raise sqlite3.OperationalError("SQLite Writerを起動できませんでした。")


class Row:
    """The integer/name lookup and mapping interface used by sqlite3.Row callers."""

    def __init__(self, columns, values):
        self.columns, self.values = columns, tuple(values)

    def keys(self):
        return self.columns

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                key = next(
                    i for i, name in enumerate(self.columns) if name.casefold() == key.casefold()
                )
            except StopIteration:
                raise IndexError("No item with that key") from None
        return self.values[key]

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)


class Cursor:
    def __init__(self, result, row_factory):
        self.rowcount = result["rowcount"]
        self.lastrowid = result["lastrowid"]
        self.description = result["description"]
        columns = [entry[0] for entry in self.description or []]
        self._rows = iter(
            Row(columns, row) if row_factory is sqlite3.Row else tuple(row)
            for row in result["rows"]
        )

    def fetchone(self):
        return next(self._rows, None)

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return self._rows


class Connection:
    def __init__(self, path, *, timeout=30, isolation_level="", uri=False):
        self.path = str(path) if uri else str(Path(path).resolve())
        self.row_factory = None
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._sock = _socket()
        self._stream = self._sock.makefile("rwb")
        self._closed = False
        try:
            self.writer_pid = self._request(
                "open", path=self.path, timeout=timeout, isolation_level=isolation_level, uri=uri
            )["pid"]
        except BaseException:
            self.close()
            raise

    def _request(self, operation, **values):
        if self._closed or self._pid != os.getpid():
            raise sqlite3.ProgrammingError("SQLite Writer接続は閉じているか別プロセスのものです。")
        with self._lock:
            try:
                self._stream.write((json.dumps({"op": operation, **values}) + "\n").encode())
                self._stream.flush()
                response = self._stream.readline()
                if not response:
                    raise OSError("Writer disconnected")
                result = json.loads(response)
            except (OSError, ValueError) as exc:
                self.close()
                raise sqlite3.OperationalError(
                    "SQLite Writerとの通信が切れました。処理結果を確認してから再実行してください。"
                ) from exc
        if "error" in result:
            cls = getattr(sqlite3, result["error"], sqlite3.OperationalError)
            if not isinstance(cls, type) or not issubclass(cls, sqlite3.Error):
                cls = sqlite3.OperationalError
            raise cls(result["message"])
        return result

    def execute(self, sql, parameters=()):
        return Cursor(self._request("execute", sql=sql, parameters=parameters), self.row_factory)

    def executemany(self, sql, parameters):
        return Cursor(
            self._request("executemany", sql=sql, parameters=list(parameters)), self.row_factory
        )

    def executescript(self, sql):
        self._request("executescript", sql=sql)

    def commit(self):
        self._request("commit")

    def rollback(self):
        self._request("rollback")

    def backup(self, target):
        if target._closed or target._pid != os.getpid():
            raise sqlite3.ProgrammingError("バックアップ先の接続が閉じています。")
        self._request("backup", target=target.path)

    def close(self):
        if not self._closed:
            self._closed = True
            # shutdown wakes the server even if another thread is awaiting a reply.
            if self._pid == os.getpid():
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            try:
                self._stream.close()
            except OSError:
                # Buffered writes may fail again while closing a disconnected
                # stream. Preserve the original transport error and never replay.
                pass
            finally:
                self._sock.close()

    def __del__(self):
        if hasattr(self, "_closed"):
            self.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if not self._closed:
                if kind is None:
                    self.commit()
                else:
                    self.rollback()
        finally:
            self.close()


connect = Connection


def _session(sock, writer_lock):
    db, held = None, False
    timeout = 30
    readonly = False
    try:
        with sock, sock.makefile("rwb") as stream:
            for line in stream:
                request = json.loads(line)
                operation = request["op"]
                if operation == "open":
                    readonly = request["uri"] and parse_qs(urlsplit(request["path"]).query).get(
                        "mode"
                    ) == ["ro"]
                # The same thread owns the lock for the entire SQLite transaction.
                if not held and (not readonly or operation == "backup"):
                    timeout = request.get("timeout", timeout)
                    held = writer_lock.acquire(timeout=timeout)
                    if not held:
                        stream.write(
                            b'{"error":"OperationalError","message":"database is locked"}\n'
                        )
                        stream.flush()
                        continue
                try:
                    if operation == "open" and db is None:
                        db = sqlite3.connect(
                            request["path"],
                            timeout=request["timeout"],
                            isolation_level=request["isolation_level"],
                            uri=request["uri"],
                        )
                        result = {"pid": os.getpid()}
                    elif operation in {"execute", "executemany"}:
                        cursor = getattr(db, operation)(request["sql"], request["parameters"])
                        result = {
                            "rows": cursor.fetchall(),
                            "description": cursor.description,
                            "rowcount": cursor.rowcount,
                            "lastrowid": cursor.lastrowid,
                        }
                        cursor.close()
                    elif operation == "executescript":
                        db.executescript(request["sql"])
                        result = {}
                    elif operation in {"commit", "rollback"}:
                        getattr(db, operation)()
                        result = {}
                    elif operation == "backup":
                        if db.in_transaction:
                            raise sqlite3.OperationalError(
                                "トランザクション中はバックアップできません。"
                            )
                        with closing(sqlite3.connect(request["target"], uri=True)) as target:
                            source_file = db.execute("PRAGMA database_list").fetchone()[2]
                            target_file = target.execute("PRAGMA database_list").fetchone()[2]
                            if source_file == target_file:
                                raise sqlite3.OperationalError("同じDBへバックアップできません。")
                            db.backup(target)
                        result = {}
                    else:
                        raise sqlite3.ProgrammingError("Unsupported Writer operation")
                except sqlite3.Error as exc:
                    result = {"error": type(exc).__name__, "message": str(exc)}
                finally:
                    if held and (db is None or not db.in_transaction):
                        writer_lock.release()
                        held = False
                stream.write((json.dumps(result) + "\n").encode())
                stream.flush()
    except (OSError, ValueError):
        pass
    finally:
        if db is not None:
            db.close()  # SQLite rolls back on EOF, including an abruptly killed client.
        if held:
            writer_lock.release()


def serve(root: Path):
    with (root / "owner.lock").open("a") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        path = root / "writer.sock"
        path.unlink(missing_ok=True)
        writer_lock = threading.Lock()
        threads = []
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            listener.listen(128)
            listener.settimeout(1)
            idle_since = time.monotonic()
            try:
                while True:
                    threads = [thread for thread in threads if thread.is_alive()]
                    try:
                        sock, _ = listener.accept()
                    except TimeoutError:
                        if threads:
                            idle_since = time.monotonic()
                        elif time.monotonic() - idle_since >= 30:
                            # Startup discovery cannot connect while the listener closes.
                            with (root / "startup.lock").open("a") as startup:
                                fcntl.flock(startup, fcntl.LOCK_EX)
                                # A client may have connected just before discovery was locked.
                                listener.settimeout(0)
                                try:
                                    sock, _ = listener.accept()
                                except BlockingIOError:
                                    listener.close()
                                    path.unlink(missing_ok=True)
                                    return
                                finally:
                                    if listener.fileno() >= 0:
                                        listener.settimeout(1)
                            idle_since = time.monotonic()
                            thread = threading.Thread(
                                target=_session, args=(sock, writer_lock), daemon=True
                            )
                            threads.append(thread)
                            thread.start()
                        continue
                    idle_since = time.monotonic()
                    thread = threading.Thread(
                        target=_session, args=(sock, writer_lock), daemon=True
                    )
                    threads.append(thread)
                    thread.start()
            finally:
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
