"""Token encodings read only from pinned local copies, never fetched while serving.

``tiktoken`` downloads its BPE files on first use. The installer fetches them once
with the models (``download_models``) into ``<models>/tiktoken``; at run time the
copy is checked against the hash tiktoken itself pins before tiktoken reads it
from that cache, so a missing or altered file fails instead of going online.
"""

from __future__ import annotations

import hashlib
import os
import threading
from functools import cache
from pathlib import Path

import httpx
import tiktoken

from docling_desk import config
from docling_desk.atomic_files import atomic_write_bytes

# The URLs and hashes tiktoken_ext.openai_public pins for tiktoken 0.12.
ENCODINGS = {
    "cl100k_base": (
        "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    ),
    "o200k_base": (
        "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    ),
}
MAX_ENCODING_BYTES = 16 * 1024 * 1024
_lock = threading.Lock()


def directory(models: Path | None = None) -> Path:
    return (config.MODELS if models is None else models) / "tiktoken"


def cache_path(name: str, models: Path | None = None) -> Path:
    # tiktoken names its cache entries by the SHA-1 of the source URL.
    return directory(models) / hashlib.sha1(ENCODINGS[name][0].encode()).hexdigest()


def problems(models: Path | None = None) -> list[str]:
    """``missing:tiktoken/<name>`` or ``revision:tiktoken/<name>`` for each unusable copy."""
    found = []
    for name, (_, expected) in ENCODINGS.items():
        path = cache_path(name, models)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            found.append(f"missing:tiktoken/{name}")
            continue
        if hashlib.sha256(data).hexdigest() != expected:
            found.append(f"revision:tiktoken/{name}")
    return found


@cache
def _verified(name: str, path: Path) -> None:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise RuntimeError(
            f"トークン辞書 {name} がありません。download_models を実行してください。"
        ) from None
    if hashlib.sha256(data).hexdigest() != ENCODINGS[name][1]:
        raise RuntimeError(
            f"トークン辞書 {name} が固定版と一致しません。download_models を実行してください。"
        )


def encoding(name: str) -> tiktoken.Encoding:
    """The tiktoken encoding, loaded only from the verified local copy."""
    if name not in ENCODINGS:
        raise ValueError(f"unknown encoding: {name}")
    with _lock:
        _verified(name, cache_path(name))
        # Point tiktoken at the verified copy; it re-checks the same hash on read.
        os.environ["TIKTOKEN_CACHE_DIR"] = str(directory())
        return tiktoken.get_encoding(name)


def install(models: Path | None = None) -> None:
    """Fetch each pinned encoding once, verify it, then publish it atomically."""
    for name, (url, expected) in ENCODINGS.items():
        path = cache_path(name, models)
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.content
        if len(data) > MAX_ENCODING_BYTES or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError(f"downloaded encoding does not match its pinned hash: {name}")
        atomic_write_bytes(path, data)
