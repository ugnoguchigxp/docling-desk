"""Explicit workspace instructions; never import project application code."""

from __future__ import annotations

import json
from pathlib import Path

from . import prompts
from .files import NeedsReview

NAMES = {
    "draft": "DRAFT",
    "verify": "VERIFY",
    "analyze": "ANALYZE",
    "plan": "PLAN_SEARCH",
    "read": "READ_RELATED",
    "synthesize": "SYNTHESIZE_RESEARCH",
    "assess": "ASSESS_RESEARCH",
    "resolve": "RESOLVE_TERMS",
}


def defaults():
    return {key: getattr(prompts, name) for key, name in NAMES.items()}


def validate(value):
    if (
        not isinstance(value, dict)
        or value.keys() != NAMES.keys()
        or any(not isinstance(v, str) or not v.strip() or len(v) > 64000 for v in value.values())
    ):
        raise NeedsReview("翻訳指示は8段階すべての空でない本文を指定してください。")
    return dict(value)


def load(root: Path, file: Path | None = None):
    selected = file or root / "manifests/translation-instructions.json"
    if file is None and not selected.exists():
        return defaults()
    try:
        raw = selected.read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            raise ValueError
        if set(value) == {"schemaVersion", "termRules"}:
            rules = value["termRules"]
            if not isinstance(rules, str) or not rules.strip() or len(rules) > 32000:
                raise ValueError
            return validate(
                {k: v.replace(prompts.TERM_RULES, rules, 1) for k, v in defaults().items()}
            )
        if set(value) != {"schemaVersion", "instructions"}:
            raise ValueError
        return validate(value["instructions"])
    except (OSError, ValueError, TypeError) as exc:
        raise NeedsReview(
            "翻訳指示の設定を読み取れません。schemaVersion: 1のJSONを確認してください。"
        ) from exc
