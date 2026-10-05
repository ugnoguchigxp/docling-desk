"""Wiki file contracts shared by the upload API, workspace importer and batch."""

from __future__ import annotations

import csv
import io
import json
import posixpath
import re
from pathlib import Path

import yaml

STATES = {"untranslated", "translated", "reviewed", "needs_review"}


def split_frontmatter(raw: str) -> tuple[str, dict]:
    if not raw.startswith("---\n") and not raw.startswith("---\r\n"):
        return raw, {}
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", raw, re.S)
    if not match or len(match[1]) > 256 * 1024:
        raise ValueError("Markdownの管理情報が不正です。")
    try:
        metadata = yaml.safe_load(match[1]) or {}
    except yaml.YAMLError as exc:
        raise ValueError("Markdownの管理情報が不正です。") from exc
    if not isinstance(metadata, dict):
        raise ValueError("Markdownの管理情報はオブジェクトにしてください。")
    return raw[match.end() :], metadata


def article_metadata(meta: dict) -> dict:
    result = {
        key: meta[key]
        for key in (
            "title",
            "language",
            "translation_group",
            "source_job_id",
            "source_unit",
            "translation_status",
            "source_hash",
            "category",
            "external_key",
            "external_id",
            "entry_kind",
            "workspace",
            "collection",
        )
        if key in meta and meta[key] is not None
    }
    if meta.get("id") is not None:
        result["external_id"] = str(meta["id"])
    if meta.get("collection") and result.get("external_id"):
        result.setdefault("external_key", f"{meta['collection']}/{result['external_id']}")
    if result.get("external_key"):
        result.setdefault("translation_group", result["external_key"])
    status = result.get("translation_status")
    if "translation_status" in result and (not isinstance(status, str) or status not in STATES):
        raise ValueError("記事の翻訳状態が不正です。")
    for key, value in result.items():
        if key == "source_unit":
            if type(value) is not int or not 1 <= value <= 10000:
                raise ValueError("関連資料の位置が不正です。")
        elif not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise ValueError("記事の管理情報が不正です。")
    return result


def evidence_body(body: str) -> str:
    body = re.sub(r"^\[目次\].*\n", "", body, count=1, flags=re.M)
    return re.split(r"^## 書き出し原本\s*$", body, maxsplit=1, flags=re.M)[0].strip()


def csv_rows(body: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(body))
    columns = reader.fieldnames
    if not columns or len(columns) != len(set(columns)) or len(columns) > 100:
        raise ValueError("CSV目次の列が不正です。")
    rows = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CSV目次の列数が一致しません。")
        rows.append(row)
        if len(rows) > 10000:
            raise ValueError("CSV目次は10,000行以内にしてください。")
    return columns, rows


def manifest_metadata(raw: str, filename: str = "wiki-manifest.json") -> dict[str, dict]:
    try:
        if filename.endswith(".jsonl"):
            records = [json.loads(line) for line in raw.splitlines() if line.strip()]
            result = {}
            for record in records:
                for language, key, title in (
                    ("original", "original_path", "title_original"),
                    ("ja", "ja_path", "title_ja"),
                ):
                    metadata = article_metadata(
                        {
                            "external_key": record["key"],
                            "external_id": record["id"],
                            "collection": record["collection"],
                            "language": language,
                            "translation_status": record["translation_status"],
                            "source_hash": record["source_hash"],
                            "title": record.get(title),
                        }
                    )
                    if record[key] in result:
                        raise ValueError("manifestの対応パスが重複しています。")
                    result[record[key]] = metadata
            return result
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"articles"}
            or not isinstance(value["articles"], dict)
        ):
            raise ValueError
        return {path: article_metadata(m) for path, m in value["articles"].items()}
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("manifestの記事情報が不正です。") from exc


def safe_file(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative or "\x00" in relative or relative.startswith("/"):
        raise ValueError("資料の相対パスが不正です。")
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ValueError("資料の相対パスが不正です。")
    current = root.resolve()
    for part in relative.split("/"):
        # Case-sensitive IDs must stay distinct even on case-insensitive filesystems.
        if current.is_dir() and part not in {p.name for p in current.iterdir()}:
            raise ValueError("資料が見つかりません。")
        current = current / part
        if current.is_symlink():
            raise ValueError("資料のシンボリックリンクは参照できません。")
    current.resolve().relative_to(root.resolve())
    return current


def resolve_path(path: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(path), href))
