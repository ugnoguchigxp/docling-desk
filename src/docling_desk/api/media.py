"""Image metadata and thumbnails; original bytes use the existing Wiki image resolver."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import ConfigDict, Field

from docling_desk import config
from docling_desk.api.dependencies import job_detail, job_folder
from docling_desk.documents.images import ImageMetadata, image_thumbnail
from docling_desk.documents.library import (
    LOCK,
    ancestors,
    decorate,
    load,
    names_at,
    placement,
    read_jobs,
    save,
    valid_name,
)
from docling_desk.storage import original_file, safe_path

router = APIRouter()


class MediaUpdate(ImageMetadata):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)


def identifier(asset_id: str) -> str:
    try:
        return UUID(asset_id).hex
    except ValueError:
        raise HTTPException(404, "画像が見つかりません。") from None


def descriptor(library, job):
    info = job.image
    if info is None:
        raise HTTPException(404, "画像が見つかりません。")
    entry = placement(library, job)
    metadata = entry.image_metadata or ImageMetadata()
    etag = (
        '"'
        + hashlib.sha256(
            json.dumps(
                [entry.name, metadata.model_dump()], ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()
        + '"'
    )
    asset_id = str(UUID(job.id))
    return {
        "contract_version": "docling.media/1",
        "asset_id": asset_id,
        **info.model_dump(),
        "original_filename": job.original_filename or job.filename,
        "display_name": entry.name,
        "folder_id": entry.parent_id,
        "folder_path": [
            library.folders[p].name for p in reversed(ancestors(library, entry.parent_id))
        ],
        **{k: v for k, v in metadata.model_dump().items() if k != "updated_at"},
        "created_at": datetime.fromtimestamp(job.created, UTC).isoformat(),
        "updated_at": datetime.fromtimestamp(metadata.updated_at or job.created, UTC).isoformat(),
        "metadata_etag": etag,
        "availability": "available",
        "thumbnail": {"state": "ready", "href": f"/api/v1/media/{asset_id}/thumbnail"},
    }


@router.get("/api/v1/media")
def search_media(
    q: str = "",
    reference: str | None = None,
    folder_id: str | None = None,
    recursive: bool = False,
    media_type: str | None = None,
    sort: Literal["created_desc", "created_asc", "name_asc"] = "created_desc",
    cursor: str | None = None,
    limit: int = Query(default=48, ge=1, le=100),
):
    types = set(media_type.split(",")) if media_type else set()
    if types - {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        raise HTTPException(422, "画像の形式が不正です。")
    signature = hashlib.sha256(
        json.dumps([q, reference, folder_id, recursive, media_type, sort]).encode()
    ).hexdigest()
    offset = 0
    if cursor:
        try:
            page = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (
                page["query"] != signature
                or not isinstance(page["offset"], int)
                or page["offset"] < 0
            ):
                raise ValueError
            offset = page["offset"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "検索条件が変わりました。先頭から検索してください。") from None
    with LOCK:
        library = load(config.DATA)
        if folder_id not in {None, "root"} and folder_id not in library.folders:
            raise HTTPException(404, "フォルダーが見つかりません。")
        values = [descriptor(library, job) for job in read_jobs(config.DATA) if job.image]
    query = unicodedata.normalize("NFC", q).casefold()
    values = [
        v
        for v in values
        if (
            (reference is None or v["reference"] == reference)
            and (not types or v["media_type"] in types)
            and query
            in unicodedata.normalize(
                "NFC",
                " ".join([v["display_name"], v["original_filename"], v["description"], *v["tags"]]),
            ).casefold()
            and (
                folder_id is None
                or v["folder_id"] == (None if folder_id == "root" else folder_id)
                or (
                    recursive
                    and folder_id != "root"
                    and folder_id in ancestors(library, v["folder_id"])
                )
            )
        )
    ]
    values.sort(
        key=lambda v: (
            (v["display_name"].casefold() if sort == "name_asc" else v["created_at"]),
            v["asset_id"],
        ),
        reverse=sort == "created_desc",
    )
    next_offset = offset + limit
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps({"query": signature, "offset": next_offset}).encode()
        ).decode()
        if next_offset < len(values)
        else None
    )
    return {
        "contract_version": "docling.media/1",
        "items": values[offset:next_offset],
        "next_cursor": next_cursor,
    }


@router.get("/api/v1/media/{asset_id}")
def get_media(asset_id: str):
    with LOCK:
        return descriptor(load(config.DATA), job_detail(identifier(asset_id)))


@router.patch("/api/v1/media/{asset_id}")
def update_media(asset_id: str, value: MediaUpdate, if_match: str | None = Header(default=None)):
    with LOCK:
        job = job_detail(identifier(asset_id))
        library = load(config.DATA)
        current = descriptor(library, job)
        if if_match is None:
            raise HTTPException(428, "画像情報を再取得して保存してください。")
        if if_match != current["metadata_etag"]:
            raise HTTPException(412, "画像情報が更新されました。再読込して確認してください。")
        name = valid_name(value.display_name)
        if Path(name).suffix.lower() != Path(job.original_filename or job.filename).suffix.lower():
            raise HTTPException(422, "ファイルの拡張子は変更できません。")
        entry = library.files.setdefault(job.id, placement(library, job))
        if name.casefold() in names_at(
            library, {j.id: j for j in read_jobs(config.DATA)}, entry.parent_id, {("file", job.id)}
        ):
            raise HTTPException(409, "同じ名前の項目があります。")
        entry.name = name
        entry.image_metadata = ImageMetadata(
            **value.model_dump(exclude={"display_name", "updated_at"}), updated_at=time.time()
        )
        save(config.DATA, library)
        return descriptor(library, decorate(library, job))


@router.get("/api/v1/media/{asset_id}/thumbnail")
def get_thumbnail(asset_id: str):
    job_id = identifier(asset_id)
    job = job_detail(job_id)
    if not job.image:
        raise HTTPException(404)
    try:
        folder = job_folder(job_id)
        source = original_file(folder, Path(job.original_filename or job.filename).suffix.lower())
        target = image_thumbnail(folder, source, job.image)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(404, "サムネイルを取得できません。") from exc
    return FileResponse(
        target, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"}
    )


@router.get("/api/v1/media/{asset_id}/content")
def get_content(asset_id: str):
    job_id = identifier(asset_id)
    job = job_detail(job_id)
    if not job.image:
        raise HTTPException(404)
    source = original_file(
        job_folder(job_id), Path(job.original_filename or job.filename).suffix.lower()
    )
    if not safe_path(config.DATA, source) or not source.is_file():
        raise HTTPException(404, "画像原本が見つかりません。")
    raw = source.read_bytes()
    if len(raw) != job.image.byte_size or hashlib.sha256(raw).hexdigest() != job.image.sha256:
        raise HTTPException(409, "画像原本のハッシュが一致しません。")
    return FileResponse(
        source, media_type=job.image.media_type, headers={"ETag": '"' + job.image.sha256 + '"'}
    )
