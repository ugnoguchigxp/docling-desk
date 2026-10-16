"""Wiki authoring using the existing immutable originals and manifest storage."""

import hashlib
import io
import re
from collections.abc import Callable
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from docling_desk.knowledge.article_images import body_images, document_images, with_body_image
from docling_desk.knowledge.article_info import ArticleInfo, is_wiki_image
from docling_desk.knowledge.content import normalize_path
from docling_desk.knowledge.editor_document import EditorDocument
from docling_desk.knowledge.files import article_etag
from docling_desk.knowledge.store import WikiChanged, WikiConflict
from docling_desk.operations.backup import exclusive_write

MAX_IMAGE_BYTES = 3 * 1024 * 1024
IMAGE_TYPES = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "GIF": ("gif", "image/gif"),
    "WEBP": ("webp", "image/webp"),
}
MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "bin": "application/octet-stream",
}
MAX_MEDIA_BYTES = 20 * 1024 * 1024


class WikiSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    language: str = Field(default="ja", pattern=r"^[a-zA-Z][a-zA-Z0-9-]{0,31}$")
    article_info: ArticleInfo = Field(default_factory=ArticleInfo)
    editor_document: EditorDocument | None = None
    etag: str | None = Field(default=None, max_length=100)

    @field_validator("namespace", "title")
    @classmethod
    def valid_name(cls, value, info):
        value = value.strip()
        if (
            not value
            or any(ord(c) < 32 for c in value)
            or (info.field_name == "namespace" and any(c in value for c in "/\\"))
        ):
            raise ValueError("分類名・タイトルが不正です。")
        return value

    @field_validator("path")
    @classmethod
    def markdown_path(cls, value):
        value = normalize_path(value)
        if not value.lower().endswith((".md", ".markdown")):
            raise ValueError("新しい記事は.mdまたは.markdownで保存してください。")
        return value

    @field_validator("body")
    @classmethod
    def valid_body(cls, value):
        if (
            not value.strip()
            or len(value.encode("utf-8")) > 2 * 1024 * 1024
            or any(ord(c) < 32 and c not in "\r\n\t" for c in value)
        ):
            raise ValueError("本文は空でないUTF-8のMarkdownで、2 MiB以内にしてください。")
        return value


def create_router(manager: Callable) -> APIRouter:
    router = APIRouter()

    def store_blob(knowledge, folder, blob_id, raw):
        with exclusive_write(knowledge.store.data, "index"):
            target = knowledge.store.files.target(f"wiki/{folder}/{blob_id}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temporary = target.with_name(f".{uuid4().hex}.tmp")
                try:
                    temporary.write_bytes(raw)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)

    def editable(knowledge, source_id):
        record = knowledge.store.files.load().get(source_id)
        if (
            not record
            or record.get("deleted")
            or not record["path"].lower().endswith((".md", ".markdown"))
        ):
            raise HTTPException(404, "編集できるMarkdown記事が見つかりません。")
        return {
            "id": source_id,
            **{k: record[k] for k in ("namespace", "path", "title")},
            "body": knowledge.store.files.read(record),
            "language": record["metadata"].get("language", "ja"),
            "article_info": with_body_image(
                ArticleInfo.model_validate(record["metadata"].get("article_info", {})),
                knowledge.store.files.read(record),
                record["metadata"].get("editor_document"),
            ).model_dump(),
            "etag": article_etag(record),
            "editor_document": record["metadata"].get("editor_document"),
        }

    @router.get("/api/wiki/sources/{source_id}/edit")
    def edit(request: Request, source_id: str):
        knowledge = manager(request)
        with exclusive_write(knowledge.store.data, "index"):
            return editable(knowledge, source_id)

    @router.put("/api/wiki/sources/{source_id}")
    async def save(request: Request, source_id: str, payload: WikiSave):
        if not re.fullmatch(r"wiki-[a-f0-9]{32}", source_id):
            raise HTTPException(422, "記事IDが不正です。")
        knowledge = manager(request)

        def commit():
            with exclusive_write(knowledge.store.data, "index"):
                document = payload.editor_document.model_dump() if payload.editor_document else None
                image_urls = [image.attrGet("src") or "" for image in body_images(payload.body)]
                image_urls += [image["url"] for image in document_images(document)]
                for image_url in image_urls:
                    if (
                        is_wiki_image(image_url)
                        and not knowledge.store.files.target(
                            "wiki/images/" + image_url.rsplit("/", 1)[1]
                        ).is_file()
                    ):
                        raise HTTPException(
                            422, "本文の画像が見つかりません。画像を選び直してください。"
                        )
                knowledge.store.save_wiki(
                    source_id,
                    payload.namespace,
                    payload.path,
                    payload.body,
                    {
                        "title": payload.title,
                        "language": payload.language,
                        "article_info": with_body_image(
                            payload.article_info, payload.body, document
                        ).model_dump(),
                        "editor_document": document,
                    },
                    payload.etag,
                )
                return editable(knowledge, source_id)

        try:
            return await run_in_threadpool(commit)
        except WikiConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except WikiChanged as exc:
            raise HTTPException(412, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/api/wiki/images", status_code=201)
    async def image(request: Request, file: UploadFile = File(...)):
        try:
            raw = await file.read(MAX_IMAGE_BYTES + 1)
        finally:
            await file.close()
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "画像は3 MiB以内にしてください。")
        try:
            with Image.open(io.BytesIO(raw)) as picture:
                extension, media_type = IMAGE_TYPES[picture.format]
                picture.verify()
        except (KeyError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise HTTPException(422, "PNG・JPEG・GIF・WebPの画像を選択してください。") from exc
        image_id = hashlib.sha256(raw).hexdigest() + "." + extension
        knowledge = manager(request)
        store_blob(knowledge, "images", image_id, raw)
        return {"url": "/api/wiki/images/" + image_id, "media_type": media_type}

    @router.post("/api/wiki/media", status_code=201)
    async def media(request: Request, file: UploadFile = File(...)):
        extension = next(
            (ext for ext, mime in MEDIA_TYPES.items() if mime == file.content_type), "bin"
        )
        try:
            raw = await file.read(MAX_MEDIA_BYTES + 1)
        finally:
            await file.close()
        if not raw or len(raw) > MAX_MEDIA_BYTES:
            raise HTTPException(413, "添付ファイルは空でない20 MiB以内のファイルにしてください。")
        blob_id = hashlib.sha256(raw).hexdigest() + "." + extension
        store_blob(manager(request), "media", blob_id, raw)
        return {"url": "/api/wiki/media/" + blob_id, "media_type": MEDIA_TYPES[extension]}

    @router.get("/api/wiki/media/{blob_id}")
    def get_media(request: Request, blob_id: str):
        if not re.fullmatch(r"[a-f0-9]{64}\.(mp3|wav|ogg|m4a|mp4|webm|bin)", blob_id):
            raise HTTPException(404)
        target = manager(request).store.files.target("wiki/media/" + blob_id)
        if not target.is_file():
            raise HTTPException(404)
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != blob_id.split(".")[0]:
            raise HTTPException(409, "保存したファイルのハッシュが一致しません。")
        extension = blob_id.rsplit(".", 1)[1]
        return Response(
            raw,
            media_type=MEDIA_TYPES[extension],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, max-age=31536000, immutable",
                "Content-Disposition": "attachment" if extension == "bin" else "inline",
            },
        )

    @router.get("/api/wiki/images/{image_id}")
    def get_image(request: Request, image_id: str):
        if not re.fullmatch(r"[a-f0-9]{64}\.(png|jpg|gif|webp)", image_id):
            raise HTTPException(404)
        target = manager(request).store.files.target("wiki/images/" + image_id)
        if not target.is_file():
            raise HTTPException(404)
        media_type = next(
            media for ext, media in IMAGE_TYPES.values() if ext == image_id.rsplit(".", 1)[1]
        )
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != image_id.rsplit(".", 1)[0]:
            raise HTTPException(409, "保存した画像のハッシュが一致しません。")
        return Response(
            raw,
            media_type=media_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=31536000, immutable",
            },
        )

    return router
