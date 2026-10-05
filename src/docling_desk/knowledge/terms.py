"""Case-sensitive glossary expansion for Wiki retrieval."""

from __future__ import annotations

import re
import unicodedata


def expand_terms(query: str, articles: list[dict]) -> list[str]:
    terms = unicodedata.normalize("NFKC", query).split()
    expanded = []
    for article in articles:
        if article.get("collection", article.get("namespace")) != "glossary":
            continue
        if article["title"] not in terms:
            continue
        match = re.search(r"\|\s*(?:Meaning|意味)\s*\|\s*([^|]+)\|", article["body"])
        if match:
            phrase = re.split(r"\s+-\s+|\s+[—–]\s+", match[1].strip())[0]
            if phrase and len(phrase) <= 120 and phrase not in terms:
                expanded.append(phrase)
    return list(dict.fromkeys(expanded))[:4]
