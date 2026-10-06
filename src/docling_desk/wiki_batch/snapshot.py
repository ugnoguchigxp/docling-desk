"""Freeze inputs, instructions, registered terms and translation units at enqueue."""

from __future__ import annotations

from docling_desk.knowledge.catalog import evidence_body

from .files import hash_text, js_json
from .instructions import defaults, load, validate
from .markdown import packets_for, rewrite_links, split_markdown
from .terminology import INPUT_CHARS, VERSION, collect_terms, load_registry, term_packets

ENGINE = "python-wiki-batch-v1"
RESEARCH_VERSION = "wiki-fts-reading-v3-registered-terms"
LIMITS = {
    "additionalRounds": 2,
    "queriesPerAdditionalRound": 3,
    "documentsPerRound": 2,
    "totalDocuments": 6,
}
REQUEST_POLICY = {
    "inputChars": INPUT_CHARS,
    "minOutputTokens": 2048,
    "maxOutputTokens": 8192,
    "researchOutputTokens": 2048,
    "resolveOutputTokens": 4096,
}
LEGACY_REQUEST_POLICY = {**REQUEST_POLICY, "minOutputTokens": 4096, "researchOutputTokens": 4096}


def request_policy(snapshot):
    return REQUEST_POLICY if snapshot.get("engine") == ENGINE else LEGACY_REQUEST_POLICY


def recipe_hash_for(references, models=None, terminology=None, engine=None, instructions=None):
    instructions = validate(instructions) if instructions is not None else defaults()
    value = {
        "version": ENGINE if engine == ENGINE else "translation-batch-v5-registered-terms",
        "draft": instructions["draft"],
        "verify": instructions["verify"],
        "analyze": instructions["analyze"],
        "plan": instructions["plan"],
        "read": instructions["read"],
        "synthesize": instructions["synthesize"],
        "assess": instructions["assess"],
        "resolve": instructions["resolve"],
        "limits": LIMITS,
        "termVersion": VERSION,
    }
    if terminology is not None:
        value["terminology"] = terminology
    value["models"] = [(models or {}).get("draft", ""), (models or {}).get("verify", "")]
    value["references"] = references
    return hash_text(js_json(value))


def snapshot_for(repository, page, models, registry=None, instructions=None):
    instructions = load(repository.root) if instructions is None else validate(instructions)
    registry = load_registry(repository.root) if registry is None else registry
    original, translated = repository.read(page["original_path"]), repository.read(page["ja_path"])
    if original["meta"].get("source_hash") != page["source_hash"]:
        raise ValueError("原本の対応が変更されています。")
    body = evidence_body(original["body"])
    rewritten = rewrite_links(body, page["original_path"], page["ja_path"])
    category = str(page.get("category", ""))
    for size in (1800, 900, 450, 90, 45):
        units = split_markdown(
            rewritten, page["title_original"], category, page["collection"] == "glossary", size
        )
        terminology = collect_terms(registry, units)
        try:
            packets = term_packets(packets_for(units), terminology)
            break
        except ValueError:
            if size == 45:
                raise
    references = []
    return {
        "engine": ENGINE,
        "page": page,
        "meta": translated["meta"],
        "sourceBodyHash": hash_text(body),
        "jaHash": hash_text(translated["raw"]),
        "sourceText": body,
        "researchVersion": RESEARCH_VERSION,
        "models": models,
        "instructions": instructions,
        "conditions": {
            "limits": dict(LIMITS),
            "termVersion": VERSION,
            "researchVersion": RESEARCH_VERSION,
            "requestPolicy": dict(REQUEST_POLICY),
        },
        "inputHash": hash_text(
            js_json(
                {
                    "sourceHash": page["source_hash"],
                    "body": body,
                    "title": page["title_original"],
                    "category": category,
                }
            )
        ),
        "units": units,
        "packets": packets,
        "references": references,
        "terminology": terminology,
        "recipeHash": recipe_hash_for(references, models, terminology, ENGINE, instructions),
    }
