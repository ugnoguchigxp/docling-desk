"""Load the authored fictional corpus. Gold text is taken from these files, not from extraction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from docling_desk.evaluation.recording import ROOT, hash_files
from docling_desk.knowledge.catalog import split_frontmatter

CORPUS = ROOT / "qa" / "semantic-quality" / "corpus"
CASES = ROOT / "qa" / "semantic-quality" / "cases.jsonl"
REPLAY = ROOT / "qa" / "semantic-quality" / "replay"
SCENARIOS = ROOT / "qa" / "performance" / "scenarios.json"
NAMESPACE = "evaluation-small"


def corpus_files() -> list[Path]:
    return sorted(path for path in CORPUS.glob("*.md") if path.is_file())


def load_articles() -> list[dict]:
    articles = []
    for path in corpus_files():
        raw = path.read_text(encoding="utf-8")
        body, meta = split_frontmatter(raw)
        key = meta.get("external_key")
        if not isinstance(key, str) or not key:
            raise ValueError(f"{path.name} に external_key がありません。")
        articles.append(
            {
                "path": path.name,
                "source_key": key,
                "raw": raw,
                "body": body,
                "sha256": hashlib.sha256(raw.encode()).hexdigest(),
            }
        )
    keys = [article["source_key"] for article in articles]
    if len(keys) != len(set(keys)):
        raise ValueError("コーパスの external_key が重複しています。")
    return articles


def load_cases() -> list[dict]:
    cases = [
        json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    validate_cases(cases)
    return cases


def validate_cases(cases: list[dict]) -> None:
    articles = article_map(load_articles())
    for case in cases:
        if case.get("review_status") not in {"draft", "approved"}:
            raise ValueError(f"{case.get('case_id')} の review_status は draft か approved です。")
        if _contains_key(case, "chunk_id"):
            raise ValueError("chunk_id は正解に固定しません。")
        if case.get("query_policy") == "question_as_written" and case.get(
            "search_query"
        ) != case.get("question"):
            raise ValueError(f"{case.get('case_id')} の自然な質問が検索語へ置き換えられています。")
        for passage in case.get("gold_passages", []):
            article = articles.get(passage.get("source_key"))
            if article is None or passage.get("text") not in article["body"]:
                raise ValueError(f"{case.get('case_id')} の正解文が原資料の本文にありません。")
        if case.get("previous_path"):
            previous = (CORPUS / case["previous_path"]).read_text(encoding="utf-8")
            current = articles[case["source_key"]]["body"]
            for surface in case.get("obsolete_surfaces", []):
                if surface not in previous or surface in current:
                    raise ValueError(
                        f"{case.get('case_id')} の廃止文が現行本文と旧本文で区別できません。"
                    )


def _contains_key(value, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def load_replay() -> list[dict]:
    records = []
    for path in sorted(REPLAY.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        record["path"] = path.name
        records.append(record)
    return records


def input_fingerprint() -> dict:
    paths = [
        *CORPUS.rglob("*.md"),
        CASES,
        SCENARIOS,
        *REPLAY.glob("*.json"),
    ]
    files = [path for path in paths if path.is_file()]
    return {"input_sha256": hash_files(files), "input_files": len(files)}


def article_map(articles: list[dict]) -> dict[str, dict]:
    return {article["source_key"]: article for article in articles}
