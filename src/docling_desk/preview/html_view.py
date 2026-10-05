"""Trusted viewer shell around allowlisted Docling HTML; source files stay inert."""

from __future__ import annotations

import json
import re

from docling_core.types.doc import DoclingDocument
from lxml import html

from docling_desk.documents.pagination import render_paginated_html
from docling_desk.documents.tables import TableView, project_tables

# Do not insert arbitrary document markup into a script-enabled frame.
TAGS = set(
    "section header nav div p span h1 h2 h3 h4 h5 h6 a figure figcaption img "
    "table caption thead tbody tfoot tr th td ul ol li dl dt dd pre code strong b em i "
    "u del s sub sup br hr blockquote math mrow mi mn mo mtext mspace ms msup msub "
    "msubsup mfrac msqrt mroot mfenced munder mover munderover mtable mtr mtd "
    "semantics annotation".split()
)
DROP = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "link",
    "meta",
    "base",
    "form",
    "input",
    "button",
    "svg",
}


def sanitize_body(raw: str) -> str:
    body = html.fromstring(raw).find("body")
    if body is None:
        raise ValueError("Docling HTML body missing")
    for node in list(body.iterdescendants()):
        tag = str(node.tag).lower()
        if tag in DROP or not isinstance(node.tag, str):
            node.drop_tree()
            continue
        if tag not in TAGS:
            node.drop_tag()
            continue
        attrs = dict(node.attrib)
        node.attrib.clear()
        for name in ("class", "id", "dir", "lang", "title", "aria-label", "data-page"):
            if name in attrs:
                node.set(name, attrs[name])
        for name in ("rowspan", "colspan", "start", "value"):
            if name in attrs and attrs[name].isdigit():
                node.set(name, attrs[name])
        if tag == "a" and attrs.get("href", "").startswith("#"):
            node.set("href", attrs["href"])
        if tag == "img":
            src = attrs.get("src", "")
            if re.match(r"^data:image/(png|jpeg|webp|gif);base64,", src):
                node.set("src", src)
            node.set("alt", attrs.get("alt", "抽出画像"))
        if tag == "table" and re.fullmatch(r"#/tables/\d+", attrs.get("data-docling-ref", "")):
            node.set("data-docling-ref", attrs["data-docling-ref"])
    return "".join(html.tostring(child, encoding="unicode") for child in body)


def viewer_html(doc: DoclingDocument, filename: str, job_id: str) -> str:
    body = sanitize_body(render_paginated_html(doc, filename))
    tables: list[TableView] = project_tables(doc, filename)
    payload = (
        json.dumps(
            {"jobId": job_id, "tables": [table.model_dump() for table in tables]},
            ensure_ascii=False,
        )
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Docling抽出HTML・表操作</title>"
        '<link rel="stylesheet" href="/static/extracted-view.css">'
        '<link rel="stylesheet" href="/static/vendor/ag-grid.min.css">'
        '<link rel="stylesheet" href="/static/vendor/ag-theme-quartz.min.css">'
        '<link rel="stylesheet" href="/static/table-ui.css">'
        '<script src="/static/vendor/ag-grid-community.min.js" defer></script>'
        '<script src="/static/tabulens-grid.js" defer></script>'
        '<script src="/static/table-common.js" defer></script>'
        '<script src="/static/inline-tables.js" defer></script></head><body>'
        + body
        + '<script id="table-data" type="application/json">'
        + payload
        + "</script></body></html>"
    )
