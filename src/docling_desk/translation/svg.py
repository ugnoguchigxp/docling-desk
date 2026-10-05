"""Let translated SVG glyphs advance normally, retaining native line anchors."""

from __future__ import annotations

import json
import re
from copy import deepcopy


def natural_positions(span) -> None:
    # PDF SVG stores one coordinate per Latin glyph. Those advances cannot be
    # reused for CJK glyphs. Keep the first coordinate and the native transform.
    for attribute in ("x", "y"):
        positions = re.split(r"[\s,]+", span.get(attribute, "").strip())
        if len(positions) > 1:
            span.set(attribute, positions[0])


def compatible(first, following) -> bool:
    if first.getparent() is not following.getparent():
        return False
    # Do not flatten distinct styles, rotations, clips, or coordinate scales.
    attributes = (
        "style",
        "font-size",
        "font-family",
        "font-weight",
        "font-style",
        "fill",
        "clip-path",
        "text-anchor",
    )
    if any(first.get(name) != following.get(name) for name in attributes):
        return False
    transforms = [
        re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", n.get("transform", ""))
        for n in (first, following)
    ]
    if first.get("transform", "") != following.get("transform", ""):
        if not all(n.get("transform", "").startswith("matrix(") for n in (first, following)):
            return False
        if not all(len(t) == 6 for t in transforms) or transforms[0][:4] != transforms[1][:4]:
            return False
    return all(len(n) == 1 and n[0].tag == "tspan" for n in (first, following))


def flow_line(nodes: list, fragments: list[dict], changed: set) -> None:
    if not any(span in changed for node in nodes for span in node):
        return
    first = nodes[0]
    natural_positions(first[0])
    previous = first[0]
    for index, node in enumerate(nodes[1:], 1):
        span = deepcopy(node[0])
        for attribute in ("x", "y", "dx", "dy", "rotate", "id"):
            span.attrib.pop(attribute, None)
        span.tail = None
        gap = fragments[index]["bbox"][0] - fragments[index - 1]["bbox"][2]
        if (
            gap > fragments[index]["size"] * 0.12
            and previous.text
            and not previous.text[-1].isspace()
            and span.text
            and not span.text[0].isspace()
        ):
            span.text = " " + span.text
        first.append(span)
        node.getparent().remove(node)
        previous = span


def flow_translated_svg(tree, changed: set) -> None:
    # Standalone/rotated runs also need normal glyph advances; they keep their
    # original rotation and start point and never gain automatic line breaks.
    for span in changed:
        if span.tag == "tspan" and any(n.tag == "svg" for n in span.iterancestors()):
            natural_positions(span)
    for svg in tree.xpath("//svg[@data-text-layout]"):
        try:
            page = json.loads(svg.get("data-text-layout"))
            fragments = {f["id"]: f for f in page["fragments"]}
            boxes = {b["id"]: b for b in page["textboxes"]}
        except (ValueError, KeyError, TypeError):
            continue
        groups: list[tuple[list, list[dict]]] = []
        for node in svg.xpath(".//text"):
            fragment = fragments.get(node.get("id"))
            box = boxes.get(fragment.get("source_id")) if fragment else None
            if (
                not fragment
                or not box
                or abs(fragment["direction"][1]) > 0.01
                or "\t" in box["source_text"]
                or box["alignment"] != "left"
                or len(node) != 1
                or node[0].tag != "tspan"
            ):
                if groups:
                    flow_line(*groups[-1], changed)
                groups = []
                continue
            previous = groups[-1] if groups else None
            if previous:
                nodes, rows = previous
                last = rows[-1]
                same_line = (
                    last["source_id"] == fragment["source_id"]
                    and abs(last["bbox"][1] - fragment["bbox"][1]) < 1
                    and -1 <= fragment["bbox"][0] - last["bbox"][2] <= max(20, last["size"] * 1.5)
                    and compatible(nodes[0], node)
                )
                if same_line:
                    nodes.append(node)
                    rows.append(fragment)
                    continue
            if groups:
                flow_line(*groups[-1], changed)
            groups.append(([node], [fragment]))
        if groups:
            flow_line(*groups[-1], changed)
