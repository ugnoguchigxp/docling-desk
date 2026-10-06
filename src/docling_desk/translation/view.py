"""Apply translations and give SVG text normal advances at its native anchors."""

from __future__ import annotations

from lxml import html

from docling_desk.translation.source import serialize
from docling_desk.translation.svg import flow_translated_svg


def apply_text(raw: str, unit: dict, translations: dict[str, str]) -> str:
    tree = html.fromstring(raw)
    if set(translations) != {s["id"] for s in unit["segments"]}:
        raise ValueError("訳文の対応が不完全です。")
    changed = set()
    for segment in unit["segments"]:
        locator = segment["locator"]
        nodes = tree.xpath(locator["node_path"])
        slot = locator["slot"]
        if slot == "svg_text":
            if len(nodes) != 1 or "".join(nodes[0].itertext()) != segment["source_text"]:
                raise ValueError("原文が変更されました。再翻訳してください。")
            node = nodes[0]
            span = node[0]
            for child in list(node):
                node.remove(child)
            node.append(span)
            span.text = translations[segment["id"]]
            span.tail = None
            node.set("font-family", "Arial,sans-serif")
            node.set("style", f"font-family:Arial,sans-serif;font-size:{node.get('font-size')}px")
            changed.add(span)
            continue
        if (
            slot not in {"text", "tail"}
            or len(nodes) != 1
            or getattr(nodes[0], slot) != segment["source_text"]
        ):
            raise ValueError("原文が変更されました。再翻訳してください。")
        setattr(nodes[0], slot, translations[segment["id"]])
        if slot == "text" and translations[segment["id"]] != segment["source_text"]:
            changed.add(nodes[0])
    flow_translated_svg(tree, changed)
    return serialize(tree, raw)
