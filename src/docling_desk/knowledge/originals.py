"""Local, explicitly registered workspace sources, opened without following links."""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

from docling_desk import config
from docling_desk.documents.library import LOCK
from docling_desk.operations.backup import exclusive_write
from docling_desk.wiki_batch.files import hash_text, write_json


class Originals:
    def __init__(self, data: Path):
        self.data = data.resolve()
        self.registry = self.data / "runtime/knowledge/wiki-workspaces.json"

    def load(self):
        if not self.registry.exists():
            return {}
        value = json.loads(self.registry.read_text(encoding="utf-8"))
        if value.get("version") != 1 or not isinstance(value.get("workspaces"), dict):
            raise ValueError("原本領域の登録が不正です。")
        return value["workspaces"]

    def register(self, root: Path):
        root = root.resolve()
        # Sync explicitly registers only sources/, never the workspace's data or secrets.
        if (root / "sources").is_symlink():
            raise ValueError("原本領域にシンボリックリンクは指定できません。")
        workspace = hash_text(str(root))
        with exclusive_write(self.data, "index"), LOCK:
            workspaces = self.load()
            workspaces[workspace] = {"root": str(root), "prefix": "sources"}
            write_json(self.registry, {"version": 1, "workspaces": workspaces})
        return workspace

    @contextmanager
    def open(self, source: dict, path: str):
        parts = path.split("/")
        if (
            not source.get("workspace")
            or not path.startswith("sources/")
            or any(p in {"", ".", ".."} for p in parts)
            or "\\" in path
            or any(ord(c) < 32 for c in path)
            or len(path) > 4096
        ):
            raise ValueError("登録した原本領域の外は参照できません。")
        entry = self.load().get(source["workspace"])
        if (
            not isinstance(entry, dict)
            or entry.get("prefix") != "sources"
            or not isinstance(entry.get("root"), str)
            or not Path(entry["root"]).is_absolute()
            or hash_text(entry["root"]) != source["workspace"]
        ):
            raise ValueError("原本領域が登録されていません。Wikiワークスペースを同期してください。")
        # Walk directory descriptors, including the registered root's parents. A rename
        # or symlink swap cannot change the directory from which the next file opens.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            components = [*Path(entry["root"]).parts[1:], *parts]
            for i, part in enumerate(components):
                if part not in os.listdir(fd):
                    raise FileNotFoundError("原本が見つかりません。")
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if i < len(components) - 1:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("原本は通常のファイルを指定してください。")
            with os.fdopen(fd, "rb") as stream:
                fd = None
                yield stream
        finally:
            if fd is not None:
                os.close(fd)

    def link(self, source: dict, path: str, fragment: str = ""):
        try:
            with self.open(source, path):
                pass
        except (OSError, ValueError):
            return ""
        return (
            config.url(f"/api/wiki/sources/{quote(source['id'], safe='')}/original")
            + "?"
            + urlencode({"path": path})
            + ("#" + quote(fragment, safe="") if fragment else "")
        )
