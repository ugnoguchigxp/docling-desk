"""Mirror ``content/`` (and optionally ``derived/``) to Azure Blob Storage.

The local data directory stays the source of truth. A pass uploads files that
changed, removes blobs whose local file was deleted, and never overwrites a blob
that somebody else changed: every write is conditional on the ETag this
instance last saw, and a mismatch is recorded as a conflict instead.
``pull`` restores a missing data directory from the container.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from docling_desk import config
from docling_desk.sqlite_writer import connect

log = logging.getLogger("docling_desk.blob")
STATE_NAME = "blob-sync.json"
SKIP_SUFFIXES = (".tmp", ".part", ".lock")
SETTLE_SECONDS = 2.0
RESTORE_MARKER = "blob-restore.incomplete"
# Per-document state that a restored host cannot start without (job.json) or that
# cannot be regenerated cheaply (explanations; SQLite is mirrored as a snapshot).
RUNTIME_FILE = re.compile(r"runtime/documents/[^/]+/(?:job\.json|explanation\.sqlite)")
_KICK = threading.Event()


def notify() -> None:
    """Ask the running worker to synchronise soon, e.g. right after a deletion."""
    _KICK.set()


class Conflict(Exception):
    """The blob changed (or appeared) since this instance last synchronised it."""


class Missing(Exception):
    pass


@dataclass
class Remote:
    etag: str
    size: int
    sha256: str


class BlobStore(Protocol):
    def list(self) -> dict[str, Remote]: ...
    def put(self, key: str, path: Path, sha256: str, etag: str | None) -> str: ...
    def get(self, key: str, target: Path) -> str: ...
    def delete(self, key: str, etag: str) -> None: ...


class AzureBlobStore:
    """Blob container accessed with a connection string or a managed identity."""

    def __init__(self, container: Any, prefix: str = "") -> None:
        self.container, self.prefix = container, prefix

    @classmethod
    def from_config(cls) -> AzureBlobStore:
        from azure.storage.blob import BlobServiceClient

        if config.BLOB_CONNECTION_STRING:
            service = BlobServiceClient.from_connection_string(config.BLOB_CONNECTION_STRING)
        else:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential(
                managed_identity_client_id=config.BLOB_CLIENT_ID or None
            )
            service = BlobServiceClient(config.BLOB_ACCOUNT_URL, credential=credential)
        return cls(service.get_container_client(config.BLOB_CONTAINER), config.BLOB_PREFIX)

    def _name(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def _key(self, name: str) -> str | None:
        if not self.prefix:
            return name
        return name[len(self.prefix) + 1 :] if name.startswith(self.prefix + "/") else None

    def list(self) -> dict[str, Remote]:
        found: dict[str, Remote] = {}
        start = f"{self.prefix}/" if self.prefix else None
        for blob in self.container.list_blobs(name_starts_with=start, include=["metadata"]):
            key = self._key(blob.name)
            if key:
                sha = (blob.metadata or {}).get("sha256", "")
                found[key] = Remote(str(blob.etag), int(blob.size), sha)
        return found

    def put(self, key: str, path: Path, sha256: str, etag: str | None) -> str:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

        options: dict[str, Any] = {"overwrite": True}
        if etag is None:
            options["overwrite"] = False
        else:
            options.update(etag=etag, match_condition=MatchConditions.IfNotModified)
        try:
            with path.open("rb") as handle:
                # The blob client (not the container) returns the new ETag.
                result = self.container.get_blob_client(self._name(key)).upload_blob(
                    handle, metadata={"sha256": sha256}, **options
                )
        except (ResourceExistsError, ResourceModifiedError) as error:
            raise Conflict(key) from error
        return str(result["etag"])

    def get(self, key: str, target: Path) -> str:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            stream = self.container.download_blob(self._name(key))
        except ResourceNotFoundError as error:
            raise Missing(key) from error
        with target.open("wb") as handle:
            stream.readinto(handle)
        return str(stream.properties.etag)

    def delete(self, key: str, etag: str) -> None:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError

        try:
            self.container.delete_blob(
                self._name(key), etag=etag, match_condition=MatchConditions.IfNotModified
            )
        except ResourceNotFoundError:
            return
        except ResourceModifiedError as error:
            raise Conflict(key) from error


@dataclass
class Report:
    uploaded: int = 0
    deleted: int = 0
    downloaded: int = 0
    unchanged: int = 0
    deferred: int = 0
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _safe_key(key: str) -> bool:
    parts = key.split("/")
    return bool(key) and not key.startswith("/") and ".." not in parts and "\\" not in key


class Mirror:
    def __init__(
        self,
        data: Path,
        store: BlobStore,
        *,
        derived: bool = False,
        settle: float = SETTLE_SECONDS,
    ) -> None:
        self.data, self.store, self.settle = data, store, settle
        self.roots = ("content", "derived") if derived else ("content",)
        self.state_path = data / "runtime" / STATE_NAME
        self.staging = data / "runtime" / "blob-snapshots"
        self.lock = threading.Lock()

    # -- state ---------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state.setdefault("files", {})
        state.setdefault("conflicts", {})
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, self.state_path)

    # -- local scan ----------------------------------------------------------
    def _wanted(self, key: str) -> bool:
        return key.split("/", 1)[0] in self.roots or bool(RUNTIME_FILE.fullmatch(key))

    def _refresh_snapshots(self) -> None:
        """Stage a consistent copy of each explanation database (SQLite is never copied live)."""
        documents = self.data / "runtime" / "documents"
        staged = self.staging / "documents"
        for source in sorted(documents.glob("*/explanation.sqlite")):
            stamp = max(
                (
                    p.stat().st_mtime_ns
                    for p in (source, source.with_name(source.name + "-wal"))
                    if p.exists()
                ),
            )
            target = staged / source.parent.name / source.name
            if target.exists() and target.stat().st_mtime_ns == stamp:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            origin = copy = None
            try:
                origin, copy = connect(source), connect(temporary)
                origin.backup(copy)
                # A snapshot must not leave a WAL sidecar that a restore would replay.
                copy.execute("PRAGMA journal_mode=DELETE")
                copy.close()
                copy = None
                os.utime(temporary, ns=(stamp, stamp))
                os.replace(temporary, target)
            except (sqlite3.Error, OSError, RuntimeError) as error:
                temporary.unlink(missing_ok=True)
                log.warning("explanation snapshot failed for %s: %s", source, error)
            finally:
                for handle in (origin, copy):
                    if handle is not None:
                        handle.close()
                for suffix in ("-wal", "-shm"):
                    temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)
        for target in sorted(staged.glob("*/explanation.sqlite")):
            if not (documents / target.parent.name / target.name).exists():
                target.unlink()

    def _local(self) -> Iterator[tuple[str, Path]]:
        self._refresh_snapshots()
        found: list[tuple[str, Path]] = []
        for root in self.roots:
            base = self.data / root
            if base.is_dir():
                found += [(p.relative_to(self.data).as_posix(), p) for p in sorted(base.rglob("*"))]
        for job in sorted((self.data / "runtime" / "documents").glob("*/job.json")):
            found.append((job.relative_to(self.data).as_posix(), job))
        for snap in sorted((self.staging / "documents").glob("*/explanation.sqlite")):
            found.append((f"runtime/documents/{snap.parent.name}/{snap.name}", snap))
        # Manifests last: a reader never sees one that names something not yet stored.
        found.sort(key=lambda item: ("/manifests/" in item[0], item[0]))
        for key, path in found:
            if path.is_symlink() or not path.is_file() or path.name.endswith(SKIP_SUFFIXES):
                continue
            yield key, path

    def has_local_content(self) -> bool:
        return next(self._local(), None) is not None

    def restore_incomplete(self) -> bool:
        return (self.data / "runtime" / RESTORE_MARKER).exists()

    # -- push ----------------------------------------------------------------
    def push(self) -> Report:
        with self.lock:
            return self._push()

    def _push(self) -> Report:
        report, state = Report(), self._load()
        files, conflicts = state["files"], state["conflicts"]
        local = list(self._local())
        present = {key for key, _ in local}
        # Deletions first, so a deleted document never outlives the manifest update.
        if files and not present:
            # An empty local area with known blobs means a missing volume, not mass deletion.
            report.errors.append("ローカルの保存領域が空のため、Blobの削除は同期しません。")
        else:
            for key in [k for k in files if k not in present and self._wanted(k)]:
                try:
                    self.store.delete(key, files[key]["etag"])
                except Conflict:
                    conflicts[key] = "Blob側が別に更新されているため削除しませんでした。"
                    report.conflicts.append(key)
                    continue
                except Exception as error:
                    report.errors.append(f"{key}: {error}")
                    continue
                del files[key]
                conflicts.pop(key, None)
                report.deleted += 1
        remote: dict[str, Remote] | None = None
        now = time.time()
        for key, path in local:
            adopted = False
            try:
                stat = path.stat()
            except OSError:
                continue
            known = files.get(key)
            if known and known["size"] == stat.st_size and known["mtime_ns"] == stat.st_mtime_ns:
                report.unchanged += 1
                continue
            if now - stat.st_mtime < self.settle:
                report.deferred += 1
                continue
            digest = _sha256(path)
            if known and known["sha256"] == digest:
                known.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
                report.unchanged += 1
                continue
            try:
                try:
                    etag = self.store.put(key, path, digest, known["etag"] if known else None)
                except Conflict:
                    if known:
                        raise
                    # A blob already exists for a file this instance never synchronised.
                    remote = remote if remote is not None else self.store.list()
                    found = remote.get(key)
                    if not found or found.sha256 != digest:
                        raise
                    etag, adopted = found.etag, True
            except Conflict:
                conflicts[key] = "Blob側が別に更新されているため上書きしませんでした。"
                report.conflicts.append(key)
                continue
            except Exception as error:  # network or permission failure: retry next pass
                log.warning("Blob upload failed for %s: %s", key, error)
                report.errors.append(f"{key}: {error}")
                continue
            after = path.stat()
            if after.st_size != stat.st_size or after.st_mtime_ns != stat.st_mtime_ns:
                report.deferred += 1  # changed while uploading; the next pass uploads it again
                continue
            files[key] = {
                "sha256": digest,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "etag": etag,
            }
            conflicts.pop(key, None)
            if adopted:
                report.unchanged += 1
            else:
                report.uploaded += 1
        state["last_push"] = time.time()
        state["last_errors"] = report.errors[:10]
        self._save(state)
        return report

    # -- pull ----------------------------------------------------------------
    def pull(self) -> Report:
        """Download blobs that are missing locally. Existing local files are kept.

        A marker file stays until every blob arrived, so an interrupted restore
        is resumed at the next start instead of being taken for a finished one.
        """
        with self.lock:
            report, state = Report(), self._load()
            marker = self.data / "runtime" / RESTORE_MARKER
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(time.time()))
            remote = self.store.list()
            order = sorted(remote, key=lambda k: ("/manifests/" in k, k))
            for key in order:
                info = remote[key]
                if not _safe_key(key) or not self._wanted(key):
                    continue
                target = self.data / key
                if target.exists():
                    report.unchanged += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".part")
                try:
                    etag = self.store.get(key, temporary)
                    digest = _sha256(temporary)
                    if info.sha256 and digest != info.sha256:
                        raise ValueError("ダウンロードしたファイルのハッシュが一致しません。")
                    os.replace(temporary, target)
                except Exception as error:
                    temporary.unlink(missing_ok=True)
                    report.errors.append(f"{key}: {error}")
                    continue
                stat = target.stat()
                state["files"][key] = {
                    "sha256": digest,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "etag": etag,
                }
                report.downloaded += 1
            self._save(state)
            if not report.errors:
                marker.unlink(missing_ok=True)
            return report

    def status(self) -> dict[str, Any]:
        state = self._load()
        return {
            "backend": "azure-blob",
            "tracked": len(state["files"]),
            "last_push": state.get("last_push"),
            "conflicts": dict(state["conflicts"]),
            "errors": state.get("last_errors", []),
            "restore_incomplete": self.restore_incomplete(),
        }


class MirrorWorker:
    """Runs a push pass on an interval and once more at shutdown."""

    def __init__(self, mirror: Mirror, interval: float) -> None:
        self.mirror, self.interval = mirror, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="blob-mirror", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _pass(self) -> None:
        try:
            self.mirror.push()
        except Exception:
            log.exception("Blob mirror pass failed")

    def _run(self) -> None:
        while not self._stop.is_set():
            kicked = _KICK.wait(self.interval)
            if self._stop.is_set():
                break
            if kicked:
                _KICK.clear()
            self._pass()

    def close(self) -> None:
        self._stop.set()
        _KICK.set()
        self._thread.join(timeout=5)
        _KICK.clear()
        self.mirror.settle = 0
        self._pass()


def create_mirror(data: Path) -> Mirror | None:
    if config.STORAGE_BACKEND != "azure-blob":
        return None
    return Mirror(data, AzureBlobStore.from_config(), derived=config.BLOB_SYNC_DERIVED)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Blob Storageとのミラーを操作します。")
    parser.add_argument("command", choices=["status", "push", "pull"])
    command = parser.parse_args(argv).command
    mirror = create_mirror(config.DATA)
    if mirror is None:
        print("DOCLING_STORAGE=azure-blob を設定してください。")
        return 2
    if command == "status":
        print(json.dumps(mirror.status(), ensure_ascii=False, indent=1))
        return 0
    mirror.settle = 0
    report = mirror.push() if command == "push" else mirror.pull()
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=1))
    return 1 if report.conflicts or report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
