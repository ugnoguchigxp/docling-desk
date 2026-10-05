"""Translate source spans while preserving Markdown, values, code and links."""

from __future__ import annotations

import json
import posixpath
import re
from urllib.parse import unquote

from markdown_it import MarkdownIt

from .files import NeedsReview, utf16_length

MARKER = re.compile(r"⟦KEEP_\d+⟧")


def keep_text(text):
    if "⟦KEEP_" in text:
        raise NeedsReview("本文に保護記号と同じ文字があります。")
    keep = {}

    def save(match):
        value = match[0] if hasattr(match, "group") else match
        key = f"⟦KEEP_{len(keep)}⟧"
        keep[key] = value
        return key

    for pattern in (
        r"(`+)[^\n]*?\1",
        r'(?<=\]\()(<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?(?=\))',
        r"https?://[^\s<>]+",
        r"\b[A-Z]{2,8}-\d+\b|\b[a-f\d]{32}\b",
    ):
        text = re.sub(pattern, save, text, flags=re.I if "[A-Z]" in pattern else 0)
    text = "".join(
        part
        if MARKER.fullmatch(part)
        else re.sub(r"\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?(?:\s*%)?", save, part)
        for part in re.split(r"(⟦KEEP_\d+⟧)", text)
    )
    return text, keep


def restore(unit, text):
    def replace(match):
        if match[0] not in unit["keep"]:
            raise NeedsReview("不明な保護記号があります。")
        return unit["keep"][match[0]]

    return unit.get("prefix", "") + MARKER.sub(replace, text) + unit.get("suffix", "")


def rewrite_links(body, original, translated):
    def replace(match):
        href = match[2].strip("<>")
        if re.match(r"(?:[a-z]+:|#)", href, re.I):
            return match[0]
        target, _, anchor = href.partition("#")
        absolute = posixpath.normpath(posixpath.join(posixpath.dirname(original), unquote(target)))
        if not absolute.startswith(("wiki/pages/", "sources/notion/")):
            raise NeedsReview("収録範囲外の相対リンクがあります。")
        relative = posixpath.relpath(absolute, posixpath.dirname(translated))
        return match[1] + "<" + relative + ("#" + anchor if anchor else "") + ">"

    return re.sub(r"(\]\()(<[^>]+>|[^\s)]+)(?=[\s)])", replace, body)


def split_markdown(body, title, category="", glossary=False, maximum=1800):
    units = []
    group = 0

    def add(raw, kind, literal=False, existing=None):
        nonlocal group
        if not raw:
            return
        current = group
        group += 1
        match = re.fullmatch(r"(\s*)([\s\S]*?)(\s*)", raw)
        text, keep = (match[2], existing) if existing is not None else keep_text(match[2])
        parts = []
        while utf16_length(text) > maximum:
            # New jobs count UTF-16 consistently; existing jobs retain their saved units.
            end = 0
            while end < len(text) and utf16_length(text[: end + 1]) <= maximum:
                end += 1
            cut = max(text.rfind(" ", 0, end + 1), text.rfind("\n", 0, end + 1))
            if cut < end // 2:
                cut = end
            for marker in MARKER.finditer(text):
                if marker.start() < cut < marker.end():
                    cut = marker.start()
            if cut <= 0:
                raise NeedsReview("分割できない本文があります。")
            parts.append(text[:cut])
            text = text[cut:]
        parts.append(text)
        for i, part in enumerate(parts):
            units.append(
                {
                    "id": f"u{len(units)}",
                    "kind": kind,
                    "text": part,
                    "keep": keep,
                    "prefix": match[1] if i == 0 else "",
                    "suffix": match[3] if i == len(parts) - 1 else "",
                    "literal": literal or not any(c.isalpha() for c in MARKER.sub("", part)),
                    "group": current,
                }
            )

    add(title, "title", glossary)
    add(category, "category")
    lines = body.splitlines(keepends=True)
    tokens = MarkdownIt("commonmark", {"html": True}).enable("table").parse(body)
    cursor = 0
    first = True
    for token in tokens:
        if token.level != 0 or not token.map or token.nesting == -1 or token.map[0] < cursor:
            continue
        start, end = token.map
        if start > cursor:
            add("".join(lines[cursor:start]), "space", True)
        raw = "".join(lines[start:end])
        if (
            first
            and token.type == "heading_open"
            and token.tag == "h1"
            and re.sub(r"^#\s+", "", raw).strip() == title.strip()
        ):
            cursor = end
            first = False
            continue
        first = False
        kind = token.type.removesuffix("_open")
        if kind == "table":
            for line in raw.splitlines(keepends=True):
                if re.fullmatch(r"[\s|:\-]+", line):
                    add(line, "literal", True)
                    continue
                protected, keep = keep_text(line)
                for cell in re.split(r"(?<!\\)(\|)", protected):
                    add(cell, "literal" if cell == "|" else "cell", cell == "|", keep)
        elif kind == "html_block":
            if re.search(r"<[^>]+>[^<]*[A-Za-z]{3}", raw):
                raise NeedsReview("HTML本文は対訳の確認が必要です。")
            add(raw, "html", True)
        else:
            add(raw, kind, kind in {"fence", "code_block", "hr"})
        cursor = end
    add("".join(lines[cursor:]), "space", True)
    return units


def packets_for(units):
    packets, pending, size = [], [], 0
    for unit in units:
        if unit["literal"]:
            continue
        if pending and (size + utf16_length(unit["text"]) > 2400 or len(pending) >= 24):
            packets.append({"position": len(packets), "units": pending})
            pending, size = [], 0
        pending.append(unit)
        size += utf16_length(unit["text"])
    if pending:
        packets.append({"position": len(packets), "units": pending})
    return packets


def parse_json(text):
    try:
        return json.loads(
            re.sub(r"\n```\s*$", "", re.sub(r"^```(?:json)?\s*\n", "", text.strip(), flags=re.I))
        )
    except (ValueError, TypeError) as exc:
        raise NeedsReview("応答JSONを読み取れません。") from exc


def validate_translation(value, packet):
    rows = value.get("translations") if isinstance(value, dict) else None
    expected = {u["id"]: u for u in packet["units"]}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise NeedsReview("訳文の単位が欠落・重複しています。")
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("id") not in expected
            or not isinstance(row.get("text"), str)
            or not row["text"].strip()
        ):
            raise NeedsReview("訳文の固定IDまたは本文が不正です。")
        unit = expected.pop(row["id"])
        if MARKER.findall(unit["text"]) != MARKER.findall(row["text"]):
            raise NeedsReview("数値・コード・リンクの保護情報が変わりました。")
        if unit["kind"] == "cell" and re.search(r"(?<!\\)\||\n", row["text"]):
            raise NeedsReview("表のセル構造が変わりました。")
        if unit["kind"] == "heading":
            heading = re.match(r"^#+\s", row["text"])
            original = re.match(r"^#+\s", unit["text"])
            if not heading or not original or heading[0] != original[0]:
                raise NeedsReview("見出しの階層が変わりました。")
        if re.search(r"\d", MARKER.sub("", row["text"])):
            raise NeedsReview("原文にない数値が訳文に追加されました。")
    return {"translations": rows}


def verify_result(value, packet):
    if (
        not isinstance(value, dict)
        or type(value.get("approved")) is not bool
        or not isinstance(value.get("issues"), list)
    ):
        raise NeedsReview("検証JSONの形式が不正です。")
    ids = {u["id"] for u in packet["units"]}
    for issue in value["issues"]:
        if (
            not isinstance(issue, dict)
            or issue.get("id") not in ids
            or not isinstance(issue.get("reason"), str)
            or not issue["reason"].strip()
        ):
            raise NeedsReview("検証の指摘単位が不正です。")
    if value["approved"] == bool(value["issues"]):
        raise NeedsReview("検証の承認と指摘が矛盾しています。")
    return {"approved": value["approved"], "issues": value["issues"]}
