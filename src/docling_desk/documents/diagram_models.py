"""Excalidraw scene validation and response models for standalone diagrams."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re

from fastapi import HTTPException
from pydantic import BaseModel

MAX_SCENE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_ELEMENTS = 10_000
MAX_DEPTH = 100
MAX_COORDINATE = 1_000_000
ELEMENT_TYPES = {
    "rectangle",
    "diamond",
    "ellipse",
    "arrow",
    "line",
    "freedraw",
    "text",
    "image",
    "frame",
}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
SURROGATE = re.compile(r"\\u[dD][89a-fA-F]")
LINK = re.compile(r"(?:https?:|mailto:)", re.IGNORECASE)
SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}


class DraftInfo(BaseModel):
    id: str
    version: int
    base_revision: str
    etag: str
    updated: float


class DiagramInfo(BaseModel):
    id: str
    name: str
    folder_id: str | None
    revision_id: str
    revision_number: int
    sha256: str
    size: int
    etag: str
    created: float
    updated: float
    draft: DraftInfo | None
    operation_id: str | None = None


class RevisionInfo(BaseModel):
    id: str
    number: int
    parent: str | None
    sha256: str
    size: int
    created: float
    restored_from: str | None
    current: bool


def invalid(detail: str) -> HTTPException:
    return HTTPException(422, detail)


def _reject_constant(name: str):
    raise ValueError(name)


def parse_json(raw: bytes, label: str = "図表", depth: int = MAX_DEPTH) -> object:
    """Strict JSON: UTF-8 only, no NaN/Infinity, bounded nesting, finite numbers."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise invalid(f"{label}はUTF-8のJSONで指定してください。") from None
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except RecursionError:
        raise invalid(f"{label}の入れ子が深すぎます。") from None
    except ValueError:
        raise invalid(
            f"{label}を読み込めません。有限の数値だけのJSONを指定してください。"
        ) from None
    check_text = SURROGATE.search(text) is not None
    stack = [(value, 1)]
    while stack:
        node, level = stack.pop()
        if level > depth:
            raise invalid(f"{label}の入れ子が深すぎます。")
        if isinstance(node, float) and not math.isfinite(node):
            raise invalid(f"{label}に有限でない数値があります。")
        if check_text and (isinstance(node, str) or isinstance(node, dict)):
            try:
                for item in (node,) if isinstance(node, str) else node:
                    item.encode("utf-8")
            except UnicodeEncodeError:
                raise invalid(f"{label}に不正なUnicode文字があります。") from None
        if isinstance(node, dict):
            stack.extend((v, level + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, level + 1) for v in node)
    return value


def parse_object(raw: bytes, label: str = "要求", depth: int = MAX_DEPTH) -> dict:
    value = parse_json(raw, label, depth)
    if not isinstance(value, dict):
        raise invalid(f"{label}はJSONオブジェクトで指定してください。")
    return value


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _coordinate(value: object, label: str) -> None:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or abs(value) > MAX_COORDINATE
        or not math.isfinite(value)
    ):
        raise invalid(f"{label}は絶対値{MAX_COORDINATE:,}以下の有限の数値にしてください。")


def _element(item: object, seen: set[str]) -> None:
    if not isinstance(item, dict):
        raise invalid("要素はオブジェクトで指定してください。")
    identifier = item.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise invalid("要素のidは空でない文字列にしてください。")
    if identifier in seen:
        raise invalid("要素のidが重複しています。")
    seen.add(identifier)
    if item.get("type") not in ELEMENT_TYPES:
        raise invalid("対応していない要素の種類です。")
    for key in ("x", "y", "width", "height"):
        if key in item:
            _coordinate(item[key], f"要素の{key}")
    if "points" in item:
        points = item["points"]
        if not isinstance(points, list):
            raise invalid("要素のpointsは配列にしてください。")
        for point in points:
            if not isinstance(point, list) or len(point) < 2:
                raise invalid("要素のpointsは座標の配列にしてください。")
            for value in point[:2]:
                _coordinate(value, "要素の座標")
    link = item.get("link")
    if link is not None and link != "" and not (isinstance(link, str) and LINK.match(link)):
        raise invalid("リンクはhttp、https、mailtoのURLだけ指定できます。")


def _image(value: object) -> None:
    if not isinstance(value, dict):
        raise invalid("filesの各値はオブジェクトにしてください。")
    mime = value.get("mimeType")
    if mime not in IMAGE_TYPES:
        raise invalid("画像はPNG、JPEG、WebP、GIFだけ指定できます。")
    url = value.get("dataURL")
    prefix = f"data:{mime};base64,"
    if not isinstance(url, str) or not url.startswith(prefix):
        raise invalid("画像のdataURLが宣言した形式と一致しません。")
    encoded = url[len(prefix) :]
    if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
        raise invalid("画像は1枚4 MiB以下にしてください。")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise invalid("画像のbase64を読み込めません。") from None
    if len(body) > MAX_IMAGE_BYTES:
        raise invalid("画像は1枚4 MiB以下にしてください。")
    if mime == "image/webp":
        matched = body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    else:
        matched = any(body.startswith(s) for s in SIGNATURES[mime])
    if not matched:
        raise invalid("画像の内容が宣言した形式と一致しません。")


def validate_scene(raw: bytes) -> dict:
    """Validate without rewriting: callers store the received bytes unchanged."""
    if len(raw) > MAX_SCENE_BYTES:
        raise HTTPException(413, "図表は16 MiB以下にしてください。")
    return validate_object(parse_json(raw, "図表"))


def validate_object(scene: object) -> dict:
    if not isinstance(scene, dict):
        raise invalid("図表はJSONオブジェクトで指定してください。")
    if scene.get("type") != "excalidraw":
        raise invalid('図表のtypeは"excalidraw"にしてください。')
    version = scene.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise invalid("図表のversionは整数にしてください。")
    elements = scene.get("elements")
    if not isinstance(elements, list):
        raise invalid("図表のelementsは配列にしてください。")
    if len(elements) > MAX_ELEMENTS:
        raise invalid(f"要素は{MAX_ELEMENTS:,}個以下にしてください。")
    if "appState" in scene and not isinstance(scene["appState"], dict):
        raise invalid("図表のappStateはオブジェクトにしてください。")
    if "files" in scene and not isinstance(scene["files"], dict):
        raise invalid("図表のfilesはオブジェクトにしてください。")
    seen: set[str] = set()
    for item in elements:
        _element(item, seen)
    for value in scene.get("files", {}).values():
        _image(value)
    return scene
