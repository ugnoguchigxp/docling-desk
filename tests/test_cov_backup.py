"""Branch coverage for backup, retention, faults, verify, and distribution evidence."""

import errno
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from docling_desk.operations import backup, distribution_evidence, faults, retention, verify

REV = "ab" * 20


@pytest.fixture(autouse=True)
def backup_uses_sqlite(monkeypatch):
    monkeypatch.setattr(backup, "connect", sqlite3.connect)


def init_wiki(path: Path, body="hello") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE sources (id TEXT, path TEXT, revision TEXT, deleted INTEGER, body TEXT, kind TEXT)"
    )
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?,?,?)",
        ("id1", "a.md", "r1", 0, body, "wiki"),
    )
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?,?,?)",
        ("id2", "b.md", "r2", 1, None, "wiki"),
    )
    db.commit()
    db.close()


def test_secret_name_and_skip_rules(tmp_path):
    assert backup._secret_name("...") is False
    assert backup._secret_name("txt") is False
    assert backup._secret_name("keyboard") is False
    assert backup._secret_name("keyboard.txt") is False
    assert backup._secret_name("hello-world.txt") is False
    assert backup._secret_name("password.txt") is True
    assert backup._secret_name("pass-word.txt") is True
    assert backup._secret_name("secret") is True
    assert backup._secret_name("my-secret.txt") is True
    assert backup._secret_name("APIKEY") is True
    assert backup._secret_name("api-key-extra.txt") is True

    kept = tmp_path / "keyboard.txt"
    kept.write_text("keep", encoding="utf-8")
    assert backup._skip_file(kept) is False
    assert backup._skip_file(tmp_path) is True
    link = tmp_path / "link.txt"
    link.symlink_to(kept)
    assert backup._skip_file(link) is True
    env = tmp_path / ".env"
    env.write_text("x", encoding="utf-8")
    assert backup._skip_file(env) is True
    pem = tmp_path / "cert.pem"
    pem.write_bytes(b"pem")
    assert backup._skip_file(pem) is True
    for name in ("foo-wal", "foo-shm", "foo-journal", "db.sqlite-wal", "notes.key"):
        path = tmp_path / name
        path.write_bytes(b"x")
        assert backup._skip_file(path) is True
    secret = tmp_path / "my-secret.txt"
    secret.write_text("nope", encoding="utf-8")
    assert backup._skip_file(secret) is True
    wiki_secret = tmp_path / "api-key-extra.md"
    wiki_secret.write_text("wiki", encoding="utf-8")
    assert backup._skip_file(wiki_secret, wiki_content=True) is False


def test_pid_parse_guard_and_held(monkeypatch):
    real_kill = os.kill

    def kill(pid, sig):
        if pid == 11:
            raise PermissionError
        if pid == 12:
            raise ProcessLookupError
        return real_kill(pid, sig)

    monkeypatch.setattr(os, "kill", kill)
    assert backup._pid_alive("no") is False
    assert backup._pid_alive(0) is False
    assert backup._pid_alive(-1) is False
    assert backup._pid_alive(os.getpid()) is True
    assert backup._pid_alive(11) is True
    assert backup._pid_alive(12) is False

    assert backup._parse_record("{") == {}
    assert backup._parse_record("[]") == {}
    assert backup._parse_record('{"purpose": "index"}') == {"purpose": "index"}

    def loads(_text):
        raise UnicodeError("bad")

    monkeypatch.setattr(json, "loads", loads)
    assert backup._parse_record("{}") == {}

    assert backup._guard("same") is backup._guard("same")
    assert backup._guard("same") is not backup._guard("other")
    backup._HELD.paths = None
    first = backup._held()
    assert first == {}
    assert backup._held() is first
    assert backup._idle()["state"] == "idle"
    assert backup._active("index", 3)["purpose"] == "index"


def test_legacy_holder_variants(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    assert backup._legacy_holder(data) is None
    lock = data / backup.LOCK_NAME
    lock.mkdir()
    owner = lock / "owner.json"
    owner.write_bytes(b"\xff")
    os.utime(lock, (time.time() - 100, time.time() - 100))
    assert backup._legacy_holder(data) is None
    assert not lock.exists()

    lock.mkdir()
    owner.write_text("{", encoding="utf-8")
    os.utime(lock, (time.time(), time.time()))
    starting = backup._legacy_holder(data)
    assert starting["purpose"] == "starting"
    assert lock.is_dir()

    owner.write_text(json.dumps({"pid": os.getpid(), "purpose": "convert"}), encoding="utf-8")
    live = backup._legacy_holder(data)
    assert live["purpose"] == "convert"
    assert live["pid"] == os.getpid()

    owner.write_text(json.dumps({"pid": 2**30, "purpose": "index"}), encoding="utf-8")
    os.utime(lock, (time.time() - 50, time.time() - 50))
    assert backup._legacy_holder(data) is None

    lock.mkdir()
    (lock / "owner.json").write_text("{}", encoding="utf-8")
    os.utime(lock, (time.time() - 50, time.time() - 50))
    monkeypatch.setattr(backup.shutil, "rmtree", lambda *_args, **_kwargs: None)
    stuck = backup._legacy_holder(data)
    assert stuck["state"] == "active"
    assert lock.exists()

    assert backup._legacy_holder(data)["state"] == "active"


def test_flock_and_writer_status(tmp_path, monkeypatch):
    handle_path = tmp_path / "lockfile"
    handle_path.write_text("{}", encoding="utf-8")
    real = fcntl.flock

    def locking(fd, op):
        if op & fcntl.LOCK_UN:
            return real(fd, op)
        raise OSError(errno.EAGAIN, "busy")

    monkeypatch.setattr(fcntl, "flock", locking)
    with handle_path.open("a+") as handle:
        assert backup._flock(handle, blocking=False) is False
        monkeypatch.setattr(
            fcntl,
            "flock",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EIO, "io")),
        )
        with pytest.raises(OSError):
            backup._flock(handle, blocking=True)

    monkeypatch.setattr(fcntl, "flock", real)
    missing = tmp_path / "missing"
    assert backup.writer_status(missing)["state"] == "idle"
    data = tmp_path / "data"
    data.mkdir()
    assert backup.writer_status(data)["state"] == "idle"

    legacy = data / backup.LOCK_NAME
    legacy.mkdir()
    (legacy / "owner.json").write_text(
        json.dumps({"pid": os.getpid(), "purpose": "convert"}), encoding="utf-8"
    )
    assert backup.writer_status(data)["purpose"] == "convert"
    legacy.rmdir() if not any(legacy.iterdir()) else None
    for child in legacy.iterdir():
        child.unlink()
    legacy.rmdir()

    lock = data / backup.LOCK_NAME
    lock.write_text(json.dumps({"purpose": "convert", "pid": 4}), encoding="utf-8")
    key = backup._key(data)
    guard = backup._guard(key)
    assert guard.acquire(blocking=False)
    try:
        backup._OWNERS.pop(key, None)
        assert backup.writer_status(data)["purpose"] == "index"
        backup._OWNERS[key] = {"purpose": "sync", "pid": 9}
        assert backup.writer_status(data)["purpose"] == "sync"
    finally:
        backup._OWNERS.pop(key, None)
        guard.release()

    monkeypatch.setattr(backup, "_flock", lambda *_args, **_kwargs: False)
    seen = backup.writer_status(data)
    assert seen["purpose"] == "convert" and seen["pid"] == 4
    lock.write_text("", encoding="utf-8")
    assert backup.writer_status(data)["purpose"] is None

    monkeypatch.setattr(backup, "_flock", lambda *_args, **_kwargs: True)
    assert backup.writer_status(data)["state"] == "idle"

    def boom(self, *args, **kwargs):
        if self.name == backup.LOCK_NAME:
            raise OSError("open")
        return Path.open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", boom)
    with pytest.raises(OSError):
        backup.writer_status(data)


def test_exclusive_write_paths(tmp_path, monkeypatch):
    missing = tmp_path / "absent"
    with pytest.raises(ValueError, match="保存ディレクトリ"):
        with backup.exclusive_write(missing, "index"):
            pass
    data = tmp_path / "data"
    data.mkdir()
    with backup.exclusive_write(data, "index", wait=None):
        assert backup.writer_status(data)["purpose"] == "index"
        with backup.exclusive_write(data, "convert"):
            pass
        with pytest.raises(ValueError, match="拒否"):
            with backup.exclusive_write(data, "backup", wait=0):
                pass
    with backup.exclusive_write(data, "backup", wait=-5):
        with backup.exclusive_write(data, "backup", wait=0):
            pass
    assert backup.writer_status(data)["state"] == "idle"

    key = backup._key(data)
    guard = backup._guard(key)
    assert guard.acquire(blocking=False)
    try:
        with pytest.raises(ValueError, match="拒否"):
            with backup.exclusive_write(data, "backup", wait=0):
                pass
    finally:
        guard.release()

    state = {"n": 0}

    def legacy_once(_data):
        state["n"] += 1
        if state["n"] == 1:
            return backup._active("index", 1)
        return None

    monkeypatch.setattr(backup, "_legacy_holder", legacy_once)
    with backup.exclusive_write(data, "backup", wait=None):
        pass

    monkeypatch.setattr(backup, "_legacy_holder", lambda _data: backup._active("index", 1))
    with pytest.raises(ValueError, match="拒否"):
        with backup.exclusive_write(data, "backup", wait=0):
            pass

    calls = {"n": 0}

    def flaky(_handle, blocking=False):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(backup, "_legacy_holder", lambda _data: None)
    monkeypatch.setattr(backup, "_flock", flaky)
    with backup.exclusive_write(data, "index", wait=1):
        pass
    monkeypatch.setattr(backup, "_flock", lambda *_args, **_kwargs: False)
    with pytest.raises(ValueError, match="拒否"):
        with backup.exclusive_write(data, "index", wait=0):
            pass

    real = fcntl.flock

    def unlock_fails(fd, op):
        if op & fcntl.LOCK_UN:
            raise OSError("unlock")
        return real(fd, op)

    monkeypatch.setattr(backup, "_flock", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(fcntl, "flock", unlock_fails)
    with backup.exclusive_write(data, "index", wait=0):
        pass


def test_backup_payload_restore_and_failures(tmp_path, monkeypatch):
    data = tmp_path / "my data"
    data.mkdir()
    db = data / "runtime" / "knowledge" / "local.sqlite"
    init_wiki(db, "本文")
    (data / "keyboard.txt").write_text("keep", encoding="utf-8")
    notes = data / "notes.sqlite"
    note_db = sqlite3.connect(notes)
    note_db.execute("CREATE TABLE t(x)")
    note_db.commit()
    note_db.close()
    wiki = data / "content" / "wiki" / "revisions"
    wiki.mkdir(parents=True)
    (wiki / "api-key-extra.md").write_text("kept wiki", encoding="utf-8")
    (wiki / "table.csv").write_text("a,b\n", encoding="utf-8")
    (wiki / "api-key-extra.txt").write_text("secret", encoding="utf-8")
    (data / ".env").write_text("SECRET", encoding="utf-8")
    (data / "leak.txt").symlink_to(data / "keyboard.txt")
    archive = tmp_path / "archive out"
    manifest = backup.backup_local(data, archive)
    names = [path.name for path in archive.rglob("*") if path.is_file()]
    assert "keyboard.txt" in names
    assert "api-key-extra.md" in names
    assert "table.csv" in names
    assert "api-key-extra.txt" not in names
    assert ".env" not in names
    assert "local.sqlite" not in names
    assert manifest["database_path"] == "runtime/knowledge/local.sqlite"
    assert {item["id"] for item in manifest["wiki"]} == {"id1", "id2"}
    destination = tmp_path / "restored data"
    restored = backup.restore_local(archive, destination)
    assert restored["wiki"]
    assert (destination / "keyboard.txt").read_text(encoding="utf-8") == "keep"
    assert (
        sqlite3.connect(destination / "runtime/knowledge/local.sqlite")
        .execute("SELECT body FROM sources WHERE id='id2'")
        .fetchone()[0]
        is None
    )

    with pytest.raises(ValueError, match="未作成"):
        backup.backup_local(data, archive)
    with pytest.raises(ValueError, match="空"):
        backup.restore_local(archive, destination)

    def boom(_data, target):
        (target / "partial").write_text("x", encoding="utf-8")
        raise RuntimeError("fail")

    monkeypatch.setattr(backup, "_backup_into", boom)
    failed = tmp_path / "failed"
    with pytest.raises(RuntimeError):
        backup.backup_local(data, failed)
    assert not failed.exists()


def test_backup_database_shapes_and_sidecars(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "note.txt").write_text("n", encoding="utf-8")
    archive = tmp_path / "no-db"
    manifest = backup.backup_local(data, archive)
    assert manifest["wiki"] == []
    assert manifest["database_path"] == "knowledge/local.sqlite"
    assert backup._wiki_rows(archive / "knowledge.sqlite") == []

    fallback = data / "knowledge" / "local.sqlite"
    init_wiki(fallback)
    archive2 = tmp_path / "fallback"
    manifest = backup.backup_local(data, archive2)
    assert manifest["database_path"] == "knowledge/local.sqlite"
    assert (archive2 / "knowledge.sqlite").is_file()

    linked = tmp_path / "linked"
    linked.mkdir()
    target = linked / "runtime" / "knowledge" / "local.sqlite"
    target.parent.mkdir(parents=True)
    target.symlink_to(fallback)
    with pytest.raises(ValueError, match="正本"):
        backup.backup_local(linked, tmp_path / "bad-link")

    src = tmp_path / "src.sqlite"
    dst = tmp_path / "nested" / "dst.sqlite"
    init_wiki(src)
    real_is_file = Path.is_file

    def is_file(self):
        if str(self).endswith(("-wal", "-shm")) and str(dst) in str(self):
            if not real_is_file(self):
                self.write_bytes(b"x")
            return True
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    backup._backup_sqlite(src, dst)
    assert dst.is_file()
    assert not Path(str(dst) + "-wal").exists()
    assert not Path(str(dst) + "-shm").exists()


def test_integrity_and_wiki_rows(tmp_path, monkeypatch):
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database")
    with pytest.raises(ValueError, match="整合性"):
        backup._integrity(bad)
    with pytest.raises(ValueError, match="整合性"):
        backup._wiki_rows(bad)

    class Conn:
        def __init__(self, row):
            self.row = row

        def execute(self, _sql):
            return self

        def fetchone(self):
            return self.row

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    db = tmp_path / "ok.sqlite"
    db.write_bytes(b"x")
    monkeypatch.setattr(backup, "connect", lambda *_args, **_kwargs: Conn(None))
    with pytest.raises(ValueError, match="整合性"):
        backup._integrity(db)
    monkeypatch.setattr(backup, "connect", lambda *_args, **_kwargs: Conn(("bad",)))
    with pytest.raises(ValueError, match="整合性"):
        backup._integrity(db)
    monkeypatch.setattr(backup, "connect", lambda *_args, **_kwargs: Conn(("ok",)))
    backup._integrity(db)
    monkeypatch.setattr(backup, "connect", sqlite3.connect)

    good = tmp_path / "wiki.sqlite"
    init_wiki(good, "")
    rows = backup._wiki_rows(good)
    assert rows[0]["body_sha256"] == hashlib.sha256(b"").hexdigest()
    assert rows[1]["body_sha256"] == hashlib.sha256(b"").hexdigest()


def test_safe_target_and_manifest_items(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").write_text("x", encoding="utf-8")
    assert backup._safe_target(root, "file.txt") == (root / "file.txt").resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sub").symlink_to(outside)
    with pytest.raises(ValueError, match="不正"):
        backup._safe_target(root, "sub/file")
    (root / "loop").symlink_to(root)
    assert backup._safe_target(root, "loop") == root.resolve()
    for relative in ("", "/abs", "a\\b", "a\x00b", "a/../b", ".."):
        with pytest.raises(ValueError, match="不正"):
            backup._safe_target(root, relative)
    assert backup._safe_target(root, "a/./b").is_relative_to(root.resolve())
    assert backup._safe_target(root, ".") == root.resolve()
    with pytest.raises(ValueError, match="不正"):
        backup._safe_target(root, None)

    link = root / "inside-link"
    link.symlink_to(root / "file.txt")
    real = Path.resolve

    def resolve(self, *args, **kwargs):
        if self.name == "evil.txt":
            return link
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="不正"):
        backup._safe_target(root, "evil.txt")

    with pytest.raises(ValueError, match="manifest"):
        backup._manifest_items([])
    with pytest.raises(ValueError, match="manifest"):
        backup._manifest_items({"files": {}, "wiki": []})
    files, wiki = backup._manifest_items({"files": [], "wiki": []})
    assert files == [] and wiki == []


def test_restore_rejects_bad_manifests_and_cleans_up(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "note.txt").write_text("hello", encoding="utf-8")
    init_wiki(data / "runtime" / "knowledge" / "local.sqlite", "body")
    archive = tmp_path / "archive"
    backup.backup_local(data, archive)
    empty = tmp_path / "empty-dest"
    empty.mkdir()
    backup.restore_local(archive, empty)
    assert (empty / "note.txt").read_text(encoding="utf-8") == "hello"

    forged = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    forged.pop("database_path")
    (archive / "manifest.json").write_text(json.dumps(forged), encoding="utf-8")
    other = tmp_path / "default-db"
    backup.restore_local(archive, other)
    assert (other / "knowledge" / "local.sqlite").is_file()

    forged["files"].append({"path": "note.txt"})
    (archive / "manifest.json").write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        backup.restore_local(archive, tmp_path / "missing-sha")

    forged["files"] = ["note.txt"]
    (archive / "manifest.json").write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        backup.restore_local(archive, tmp_path / "bad-item")

    forged = json.loads((tmp_path / "archive" / "manifest.json").read_text(encoding="utf-8"))
    # re-read original from a fresh backup because previous mutations changed archive
    fresh = tmp_path / "fresh"
    data2 = tmp_path / "data2"
    data2.mkdir()
    (data2 / "note.txt").write_text("hello", encoding="utf-8")
    init_wiki(data2 / "runtime" / "knowledge" / "local.sqlite", "body")
    backup.backup_local(data2, fresh)
    forged = json.loads((fresh / "manifest.json").read_text(encoding="utf-8"))
    forged["files"][0]["sha256"] = "0" * 64
    (fresh / "manifest.json").write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="一致しません"):
        backup.restore_local(fresh, tmp_path / "bad-hash")
    assert not (tmp_path / "bad-hash").exists()

    backup.backup_local(data2, tmp_path / "copy-src")
    # rebuild clean archive for copy mismatch
    clean = tmp_path / "clean"
    backup.backup_local(data2, clean)
    real_copy = backup.shutil.copy2

    def corrupt(source, target, *args, **kwargs):
        real_copy(source, target, *args, **kwargs)
        Path(target).write_bytes(b"corrupt")

    monkeypatch.setattr(backup.shutil, "copy2", corrupt)
    with pytest.raises(ValueError, match="ハッシュ"):
        backup.restore_local(clean, tmp_path / "corrupt-copy")

    monkeypatch.setattr(backup.shutil, "copy2", real_copy)
    wiki_only = tmp_path / "wiki-only"
    backup.backup_local(data2, wiki_only)
    (wiki_only / "knowledge.sqlite").unlink()
    with pytest.raises(ValueError, match="Wiki正本"):
        backup.restore_local(wiki_only, tmp_path / "no-wiki-db")

    mismatched = tmp_path / "mismatch"
    backup.backup_local(data2, mismatched)
    body = json.loads((mismatched / "manifest.json").read_text(encoding="utf-8"))
    body["wiki"][0]["body_sha256"] = "f" * 64
    (mismatched / "manifest.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="Wiki正本"):
        backup.restore_local(mismatched, tmp_path / "wiki-mismatch")

    duplicate = tmp_path / "duplicate"
    backup.backup_local(data2, duplicate)
    body = json.loads((duplicate / "manifest.json").read_text(encoding="utf-8"))
    body["wiki"].append(dict(body["wiki"][0]))
    (duplicate / "manifest.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        backup.restore_local(duplicate, tmp_path / "dup")

    bad_item = tmp_path / "bad-wiki-item"
    backup.backup_local(data2, bad_item)
    body = json.loads((bad_item / "manifest.json").read_text(encoding="utf-8"))
    body["wiki"] = [None]
    (bad_item / "manifest.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        backup.restore_local(bad_item, tmp_path / "bad-wiki")

    broken = tmp_path / "broken-json"
    broken.mkdir()
    (broken / "manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        backup.restore_local(broken, tmp_path / "created-then-removed")
    assert not (tmp_path / "created-then-removed").exists()
    absent = tmp_path / "absent-manifest"
    absent.mkdir()
    with pytest.raises(ValueError, match="manifest"):
        backup.restore_local(absent, tmp_path / "also-removed")

    def explode(_archive, destination, _manifest):
        (destination / "sub").mkdir()
        (destination / "f").write_text("b", encoding="utf-8")
        (destination / "link").symlink_to(destination / "sub", target_is_directory=True)
        raise RuntimeError("stop")

    kept = tmp_path / "kept"
    backup.backup_local(data2, kept)
    dest = tmp_path / "preexisting"
    dest.mkdir()
    monkeypatch.setattr(backup, "_restore_into", explode)
    with pytest.raises(RuntimeError):
        backup.restore_local(kept, dest)
    assert dest.exists() and list(dest.iterdir()) == []


def test_backup_main(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    (data / "note.txt").write_text("n", encoding="utf-8")
    assert backup.main(["status", "--data", str(data)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "idle"
    with backup.exclusive_write(data, "convert"):
        assert backup.main(["status", "--data", str(data)]) == 2
    captured = capsys.readouterr()
    assert "active convert" in captured.out
    archive = tmp_path / "out"
    assert backup.main(["backup", "--data", str(data), "--output", str(archive)]) == 0
    destination = tmp_path / "dest"
    assert (
        backup.main(["restore", "--archive", str(archive), "--destination", str(destination)]) == 0
    )
    assert (destination / "note.txt").is_file()
    assert (
        backup.main(
            ["backup", "--data", str(tmp_path / "missing"), "--output", str(tmp_path / "x")]
        )
        == 1
    )
    assert capsys.readouterr().err


def test_fault_checkpoints(monkeypatch):
    monkeypatch.delenv("DOCLING_FAULT", raising=False)
    monkeypatch.delenv("DOCLING_QUALITY_FAULT", raising=False)
    faults.checkpoint("unused")
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "other")
    monkeypatch.setenv("DOCLING_FAULT", "noseparator")
    faults.checkpoint("name")
    monkeypatch.setenv("DOCLING_FAULT", "raise:other")
    faults.checkpoint("name")
    monkeypatch.setenv("DOCLING_FAULT", "noop:name")
    faults.checkpoint("name")
    monkeypatch.setenv("DOCLING_QUALITY_FAULT", "boom")
    with pytest.raises(faults.Fault) as quality:
        faults.checkpoint("boom")
    assert quality.value.name == "boom"
    monkeypatch.delenv("DOCLING_QUALITY_FAULT", raising=False)
    monkeypatch.setenv("DOCLING_FAULT", "raise:boom")
    with pytest.raises(faults.Fault):
        faults.checkpoint("boom")
    monkeypatch.setenv("DOCLING_FAULT", "exit:boom")
    monkeypatch.setattr(os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit) as exc:
        faults.checkpoint("boom")
    assert exc.value.code == 86


def test_retention_policy_candidates_and_main(tmp_path, monkeypatch, capsys):
    policy = {"cache_max_age_days": 2, "thumbnail_max_age_days": 3}
    assert (
        retention.load_policy(write_policy(tmp_path / "ok.json", policy))["cache_max_age_days"] == 2
    )
    with pytest.raises(ValueError):
        retention.load_policy(
            write_policy(
                tmp_path / "a.json", {"cache_max_age_days": 0, "thumbnail_max_age_days": 3}
            )
        )
    with pytest.raises(ValueError):
        retention.load_policy(
            write_policy(
                tmp_path / "b.json", {"cache_max_age_days": 2, "thumbnail_max_age_days": 0}
            )
        )
    with pytest.raises(ValueError):
        retention.load_policy(write_policy(tmp_path / "c.json", {}))

    root = tmp_path / "root"
    thumb = root / "job" / "thumbnails" / "page.webp"
    cache = root / ".cache" / "old.bin"
    plain = root / "cache" / "old.bin"
    young = root / "thumbnails" / "young.webp"
    kept = root / "notes.txt"
    original = root / "job" / "original.pdf"
    library = root / "library.json"
    nested = root / "content" / "thumbnails" / "x.webp"
    runtime = root / "runtime" / ".cache" / "y.bin"
    knowledge = root / "knowledge" / "cache" / "z.bin"
    pin = root / "models" / ".cache" / "huggingface" / "trees" / "pin.json"
    stamp = root / "models" / "revision.json"
    link = root / "thumbnails" / "link.webp"
    for path in (
        thumb,
        cache,
        plain,
        young,
        kept,
        original,
        library,
        nested,
        runtime,
        knowledge,
        pin,
        stamp,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(thumb)
    old = time.time() - 10 * 86400
    for path in (
        thumb,
        cache,
        plain,
        young,
        kept,
        original,
        library,
        nested,
        runtime,
        knowledge,
        pin,
        stamp,
        link,
    ):
        os.utime(path, (old, old))
    os.utime(young, (time.time(), time.time()))
    found = retention.candidates(root, policy)
    assert thumb in found and cache in found and plain in found
    assert young not in found and kept not in found and original not in found
    assert library not in found and nested not in found and runtime not in found
    assert knowledge not in found and pin not in found and stamp not in found and link not in found
    assert retention.protected(original) is True
    assert retention.protected(root / "content" / "a.txt") is True
    assert retention.protected(root / "runtime" / "a.txt") is True
    assert retention.protected(root / "knowledge" / "a.txt") is True
    assert retention.protected(kept) is False
    assert retention.candidates(root, policy, now=time.time()) == found
    # now=None uses the wall clock; ages were set relative to time.time().
    assert thumb in retention.candidates(root, policy, now=None)
    removed = retention.apply(found)
    assert str(thumb) in removed and not thumb.exists()
    assert retention.apply([]) == []
    with pytest.raises(ValueError, match="protected"):
        retention.apply([original, thumb])
    assert original.exists()

    fresh = tmp_path / "apply-root" / "thumbnails" / "old.webp"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"img")
    os.utime(fresh, (old, old))
    monkeypatch.setattr(retention, "load_policy", lambda path=None: policy)
    assert retention.main(["--root", str(tmp_path / "apply-root")]) == 0
    assert fresh.exists()
    assert json.loads(capsys.readouterr().out)["applied"] is False
    assert retention.main(["--root", str(tmp_path / "apply-root"), "--apply"]) == 0
    assert not fresh.exists()
    assert json.loads(capsys.readouterr().out)["applied"] is True


def write_policy(path: Path, policy: dict) -> Path:
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def test_verify_commands(tmp_path, monkeypatch, capsys):
    seen = {}

    def run(args, cwd, env, check):
        seen["args"] = args
        seen["cwd"] = cwd
        seen["env"] = env
        seen["check"] = check

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(verify.subprocess, "run", run)
    result = verify.command_result(["python", "-m", "ruff"], tmp_path)
    assert result["exit_code"] == 0
    assert "PYTHONPATH" in seen["env"] and seen["env"]["DOCLING_ENV_FILE"]
    monkeypatch.setenv("PYTHONPATH", "/already")
    verify.command_result(["python"], tmp_path)
    assert seen["env"]["PYTHONPATH"].endswith("/already")

    models = {"command": ["python", "-m", "docling_desk.operations.models"], "exit_code": 0}
    assert verify.accepted(models) is True
    models["exit_code"] = 3
    assert verify.accepted(models) is True
    models["exit_code"] = 1
    assert verify.accepted(models) is False
    other = {"command": ["python", "-m", "pytest"], "exit_code": 0}
    assert verify.accepted(other) is True
    other["exit_code"] = 1
    assert verify.accepted(other) is False

    assert len(verify.checks(False)) == 3
    assert len(verify.checks(True)) > 3

    output = tmp_path / "qa" / "verify.json"

    def command_result(args, cwd):
        code = 1 if args[-1] == "lint" else 0
        return {"command": args, "cwd": str(cwd), "exit_code": code, "seconds": 0.1}

    monkeypatch.setattr(verify, "command_result", command_result)
    assert verify.main(["--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    capsys.readouterr()
    assert verify.main(["--with-js", "--output", str(output)]) == 1
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is False
    assert "ok" in capsys.readouterr().out


def test_distribution_evidence(tmp_path, monkeypatch, capsys):
    bundle = tmp_path / "frontend"
    assert distribution_evidence.bundle_record(bundle)["problems"] == ["index_missing"]
    assets = bundle / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("console.log(1)\n", encoding="utf-8")
    (assets / "app.css").write_text("body{}\n", encoding="utf-8")
    (bundle / "index.html").write_text(
        '<script src="/static/frontend/assets/app.js"></script>\n'
        "<link href='/static/frontend/assets/app.css'>\n"
        '<script src="/static/frontend/assets/missing.js"></script>\n',
        encoding="utf-8",
    )
    record = distribution_evidence.bundle_record(bundle)
    assert record["ok"] is False
    assert "print_missing" in record["problems"]
    assert any(item.startswith("asset_missing:") for item in record["problems"])
    (bundle / "print.html").write_text("<script>bad</script>\n", encoding="utf-8")
    scripted = distribution_evidence.bundle_record(bundle)
    assert "print_has_script" in scripted["problems"]
    (bundle / "print.html").write_text("<p>print</p>\n", encoding="utf-8")
    (assets / "missing.js").write_text("x\n", encoding="utf-8")
    ready = distribution_evidence.bundle_record(bundle)
    assert ready["ok"] is True and ready["sha256"]

    class Result:
        def __init__(self, code, out=""):
            self.returncode = code
            self.stdout = out

    monkeypatch.setattr(
        distribution_evidence.subprocess, "run", lambda *args, **kwargs: Result(0, "abc\n")
    )
    assert distribution_evidence.git_commit(tmp_path) == "abc"
    monkeypatch.setattr(
        distribution_evidence.subprocess, "run", lambda *args, **kwargs: Result(1, "")
    )
    monkeypatch.setenv("GITHUB_SHA", "def")
    assert distribution_evidence.git_commit(tmp_path) == "def"
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    assert distribution_evidence.git_commit(tmp_path) == "unknown"

    for value, expected in {
        "passed": "passed",
        "success": "passed",
        "failed": "failed",
        "failure": "failed",
        "not_run": "not_run",
        "skipped": "not_run",
        "cancelled": "not_run",
        "weird": "failed",
    }.items():
        assert distribution_evidence.lane_status(value) == expected

    failed_build = distribution_evidence.evidence(
        "abc",
        {"ok": False, "sha256": None},
        {"frontend_build": "success", "browser": "failed", "http": "failure"},
    )
    assert failed_build["lanes"]["frontend_build"]["status"] == "failed"
    assert failed_build["ok"] is False
    assert failed_build["bundle_problems"] == []
    skipped_build = distribution_evidence.evidence(
        "abc", {"ok": False}, {"frontend_build": "skipped"}
    )
    assert skipped_build["lanes"]["frontend_build"]["status"] == "not_run"
    passed = distribution_evidence.evidence(
        "abc",
        ready,
        {"frontend_build": "passed", "browser": "success", "http": "passed", "docker": "cancelled"},
    )
    assert passed["ok"] is True
    assert passed["lanes"]["azure"]["status"] == "not_run"
    unknown = distribution_evidence.evidence(
        "unknown",
        ready,
        {"frontend_build": "passed", "browser": "passed", "http": "passed"},
    )
    assert unknown["ok"] is False
    empty = distribution_evidence.evidence(
        "",
        ready,
        {"frontend_build": "passed", "browser": "passed", "http": "passed"},
    )
    assert empty["ok"] is False

    output = tmp_path / "qa" / "latest.json"
    assert (
        distribution_evidence.main(
            [
                "--bundle",
                str(bundle),
                "--output",
                str(output),
                "--commit",
                "abc",
                "--frontend-build",
                "success",
                "--browser",
                "passed",
                "--http",
                "success",
            ]
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8"))["ok"] is True
    assert (
        distribution_evidence.main(
            ["--bundle", str(bundle), "--output", str(output), "--commit", "abc"]
        )
        == 1
    )
    assert "ok" in capsys.readouterr().out
    monkeypatch.setattr(distribution_evidence, "git_commit", lambda _root: "zzz")
    assert (
        distribution_evidence.main(
            [
                "--bundle",
                str(tmp_path / "missing-bundle"),
                "--output",
                str(output),
                "--frontend-build",
                "success",
                "--browser",
                "success",
                "--http",
                "success",
            ]
        )
        == 1
    )
