"""Apply translations to a page's text blocks, drawing each over its original text."""

from __future__ import annotations

from lxml import html

from docling_desk.translation.fit import fitted_size
from docling_desk.translation.source import serialize


def show_block(node, text: str) -> None:
    """Make a page text block visible and opaque so the translation covers the image text."""
    if "pdf-text" not in node.get("class", "").split():
        return
    try:
        page, width, height, size, line_height = (
            float(node.get(name, ""))
            for name in ("data-pw", "data-cw", "data-ch", "data-size", "data-lh")
        )
    except ValueError:
        return
    fitted = fitted_size(text, width, height, size, line_height)
    node.set("data-translated", "true")
    node.set("style", f"{node.get('style', '')};font-size:{fitted / page * 100:.4f}cqw")


def apply_text(raw: str, unit: dict, translations: dict[str, str]) -> str:
    tree = html.fromstring(raw)
    if set(translations) != {s["id"] for s in unit["segments"]}:
        raise ValueError("訳文の対応が不完全です。")
    for segment in unit["segments"]:
        locator = segment["locator"]
        nodes = tree.xpath(locator["node_path"])
        slot = locator["slot"]
        if (
            slot not in {"text", "tail"}
            or len(nodes) != 1
            or getattr(nodes[0], slot) != segment["source_text"]
        ):
            raise ValueError("原文が変更されました。再翻訳してください。")
        setattr(nodes[0], slot, translations[segment["id"]])
        if slot == "text" and translations[segment["id"]] != segment["source_text"]:
            show_block(nodes[0], translations[segment["id"]])
    return serialize(tree, raw)
