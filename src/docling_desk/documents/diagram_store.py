"""Standalone Excalidraw diagrams: immutable revisions, one draft per diagram, idempotent writes.

Layout and ordering follow docs/diagram-p1-p2-contract.md. Immutable revision files are written
before the manifest that publishes them; the manifest is the only mutable source of truth.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from docling_desk.atomic_files import atomic_write_bytes
from docling_desk.blob_mirror import notify
from docling_desk.documents.conversion import Job, save_job
from docling_desk.documents.diagram_models import (
    DiagramInfo,
    DraftInfo,
    RevisionInfo,
    validate_scene,
)
from docling_desk.documents.library import (
    LOCK,
    Placement,
    decorate,
    destination_exists,
    names_at,
    read_jobs,
    unique_name,
    valid_name,
)
from docling_desk.documents.library import load as load_library
from docling_desk.documents.library import save as save_library
from docling_desk.operations.backup import exclusive_write
from docling_desk.storage import (
    document_folder,
    job_file,
    remove_document,
    safe_path,
)

SUFFIX = ".excalidraw"
OPERATION_TTL = 7 * 24 * 3600
OPERATION_LIMIT = 200
DRAFT_VERSIONS = 3
STALE = 3600
LOGGER = logging.getLogger(__name__)

_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


def _lock(identifier: str) -> threading.RLock:
    with _GUARD:
        return _LOCKS.setdefault(identifier, threading.RLock())


@contextmanager
def _writing(data: Path, identifier: str) -> Iterator[None]:
    with exclusive_write(data, "diagram"), _lock(identifier):
        yield


def valid_id(identifier: str) -> bool:
    return len(identifier) == 32 and all(c in "0123456789abcdef" for c in identifier)


def content_dir(data: Path, identifier: str) -> Path:
    return data / "content" / "documents" / identifier


def source_etag(revision_id: str) -> str:
    return f'"r-{revision_id}"'


def draft_etag(draft_id: str, version: int) -> str:
    return f'"d-{draft_id}-{version}"'


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def request_hash(*parts: str) -> str:
    return sha256(json.dumps(parts, ensure_ascii=False).encode())


def _same_tag(given: str | None, expected: str) -> bool:
    return given is not None and given.removeprefix("W/") == expected


def write_file(path: Path, raw: bytes) -> None:
    atomic_write_bytes(path, raw)


def write_json(path: Path, value: object) -> None:
    write_file(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _not_found() -> HTTPException:
    return HTTPException(404, "図表が見つかりません。")


def _read_manifest(data: Path, identifier: str) -> dict | None:
    if not valid_id(identifier):
        return None
    content = content_dir(data, identifier)
    if not safe_path(data, content):
        return None
    try:
        value = json.loads((content / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("version") != 2
        or value.get("kind") != "excalidraw"
        or value.get("id") != identifier
        or not isinstance(value.get("operations"), dict)
    ):
        return None
    return value


def _load(data: Path, identifier: str) -> dict:
    manifest = _read_manifest(data, identifier)
    if manifest is None or not job_file(document_folder(data, identifier)).is_file():
        raise _not_found()
    return manifest


def _revision(content: Path, revision_id: str) -> dict:
    try:
        return json.loads((content / "revisions" / revision_id / "revision.json").read_text())
    except (OSError, ValueError):
        raise HTTPException(404, "版が見つかりません。") from None


def _draft_info(draft: dict | None) -> DraftInfo | None:
    if draft is None:
        return None
    return DraftInfo(
        id=draft["id"],
        version=draft["version"],
        base_revision=draft["base_revision"],
        etag=draft_etag(draft["id"], draft["version"]),
        updated=draft["updated"],
    )


def _info(data: Path, manifest: dict, revision: dict, operation_id: str | None = None):
    with LOCK:
        entry = load_library(data).files.get(manifest["id"])
    return DiagramInfo(
        id=manifest["id"],
        name=entry.name if entry else manifest["filename"],
        folder_id=entry.parent_id if entry else None,
        revision_id=revision["id"],
        revision_number=revision["number"],
        sha256=revision["sha256"],
        size=revision["size"],
        etag=source_etag(revision["id"]),
        created=manifest["created"],
        updated=revision["created"],
        draft=_draft_info(manifest.get("draft")),
        operation_id=operation_id,
    )


def _replay(content: Path, manifest: dict, key_hash: str, digest: str) -> tuple[int, dict] | None:
    entry = manifest["operations"].get(key_hash)
    if not entry or entry.get("created", 0) < time.time() - OPERATION_TTL:
        return None
    try:
        operation = json.loads((content / "operations" / f"{entry['id']}.json").read_text())
    except (OSError, ValueError, KeyError):
        return None
    if operation.get("request_sha256") != digest:
        raise HTTPException(409, "同じIdempotency-Keyで異なる内容の要求が送信されました。")
    return operation["status"], operation["result"]


def _operation(
    action: str, key_hash: str, digest: str, status: int, result: DiagramInfo, now: float
) -> dict:
    return {
        "id": result.operation_id,
        "key_sha256": key_hash,
        "request_sha256": digest,
        "action": action,
        "status": status,
        "result": result.model_dump(),
        "created": now,
    }


def _publish(content: Path, manifest: dict, operation: dict | None = None) -> None:
    now = time.time()
    replaced = None
    if operation is not None:
        replaced = manifest["operations"].get(operation["key_sha256"])
        write_json(content / "operations" / f"{operation['id']}.json", operation)
        manifest["operations"][operation["key_sha256"]] = {
            "id": operation["id"],
            "created": operation["created"],
        }
    operations = manifest["operations"]
    ordered = sorted(operations.items(), key=lambda item: item[1]["created"], reverse=True)
    keep = {
        key: entry
        for key, entry in ordered[:OPERATION_LIMIT]
        if entry["created"] >= now - OPERATION_TTL
    }
    dropped = [entry["id"] for key, entry in operations.items() if key not in keep]
    if replaced and replaced["id"] != operation["id"]:
        dropped.append(replaced["id"])
    manifest["operations"] = keep
    write_json(content / "manifest.json", manifest)
    for identifier in dropped:
        (content / "operations" / f"{identifier}.json").unlink(missing_ok=True)
    notify()


def _new_revision(content: Path, manifest: dict, raw: bytes, restored_from: str | None) -> dict:
    revision_id = uuid4().hex
    revision = {
        "id": revision_id,
        "number": manifest.get("revision_count", 0) + 1,
        "parent": manifest.get("current_revision"),
        "sha256": sha256(raw),
        "size": len(raw),
        "created": time.time(),
        "restored_from": restored_from,
    }
    directory = content / "revisions" / revision_id
    write_file(directory / "original.excalidraw", raw)
    write_json(directory / "revision.json", revision)
    manifest.update(
        key=f"documents/{manifest['id']}/revisions/{revision_id}/original.excalidraw",
        sha256=revision["sha256"],
        current_revision=revision_id,
        revision_count=revision["number"],
    )
    return revision


def _append(
    data: Path,
    manifest: dict,
    raw: bytes,
    action: str,
    key_hash: str,
    digest: str,
    restored_from: str | None = None,
    drop_draft: bool = False,
) -> tuple[int, dict]:
    content = content_dir(data, manifest["id"])
    revision = _new_revision(content, manifest, raw, restored_from)
    draft = manifest["draft"] if drop_draft else None
    if drop_draft:
        manifest["draft"] = None
    operation_id = uuid4().hex
    info = _info(data, manifest, revision, operation_id)
    _publish(content, manifest, _operation(action, key_hash, digest, 201, info, time.time()))
    if draft:
        shutil.rmtree(content / "drafts" / draft["id"], ignore_errors=True)
    return 201, info.model_dump()


def _key_hash(key: str) -> str:
    return sha256(key.encode("utf-8"))


def _current_bytes(content: Path, revision_id: str) -> bytes:
    try:
        return (content / "revisions" / revision_id / "original.excalidraw").read_bytes()
    except OSError:
        raise HTTPException(404, "版が見つかりません。") from None


def _display_name(name: str) -> str:
    name = valid_name(name)
    if not name.lower().endswith(SUFFIX):
        name += SUFFIX
    return valid_name(name)


def create(
    data: Path,
    identifier: str,
    name: str,
    folder_id: str | None,
    raw: bytes,
    key: str | None,
) -> tuple[int, dict]:
    """Create a diagram. With a key, a repeated identical request returns the stored result."""
    validate_scene(raw)
    display = _display_name(name)
    key_hash = _key_hash(key) if key else ""
    digest = request_hash("create", identifier, display, folder_id or "", sha256(raw))
    with _writing(data, identifier):
        content = content_dir(data, identifier)
        folder = document_folder(data, identifier)
        existing = _read_manifest(data, identifier)
        if existing is not None:
            _repair_one(data, existing)
            if key and (hit := _replay(content, existing, key_hash, digest)):
                return 200, hit[1]
        if (
            (content / "manifest.json").exists()
            or folder.exists()
            or (data / identifier).exists()
            or job_file(folder).exists()
        ):
            raise HTTPException(412, "同じIDの資料が既にあります。")
        try:
            return _create_new(
                data, identifier, display, folder_id, raw, key_hash, digest, bool(key)
            )
        except BaseException:
            shutil.rmtree(content, ignore_errors=True)
            if folder.exists() or job_file(folder).exists():
                remove_document(folder)
            raise


def _create_new(
    data: Path,
    identifier: str,
    display: str,
    folder_id: str | None,
    raw: bytes,
    key_hash: str,
    digest: str,
    keyed: bool,
) -> tuple[int, dict]:
    content = content_dir(data, identifier)
    folder = document_folder(data, identifier)
    created = time.time()
    with LOCK:
        library = load_library(data)
        destination_exists(library, folder_id)
        final = unique_name(
            display, names_at(library, {j.id: j for j in read_jobs(data)}, folder_id), file=True
        )
        manifest = {
            "version": 2,
            "kind": "excalidraw",
            "id": identifier,
            "filename": final,
            "created": created,
            "key": "",
            "sha256": "",
            "current_revision": None,
            "revision_count": 0,
            "draft": None,
            "operations": {},
            "folder_id": folder_id,
        }
        revision = _new_revision(content, manifest, raw, None)
        operation = None
        if keyed:
            info = _info_for_new(identifier, final, folder_id, manifest, revision, uuid4().hex)
            operation = _operation("create", key_hash, digest, 200, info, created)
        else:
            info = _info_for_new(identifier, final, folder_id, manifest, revision, None)
        _publish(content, manifest, operation)
        folder.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=identifier,
            filename=final,
            state="success",
            created=created,
            preview="excalidraw",
            excalidraw=True,
        )
        save_job(folder, job)
        _place(data, identifier, final, folder_id)
    return 201, info.model_dump()


def _info_for_new(identifier, name, folder_id, manifest, revision, operation_id) -> DiagramInfo:
    return DiagramInfo(
        id=identifier,
        name=name,
        folder_id=folder_id,
        revision_id=revision["id"],
        revision_number=1,
        sha256=revision["sha256"],
        size=revision["size"],
        etag=source_etag(revision["id"]),
        created=manifest["created"],
        updated=revision["created"],
        draft=None,
        operation_id=operation_id,
    )


def _place(data: Path, identifier: str, name: str, folder_id: str | None) -> None:
    with LOCK:
        library = load_library(data)
        jobs = {j.id: j for j in read_jobs(data)}
        taken = names_at(library, jobs, folder_id, {("file", identifier)})
        library.files[identifier] = Placement(
            name=unique_name(name, taken, file=True), parent_id=folder_id
        )
        save_library(data, library)


def import_scene(data: Path, filename: str, folder_id: str | None, raw: bytes) -> Job:
    """Upload path: new id, no idempotency key, bytes stored unchanged."""
    identifier = uuid4().hex
    create(data, identifier, filename, folder_id, raw, None)
    with LOCK:
        job = Job.model_validate_json(job_file(document_folder(data, identifier)).read_text())
        return decorate(load_library(data), job)


def info(data: Path, identifier: str) -> DiagramInfo:
    manifest = _load(data, identifier)
    revision = _revision(content_dir(data, identifier), manifest["current_revision"])
    return _info(data, manifest, revision)


def scene_content(data: Path, identifier: str, revision_id: str | None) -> tuple[bytes, str, str]:
    """Scene bytes, revision id and display name for the current or a named revision."""
    manifest = _load(data, identifier)
    revision_id = revision_id or manifest["current_revision"]
    if not valid_id(revision_id):
        raise HTTPException(404, "版が見つかりません。")
    directory = content_dir(data, identifier)
    _revision(directory, revision_id)
    with LOCK:
        entry = load_library(data).files.get(identifier)
    return (
        _current_bytes(directory, revision_id),
        revision_id,
        entry.name if entry else manifest["filename"],
    )


def revisions(
    data: Path, identifier: str, before: int | None, limit: int
) -> tuple[list[RevisionInfo], int | None]:
    manifest = _load(data, identifier)
    directory = content_dir(data, identifier)
    page: list[dict] = []
    seen: set[str] = set()
    revision_id = manifest["current_revision"]
    more = False
    while revision_id and revision_id not in seen:
        seen.add(revision_id)
        revision = _revision(directory, revision_id)
        revision_id = revision["parent"]
        if before is not None and revision["number"] >= before:
            continue
        if len(page) == limit:
            more = True
            break
        page.append(revision)
    items = [
        RevisionInfo(
            **{k: r[k] for k in ("id", "number", "parent", "sha256", "size", "created")},
            restored_from=r.get("restored_from"),
            current=r["id"] == manifest["current_revision"],
        )
        for r in page
    ]
    return items, page[-1]["number"] if more else None


def save(data: Path, identifier: str, raw: bytes, if_match: str, key: str) -> tuple[int, dict]:
    validate_scene(raw)
    digest = request_hash("save", identifier, if_match, sha256(raw))
    with _writing(data, identifier):
        manifest = _load(data, identifier)
        if hit := _replay(content_dir(data, identifier), manifest, _key_hash(key), digest):
            return hit
        _check_source(manifest, if_match)
        return _append(data, manifest, raw, "save", _key_hash(key), digest)


def _check_source(manifest: dict, if_match: str | None) -> None:
    if not _same_tag(if_match, source_etag(manifest["current_revision"])):
        raise HTTPException(412, "図表が更新されています。最新の状態を読み込んでください。")


def restore(
    data: Path, identifier: str, revision_id: str, if_match: str, key: str
) -> tuple[int, dict]:
    digest = request_hash("restore", identifier, if_match, revision_id)
    with _writing(data, identifier):
        manifest = _load(data, identifier)
        directory = content_dir(data, identifier)
        if hit := _replay(directory, manifest, _key_hash(key), digest):
            return hit
        _check_source(manifest, if_match)
        if not valid_id(revision_id):
            raise HTTPException(404, "版が見つかりません。")
        _revision(directory, revision_id)
        raw = _current_bytes(directory, revision_id)
        return _append(
            data, manifest, raw, "restore", _key_hash(key), digest, restored_from=revision_id
        )


def _draft(manifest: dict, draft_id: str) -> dict:
    draft = manifest.get("draft")
    if not draft or draft["id"] != draft_id:
        raise HTTPException(404, "下書きが見つかりません。")
    return draft


def _draft_file(content: Path, draft: dict, version: int | None = None) -> Path:
    return content / "drafts" / draft["id"] / "versions" / str(version or draft["version"])


def draft_create(data: Path, identifier: str, raw: bytes | None, if_match: str) -> DraftInfo:
    if raw is not None:
        validate_scene(raw)
    with _writing(data, identifier):
        manifest = _load(data, identifier)
        _check_source(manifest, if_match)
        if manifest["draft"]:
            raise HTTPException(409, f"既に下書きがあります。draft_id: {manifest['draft']['id']}")
        directory = content_dir(data, identifier)
        if raw is None:
            raw = _current_bytes(directory, manifest["current_revision"])
        draft = {
            "id": uuid4().hex,
            "version": 1,
            "base_revision": manifest["current_revision"],
            "versions": [1],
            "updated": time.time(),
        }
        write_file(_draft_file(directory, draft) / "original.excalidraw", raw)
        manifest["draft"] = draft
        _publish(directory, manifest)
        return _draft_info(draft)


def draft_info(data: Path, identifier: str, draft_id: str) -> DraftInfo:
    return _draft_info(_draft(_load(data, identifier), draft_id))


def draft_content(data: Path, identifier: str, draft_id: str) -> tuple[bytes, DraftInfo]:
    for _ in range(3):
        draft = _draft(_load(data, identifier), draft_id)
        try:
            raw = (
                _draft_file(content_dir(data, identifier), draft) / "original.excalidraw"
            ).read_bytes()
        except OSError:
            continue  # a newer version replaced it between the manifest and file reads
        return raw, _draft_info(draft)
    raise HTTPException(404, "下書きが見つかりません。")


def draft_update(
    data: Path, identifier: str, draft_id: str, raw: bytes, if_match: str
) -> DraftInfo:
    validate_scene(raw)
    with _writing(data, identifier):
        manifest = _load(data, identifier)
        draft = _draft(manifest, draft_id)
        if not _same_tag(if_match, draft_etag(draft["id"], draft["version"])):
            raise HTTPException(412, "下書きが更新されています。最新の状態を読み込んでください。")
        directory = content_dir(data, identifier)
        version = draft["version"] + 1
        write_file(_draft_file(directory, draft, version) / "original.excalidraw", raw)
        versions = [*draft["versions"], version]
        removed, draft["versions"] = versions[:-DRAFT_VERSIONS], versions[-DRAFT_VERSIONS:]
        draft["version"], draft["updated"] = version, time.time()
        _publish(directory, manifest)
        for old in removed:
            shutil.rmtree(_draft_file(directory, draft, old), ignore_errors=True)
        return _draft_info(draft)


def draft_commit(
    data: Path, identifier: str, draft_id: str, source: str, if_match: str, key: str
) -> tuple[int, dict]:
    digest = request_hash("commit", identifier, draft_id, if_match, source)
    with _writing(data, identifier):
        manifest = _load(data, identifier)
        directory = content_dir(data, identifier)
        if hit := _replay(directory, manifest, _key_hash(key), digest):
            return hit
        draft = _draft(manifest, draft_id)
        if not _same_tag(if_match, draft_etag(draft["id"], draft["version"])):
            raise HTTPException(412, "下書きが更新されています。最新の状態を読み込んでください。")
        _check_source(manifest, source)
        if draft["base_revision"] != manifest["current_revision"]:
            raise HTTPException(412, "下書きの基準になった版が更新されています。")
        try:
            raw = (_draft_file(directory, draft) / "original.excalidraw").read_bytes()
        except OSError:
            raise HTTPException(404, "下書きの内容が見つかりません。") from None
        return _append(data, manifest, raw, "commit", _key_hash(key), digest, drop_draft=True)


def draft_discard(data: Path, identifier: str, draft_id: str, if_match: str) -> None:
    with _writing(data, identifier):
        manifest = _load(data, identifier)
        draft = _draft(manifest, draft_id)
        if not _same_tag(if_match, draft_etag(draft["id"], draft["version"])):
            raise HTTPException(412, "下書きが更新されています。最新の状態を読み込んでください。")
        directory = content_dir(data, identifier)
        manifest["draft"] = None
        _publish(directory, manifest)
        shutil.rmtree(directory / "drafts" / draft["id"], ignore_errors=True)


def operation_result(data: Path, identifier: str, operation_id: str) -> dict:
    manifest = _load(data, identifier)
    entry = next((e for e in manifest["operations"].values() if e["id"] == operation_id), None)
    if not entry or entry["created"] < time.time() - OPERATION_TTL or not valid_id(operation_id):
        raise HTTPException(404, "操作結果が見つかりません。")
    try:
        stored = json.loads(
            (content_dir(data, identifier) / "operations" / f"{operation_id}.json").read_text()
        )
    except (OSError, ValueError):
        raise HTTPException(404, "操作結果が見つかりません。") from None
    return stored["result"]


def _repair_one(data: Path, manifest: dict) -> bool:
    identifier = manifest["id"]
    folder = document_folder(data, identifier)
    if folder.parent == data or not safe_path(data, folder):
        return False
    changed = not folder.is_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if not job_file(folder).is_file():
        job = Job(
            id=identifier,
            filename=manifest["filename"],
            state="success",
            created=manifest["created"],
            preview="excalidraw",
            excalidraw=True,
        )
        save_job(folder, job)
        changed = True
    with LOCK:
        library = load_library(data)
        if identifier not in library.files:
            parent = manifest.get("folder_id")
            _place(
                data,
                identifier,
                manifest["filename"],
                parent if parent in library.folders else None,
            )
            changed = True
    return changed


def _stale(path: Path, now: float) -> bool:
    try:
        return path.stat().st_mtime < now - STALE
    except OSError:
        return False


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _clean(content: Path, manifest: dict, now: float) -> int:
    removed = 0

    def drop(path: Path) -> None:
        nonlocal removed
        if _stale(path, now):
            _remove(path)
            removed += 1

    chain, revision_id = set(), manifest.get("current_revision")
    try:
        while revision_id:
            if revision_id in chain:
                LOGGER.warning("図表 %s の版の親が循環しています。", manifest.get("id"))
                chain = set()
                break
            chain.add(revision_id)
            revision_id = json.loads(
                (content / "revisions" / revision_id / "revision.json").read_text()
            )["parent"]
    except (OSError, ValueError, KeyError):
        chain = set()
    if chain and len(chain) == manifest["revision_count"]:
        for path in (content / "revisions").glob("*"):
            if path.name not in chain:
                drop(path)
    draft = manifest.get("draft")
    for path in (content / "drafts").glob("*"):
        if not draft or path.name != draft["id"]:
            drop(path)
        else:
            for version in (path / "versions").glob("*"):
                if version.name not in {str(v) for v in draft["versions"]}:
                    drop(version)
    referenced = {f"{e['id']}.json" for e in manifest["operations"].values()}
    for path in (content / "operations").glob("*"):
        if path.name not in referenced:
            drop(path)
    for path in content.rglob("*.tmp"):
        drop(path)
    return removed


def repair_diagrams(data: Path) -> dict:
    """Startup repair: rebuild missing job.json/derived folders and drop stale unreferenced files."""
    repaired: list[str] = []
    removed = 0
    root = data / "content" / "documents"
    if not root.is_dir():
        return {"repaired": repaired, "removed": removed}
    now = time.time()
    with exclusive_write(data, "diagram"):
        for directory in sorted(root.iterdir()):
            if not directory.is_dir() or directory.is_symlink() or not valid_id(directory.name):
                continue
            with _lock(directory.name):
                try:
                    manifest = _read_manifest(data, directory.name)
                    if manifest is None:
                        if (
                            (directory / "revisions").is_dir()
                            and not (directory / "manifest.json").exists()
                            and not document_folder(data, directory.name).exists()
                            and _stale(directory, now)
                        ):
                            removed += 1
                            shutil.rmtree(directory, ignore_errors=True)
                        continue
                    if _repair_one(data, manifest):
                        repaired.append(directory.name)
                    removed += _clean(directory, manifest, now)
                except (KeyError, TypeError, ValueError, OSError, HTTPException) as error:
                    LOGGER.warning("図表 %s の修復をスキップしました: %s", directory.name, error)
    if repaired:
        notify()
    return {"repaired": repaired, "removed": removed}
