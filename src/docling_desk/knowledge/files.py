"""Portable Wiki originals and an atomically published catalog; no SQLite here."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from docling_desk.knowledge.content import normalize_path


def sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class WikiFiles:
    def __init__(self, data: Path):
        self.root = data / "content"
        self.manifest = self.root / "manifests" / "wiki.json"

    def load(self) -> dict[str, dict]:
        if not self.manifest.exists():
            return {}
        value = json.loads(self.manifest.read_text(encoding="utf-8"))
        if value.get("version") != 1 or not isinstance(value.get("articles"), dict):
            raise ValueError("Wikiの保存情報が不正です。")
        paths = set()
        for sid, article in value["articles"].items():
            if not isinstance(article, dict) or article.get("id") != sid:
                raise ValueError("Wikiの記事IDが不正です。")
            if any(
                not isinstance(article.get(field), str)
                for field in ("namespace", "path", "title", "revision", "key", "sha256")
            ):
                raise ValueError("Wikiの保存情報が不正です。")
            logical = (article["namespace"], normalize_path(article["path"]))
            if logical in paths or article.get("deleted", 0) not in {0, 1}:
                raise ValueError("Wikiの記事情報が重複または不正です。")
            paths.add(logical)
            metadata = article["metadata"]
            if not isinstance(
                metadata if isinstance(metadata, dict) else json.loads(metadata), dict
            ):
                raise ValueError("Wikiの記事情報が不正です。")
        return value["articles"]

    def stage(self, source: dict, raw: str, previous: dict | None = None) -> dict:
        # Keep logical relative paths in the catalog. Hash the namespace to avoid
        # collisions and platform-specific directory naming restrictions.
        path = normalize_path(source["path"])
        checksum = sha256(raw)
        metadata = json.loads(source["metadata"])
        if previous and previous["sha256"] == checksum:
            self.read(previous)
            record = {
                **previous,
                **{k: v for k, v in source.items() if k != "body"},
                "metadata": metadata,
            }
            if {k: v for k, v in record.items() if k != "updated"} == {
                k: v for k, v in previous.items() if k != "updated"
            }:
                return previous
            return record
        key = f"wiki/revisions/{uuid4().hex}/{sha256(source['namespace'])[:32]}/{path}"
        target = self.target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="") as stream:
            stream.write(raw)
        record = {
            **{k: v for k, v in source.items() if k != "body"},
            "key": key,
            "sha256": checksum,
        }
        record["metadata"] = metadata
        return record

    def target(self, key: str) -> Path:
        if not isinstance(key, str) or not key.startswith("wiki/revisions/") or "\\" in key:
            raise ValueError("Wikiの保存パスが不正です。")
        relative = Path(key)
        if relative.is_absolute() or any(p in {".", ".."} for p in relative.parts):
            raise ValueError("Wikiの保存パスが不正です。")
        target = self.root / relative
        if any(p.is_symlink() for p in (self.root, target, *target.parents)):
            raise ValueError("Wikiの保存パスが不正です。")
        return target

    def read(self, article: dict) -> str:
        raw = self.target(article["key"]).read_bytes().decode("utf-8")
        if sha256(raw) != article["sha256"]:
            raise ValueError("Wiki本文が保存情報のハッシュと一致しません。")
        return raw

    def publish(self, articles: dict[str, dict]) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        if any(p.is_symlink() for p in (self.manifest, *self.manifest.parents)):
            raise ValueError("Wikiの保存先が不正です。")
        temporary = self.manifest.with_name(f".wiki-{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps({"version": 1, "articles": articles}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.manifest)
        finally:
            temporary.unlink(missing_ok=True)
