"""Validated image originals, sharing the Wiki's immutable Markdown image URLs."""

from __future__ import annotations

import hashlib
import io
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator

from docling_desk.knowledge.files import WikiFiles
from docling_desk.operations.backup import exclusive_write
from docling_desk.preview.thumbnails import encode_thumbnail
from docling_desk.storage import data_root, document_cache, safe_path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
IMAGE_TYPES = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "GIF": ("gif", "image/gif"),
    "WEBP": ("webp", "image/webp"),
}
MAX_IMAGE_PIXELS = 40_000_000


class ImageInfo(BaseModel):
    reference: str
    sha256: str
    media_type: str
    byte_size: int
    width: int
    height: int
    animated: bool = False


class ImageMetadata(BaseModel):
    description: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    default_alt: str = Field(default="", max_length=1000)
    default_credit: str = Field(default="", max_length=2000)
    source_url: str = Field(default="", max_length=2048)
    updated_at: float | None = None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("タグは1件100文字以内で入力してください。")
        return list(dict.fromkeys(value.strip() for value in values))

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, value):
        from docling_desk.knowledge.article_info import public_url

        return public_url(value)


def inspect_image(source: Path) -> ImageInfo:
    raw = source.read_bytes()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as picture:
                extension, media_type = IMAGE_TYPES[picture.format]
                expected = "jpg" if source.suffix.lower() == ".jpeg" else source.suffix[1:].lower()
                if expected != extension:
                    raise ValueError("画像の形式と拡張子が一致しません。")
                if picture.width * picture.height > MAX_IMAGE_PIXELS:
                    raise ValueError("画像は4000万画素以内にしてください。")
                picture.verify()
            # verify() alone does not decode JPEG/WebP pixels or reject truncated data.
            with Image.open(io.BytesIO(raw)) as picture:
                picture.load()
                width, height = picture.size
                if picture.getexif().get(274) in {5, 6, 7, 8}:
                    width, height = height, width
                animated = bool(getattr(picture, "is_animated", False))
    except (
        KeyError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("PNG・JPEG・GIF・WebPの有効な画像を選んでください。") from exc
    checksum = hashlib.sha256(raw).hexdigest()
    return ImageInfo(
        reference=f"/api/wiki/images/{checksum}.{extension}",
        sha256=checksum,
        media_type=media_type,
        byte_size=len(raw),
        width=width,
        height=height,
        animated=animated,
    )


def publish_image(data: Path, source: Path, info: ImageInfo) -> None:
    """Keep the shared MD original even if its library placement is later deleted."""
    target = WikiFiles(data).target("wiki/images/" + info.reference.rsplit("/", 1)[1])
    with exclusive_write(data, "index"):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            if hashlib.sha256(target.read_bytes()).hexdigest() != info.sha256:
                raise ValueError("保存済み画像のハッシュが一致しません。")
            return
        temporary = target.with_name(f".{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(source.read_bytes())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def image_thumbnail(folder: Path, source: Path, info: ImageInfo) -> Path:
    target = document_cache(folder) / "thumbnails" / f"image-{info.sha256}.webp"
    if not safe_path(data_root(folder), target) or not safe_path(data_root(folder), source):
        raise ValueError("画像の保存先が不正です。")
    if target.is_file():
        return target
    with Image.open(source) as picture:
        picture.seek(0)
        image = ImageOps.exif_transpose(picture)
        image.thumbnail((480, 360))
        if image.mode in {"RGBA", "LA", "P"}:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        raw = encode_thumbnail(image, size=(480, 360), max_bytes=32_000)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(raw)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
