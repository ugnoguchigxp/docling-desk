"""Persisted-format compatibility, atomic files and host-wide worker locking."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


class NeedsReview(ValueError):
    pass


class Stopped(Exception):
    pass


class RetryLater(Exception):
    def __init__(self, message: str, deadline: int):
        super().__init__(message)
        self.deadline = deadline


class FatalProviderError(Exception):
    pass


def js_json(value) -> str:
    """JSON.stringify-compatible bytes for the values in persisted snapshots."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        text = json.dumps(value, ensure_ascii=False)
        return "".join(f"\\u{ord(c):04x}" if 0xD800 <= ord(c) <= 0xDFFF else c for c in text)
    if isinstance(value, (int, float)):
        if isinstance(value, int):
            return str(value)
        if not math.isfinite(value):
            return "null"
        if value == 0:
            return "0"
        number = repr(value)
        if 1e-6 <= abs(value) < 1e21:
            fixed = format(Decimal(number), "f")
            return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
        mantissa, exponent = number.lower().split("e") if "e" in number.lower() else (number, "0")
        return (
            mantissa.removesuffix(".0")
            + "e"
            + ("+" if int(exponent) >= 0 else "-")
            + str(abs(int(exponent)))
        )
    if isinstance(value, list):
        return "[" + ",".join(js_json(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = list(value)
        integer = [
            k
            for k in keys
            if isinstance(k, str) and k.isdigit() and str(int(k)) == k and int(k) < 2**32 - 1
        ]
        keys = sorted(integer, key=int) + [k for k in keys if k not in integer]
        return "{" + ",".join(js_json(str(k)) + ":" + js_json(value[k]) for k in keys) + "}"
    raise TypeError("JSONに保存できない値です。")


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def utf16_offset(value: str, offset: int) -> int:
    return utf16_length(value[:offset])


def atomic_write(file: Path, text: str) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + "." + uuid4().hex + ".tmp")
    try:
        with os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(file)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(file: Path, value) -> None:
    atomic_write(file, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def frontmatter(meta: dict) -> str:
    return (
        "---\n" + "".join(f"{key}: {js_json(value)}\n" for key, value in meta.items()) + "---\n\n"
    )


@contextmanager
def worker_lock(root: Path):
    file = root / "data/translation/worker.lock"
    file.parent.mkdir(parents=True, exist_ok=True)
    if file.is_symlink():
        raise ValueError("ワーカーロックの保存先が不正です。")
    with file.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("翻訳バッチは既に実行中です。") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
