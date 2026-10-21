"""Registry of conversions that compute without holding the data write lock.

A conversion computes in ``runtime/conversions/<attempt>/`` and takes the write
lock only to start and to publish (see docs/refactoring/R13-design.md). While it
computes, the backup must still refuse, so each attempt owns one file,
``runtime/activity/<attempt>.lock``, on which its process holds ``flock(LOCK_EX)``.

Liveness is the lock and nothing else. The JSON inside the file (pid, purpose,
document, start time) is for diagnostics. A process that dies releases the lock
in the kernel, so a leftover file never refuses a backup: a probe that can take
the lock proves the owner is gone, and the file is removed.

Registration and every scan that removes files happen under the data write lock
(``backup.exclusive_write``), so a scan and a registration never overlap. Lock
order is write lock first, then activity locks, which are only ever tried without
waiting. POSIX only, like the write lock itself.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import shutil
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)
SUFFIX = ".lock"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def directory(data: Path) -> Path:
    return data / "runtime" / "activity"


def work_root(data: Path) -> Path:
    return data / "runtime" / "conversions"


def attempt_record(data: Path, identifier: str) -> Path:
    return data / "runtime" / "documents" / identifier / "attempt.json"


class ActivityConflict(RuntimeError):
    """Another holder already owns this attempt's lock."""


class Activity:
    """One registered attempt. ``end`` acts once, on every exit path."""

    def __init__(self, data: Path, attempt: str, purpose: str, **details: object):
        self.data, self.attempt, self.purpose = data, attempt, purpose
        self.details = details
        self._descriptor: int | None = None

    @property
    def path(self) -> Path:
        return directory(self.data) / f"{self.attempt}{SUFFIX}"

    def begin(self) -> None:
        """Take the lock and describe the work. Call under the data write lock."""
        if self._descriptor is not None:
            raise ActivityConflict(self.attempt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | _NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                raise ActivityConflict(self.attempt) from error
            raise
        self._descriptor = descriptor
        record = {
            "attempt": self.attempt,
            "purpose": self.purpose,
            "pid": os.getpid(),
            "started": time.time(),
            **self.details,
        }
        try:
            os.ftruncate(descriptor, 0)
            os.write(descriptor, json.dumps(record, ensure_ascii=False).encode("utf-8"))
        except OSError:
            LOGGER.warning("activity record could not be written for %s", self.attempt)

    def end(self) -> bool:
        """Release the lock and remove the file. True only for the call that ended it."""
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return False
        try:
            self.path.unlink(missing_ok=True)
        finally:
            os.close(descriptor)  # closing releases the flock
        return True

    def __enter__(self) -> Activity:
        self.begin()
        return self

    def __exit__(self, *_exc) -> None:
        self.end()


def _held_elsewhere(path: Path) -> bool | None:
    """True when a lock holder exists, False when the file is stale, None when gone."""
    try:
        descriptor = os.open(path, os.O_RDWR | _NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                return True
            raise
        # Stale: we hold the only lock. Remove the file before letting it go so a
        # concurrent scan cannot mistake it for a live holder.
        path.unlink(missing_ok=True)
        return False
    finally:
        os.close(descriptor)


def _described(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def sweep(data: Path) -> list[dict]:
    """Live activities of this data root. Stale files are removed.

    Call under the data write lock. The backup refuses when this is not empty.
    """
    found = []
    for path in sorted(directory(data).glob(f"*{SUFFIX}")):
        if path.is_symlink():
            path.unlink(missing_ok=True)
            continue
        if _held_elsewhere(path):
            found.append({"attempt": path.stem, "purpose": None, **_described(path)})
    return found


def live(data: Path) -> list[dict]:
    """Live activities, read-only: nothing is removed (for status reporting)."""
    found = []
    for path in sorted(directory(data).glob(f"*{SUFFIX}")):
        if path.is_symlink():
            continue
        try:
            descriptor = os.open(path, os.O_RDWR | _NOFOLLOW)
        except OSError:
            continue
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    found.append({"attempt": path.stem, "purpose": None, **_described(path)})
                    continue
                raise
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    return found


def is_live(data: Path, attempt: str) -> bool:
    """Whether the attempt's lock is held by a running process (read-only)."""
    return any(item["attempt"] == attempt for item in live(data))


def read_attempt(data: Path, identifier: str) -> dict:
    return _described(attempt_record(data, identifier))


def _inside(root: Path, path: Path) -> bool:
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _discard(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def clear_stale(data: Path) -> int:
    """Remove what attempts that no longer run left behind. Call under the write lock.

    Stale activity files, work directories that no live attempt owns, attempt
    records, and, for an attempt that died while publishing, the artifacts it had
    already moved into the document folder (the document stays unpublished: its
    job.json never left ``running``, and startup recovery marks it failed).
    """
    removed = 0
    alive = {item["attempt"] for item in sweep(data)}
    root = work_root(data)
    for path in sorted(root.glob("*")) if root.is_dir() else []:
        if path.name not in alive:
            _discard(path)
            removed += 1
    documents = data / "runtime" / "documents"
    for record in sorted(documents.glob("*/attempt.json")) if documents.is_dir() else []:
        value = _described(record)
        if value.get("attempt") in alive:
            continue
        identifier = record.parent.name
        folder = data / "derived" / "documents" / identifier
        if not _published(data, identifier):
            for name in value.get("publishing") or []:
                if isinstance(name, str) and name and "/" not in name and name not in {".", ".."}:
                    target = folder / name
                    if _inside(folder, target) or target.is_symlink():
                        _discard(target)
        record.unlink(missing_ok=True)
        removed += 1
    return removed


def _published(data: Path, identifier: str) -> bool:
    """A job.json that already says success/partial with its manifest next to it."""
    job = data / "runtime" / "documents" / identifier / "job.json"
    manifest = data / "derived" / "documents" / identifier / "manifest.json"
    return _described(job).get("state") in {"success", "partial"} and manifest.is_file()
