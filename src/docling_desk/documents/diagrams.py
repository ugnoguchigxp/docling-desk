"""Read the draw.io file contract without interpreting or redrawing its contents."""

from __future__ import annotations

import base64
import binascii
import io
import json
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from PIL import Image

from docling_desk.config import MAX_BYTES, MAX_PAGES

DIAGRAM_SUFFIXES = {".drawio", ".dio", ".xml", ".svg", ".html", ".htm"}


def diagram_filename(name: str) -> bool:
    return Path(name).suffix.lower() in DIAGRAM_SUFFIXES or name.lower().endswith(".drawio.png")


@dataclass
class Diagram:
    xml: str
    names: list[str]


class HtmlDiagram(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.models: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "div" and "mxgraph" in (values.get("class") or "").split():
            data = json.loads(values.get("data-mxgraph") or "{}")
            if not isinstance(data, dict) or not isinstance(data.get("xml"), str):
                raise ValueError("HTMLに図データがありません。外部URLの図は取り込みません。")
            self.models.append(data["xml"])


def png_has_diagram(source: Path) -> bool:
    try:
        with Image.open(source) as image:
            return image.format == "PNG" and isinstance(image.info.get("mxfile"), str)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("PNG画像を読み込めません。") from exc


def embedded_xml(value: str) -> str:
    # Exporters store either plain XML or URI-encoded XML. Decode only the
    # latter, so literal percent sequences in labels and URLs are preserved.
    return value if value.lstrip().startswith("<") else unquote(value, errors="strict")


def xml_node(raw: bytes | str) -> ET.Element:
    if len(raw) > MAX_BYTES:
        raise ValueError("展開した図データの上限50 MiBを超えています。")
    return SafeET.fromstring(raw, forbid_dtd=True)


def expand_page(text: str, remaining: int) -> str:
    """draw.io: base64 -> raw DEFLATE -> URI-decoded UTF-8 XML, with a budget."""
    if text.lstrip().startswith("<"):
        if len(text.encode("utf-8")) > remaining:
            raise ValueError("展開した図データの上限50 MiBを超えています。")
        return text
    compressed = base64.b64decode("".join(text.split()), validate=True)
    decoder = zlib.decompressobj(-15)
    raw = decoder.decompress(compressed, remaining + 1)
    if len(raw) > remaining or decoder.unconsumed_tail:
        raise ValueError("展開した図データの上限50 MiBを超えています。")
    if not decoder.eof or decoder.unused_data:
        raise ValueError("圧縮された図データが不正です。")
    return unquote(raw.decode("utf-8"), errors="strict")


def read_diagram(source: Path) -> Diagram:
    raw = source.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("上限50 MiBを超えています。")
    try:
        if source.suffix.lower() == ".png":
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG" or not isinstance(image.info.get("mxfile"), str):
                    raise ValueError("PNGにdraw.ioの図データが含まれていません。")
                raw = embedded_xml(image.info["mxfile"]).encode("utf-8")
        if source.suffix.lower() in {".html", ".htm"}:
            parser = HtmlDiagram()
            parser.feed(raw.decode("utf-8-sig"))
            if len(parser.models) != 1:
                raise ValueError("draw.ioの図を1つ含むHTMLを書き出して選んでください。")
            raw = parser.models[0].encode("utf-8")
        root = xml_node(raw)
        if root.tag == "{http://www.w3.org/2000/svg}svg" or root.tag == "svg":
            content = root.get("content")
            if not content:
                raise ValueError("SVGにdraw.ioの図データが含まれていません。")
            root = xml_node(embedded_xml(content))
        if root.tag == "mxGraphModel":
            model = root
            root = ET.Element("mxfile")
            ET.SubElement(root, "diagram", id="page-1", name="ページ 1").append(model)
        if root.tag != "mxfile":
            raise ValueError("draw.ioの図データを含むファイルを選んでください。")
        pages = root.findall("diagram")
        if not pages:
            raise ValueError("図のページが含まれていません。")
        if len(pages) > MAX_PAGES:
            raise ValueError("図のページ数が設定された上限を超えています。")
        names, identifiers, total = [], set(), 0
        for number, page in enumerate(pages, 1):
            identifier = page.get("id") or f"page-{number}"
            if identifier in identifiers:
                raise ValueError("図のページIDが重複しています。")
            identifiers.add(identifier)
            page.set("id", identifier)
            name = page.get("name") or f"ページ {number}"
            names.append(name)
            model = page.find("mxGraphModel")
            if model is None:
                model = xml_node(expand_page(page.text or "", MAX_BYTES - total))
                page.text = None
                page.append(model)
            if model.tag != "mxGraphModel" or model.find("root") is None:
                raise ValueError("図のページ構造を読み込めません。")
            total += len(ET.tostring(model, encoding="utf-8"))
            if total > MAX_BYTES:
                raise ValueError("展開した図データの上限50 MiBを超えています。")
            # Embedded stencils use the same compressed XML contract. Validate
            # their expanded size too before letting the official viewer decode.
            for cell in model.iter():
                for entry in cell.get("style", "").split(";"):
                    if entry.startswith("shape=stencil(") and entry.endswith(")"):
                        stencil = expand_page(entry[len("shape=stencil(") : -1], MAX_BYTES - total)
                        xml_node(stencil)
                        total += len(stencil.encode("utf-8"))
                        if total > MAX_BYTES:
                            raise ValueError("展開した図データの上限50 MiBを超えています。")
        root.set("compressed", "false")
        return Diagram(ET.tostring(root, encoding="unicode"), names)
    except (
        ET.ParseError,
        DefusedXmlException,
        binascii.Error,
        zlib.error,
        UnicodeError,
        json.JSONDecodeError,
        OSError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError(
            "draw.ioの図データを読み込めません。破損や圧縮形式を確認してください。"
        ) from exc
