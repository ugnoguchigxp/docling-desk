"""Preserve PowerPoint's native graphics and real text in fixed-layout HTML/SVG."""

from __future__ import annotations

import base64
import html
import json
import math
import re
import threading
from collections import defaultdict
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import cast

import lxml.etree as etree  # ty: ignore[unresolved-import]  # lxml's installed binary extension.
import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import parse_path
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable, table__c_m_a_p
from pptx import Presentation
from pptx.shapes.group import GroupShape

from docling_desk.config import MAX_PAGES
from docling_desk.preview.native_preview import digest
from docling_desk.storage import original_file

SVG = "http://www.w3.org/2000/svg"
XHTML = "http://www.w3.org/1999/xhtml"
NS = {"s": SVG}
XLINK = "http://www.w3.org/1999/xlink"
NOTICE = "原本側はPowerPointの描画から生成したHTML/SVGです。文字はテキスト、図形はベクターとして保持し、翻訳用に元の文字枠との対応を記録しています。"
PDF_NOTICE = "PDFの描画をHTML/SVGで保持しています。文字はテキスト、図形はベクターです。スキャン画像内の文字は原本画像のままです。"
RENDERER_VERSION = 2
PDF_RENDER_LOCK = threading.Lock()


def editable_preview(source: Path, folder: Path) -> str | None:
    directory = folder / "editable-preview"
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        pdf = (
            original_file(folder, ".pdf")
            if manifest["pdf"] == "original.pdf"
            else folder / manifest["pdf"]
        ).resolve()
        if (
            not pdf.is_relative_to(folder.resolve())
            and pdf != original_file(folder, ".pdf").resolve()
        ) or manifest["version"] != RENDERER_VERSION:
            return None
        if digest(source) != manifest["source_sha256"] or digest(pdf) != manifest["pdf_sha256"]:
            return None
        assets = manifest["assets"]
        if not assets or not all(
            (directory / name).resolve().is_relative_to(directory.resolve())
            and (directory / name).is_file()
            for name in assets
        ):
            return None
        return (directory / "Preview.html").relative_to(folder).as_posix()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def source_textboxes(slide) -> list[dict]:
    """Record source IDs and absolute boxes, including scaled/rotated groups and table cells."""
    boxes = []

    def visit(shapes, matrix, path):
        for shape in shapes:
            a, b, c, d, e, f = matrix
            x, y, w, h = [
                float(value) for value in (shape.left, shape.top, shape.width, shape.height)
            ]
            current_path = [*path, shape.shape_id]
            if isinstance(shape, GroupShape):
                transform = shape._element.grpSpPr.xfrm
                if transform.chExt is None or not transform.chExt.cx or not transform.chExt.cy:
                    continue
                sx, sy = transform.cx / transform.chExt.cx, transform.cy / transform.chExt.cy
                angle = math.radians(shape.rotation)
                ca, sa = math.cos(angle), math.sin(angle)
                ga, gb, gc, gd = ca * sx, sa * sx, -sa * sy, ca * sy
                ge = (
                    x
                    + w / 2
                    - ca * w / 2
                    + sa * h / 2
                    - ga * transform.chOff.x
                    - gc * transform.chOff.y
                )
                gf = (
                    y
                    + h / 2
                    - sa * w / 2
                    - ca * h / 2
                    - gb * transform.chOff.x
                    - gd * transform.chOff.y
                )
                visit(
                    shape.shapes,
                    (
                        a * ga + c * gb,
                        b * ga + d * gb,
                        a * gc + c * gd,
                        b * gc + d * gd,
                        a * ge + c * gf + e,
                        b * ge + d * gf + f,
                    ),
                    current_path,
                )
                continue
            frames = []
            if shape.has_text_frame:
                frames.append((shape.text_frame, x, y, w, h, None))
            if shape.has_table:
                table = shape.table
                top = y
                for row_index, row in enumerate(table.rows):
                    left = x
                    for column_index, column in enumerate(table.columns):
                        cell = table.cell(row_index, column_index)
                        if not cell.is_spanned:
                            cw = sum(
                                table.columns[i].width
                                for i in range(column_index, column_index + cell.span_width)
                            )
                            ch = sum(
                                table.rows[i].height
                                for i in range(row_index, row_index + cell.span_height)
                            )
                            frames.append(
                                (cell.text_frame, left, top, cw, ch, [row_index, column_index])
                            )
                        left += column.width
                    top += row.height
            for frame, bx, by, bw, bh, cell in frames:
                if not frame.text.strip():
                    continue
                corners = [
                    (a * px + c * py + e, b * px + d * py + f)
                    for px, py in [(bx, by), (bx + bw, by), (bx, by + bh), (bx + bw, by + bh)]
                ]
                rect = [
                    min(p[0] for p in corners),
                    min(p[1] for p in corners),
                    max(p[0] for p in corners),
                    max(p[1] for p in corners),
                ]
                alignment = frame.paragraphs[0].alignment
                boxes.append(
                    {
                        "id": "shape-"
                        + "-".join(map(str, current_path))
                        + (f"-cell-{cell[0]}-{cell[1]}" if cell else ""),
                        "shape_id": shape.shape_id,
                        "shape_path": current_path,
                        "cell": cell,
                        "source_text": frame.text.replace("\v", "\n"),
                        "bbox": [v / 12700 for v in rect],
                        "rotation": shape.rotation,
                        "alignment": "center"
                        if alignment == 2
                        else "right"
                        if alignment == 3
                        else "left",
                        "vertical_alignment": "middle"
                        if frame.vertical_anchor == 3
                        else "bottom"
                        if frame.vertical_anchor == 4
                        else "top",
                        "margins": [
                            v / 12700
                            for v in [
                                frame.margin_left,
                                frame.margin_top,
                                frame.margin_right,
                                frame.margin_bottom,
                            ]
                        ],
                        "node_ids": [],
                    }
                )

    visit(slide.shapes, (1, 0, 0, 1, 0, 0), [])
    return boxes


def match_box(trace: dict, text: str, boxes: list[dict]) -> dict | None:
    x0, y0, x1, y1 = trace["bbox"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    candidates = []
    for box in boxes:
        bx0, by0, bx1, by1 = box["bbox"]
        if not (bx0 - 3 <= cx <= bx1 + 3 and by0 - 3 <= cy <= by1 + 3):
            continue
        if normalize(text) not in normalize(box["source_text"]):
            continue
        candidates.append(((bx1 - bx0) * (by1 - by0), box))
    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    # Native PowerPoint sometimes lets a paragraph extend below its nominal box.
    # Allow that overflow only when the actual source text agrees.
    nearby = []
    for box in boxes:
        bx0, by0, bx1, by1 = box["bbox"]
        dx, dy = max(bx0 - cx, 0, cx - bx1), max(by0 - cy, 0, cy - by1)
        if dx > 3 or dy > trace["size"] * 2:
            continue
        native, source = normalize(text), normalize(box["source_text"])
        if native in source:
            nearby.append((dx + dy, box))
        elif dx == 0 and dy == 0 and len(native) >= 5:
            # PDF ligature decoding may differ from the PPTX Unicode source.
            # Retain both strings; do not use the decoded PDF as the translation source.
            strings = [source, *[normalize(word) for word in box["source_text"].split()]]
            score = max(SequenceMatcher(None, native, value).ratio() for value in strings)
            if score >= 0.8:
                nearby.append((100 * (1 - score), box))
    nearby.sort(key=lambda item: item[0])
    if not nearby or (len(nearby) > 1 and abs(nearby[0][0] - nearby[1][0]) < 0.1):
        return None
    return nearby[0][1]


def web_font(content: bytes, cmap: dict[int, int]) -> bytes:
    """PDF subset fonts often omit Unicode cmaps; rebuild from native glyph/Unicode pairs."""
    with TTFont(BytesIO(content)) as font:
        glyphs = font.getGlyphOrder()
        mapped = dict(font.getBestCmap() or {})
        mapped.update({u: glyphs[g] for u, g in cmap.items() if 0 <= g < len(glyphs)})
        table = cast(table__c_m_a_p, newTable("cmap"))
        table.tableVersion, table.tables = 0, []
        for format_, platform, encoding in [(4, 3, 1), (12, 3, 10)]:
            subtable = CmapSubtable.newSubtable(format_)
            subtable.platformID, subtable.platEncID, subtable.language = platform, encoding, 0
            subtable.cmap = {u: g for u, g in mapped.items() if format_ == 12 or u <= 0xFFFF}
            table.tables.append(subtable)
        font["cmap"] = table
        font.sfntVersion = "OTTO" if "CFF " in font or "CFF2" in font else "\x00\x01\x00\x00"
        # PDF fonts omit tables required by browser font sanitizers. Recreate
        # metrics without touching glyph outlines; discard malformed glyph names.
        builder = FontBuilder(font=font, isTTF="glyf" in font)
        if "OS/2" not in font:
            builder.setupOS2(
                sTypoAscender=font["hhea"].ascent,
                sTypoDescender=font["hhea"].descent,
                usWinAscent=max(font["head"].yMax, 0),
                usWinDescent=max(-font["head"].yMin, 0),
            )
        builder.setupPost(keepGlyphNames=False)
        output = BytesIO()
        font.save(output)
        return output.getvalue()


def outline_font(glyphs: dict[int, tuple[str, float]]) -> bytes:
    """Embed the renderer's exact glyph outlines even when a PDF omits its font.

    This creates a real Unicode font, not images or visible outlined text.
    The SVG text remains selectable and replaceable.
    """
    units = 2048
    names = {u: f"u{u:06x}" for u in glyphs}
    builder = FontBuilder(units, isTTF=True)
    builder.setupGlyphOrder([".notdef", *names.values()])
    builder.setupCharacterMap(names)
    outlines = {".notdef": TTGlyphPen(None).glyph()}
    metrics = {".notdef": (units, 0)}
    for unicode, (path, advance) in glyphs.items():
        pen = TTGlyphPen(None)
        parse_path(path, TransformPen(Cu2QuPen(pen, max_err=0.5), (units, 0, 0, units, 0, 0)))
        glyph = pen.glyph()
        glyph.recalcBounds(None)
        outlines[names[unicode]] = glyph
        metrics[names[unicode]] = (max(0, round(advance * units)), getattr(glyph, "xMin", 0))
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=units, descent=-units // 4)
    builder.setupNameTable(
        {
            "familyName": "PDF glyphs",
            "styleName": "Regular",
            "uniqueFontIdentifier": "PDFGlyphs",
            "fullName": "PDF glyphs",
            "psName": "PDFGlyphs",
            "version": "Version 1.0",
        }
    )
    builder.setupOS2(
        sTypoAscender=units, sTypoDescender=-units // 4, usWinAscent=units, usWinDescent=units // 4
    )
    builder.setupPost(keepGlyphNames=False)
    builder.setupMaxp()
    output = BytesIO()
    builder.font.save(output)
    return output.getvalue()


def pdf_textboxes(page) -> list[dict]:
    boxes = []
    for index, block in enumerate(page.get_text("dict")["blocks"]):
        if block["type"] != 0:
            continue
        text = "\n".join("".join(s["text"] for s in line["spans"]) for line in block["lines"])
        boxes.append(
            {
                "id": f"block-{index}",
                "source_text": text,
                "bbox": list(block["bbox"]),
                "margins": [0, 0, 0, 0],
                "alignment": "left",
                "vertical_alignment": "top",
                "rotation": 0,
                "node_ids": [],
            }
        )
    return boxes


def build_editable_preview(source: Path, pdf: Path, folder: Path) -> str:
    pdf_relative = (
        "original.pdf"
        if pdf.resolve() == original_file(folder, ".pdf").resolve()
        else pdf.resolve().relative_to(folder.resolve()).as_posix()
    )
    deck = Presentation(str(source)) if source.suffix.lower() == ".pptx" else None
    if deck is not None and (deck.slide_width is None or deck.slide_height is None):
        raise ValueError("PowerPointのページ寸法がありません。")
    deck_width = float(deck.slide_width or 0) / 12700 if deck is not None else 0
    deck_height = float(deck.slide_height or 0) / 12700 if deck is not None else 0
    original_digest, pdf_digest = digest(source), digest(pdf)
    directory = folder / "editable-preview"
    font_maps, font_files, fallback_glyphs, trees, pages = (
        defaultdict(dict),
        {},
        defaultdict(dict),
        [],
        [],
    )
    with pymupdf.open(pdf) as document:
        if document.needs_pass or not 0 < len(document) <= MAX_PAGES:
            raise ValueError("暗号化PDF、空のPDF、またはページ数の上限を超えるPDFです。")
        if deck is not None and len(document) != len(deck.slides):
            raise ValueError("PowerPointとPDFのページ数が一致しません。")
        if deck is not None and any(
            abs(p.rect.width - deck_width) > 0.1 or abs(p.rect.height - deck_height) > 0.1
            for p in document
        ):
            raise ValueError("PowerPointとPDFのページ寸法が一致しません。")
        for number, page in enumerate(document, 1):
            width, height = page.rect.width, page.rect.height
            tree = etree.fromstring(page.get_svg_image(text_as_path=False).encode())
            # Avoid duplicate clip/gradient IDs in the standalone multi-slide HTML.
            for element in tree.iter():
                if element.get("id"):
                    element.set("id", f"p{number}-" + element.get("id"))
                for attribute, value in list(element.attrib.items()):
                    if value.startswith("#") and attribute.endswith("href"):
                        element.set(attribute, f"#p{number}-" + value[1:])
                    elif "url(#" in value:
                        element.set(attribute, value.replace("url(#", f"url(#p{number}-"))
            fonts = {f[3]: f[0] for f in page.get_fonts()}
            nodes, traces = tree.findall(".//s:text", NS), page.get_texttrace()
            if len(nodes) != len(traces):
                raise ValueError(f"スライド{number}の文字と描画を対応付けできません。")
            boxes = (
                source_textboxes(deck.slides[number - 1])
                if deck is not None
                else pdf_textboxes(page)
            )
            fragments = []
            glyph_uses, glyph_paths, cursor = None, {}, 0
            for index, (node, trace) in enumerate(zip(nodes, traces, strict=True), 1):
                text = "".join(node.itertext())
                if text != "".join(chr(c[0]) for c in trace["chars"]):
                    raise ValueError(f"スライド{number}の文字列が描画と一致しません。")
                node_id = f"p{number}-text-{index}"
                node.set("id", node_id)
                box = match_box(trace, text, boxes)
                if box:
                    node.set("data-source-id", box["id"])
                    box["node_ids"].append(node_id)
                font_name = node.get("font-family")
                xref = fonts.get(font_name) or fonts.get(trace["font"])
                alias = f"pdf-font-{xref}" if xref else font_name
                embedded = False
                if xref:
                    name, extension, _, content = document.extract_font(xref)
                    if content and extension in {"ttf", "otf"}:
                        pairs = {unicode: glyph for unicode, glyph, *_ in trace["chars"]}
                        key = xref
                        if any(
                            u in font_maps[xref] and font_maps[xref][u] != g
                            for u, g in pairs.items()
                        ):
                            # Keep alternate glyphs in their own face, without changing other runs.
                            key = f"{xref}-p{number}-{index}"
                            alias = f"pdf-font-{key}"
                        font_files[key] = (content, extension)
                        font_maps[key].update(pairs)
                        embedded = True
                if not embedded:
                    if glyph_uses is None:
                        outlined = etree.fromstring(page.get_svg_image(text_as_path=True).encode())
                        glyph_uses = outlined.xpath("//*[@data-text]")
                        if len(glyph_uses) != sum(len(t["chars"]) for t in traces):
                            raise ValueError(
                                f"ページ{number}のフォント輪郭と文字を対応付けできません。"
                            )
                        glyph_paths = {
                            p.get("id"): p.get("d", "")
                            for p in outlined.findall(".//s:defs/s:path", NS)
                        }
                    font_key = xref or re.sub(r"[^a-zA-Z0-9]", "_", trace["font"])
                    alias = f"pdf-outline-p{number}-{font_key}"
                    for use, char in zip(
                        glyph_uses[cursor : cursor + len(trace["chars"])],
                        trace["chars"],
                        strict=True,
                    ):
                        if use.get("data-text") != chr(char[0]):
                            raise ValueError("PDFのフォント輪郭と文字列が一致しません。")
                        path = glyph_paths[use.get(f"{{{XLINK}}}href", "").removeprefix("#")]
                        advance = (char[3][2] - char[3][0]) / trace["size"] if trace["size"] else 1
                        fallback_glyphs[alias][char[0]] = (path, advance)
                cursor += len(trace["chars"])
                node.set("font-family", alias)
                node.set(
                    "style",
                    f"font-family:{json.dumps(alias)},Arial,sans-serif;font-size:{float(node.get('font-size')):g}px",
                )
                fragments.append(
                    {
                        "id": node_id,
                        "text": text,
                        "bbox": list(trace["bbox"]),
                        "font": font_name,
                        "size": trace["size"],
                        "color": trace["color"],
                        "direction": trace["dir"],
                        "source_id": box["id"] if box else None,
                        "source_text_differs": bool(
                            box and normalize(text) not in normalize(box["source_text"])
                        ),
                    }
                )
            pages.append(
                {
                    "number": number,
                    "width": width,
                    "height": height,
                    "textboxes": boxes,
                    "fragments": fragments,
                }
            )
            trees.append(tree)
    directory.mkdir(exist_ok=True)
    (directory / "manifest.json").unlink(missing_ok=True)
    css = []
    for alias, glyphs in fallback_glyphs.items():
        encoded = base64.b64encode(outline_font(glyphs)).decode()
        css.append(f'@font-face{{font-family:"{alias}";src:url(data:font/ttf;base64,{encoded})}}')
    for xref, (content, extension) in font_files.items():
        # Keep embedded fonts in CSS data URLs, avoiding opaque-frame CORS restrictions.
        content = web_font(content, font_maps[xref])
        encoded = base64.b64encode(content).decode()
        css.append(
            f'@font-face{{font-family:"pdf-font-{xref}";src:url(data:font/{extension};base64,{encoded})}}'
        )
    font_css = "\n".join(css)
    (directory / "fonts.css").write_text(font_css)
    styles = (
        "html,body{margin:0;padding:0;background:#e8edf2}.slide{position:relative;"
        "margin:12px auto;background:white}"
        ".slide svg{display:block;width:100%;height:100%}.slide text{user-select:text}" + font_css
    )
    sections = []
    assets = ["Preview.html", "fonts.css", "text-layout.json"]
    for number, tree in enumerate(trees, 1):
        tree.set("data-text-layout", json.dumps(pages[number - 1], ensure_ascii=False))
        svg = etree.tostring(tree, encoding="unicode")
        svg_path = directory / f"slide-{number}.svg"
        svg_path.write_text(svg)
        assets.append(svg_path.name)
        page = pages[number - 1]
        section = f'<div class="slide" data-number="{number}" style="width:{page["width"]:g}px;height:{page["height"]:g}px">{svg}</div>'
        sections.append(section)
        page_path = directory / f"page-{number}.html"
        page_path.write_text(
            '<!doctype html><html lang="ja"><head><meta charset="utf-8"><link rel="stylesheet" href="fonts.css"><style>html,body,.slide,svg{margin:0;width:100%;height:100%;overflow:hidden}svg{display:block}text{user-select:text}</style></head><body>'
            + svg
            + "</body></html>"
        )
        assets.append(page_path.name)
    (directory / "Preview.html").write_text(
        '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
        f"<title>{html.escape(source.name)}</title><style>{styles}</style></head><body>"
        + "".join(sections)
        + "</body></html>"
    )
    (directory / "text-layout.json").write_text(
        json.dumps({"version": 1, "pages": pages}, ensure_ascii=False, indent=2)
    )
    if digest(source) != original_digest or digest(pdf) != pdf_digest:
        raise ValueError("生成中に原本またはPDFが変更されました。")
    manifest = {
        "version": RENDERER_VERSION,
        "renderer": "Native PDF / editable SVG text",
        "pdf": pdf_relative,
        "source_sha256": original_digest,
        "pdf_sha256": pdf_digest,
        "pages": len(pages),
        "assets": assets,
        "text_fragments": sum(len(p["fragments"]) for p in pages),
        "mapped_fragments": sum(bool(f["source_id"]) for p in pages for f in p["fragments"]),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return (directory / "Preview.html").relative_to(folder).as_posix()


def ensure_pdf_preview(source: Path, folder: Path) -> str:
    with PDF_RENDER_LOCK:
        return editable_preview(source, folder) or build_editable_preview(source, source, folder)


def replace_textbox(tree, page: dict, source_id: str, text: str) -> None:
    box = next(b for b in page["textboxes"] if b["id"] == source_id)
    nodes = [tree.xpath("//*[@id=$id]", id=node_id)[0] for node_id in box["node_ids"]]
    if not nodes:
        raise ValueError("描画文字と対応しない文字枠は置換できません。")
    fragment = next(f for f in page["fragments"] if f["id"] == box["node_ids"][0])
    if abs(fragment["direction"][1]) > 0.01:
        raise ValueError("回転・縦書き文字枠の翻訳描画は未対応です。")
    x0, y0, x1, y1 = box["bbox"]
    left, top, right, bottom = box["margins"]
    tag = f"{{{SVG}}}foreignObject" if etree.QName(tree).namespace == SVG else "foreignObject"
    replacement = etree.Element(tag, id=f"translation-{source_id}")
    replacement.attrib.update(
        {
            "x": str(x0 + left),
            "y": str(y0 + top),
            "width": str(x1 - x0 - left - right),
            "height": str(y1 - y0 - top - bottom),
        }
    )
    container = etree.SubElement(replacement, f"{{{XHTML}}}div", nsmap={None: XHTML})
    color = fragment["color"]
    rgb = [round(c * 255) for c in color] if len(color) == 3 else [round(color[0] * 255)] * 3
    container.set(
        "style",
        f'height:100%;display:flex;flex-direction:column;justify-content:{"center" if box["vertical_alignment"] == "middle" else "flex-end" if box["vertical_alignment"] == "bottom" else "flex-start"};font-family:Arial,"Hiragino Sans","Yu Gothic",sans-serif;font-size:{fragment["size"]:g}px;line-height:1.2;white-space:pre-wrap;overflow-wrap:anywhere;text-align:{box["alignment"]};color:rgb({rgb[0]},{rgb[1]},{rgb[2]})',
    )
    paragraph = etree.SubElement(container, f"{{{XHTML}}}div")
    paragraph.set("style", "width:100%;max-height:100%")
    paragraph.text = text
    nodes[0].addprevious(replacement)
    for node in nodes:
        node.getparent().remove(node)


def translated_slide_html(directory: Path, number: int, replacements: dict[str, str]) -> str:
    """A translation consumer can replace a whole source textbox, preserving native graphics."""
    page = json.loads((directory / "text-layout.json").read_text())["pages"][number - 1]
    tree = etree.fromstring((directory / f"slide-{number}.svg").read_bytes())
    for source_id, text in replacements.items():
        replace_textbox(tree, page, source_id, text)
    styles = (directory / "fonts.css").read_text()
    return (
        '<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>body{margin:0}'
        + styles
        + "</style></head><body>"
        + etree.tostring(tree, encoding="unicode")
        + "</body></html>"
    )
