"""Copy an idle file workspace and its SQLite ledger into an empty destination."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from docling_desk.knowledge.catalog import safe_file
from docling_desk.sqlite_writer import connect

from .files import NeedsReview, write_json
from .repository import Repository


def file_hash(file):
    with file.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def import_workspace(source: Path, target: Path, data: Path):
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("移行元と移行先は独立したフォルダーにしてください。")
    allowed = {"data/translation/worker.lock"}
    if any(
        p.is_symlink() or (p.is_file() and p.relative_to(target).as_posix() not in allowed)
        for p in target.rglob("*")
    ):
        raise ValueError("移行先は空のWikiワークスペースにしてください。")
    Repository(source, data)  # Validate the file contract without indexing or creating source DBs.
    staging = target / (".import-" + uuid4().hex)
    inventory = []
    with ExitStack() as locks:
        for relative in ("data/translation/worker.lock", "data/wiki-write.lock"):
            file = source / relative
            if file.exists():
                handle = locks.enter_context(safe_file(source, relative).open("rb"))
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise NeedsReview(
                        "移行元でバッチまたはWiki更新が実行中です。停止後に移行してください。"
                    ) from exc
        database = source / "data/translation.sqlite"
        try:
            staging.mkdir()
            for directory in ("sources", "wiki", "manifests", "data/translation"):
                base = source / directory
                if not base.exists():
                    continue
                if base.is_symlink():
                    raise NeedsReview("移行元にシンボリックリンクがあります。")
                for file in sorted(base.rglob("*")):
                    if file.is_symlink():
                        raise NeedsReview("移行元にシンボリックリンクがあります。")
                    if not file.is_file() or file.relative_to(source).as_posix() in {
                        "data/translation/worker.lock"
                    }:
                        continue
                    relative = file.relative_to(source).as_posix()
                    safe_file(source, relative)
                    digest = file_hash(file)
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, destination)
                    if file_hash(destination) != digest:
                        raise NeedsReview("コピー中に移行元の資料が更新されました。")
                    shutil.copystat(file, destination)
                    inventory.append(
                        {"path": relative, "sha256": digest, "bytes": file.stat().st_size}
                    )
            if database.exists():
                database = safe_file(source, "data/translation.sqlite")
                with connect(database.as_uri() + "?mode=ro", uri=True) as old:
                    row = old.execute(
                        "SELECT value FROM settings WHERE key='worker_pid'"
                    ).fetchone()
                    if row and int(row[0]) > 0:
                        try:
                            os.kill(int(row[0]), 0)
                        except ProcessLookupError:
                            pass
                        else:
                            raise NeedsReview(
                                "移行元の翻訳ワーカーが稼働しています。停止後に移行してください。"
                            )
                    destination = staging / "data/translation.sqlite"
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with connect(destination) as new:
                        old.backup(new)
                        if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise NeedsReview("移行した翻訳DBの検査に失敗しました。")
                        # The copied worker stays paused until an explicit operator run.
                        for key, value in (
                            ("worker_pid", "0"),
                            ("worker_stage", ""),
                            ("paused", "1"),
                        ):
                            new.execute(
                                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (key, value),
                            )
            for record in inventory:
                if file_hash(safe_file(source, record["path"])) != record["sha256"]:
                    raise NeedsReview("コピー中に移行元の資料が更新されました。")
            Repository(staging, data)
            write_json(
                staging / "manifests/migration.json",
                {"source": str(source), "files": inventory, "sqlite_backup": database.exists()},
            )
            # Staging has been validated; preserve the caller's held destination lock.
            for item in staging.iterdir():
                if item.name == "data":
                    (target / "data").mkdir(exist_ok=True)
                    for child in item.iterdir():
                        if child.name == "translation":
                            shutil.copytree(child, target / "data/translation", dirs_exist_ok=True)
                        else:
                            child.replace(target / "data" / child.name)
                else:
                    item.replace(target / item.name)
            result = Repository(target, data).sync()
            return {
                **result,
                "copied_files": len(inventory),
                "translation_history": database.exists(),
                "paused": True,
            }
        finally:
            shutil.rmtree(staging, ignore_errors=True)
