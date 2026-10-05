"""Deterministic document context packing; never truncate target text or a draft."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from docling_desk.explanation.provider import (
    INSTRUCTIONS,
    SCHEMAS,
    TASKS,
    ExplanationError,
    Profile,
)


@lru_cache(maxsize=1)
def encoding():
    import tiktoken

    return tiktoken.get_encoding("o200k_base")


def input_tokens(task: str, payload: dict) -> int:
    # Includes stable instructions, the exact serialized payload and output schema.
    # The SDK owns message framing; reserve space rather than count only user text.
    text = "\n".join(
        (
            INSTRUCTIONS,
            TASKS[task],
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            json.dumps(SCHEMAS[task].model_json_schema(), ensure_ascii=False),
        )
    )
    return len(encoding().encode(text, disallowed_special=())) + 5000


def target_blocks(unit: dict) -> list[dict]:
    return [
        {
            "part_id": f"{block['id']}:whole",
            "original_ref": block["id"],
            "kind": block["kind"],
            "pages": block["pages"],
            "text": block["text"],
        }
        for block in unit["blocks"]
    ]


def document_context(source: dict, unit: dict) -> tuple[list[dict], list[dict]]:
    outline, evidence = [], []
    for other in source["units"]:
        # Extracted text, not an invented summary. Every unit remains in the outline.
        title = (
            other.get("name")
            or next(
                (
                    b["text"].splitlines()[0][:160]
                    for b in other["blocks"]
                    if b["kind"] in {"title", "section_header"} and b["text"].strip()
                ),
                "",
            )
            or next(
                (b["text"].splitlines()[0][:160] for b in other["blocks"] if b["text"].strip()), ""
            )
        )
        outline.append(
            {
                "id": other["id"],
                "number": other["number"],
                "title": title,
                "evidence_id": f"outline-{other['id']}",
            }
        )
        if other["id"] == unit["id"]:
            continue
        if other["blocks"]:
            evidence.append(
                {
                    "id": f"context-{other['id']}",
                    "kind": "document",
                    "unit_id": other["id"],
                    "pages": [other["number"]],
                    "title": title,
                    "text": "\n\n".join(b["text"] for b in other["blocks"]),
                    "distance": other["number"] - unit["number"],
                }
            )
    # Alternate preceding and following units; preserve order within each unit.
    evidence.sort(key=lambda e: (abs(e["distance"]), e["distance"] > 0))
    return outline, evidence


def seed_terms(blocks: list[dict]) -> list[str]:
    # No extra AI call: literal identifiers/phrases are a first-pass FTS seed only.
    text = "\n".join(b["text"] for b in blocks)
    acronyms = re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", text)
    return list(
        dict.fromkeys(
            acronyms + re.findall(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b|[一-龯ァ-ヶー]{2,12}", text)
        )
    )[:12]


def outline_evidence(outline: list[dict]) -> list[dict]:
    return [
        {
            "id": item["evidence_id"],
            "kind": "document",
            "unit_id": item["id"],
            "pages": [item["number"]],
            "title": item["title"],
            "text": item["title"],
            "scope": "heading_only",
        }
        for item in outline
    ]


def pack(task: str, base: dict, candidates: list[dict], profile: Profile) -> dict:
    unique, seen = [], set()
    for item in candidates:
        if item["id"] not in seen:
            unique.append(item)
            seen.add(item["id"])

    def assemble(evidence):
        value = {
            **base,
            "evidence": evidence,
            "context_omitted_count": len(unique) - len(evidence),
            "allowed_evidence_ids": [e["id"] for e in evidence]
            + [item["evidence_id"] for item in base.get("document_outline", [])],
        }
        if "research_topics" in base:
            covered = {
                topic
                for item in evidence
                if item.get("kind") == "web"
                for topic in item.get("topics", [item.get("topic")])
            }
            value["unresolved_topics"] = [t for t in base["research_topics"] if t not in covered]
        return value

    payload: dict = assemble([])
    limit = min(10000, profile.input_tokens)
    if input_tokens(task, payload) > limit:
        raise ExplanationError(
            "input_limit", "原文・資料構成・下書きを省略せずに10,000トークン以内へ収められません。"
        )
    for item in unique:
        candidate = {k: v for k, v in item.items() if k not in {"distance"}}
        trial = assemble([*payload["evidence"], candidate])
        if input_tokens(task, trial) <= limit:
            payload = trial
    return payload


def numeric_issues(value: dict, blocks: list[dict]) -> list[str]:
    """Detect omitted literal numbers per source block; semantics are audited in turn 2."""
    import unicodedata
    from decimal import Decimal, InvalidOperation, localcontext

    def numbers(text):
        text = unicodedata.normalize("NFKC", text).replace("−", "-")
        literals = re.findall(r"(?<![0-9A-Za-z_])[-+]?\d+(?:[.,]\d+)*(?![0-9A-Za-z_])", text)
        result = set()
        for literal in literals:
            if re.fullmatch(r"[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", literal):
                literal = literal.replace(",", "")
            # Preserve unfamiliar separators literally; do not guess a locale.
            try:
                with localcontext() as decimal_context:
                    decimal_context.prec = max(28, len(literal))
                    canonical = str(Decimal(literal).normalize()) if "," not in literal else literal
            except InvalidOperation:
                canonical = literal
            result.add(canonical)
        return result

    issues = []
    for block in blocks:
        explained = "\n".join(
            s["text"] for s in value["sections"] if block["part_id"] in s["source_ids"]
        )
        missing = numbers(block["text"]) - numbers(explained)
        if missing:
            issues.append(
                f"{block['part_id']} の数値を省略しないでください: {', '.join(sorted(missing))}"
            )
    return issues
