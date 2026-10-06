"""Markdown-only Wiki parsing and rendering. Imported text is never executable HTML."""

from __future__ import annotations

import hashlib
import html
import posixpath
import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from markdown_it import MarkdownIt

from docling_desk import config
from docling_desk.knowledge.catalog import csv_rows


def parse(body: str) -> tuple[str, list[dict], list[dict]]:
    tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(body)
    lines = body.splitlines(keepends=True)
    outline, counts = [], {}
    for i, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        label = tokens[i + 1].content
        base = "h-" + hashlib.sha256(label.encode()).hexdigest()[:12]
        counts[base] = counts.get(base, 0) + 1
        outline.append(
            {
                "title": label,
                "level": int(token.tag[1:]),
                "anchor": f"{base}-{counts[base]}",
                "line": token.map[0],
            }
        )
    title = outline[0]["title"] if outline else ""
    starts = [(0, "", "")] if not outline or outline[0]["line"] else []
    starts += [(h["line"], h["anchor"], h["title"]) for h in outline]
    sections = []
    for i, (start, anchor, label) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        text = "".join(lines[start:end]).strip()
        if text:
            sections.append({"text": text, "anchor": anchor, "heading": label})
    return title, outline, sections


def render(
    body: str,
    namespace: str,
    path: str,
    articles: list[dict],
    documents: list[dict],
    load_article=None,
    original_link=None,
    workspace=None,
) -> str:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    by_path = {(s["namespace"], s["path"]): s for s in articles}
    current = by_path.get((namespace, path), {})
    workspace = current.get("workspace") or workspace

    def registered_path(target):
        return by_path.get((namespace, target)) or next(
            (
                s
                for s in articles
                if workspace and s.get("workspace") == workspace and s["path"] == target
            ),
            None,
        )

    jobs = {s["job_id"]: s for s in documents}
    _, outline, _ = parse(body)
    heading = iter(outline)
    linked_outlines = {}

    if path.lower().endswith(".csv"):
        columns, rows = csv_rows(body)
        cells = []
        for row in rows:
            values = []
            for column in columns:
                text = html.escape(row[column])
                href = (
                    row.get("page_path")
                    if column == "title"
                    else row.get(column)
                    if column in {"page_path", "counterpart_path"}
                    else None
                )
                target = (
                    registered_path(
                        posixpath.normpath(posixpath.join(posixpath.dirname(path), href))
                    )
                    if href
                    else None
                )
                if target:
                    text = (
                        f'<a href="{config.url("/")}?'
                        + html.escape(
                            urlencode({"mode": "wiki", "source": target["id"]}), quote=True
                        )
                        + '">'
                        + text
                        + "</a>"
                    )
                elif href and original_link:
                    resolved = posixpath.normpath(
                        posixpath.join(posixpath.dirname(path), unquote(href))
                    )
                    url = original_link(resolved, "")
                    if url:
                        text = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{text}</a>'
                    else:
                        text = f'<span title="原本が見つからないか、参照が許可されていません" aria-disabled="true">{text}</span>'
                values.append("<td>" + text + "</td>")
            cells.append("<tr>" + "".join(values) + "</tr>")
        return (
            "<table><thead><tr>"
            + "".join("<th>" + html.escape(c) + "</th>" for c in columns)
            + "</tr></thead><tbody>"
            + "".join(cells)
            + "</tbody></table>"
        )

    def link(value: str) -> tuple[str, bool]:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return "", False
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value, True
        if parsed.scheme == "doc":
            job_id = parsed.path
            if job_id in jobs:
                unit = parse_qs(parsed.fragment).get("page", ["1"])[0]
                return config.url("/") + "?" + urlencode(
                    {"mode": "library", "job": job_id, "unit": unit if unit.isdigit() else "1"}
                ), False
            return "", False
        if parsed.scheme or parsed.netloc or "\\" in value:
            return "", False
        if not parsed.path:
            # Support both the stable anchors and conventional Markdown heading links.
            anchor = unquote(parsed.fragment)
            for h in outline:
                conventional = re.sub(r"[^\w\- ]", "", h["title"].lower()).replace(" ", "-")
                if anchor in {h["anchor"], conventional, h["title"]}:
                    return "#" + h["anchor"], False
            return "", False
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(parsed.path)))
        article = registered_path(resolved)
        if not article:
            url = original_link(resolved, unquote(parsed.fragment)) if original_link else ""
            return url, bool(url)
        anchor = unquote(parsed.fragment)
        if anchor:
            if article["id"] not in linked_outlines:
                target = (
                    article
                    if "body" in article
                    else load_article(article["id"])
                    if load_article
                    else None
                )
                linked_outlines[article["id"]] = parse(target["body"])[1] if target else []
            for h in linked_outlines[article["id"]]:
                conventional = re.sub(r"[^\w\- ]", "", h["title"].lower()).replace(" ", "-")
                if anchor in {h["anchor"], conventional, h["title"]}:
                    anchor = h["anchor"]
                    break
            else:
                anchor = ""
        return (
            config.url("/")
            + "?"
            + urlencode({"mode": "wiki", "source": article["id"], "section": anchor}),
            False,
        )

    tokens = md.parse(body)
    for token in tokens:
        if token.type == "heading_open":
            token.attrSet("id", next(heading)["anchor"])
        for child in token.children or []:
            if child.type == "link_open":
                href, external = link(child.attrGet("href") or "")
                child.attrSet("href", href or "#")
                if not href:
                    child.attrSet("aria-disabled", "true")
                    child.attrSet(
                        "title",
                        "原本が見つからないか、参照が許可されていません"
                        if original_link
                        else "未登録または利用できないリンクです",
                    )
                if external:
                    child.attrSet("target", "_blank")
                    child.attrSet("rel", "noopener noreferrer")
            if child.type == "image":
                # Remote images do not cause unrequested network access or leak article locations.
                label = f"[画像: {child.content}]"
                src = child.attrGet("src") or ""
                try:
                    parsed = urlsplit(src)
                    resolved = posixpath.normpath(
                        posixpath.join(posixpath.dirname(path), unquote(parsed.path))
                    )
                    url = (
                        original_link(resolved, "")
                        if original_link and not parsed.scheme and not parsed.netloc
                        else ""
                    )
                except ValueError:
                    url = ""
                child.type, child.children = "text", None
                child.content = label
                if url:
                    child.type = "html_inline"
                    child.content = (
                        f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                        f'rel="noopener noreferrer">{html.escape(label)}</a>'
                    )
    return md.renderer.render(tokens, md.options, {})


def normalize_path(value: str) -> str:
    if "\\" in value or "\x00" in value or any(ord(c) < 32 for c in value):
        raise ValueError("記事のパスが不正です。")
    value = value.strip()
    if not value or value.startswith("/") or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ValueError("記事は相対パスで指定してください。")
    if not value.lower().endswith((".md", ".markdown", ".csv")) or len(value) > 512:
        raise ValueError("WikiにはMarkdownまたはCSV目次を取り込めます。")
    return value
