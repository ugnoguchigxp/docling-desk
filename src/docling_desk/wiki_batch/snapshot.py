"""Freeze inputs, instructions, registered terms and translation units at enqueue."""

from __future__ import annotations

from docling_desk.knowledge.catalog import evidence_body

from . import prompts
from .files import hash_text, js_json
from .markdown import packets_for, rewrite_links, split_markdown
from .terminology import VERSION, collect_terms, load_registry, term_packets

ENGINE = "python-wiki-batch-v1"
RESEARCH_VERSION = "wiki-fts-reading-v3-registered-terms"
LIMITS = {
    "additionalRounds": 2,
    "queriesPerAdditionalRound": 3,
    "documentsPerRound": 2,
    "totalDocuments": 6,
}


def recipe_hash_for(references, models=None, terminology=None, engine=None):
    value = {
        "version": ENGINE if engine == ENGINE else "translation-batch-v5-registered-terms",
        "draft": prompts.DRAFT,
        "verify": prompts.VERIFY,
        "analyze": prompts.ANALYZE,
        "plan": prompts.PLAN_SEARCH,
        "read": prompts.READ_RELATED,
        "synthesize": prompts.SYNTHESIZE_RESEARCH,
        "assess": prompts.ASSESS_RESEARCH,
        "resolve": prompts.RESOLVE_TERMS,
        "limits": LIMITS,
        "termVersion": VERSION,
    }
    if terminology is not None:
        value["terminology"] = terminology
    value["models"] = [(models or {}).get("draft", ""), (models or {}).get("verify", "")]
    value["references"] = references
    return hash_text(js_json(value))


def snapshot_for(repository, page, models, registry=None):
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
        "recipeHash": recipe_hash_for(references, models, terminology, ENGINE),
    }
