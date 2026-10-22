"""Format adapters for diagram text, feeding the shared document search index."""

from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup

from docling_desk.documents.diagrams import read_diagram, xml_node


def excalidraw_sections(raw: str) -> list[dict]:
    # Excalidraw's text objects carry their displayed text in `text`. Deleted
    # objects remain in the scene for collaboration; they are not evidence.
    scene = json.loads(raw)
    if not isinstance(scene, dict) or not isinstance(scene.get("elements"), list):
        raise ValueError("Invalid Excalidraw scene")
    if any(not isinstance(element, dict) for element in scene["elements"]):
        raise ValueError("Invalid Excalidraw element")
    elements = [
        element
        for element in scene["elements"]
        if element.get("type") == "text"
        and not element.get("isDeleted", False)
        and isinstance(element.get("text"), str)
        and element["text"].strip()
    ]
    return (
        [{"text": "\n".join(e["text"] for e in elements), "refs": [e["id"] for e in elements]}]
        if elements
        else []
    )


def drawio_sections(source: Path) -> tuple[str, list[dict]]:
    # Reuse the bounded, offline decoder for XML, compressed pages, and the
    # HTML/SVG/PNG containers accepted by the existing upload/viewer contract.
    diagram = read_diagram(source)
    root = xml_node(diagram.xml)
    sections = []
    for number, page in enumerate(root.findall("diagram"), 1):
        labels, refs = [], []
        cells = page.find("mxGraphModel/root")
        if cells is None:
            raise ValueError("Missing draw.io cells")
        for node in cells:
            cell = node if node.tag == "mxCell" else node.find("mxCell")
            if cell is None:
                continue
            # The draw.io object wrapper's label replaces mxCell.value.
            label = node.get("label", "") if node is not cell else cell.get("value", "")
            if "html=1" in cell.get("style", "").split(";"):
                html = BeautifulSoup(label, "html.parser")
                for tag in html(["script", "style"]):
                    tag.decompose()
                label = html.get_text(" ", strip=True)
            if label.strip():
                labels.append(label)
                refs.append(node.get("id") or cell.get("id", ""))
        # Page names are also visible text, including otherwise empty pages.
        title = page.get("name", "")
        sections.append(
            {
                "text": "\n".join([title, *labels]).strip(),
                "heading": title,
                "pages": [number],
                "refs": refs,
                "unit": {"number": number},
            }
        )
    return diagram.xml, sections
