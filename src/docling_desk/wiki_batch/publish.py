"""Recoverable file/manifest/index publication without overwriting hand edits."""

from __future__ import annotations

import csv
import io
import json
import posixpath

from docling_desk.knowledge.catalog import csv_rows, evidence_body, safe_file, split_frontmatter

from .files import NeedsReview, atomic_write, frontmatter, hash_text, write_json
from .markdown import restore
from .writer_lock import wiki_lock


def assert_inputs(repository, snapshot, models):
    original = repository.read(snapshot["page"]["original_path"])
    translated = repository.read(snapshot["page"]["ja_path"])
    if (
        hash_text(evidence_body(original["body"])) != snapshot["sourceBodyHash"]
        or original["meta"].get("source_hash") != snapshot["page"]["source_hash"]
    ):
        raise NeedsReview("翻訳開始後に原文が変更されました。")
    if (
        snapshot["meta"].get("translation_status") != "untranslated"
        or hash_text(translated["raw"]) != snapshot["jaHash"]
    ):
        raise NeedsReview("翻訳開始後に日本語ページが編集されました。上書きしません。")
    if snapshot.get("models") and models != snapshot["models"]:
        raise NeedsReview("登録時からモデルのデプロイ名が変わりました。")


def candidate(store, job, snapshot, completed):
    values = {}
    for packet in snapshot["packets"]:
        saved = store.packet(job["id"], packet["position"])
        if saved["draft"]:
            values.update({r["id"]: r["text"] for r in json.loads(saved["draft"])["translations"]})

    def text(unit):
        if completed and not unit["literal"] and unit["id"] not in values:
            raise NeedsReview("公開する訳文に欠落があります。")
        return restore(
            unit, unit["text"] if unit["literal"] else values.get(unit["id"], unit["text"])
        )

    title = "".join(text(u) for u in snapshot["units"] if u["kind"] == "title").strip()
    category = "".join(text(u) for u in snapshot["units"] if u["kind"] == "category").strip()
    body = "".join(
        text(u) for u in snapshot["units"] if u["kind"] not in {"title", "category"}
    ).strip()
    meta = {
        **snapshot["meta"],
        "id": snapshot["page"]["id"],
        "collection": snapshot["page"]["collection"],
        "language": "ja",
        "title": title,
        "source_hash": snapshot["page"]["source_hash"],
        "counterpart_path": posixpath.relpath(
            snapshot["page"]["original_path"], posixpath.dirname(snapshot["page"]["ja_path"])
        ),
        "translation_status": "translated" if completed else "untranslated",
        "category_ja": category,
        "translation_job_id": job["id"],
        "translation_input_hash": snapshot["inputHash"],
        "translation_recipe_hash": snapshot["recipeHash"],
    }
    file = store.root / "data/translation/jobs" / job["id"] / "candidate.md"
    atomic_write(file, frontmatter(meta) + "# " + title + "\n\n" + body + "\n")
    return file


def publish(repository, job, snapshot, file):
    with wiki_lock(repository.root):
        return _publish(repository, job, snapshot, file)


def _publish(repository, job, snapshot, file):
    root = repository.root
    marker = root / "data/translation/publication.json"
    if marker.exists():
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if saved.get("id") != job["id"]:
            raise NeedsReview("別の記事の公開復旧が必要です。")
    else:
        saved = {
            "id": job["id"],
            "key": job["page_key"],
            "source_hash": snapshot["page"]["source_hash"],
            "source_body_hash": snapshot["sourceBodyHash"],
            "ja_hash": snapshot["jaHash"],
            "candidate_path": file.relative_to(root).as_posix(),
            "candidate_hash": hash_text(file.read_bytes().decode("utf-8")),
        }
        write_json(marker, saved)
    file = safe_file(root, saved["candidate_path"])
    file.resolve().relative_to((root / "data/translation/jobs" / job["id"]).resolve())
    raw = file.read_bytes().decode("utf-8")
    if hash_text(raw) != saved["candidate_hash"]:
        raise NeedsReview("公開候補が変更されました。")
    _, meta = split_frontmatter(raw)
    if any(
        meta.get(k) != v
        for k, v in {
            "id": snapshot["page"]["id"],
            "collection": snapshot["page"]["collection"],
            "language": "ja",
            "source_hash": saved["source_hash"],
            "translation_status": "translated",
            "translation_job_id": job["id"],
        }.items()
    ):
        raise NeedsReview("公開候補の識別情報が不正です。")
    repository.refresh()
    page = next((p for p in repository.pages if p["key"] == job["page_key"]), None)
    if page is None or page["source_hash"] != saved["source_hash"]:
        raise NeedsReview("原本の対応が変更されました。")
    original = repository.read(page["original_path"])
    if hash_text(evidence_body(original["body"])) != saved["source_body_hash"]:
        raise NeedsReview("翻訳開始後に原文が変更されました。")
    target = safe_file(root, page["ja_path"])
    current = target.read_bytes().decode("utf-8")
    if hash_text(current) != saved["candidate_hash"]:
        if (
            hash_text(current) != saved["ja_hash"]
            or split_frontmatter(current)[1].get("translation_status") != "untranslated"
        ):
            raise NeedsReview("日本語本文は編集済みです。上書きしません。")
        backup = file.parent / "before.md"
        if not backup.exists():
            atomic_write(backup, current)
        atomic_write(target, raw)
    page.update(
        {
            "translation_status": "translated",
            "title_ja": meta["title"],
            "category_ja": meta.get("category_ja", ""),
        }
    )
    atomic_write(
        root / "manifests/pages.jsonl",
        "".join(
            json.dumps(p, ensure_ascii=False, separators=(",", ":")) + "\n"
            for p in repository.pages
        ),
    )
    for language in ("original", "ja"):
        csv_file = root / f"wiki/pages/{language}/{page['collection']}/index.csv"
        if csv_file.exists():
            csv_file = safe_file(root, csv_file.relative_to(root).as_posix())
            columns, rows = csv_rows(csv_file.read_text(encoding="utf-8"))
            for row in rows:
                if row.get("id") == page["id"]:
                    if "translation_status" in row:
                        row["translation_status"] = "translated"
                    if language == "ja":
                        if "title" in row:
                            row["title"] = meta["title"]
                        if "category" in row:
                            row["category"] = meta.get("category_ja", "")
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            atomic_write(csv_file, buffer.getvalue())
    result = repository.sync(page["key"])
    if result.get("indexed") is not True:
        raise NeedsReview("公開本文の検索登録を確認できません。")
    if not any(
        h["key"] == page["key"] and h["language"] == "ja"
        for h in repository.search(page["id"], limit=50)["results"]
    ):
        # Exact ID is metadata; titles can change. Check published body via its stable key too.
        from docling_desk.knowledge.store import Store

        if not any(
            s.get("external_key") == page["key"]
            and s.get("language") == "ja"
            and s.get("translation_status") == "translated"
            and s["body"] == split_frontmatter(raw)[0]
            for s in Store(repository.data).sources("wiki")
        ):
            raise NeedsReview("公開本文の検索登録を確認できません。")
    write_json(root / "manifests/publication.json", {"generation": job["id"], "key": page["key"]})
    marker.unlink()
    return {"published": True, "indexed": True, "key": page["key"]}
