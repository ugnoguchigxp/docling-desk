"""Translation artifacts are independent of conversion jobs and original documents."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from docling_desk.storage import document_folders

LOCK = threading.RLock()
LANGUAGES = {"en", "ja"}
ACTIVE = {"queued", "running", "waiting"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def result_path(folder: Path, language: str, unit_id: str) -> Path:
    import re

    if language not in LANGUAGES or not re.fullmatch(
        r"(?:slide|sheet|page|document)-[1-9]\d*", unit_id
    ):
        raise ValueError("翻訳言語または単位が不正です。")
    return folder / "translations" / language / f"{unit_id}.json"


def read_result(folder: Path, language: str, unit_id: str) -> dict:
    path = result_path(folder, language, unit_id)
    if not path.exists():
        return {"state": "untranslated", "result": None, "error": None}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("unit_id") != unit_id:
        raise ValueError("保存済み翻訳データを読み込めません。")
    return value


def usable(record: dict, unit: dict) -> bool:
    result = record.get("result")
    return bool(
        result
        and result.get("source_hash") == unit["source_hash"]
        and set(result.get("translations", {})) == {s["id"] for s in unit["segments"]}
        and all(isinstance(v, str) for v in result["translations"].values())
    )


def interrupt_pending(data: Path) -> None:
    with LOCK:
        paths = set(data.glob("*/translations/*/*.json"))
        paths.update(
            p for folder in document_folders(data) for p in folder.glob("translations/*/*.json")
        )
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            if value.get("state") in ACTIVE:
                value.update(
                    state="interrupted",
                    error="サーバー停止で中断しました。再実行できます。",
                    updated_at=now(),
                    wait_reason=None,
                    next_attempt_at=None,
                )
                try:
                    atomic_json(path, value)
                except OSError:
                    continue


def summary(folder: Path) -> dict:
    """Cheap library summary, without generating source maps or calling a provider."""
    result = {}
    with LOCK:
        for language in sorted(LANGUAGES):
            records = []
            for path in (folder / "translations" / language).glob("*.json"):
                try:
                    record = json.loads(path.read_text())
                    if not isinstance(record, dict):
                        raise ValueError("invalid record")
                    records.append(record)
                except (OSError, ValueError):
                    records.append({"state": "failed"})
            if records:
                result[language] = {
                    "saved": sum(bool(r.get("result")) for r in records),
                    "active": sum(r.get("state") in ACTIVE for r in records),
                    "failed": sum(r.get("state") in {"failed", "interrupted"} for r in records),
                }
    return result
