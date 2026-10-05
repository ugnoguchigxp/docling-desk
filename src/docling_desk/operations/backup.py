"""Consistent local backup and restore. Does not contact embedding, OCR, or other networks."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from docling_desk.sqlite_writer import connect
from docling_desk.storage import knowledge_database

LOCK_NAME = ".write-lock"
REFUSAL = "書き込み中のためバックアップを拒否しました。変換または索引の完了を確認してから再実行してください。"
_GUARDS_LOCK = threading.Lock()
_GUARDS: dict[str, threading.Lock] = {}
_OWNERS: dict[str, dict] = {}
_HELD = threading.local()
SKIP_NAMES = {".env", "credentials.json"}
SKIP_SUFFIXES = {".pem", ".key", ".wal", ".shm", ".sqlite-wal", ".sqlite-shm"}
SECRET_STEMS = {"apikey", "secret", "credential", "credentials", "password"}
_NAME_SUFFIXES = {"txt", "json", "pem", "key", "yml", "yaml", "env", "md"}


def _secret_name(name: str) -> bool:
    """Skip credential file names without dropping ordinary words such as keyboard."""
    tokens = [part for part in re.split(r"[^a-z0-9]+", name.lower()) if part]
    if tokens and tokens[-1] in _NAME_SUFFIXES:
        tokens = tokens[:-1]
    if not tokens:
        return False
    joined = "".join(tokens)
    token_stems = SECRET_STEMS - {"password"}
    if joined in SECRET_STEMS or any(token in token_stems for token in tokens):
        return True
    return any(tokens[index] + tokens[index + 1] == "apikey" for index in range(len(tokens) - 1))


def _skip_file(path: Path, *, wiki_content: bool = False) -> bool:
    name = path.name
    return (
        path.is_symlink()
        or not path.is_file()
        or name in SKIP_NAMES
        or path.suffix in SKIP_SUFFIXES
        or name.endswith(("-wal", "-shm", "-journal"))
        or (not wiki_content and _secret_name(name))
    )


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = connect(source)
    dst = connect(destination)
    try:
        src.backup(dst)
        # A copied WAL database must not leave a sidecar that restore would replay.
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        src.close()
        dst.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(destination) + suffix)
        if sidecar.is_file():
            sidecar.unlink()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload_files(data: Path):
    for path in sorted(data.rglob("*")):
        relative = path.relative_to(data)
        wiki_content = relative.parts[:3] == (
            "content",
            "wiki",
            "revisions",
        ) and path.suffix.lower() in {".md", ".markdown", ".csv"}
        if _skip_file(path, wiki_content=wiki_content) or relative.as_posix() in {
            "knowledge/local.sqlite",
            "runtime/knowledge/local.sqlite",
        }:
            continue
        yield path, relative


def _wiki_rows(database: Path) -> list[dict]:
    if not database.is_file():
        return []
    _integrity(database)
    with connect(database) as db:
        return [
            {
                "id": row[0],
                "path": row[1],
                "revision": row[2],
                "deleted": row[3],
                "body_sha256": hashlib.sha256((row[4] or "").encode()).hexdigest(),
            }
            for row in db.execute(
                "SELECT id,path,revision,deleted,body FROM sources WHERE kind='wiki' ORDER BY id"
            )
        ]


def _integrity(database: Path) -> None:
    try:
        connection = connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("Wiki正本の整合性確認に失敗しました。") from exc
    if not row or row[0] != "ok":
        raise ValueError("Wiki正本の整合性確認に失敗しました。")


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _parse_record(text: str) -> dict:
    try:
        record = json.loads(text)
    except (json.JSONDecodeError, UnicodeError):
        return {}
    return record if isinstance(record, dict) else {}


def _key(data: Path) -> str:
    return os.path.normcase(str(data.resolve()))


def _guard(key: str) -> threading.Lock:
    with _GUARDS_LOCK:
        guard = _GUARDS.get(key)
        if guard is None:
            guard = threading.Lock()
            _GUARDS[key] = guard
        return guard


def _held() -> dict[str, str]:
    paths = getattr(_HELD, "paths", None)
    if paths is None:
        paths = {}
        _HELD.paths = paths
    return paths


def _idle() -> dict:
    return {"state": "idle", "purpose": None, "pid": None}


def _active(purpose: object, pid: object) -> dict:
    return {"state": "active", "purpose": purpose, "pid": pid}


def _legacy_holder(data: Path) -> dict | None:
    """A directory lock is the previous protocol. A dead one is removed."""
    path = data / LOCK_NAME
    if not path.is_dir():
        return None
    owner = path / "owner.json"
    try:
        record = _parse_record(owner.read_text(encoding="utf-8")) if owner.is_file() else {}
    except (OSError, UnicodeError):
        record = {}
    if _pid_alive(record.get("pid")):
        return _active(record.get("purpose"), record.get("pid"))
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = 999
    if not record and age < 2:
        return _active("starting", None)
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        return _active(record.get("purpose") or "starting", record.get("pid"))
    return None


def _flock(handle, blocking: bool) -> bool:
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(handle.fileno(), flags)
    except OSError as exc:
        if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
            return False
        raise
    return True


def writer_status(data: Path) -> dict:
    """Live conversion or index writer. A stopped process does not keep the lock."""
    if not data.is_dir():
        return _idle()
    key = _key(data)
    current = _held().get(key)
    if current is not None:
        return _active(current, os.getpid())
    legacy = _legacy_holder(data)
    if legacy is not None:
        return legacy
    path = data / LOCK_NAME
    if not path.is_file():
        return _idle()
    guard = _guard(key)
    if not guard.acquire(blocking=False):
        owner = _OWNERS.get(key) or {}
        return _active(owner.get("purpose") or "index", owner.get("pid"))
    handle = None
    try:
        handle = path.open("a+")
        if not _flock(handle, blocking=False):
            handle.seek(0)
            record = _parse_record(handle.read())
            return _active(record.get("purpose"), record.get("pid"))
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return _idle()
    finally:
        if handle is not None:
            handle.close()
        guard.release()


@contextmanager
def exclusive_write(data: Path, purpose: str, wait: float | None = None):
    """Hold the data-directory lock for one conversion, index update, or backup.

    Writers wait until the current holder finishes. Backup passes wait=0 and refuses.
    """
    if not data.is_dir():
        raise ValueError("保存ディレクトリがありません。")
    key = _key(data)
    current = _held().get(key)
    if current is not None:
        if purpose == "backup" and current != "backup":
            raise ValueError(REFUSAL)
        yield
        return
    deadline = None if wait is None else time.monotonic() + max(wait, 0)

    def expired() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    guard = _guard(key)
    remaining = -1 if deadline is None else max(0, deadline - time.monotonic())
    if not guard.acquire(timeout=remaining):
        raise ValueError(REFUSAL)
    handle = None
    try:
        while True:
            legacy = _legacy_holder(data)
            if legacy is not None:
                if expired():
                    raise ValueError(REFUSAL)
                time.sleep(0.02)
                continue
            handle = (data / LOCK_NAME).open("a+")
            if _flock(handle, blocking=False):
                break
            handle.close()
            handle = None
            if expired():
                raise ValueError(REFUSAL)
            time.sleep(0.02)
        payload = json.dumps({"pid": os.getpid(), "purpose": purpose}, ensure_ascii=False)
        handle.seek(0)
        handle.truncate()
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        _held()[key] = purpose
        _OWNERS[key] = {"pid": os.getpid(), "purpose": purpose}
        try:
            yield
        finally:
            _held().pop(key, None)
            _OWNERS.pop(key, None)
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()
        guard.release()


def backup_local(data: Path, archive: Path) -> dict:
    if archive.exists():
        raise ValueError("バックアップ先は未作成のディレクトリにしてください。")
    with exclusive_write(data, "backup", wait=0):
        archive.mkdir()
        try:
            return _backup_into(data, archive)
        except Exception:
            shutil.rmtree(archive, ignore_errors=True)
            raise


def _backup_into(data: Path, archive: Path) -> dict:
    database = knowledge_database(data)
    if not database.exists():
        database = data / "knowledge" / "local.sqlite"
    if database.is_symlink():
        raise ValueError("Wikiの正本データベースがありません。")
    if database.is_file():
        _backup_sqlite(database, archive / "knowledge.sqlite")
        _integrity(archive / "knowledge.sqlite")
    files = []
    for path, relative in _payload_files(data):
        target = archive / "files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".sqlite":
            _backup_sqlite(path, target)
        else:
            shutil.copy2(path, target)
        files.append({"path": relative.as_posix(), "sha256": _sha256(target)})
    wiki = _wiki_rows(archive / "knowledge.sqlite")
    manifest = {
        "wiki": wiki,
        "files": files,
        "database_path": database.relative_to(data).as_posix(),
    }
    (archive / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _safe_target(root: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or relative.startswith("/")
        or "\\" in relative
        or "\x00" in relative
    ):
        raise ValueError("復元対象のパスが不正です。")
    parts = Path(relative).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("復元対象のパスが不正です。")
    target = (root / relative).resolve()
    if root.resolve() != target and root.resolve() not in target.parents:
        raise ValueError("復元対象のパスが不正です。")
    if target.is_symlink():
        raise ValueError("復元対象のパスが不正です。")
    return target


def _manifest_items(manifest: dict) -> tuple[list[dict], list[dict]]:
    if not isinstance(manifest, dict):
        raise ValueError("manifestが不正です。")
    files, wiki = manifest.get("files"), manifest.get("wiki")
    if not isinstance(files, list) or not isinstance(wiki, list):
        raise ValueError("manifestが不正です。")
    return files, wiki


def restore_local(archive: Path, destination: Path) -> dict:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("復元先は空である必要があります。")
    created = not destination.exists()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        try:
            manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("manifestが不正です。") from exc
        return _restore_into(archive, destination, manifest)
    except Exception:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        else:
            for child in list(destination.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        raise


def _restore_into(archive: Path, destination: Path, manifest: dict) -> dict:
    files, wiki = _manifest_items(manifest)
    file_root = destination.resolve()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("manifestが不正です。")
        if not isinstance(item.get("sha256"), str):
            raise ValueError("manifestが不正です。")
        source = _safe_target((archive / "files").resolve(), item["path"])
        if not source.is_file() or _sha256(source) != item["sha256"]:
            raise ValueError("バックアップ内のファイルがmanifestと一致しません。")
        target = _safe_target(file_root, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if _sha256(target) != item["sha256"]:
            raise ValueError("復元したファイルのハッシュが一致しません。")
    archived = archive / "knowledge.sqlite"
    if wiki and not archived.is_file():
        raise ValueError("バックアップにWiki正本がありません。")
    database = _safe_target(destination, manifest.get("database_path", "knowledge/local.sqlite"))
    restored = {}
    if archived.is_file():
        _integrity(archived)
        _backup_sqlite(archived, database)
        _integrity(database)
        with connect(database) as db:
            restored = {
                row[0]: (row[1], row[2], hashlib.sha256((row[3] or "").encode()).hexdigest())
                for row in db.execute(
                    "SELECT id,revision,deleted,body FROM sources WHERE kind='wiki'"
                )
            }
    expected = {}
    for item in wiki:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or item["id"] in expected
        ):
            raise ValueError("manifestが不正です。")
        expected[item["id"]] = (item.get("revision"), item.get("deleted"), item.get("body_sha256"))
    if restored != expected:
        raise ValueError("復元したWiki正本がmanifestと一致しません。")
    return manifest


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Backup or restore the local Wiki and documents")
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("backup")
    save.add_argument("--data", type=Path, required=True)
    save.add_argument("--output", type=Path, required=True)
    load = sub.add_parser("restore")
    load.add_argument("--archive", type=Path, required=True)
    load.add_argument("--destination", type=Path, required=True)
    check = sub.add_parser("status")
    check.add_argument("--data", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            state = writer_status(args.data)
            print(state["state"] if state["state"] == "idle" else f"active {state['purpose']}")
            return 0 if state["state"] == "idle" else 2
        if args.command == "backup":
            result = backup_local(args.data, args.output)
        else:
            result = restore_local(args.archive, args.destination)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
