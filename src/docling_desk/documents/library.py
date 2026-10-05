"""Persist library organization separately from conversion jobs and their source artifacts."""

from __future__ import annotations

import json
import shutil
import threading
import time
import unicodedata
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from docling_desk.documents.conversion import Job, save_job
from docling_desk.explanation.store import LOCK as EXPLANATION_LOCK
from docling_desk.explanation.store import active as explanation_active
from docling_desk.explanation.store import copy_saved
from docling_desk.operations.faults import checkpoint
from docling_desk.storage import (
    document_folder,
    document_folders,
    document_paths,
    job_file,
    original_file,
    record_original,
    remove_document,
    safe_path,
)
from docling_desk.translation.store import LOCK as TRANSLATION_LOCK
from docling_desk.translation.store import summary as translation_summary

LOCK = threading.RLock()


class Folder(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    created: float = Field(default_factory=time.time)


class Placement(BaseModel):
    name: str
    parent_id: str | None = None


class Library(BaseModel):
    folders: dict[str, Folder] = Field(default_factory=dict)
    files: dict[str, Placement] = Field(default_factory=dict)


class FolderCreate(BaseModel):
    name: str
    parent_id: str | None = None


class ItemRef(BaseModel):
    kind: Literal["file", "folder"]
    id: str


class Operation(BaseModel):
    action: Literal["rename", "move", "copy", "delete"]
    items: list[ItemRef] = Field(min_length=1, max_length=500)
    name: str | None = None
    destination: str | None = None


def load(data: Path) -> Library:
    path = data / "content" / "manifests" / "library.json"
    if not path.exists():
        path = data / "library.json"
    return Library.model_validate_json(path.read_text()) if path.exists() else Library()


def save(data: Path, library: Library) -> None:
    root = data / "content" / "manifests"
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / "library.tmp"
    try:
        checkpoint("library_save_before_write")
        temporary.write_text(library.model_dump_json(indent=2), encoding="utf-8")
        checkpoint("library_save_before_replace")
        temporary.replace(root / "library.json")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    checkpoint("library_save_after_replace")


def valid_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name.strip())
    if (
        not name
        or name in {".", ".."}
        or len(name) > 200
        or any(c in '/\\:*?"<>|' or ord(c) < 32 or ord(c) == 127 for c in name)
        or name.endswith(".")
    ):
        raise HTTPException(
            422,
            '名前は200文字以内で、/ \\ : * ? " < > | や末尾のピリオドを含めず入力してください。',
        )
    return name


def ancestors(library: Library, parent: str | None) -> list[str]:
    result = []
    while parent is not None:
        if parent not in library.folders or parent in result:
            raise HTTPException(409, "フォルダー構造を読み込めません。")
        result.append(parent)
        parent = library.folders[parent].parent_id
    return result


def destination_exists(library: Library, parent: str | None) -> None:
    if parent is not None and parent not in library.folders:
        raise HTTPException(404, "移動先のフォルダーが見つかりません。")


def read_jobs(data: Path) -> list[Job]:
    return [
        Job.model_validate_json(p.read_text())
        for p in (job_file(f) for f in document_folders(data))
    ]


def placement(library: Library, job: Job) -> Placement:
    return library.files.get(job.id, Placement(name=job.filename))


def decorate(library: Library, job: Job) -> Job:
    item = placement(library, job)
    return job.model_copy(
        update={
            "filename": item.name,
            "original_filename": job.filename,
            "folder_id": item.parent_id,
        }
    )


def snapshot(data: Path) -> dict:
    with LOCK:
        library = load(data)
        return {
            "folders": [f.model_dump() for f in library.folders.values()],
            "jobs": [decorate(library, j).model_dump() for j in read_jobs(data)],
        }


def names_at(
    library: Library,
    jobs: dict[str, Job],
    parent: str | None,
    exclude: set[tuple[str, str]] | None = None,
) -> set[str]:
    exclude = exclude or set()
    items = [("folder", id, f) for id, f in library.folders.items()] + [
        ("file", id, placement(library, j)) for id, j in jobs.items()
    ]
    return {
        unicodedata.normalize("NFC", f.name).casefold()
        for kind, id, f in items
        if (kind, id) not in exclude and f.parent_id == parent
    }


def unique_name(name: str, occupied: set[str], copy: bool = False, file: bool = False) -> str:
    stem, suffix = (Path(name).stem, Path(name).suffix) if file else (name, "")
    candidate = f"{stem} (コピー){suffix}" if copy else name
    index = 2
    while candidate.casefold() in occupied:
        candidate = f"{stem} ({'コピー ' if copy else ''}{index}){suffix}"
        index += 1
    return candidate


def create_folder(data: Path, request: FolderCreate) -> Folder:
    with LOCK:
        library = load(data)
        destination_exists(library, request.parent_id)
        name = valid_name(request.name)
        if name.casefold() in names_at(
            library, {j.id: j for j in read_jobs(data)}, request.parent_id
        ):
            raise HTTPException(409, "同じ名前の項目があります。別の名前を指定してください。")
        folder = Folder(id=uuid4().hex, name=name, parent_id=request.parent_id)
        library.folders[folder.id] = folder
        save(data, library)
        return folder


def register_upload(data: Path, job: Job, parent: str | None) -> None:
    """Called under LOCK together with saving and scheduling the new job."""
    library = load(data)
    destination_exists(library, parent)
    name = unique_name(
        valid_name(job.filename),
        names_at(library, {j.id: j for j in read_jobs(data)}, parent),
        file=True,
    )
    library.files[job.id] = Placement(name=name, parent_id=parent)
    save(data, library)


def operate(data: Path, request: Operation) -> dict:
    with LOCK, TRANSLATION_LOCK, EXPLANATION_LOCK:
        library = load(data)
        jobs = {j.id: j for j in read_jobs(data)}
        refs = {(r.kind, r.id) for r in request.items}
        items: dict[tuple[str, str], Folder | Placement] = {}
        for kind, id in refs:
            if kind == "folder":
                if id not in library.folders:
                    raise HTTPException(404, "フォルダーが見つかりません。")
                item = library.folders[id]
            else:
                if id not in jobs:
                    raise HTTPException(404, "資料が見つかりません。")
                item = library.files.setdefault(id, placement(library, jobs[id]))
            items[(kind, id)] = item
        # A selected folder already includes its selected descendants.
        items = {
            ref: item
            for ref, item in items.items()
            if not any(("folder", p) in refs for p in ancestors(library, item.parent_id))
        }
        if request.action == "rename" and len(items) != 1:
            raise HTTPException(422, "名前変更は1件ずつ行ってください。")
        if request.action in {"move", "copy"}:
            destination_exists(library, request.destination)
            for kind, id in items:
                if kind == "folder" and (
                    request.destination == id or id in ancestors(library, request.destination)
                ):
                    raise HTTPException(
                        409, "自分自身や配下のフォルダーには移動・コピーできません。"
                    )
        affected_jobs = [
            j
            for j in jobs.values()
            if ("file", j.id) in items
            or any(
                ("folder", p) in items for p in ancestors(library, placement(library, j).parent_id)
            )
        ]
        if request.action in {"delete", "copy"} and any(
            j.state in {"queued", "running"} for j in affected_jobs
        ):
            raise HTTPException(
                409, "抽出中・待機中の資料を含んでいます。完了後に操作してください。"
            )
        if request.action in {"delete", "copy"} and any(
            counts["active"]
            for job in affected_jobs
            for counts in translation_summary(document_folder(data, job.id)).values()
        ):
            raise HTTPException(
                409, "翻訳中・待機中の資料を含んでいます。完了後に操作してください。"
            )
        if request.action in {"delete", "copy"} and any(
            explanation_active(document_folder(data, job.id)) for job in affected_jobs
        ):
            raise HTTPException(
                409, "解説中・待機中の資料を含んでいます。完了後に操作してください。"
            )
        if request.action == "delete":
            # Resolve the entire selection before touching originals. Parent selection
            # includes descendants; overlapping file selections are removed only once.
            folder_ids = {
                id
                for id, f in library.folders.items()
                if ("folder", id) in items
                or any(("folder", p) in items for p in ancestors(library, f.parent_id))
            }
            file_ids = {j.id for j in affected_jobs}
            for id in file_ids:
                if (
                    len(id) != 32
                    or any(c not in "0123456789abcdef" for c in id)
                    or any(
                        not safe_path(data, path)
                        for path in document_paths(document_folder(data, id))
                    )
                ):
                    raise HTTPException(409, "資料の保存先が不正なため削除できません。")
            removed = set()
            try:
                for id in sorted(file_ids):
                    remove_document(document_folder(data, id))
                    removed.add(id)
            except OSError as exc:
                # Physical deletes cannot be rolled back. Retain remaining folders
                # and report a partial failure so the UI can refresh and retry.
                for id in removed:
                    library.files.pop(id, None)
                save(data, library)
                raise HTTPException(
                    500,
                    "一部の資料を削除できませんでした。一覧を更新しました。残った項目を再度選んでください。",
                ) from exc
            library.files = {
                id: entry
                for id, entry in library.files.items()
                if id not in file_ids and entry.parent_id not in folder_ids
            }
            library.folders = {id: f for id, f in library.folders.items() if id not in folder_ids}
            save(data, library)
            return {"items": [{"kind": kind, "id": id} for kind, id in items]}
        new_paths: list[Path] = []
        affected = []
        try:
            for (kind, id), item in items.items():
                if request.action == "rename":
                    name = valid_name(request.name or "")
                    if (
                        kind == "file"
                        and Path(name).suffix.lower() != Path(jobs[id].filename).suffix.lower()
                    ):
                        raise HTTPException(422, "ファイルの拡張子は変更できません。")
                    if name.casefold() in names_at(library, jobs, item.parent_id, {(kind, id)}):
                        raise HTTPException(
                            409, "同じ名前の項目があります。別の名前を指定してください。"
                        )
                    item.name = name
                elif request.action == "move":
                    if item.name.casefold() in names_at(
                        library, jobs, request.destination, {(kind, id)}
                    ):
                        raise HTTPException(
                            409, "移動先に同じ名前の項目があります。先に名前を変更してください。"
                        )
                    item.parent_id = request.destination
                else:
                    copied_name = unique_name(
                        item.name,
                        names_at(library, jobs, request.destination),
                        copy=True,
                        file=kind == "file",
                    )

                    def copy_item(
                        source_kind: str, source_id: str, parent: str | None, name: str
                    ) -> str:
                        new_id = uuid4().hex
                        if source_kind == "folder":
                            library.folders[new_id] = Folder(id=new_id, name=name, parent_id=parent)
                            for f in list(library.folders.values()):
                                if f.parent_id == source_id:
                                    copy_item("folder", f.id, new_id, f.name)
                            for j in list(jobs.values()):
                                entry = placement(library, j)
                                if entry.parent_id == source_id:
                                    copy_item("file", j.id, new_id, entry.name)
                        else:
                            source_job = jobs[source_id]
                            path = document_folder(data, new_id)
                            new_paths.append(path)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copytree(
                                document_folder(data, source_id),
                                path,
                                ignore=shutil.ignore_patterns("explanations", "original.*"),
                            )
                            suffix = Path(source_job.filename).suffix.lower()
                            original = original_file(path, suffix)
                            original.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(
                                original_file(document_folder(data, source_id), suffix), original
                            )
                            new_job = source_job.model_copy(
                                update={"id": new_id, "created": time.time()}
                            )
                            record_original(path, new_job.filename, new_job.created)
                            save_job(path, new_job)
                            for rag in path.glob("rag*.jsonl"):
                                rows = [json.loads(line) for line in rag.read_text().splitlines()]
                                for row in rows:
                                    for key in ("id", "parent_id"):
                                        if isinstance(row.get(key), str):
                                            row[key] = row[key].replace(
                                                source_id + ":", new_id + ":"
                                            )
                                rag.write_text(
                                    "".join(
                                        json.dumps(row, ensure_ascii=False) + "\n" for row in rows
                                    ),
                                    encoding="utf-8",
                                )
                            copy_saved(document_folder(data, source_id), path)
                            jobs[new_id] = new_job
                            library.files[new_id] = Placement(name=name, parent_id=parent)
                        return new_id

                    copied_id = copy_item(kind, id, request.destination, copied_name)
                    affected.append({"kind": kind, "id": copied_id})
                    continue
                affected.append({"kind": kind, "id": id})
            save(data, library)
        except Exception:
            for path in new_paths:
                if path.exists():
                    remove_document(path)
            raise
        return {"items": affected}
