"""Local layout with portable content keys and local-only databases.

Job folders contain derived artifacts. Originals live separately; display folders
remain library metadata, so renaming a document never renames its storage key.
Legacy job folders remain readable until startup migration completes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from docling_desk.blob_mirror import notify
from docling_desk.sqlite_writer import connect


def safe_path(data: Path, path: Path) -> bool:
    root = data.resolve()
    if not path.resolve().is_relative_to(root):
        return False
    return not any(
        p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)
    )


def document_root(data: Path) -> Path:
    return data / "derived" / "documents"


def document_folder(data: Path, identifier: str) -> Path:
    current = document_root(data) / identifier
    legacy = data / identifier
    return legacy if not current.exists() and legacy.is_dir() else current


def document_folders(data: Path) -> list[Path]:
    current = {p.parent.name: p.parent for p in document_root(data).glob("*/job.json")}
    current.update(
        {
            p.parent.name: document_root(data) / p.parent.name
            for p in (data / "runtime" / "documents").glob("*/job.json")
            if (document_root(data) / p.parent.name).is_dir()
        }
    )
    legacy = {p.parent.name: p.parent for p in data.glob("*/job.json")}
    return list({**legacy, **current}.values())


def data_root(folder: Path) -> Path:
    if folder.parent.name == "documents" and folder.parent.parent.name == "derived":
        return folder.parent.parent.parent
    return folder.parent


def managed(folder: Path) -> bool:
    return folder.parent == document_root(data_root(folder))


def job_file(folder: Path) -> Path:
    if managed(folder):
        return data_root(folder) / "runtime" / "documents" / folder.name / "job.json"
    return folder / "job.json"


def original_file(folder: Path, suffix: str) -> Path:
    if managed(folder):
        target = data_root(folder) / "content" / "documents" / folder.name / ("original" + suffix)
        if target.exists() or not (folder / ("original" + suffix)).exists():
            return target
    return folder / ("original" + suffix)


def knowledge_database(data: Path) -> Path:
    return data / "runtime" / "knowledge" / "local.sqlite"


def explanation_database(folder: Path) -> Path:
    if managed(folder):
        return data_root(folder) / "runtime" / "documents" / folder.name / "explanation.sqlite"
    return folder / "explanations" / "index.sqlite"


def document_cache(folder: Path) -> Path:
    if managed(folder):
        return data_root(folder) / "cache" / "documents" / folder.name
    return folder


def source_folder(source: Path) -> Path:
    directory = source.parent
    if directory.parent.name == "documents" and directory.parent.parent.name == "content":
        return document_root(directory.parent.parent.parent) / directory.name
    return directory


def record_original(folder: Path, filename: str, created: float) -> None:
    if not managed(folder):
        return
    source = original_file(folder, Path(filename).suffix.lower())
    target = source.parent / "manifest.json"
    with source.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    value = {
        "version": 1,
        "id": folder.name,
        "filename": filename,
        "created": created,
        "key": source.relative_to(data_root(folder) / "content").as_posix(),
        "sha256": checksum,
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def record_artifacts(folder: Path, job: dict) -> None:
    if not managed(folder) or job["state"] not in {"success", "partial"}:
        return
    value = {
        "version": 1,
        "job": {k: v for k, v in job.items() if k not in {"ocr_profile", "error", "error_code"}},
    }
    target = folder / "manifest.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def document_paths(folder: Path) -> tuple[Path, ...]:
    if managed(folder):
        root = data_root(folder)
        return (
            root / "content" / "documents" / folder.name,
            root / "runtime" / "documents" / folder.name,
            root / "cache" / "documents" / folder.name,
            folder,
        )
    return (folder,)


def remove_document(folder: Path) -> None:
    paths = document_paths(folder)
    if any(not safe_path(data_root(folder), path) for path in paths):
        raise ValueError("資料の保存先が不正です。")
    for path in paths:
        if path.exists():
            shutil.rmtree(path)
    notify()  # mirror the deletion now rather than at the next interval


def _move(source: Path, target: Path) -> None:
    if any(p.is_symlink() for p in (source, target, *source.parents, *target.parents)):
        raise ValueError("保存先のシンボリックリンクは移行できません。")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with source.open("rb") as src, target.open("rb") as dst:
            if (
                hashlib.file_digest(src, "sha256").digest()
                != hashlib.file_digest(dst, "sha256").digest()
            ):
                raise ValueError("移行先に異なる内容のファイルがあります。")
        source.unlink()
    else:
        source.replace(target)


def _checksum(path: Path) -> str | None:
    if not path.exists():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _database_signature(source: Path) -> list[str | None]:
    return [_checksum(Path(str(source) + suffix)) for suffix in ("", "-wal")]


def migrate_database(source: Path, target: Path) -> None:
    marker = target.with_suffix(".migration.json")
    if not source.exists():
        marker.unlink(missing_ok=True)
        return
    if any(p.is_symlink() for p in (source, target, marker, *source.parents, *target.parents)):
        raise ValueError("データベースの移行先が既に存在するか不正です。")
    if target.exists():
        if not marker.is_file():
            raise ValueError("データベースの移行先が既に存在します。")
        record = json.loads(marker.read_text())
        if record != {"source": _database_signature(source), "target": _checksum(target)}:
            raise ValueError("中断した移行のデータベースが変更されています。")
        source.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(source) + suffix).unlink(missing_ok=True)
        marker.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".migrating")
    try:
        with connect(source) as src, connect(temporary) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("データベースの整合性確認に失敗しました。")
            dst.execute("PRAGMA journal_mode=DELETE")
        marker.write_text(
            json.dumps({"source": _database_signature(source), "target": _checksum(temporary)})
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    source.unlink()
    for suffix in ("-wal", "-shm"):
        Path(str(source) + suffix).unlink(missing_ok=True)
    marker.unlink(missing_ok=True)


def migrate_layout(data: Path) -> None:
    """Run at startup, under the shared write lock and before starting workers."""
    for name in ("content", "derived", "runtime"):
        if not safe_path(data, data / name):
            raise ValueError("保存先のシンボリックリンクは移行できません。")
        (data / name).mkdir(parents=True, exist_ok=True)
    if (data / "library.json").exists():
        _move(data / "library.json", data / "content" / "manifests" / "library.json")
    for old in data.glob("*/job.json"):
        folder = old.parent
        if folder.is_symlink():
            raise ValueError("資料の保存先が不正です。")
        target = document_root(data) / folder.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("新旧の保存領域に同じ資料IDがあります。")
        folder.replace(target)
    for folder in document_folders(data):
        for source in folder.glob("original.*"):
            target = data / "content" / "documents" / folder.name / source.name
            _move(source, target)
        migrate_database(folder / "explanations" / "index.sqlite", explanation_database(folder))
        if (folder / "job.json").is_file():
            _move(folder / "job.json", job_file(folder))
        job = json.loads(job_file(folder).read_text())
        if not (folder / "manifest.json").exists():
            record_artifacts(folder, job)
        source = original_file(folder, Path(job["filename"]).suffix.lower())
        if source.is_file() and not (source.parent / "manifest.json").exists():
            record_original(folder, job["filename"], job["created"])
    migrate_database(data / "knowledge" / "local.sqlite", knowledge_database(data))
