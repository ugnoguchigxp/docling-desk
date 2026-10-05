import json

from lxml import html

from docling_desk.translation.source import html_segments
from docling_desk.translation.view import apply_text


def slide(lines):
    tree = html.fromstring(
        '<html><body><div class="slide"><svg width="960" height="540">'
        '<path d="M0 0L10 10" fill="orange"></path><image href="logo.png"></image>'
        "</svg></div></body></html>"
    )
    svg = tree.xpath("//svg")[0]
    fragments = []
    boxes = {}
    for index, (text, source_id, x, y, width, color, direction) in enumerate(lines):
        node = html.Element("text", id=f"n{index}", fill=color)
        node.set("font-size", "20")
        node.set("font-family", "Arial")
        node.set("transform", f"matrix(1 0 0 1 {x} {y})")
        span = html.Element("tspan", x="0,8,16,24", y="0")
        span.text = text
        node.append(span)
        svg.append(node)
        fragments.append(
            {
                "id": node.get("id"),
                "text": text,
                "bbox": [x, y, x + width, y + 20],
                "size": 20,
                "direction": direction,
                "source_id": source_id,
            }
        )
        if source_id:
            boxes[source_id] = {
                "id": source_id,
                "source_text": "original paragraph",
                "alignment": "left",
            }
    svg.set(
        "data-text-layout", json.dumps({"fragments": fragments, "textboxes": list(boxes.values())})
    )
    raw = html.tostring(tree, encoding="unicode")
    segments = html_segments(raw, tree.xpath('//div[@class="slide"]'))
    return raw, {"segments": segments}


def translate(raw, unit, values):
    return html.fromstring(
        apply_text(
            raw,
            unit,
            {s["id"]: values.get(s["source_text"], s["source_text"]) for s in unit["segments"]},
        )
    )


def test_fragmented_line_flows_without_wrap_and_keeps_existing_line_breaks():
    raw, unit = slide(
        [
            ("Assessment Phase", "box", 64, 188, 245, "black", [1, 0]),
            ("High", "box", 64, 223, 66, "black", [1, 0]),
            ("-", "box", 130, 223, 12, "black", [1, 0]),
            ("Level Assessment ", "box", 142, 223, 244, "black", [1, 0]),
            ("–", "box", 387, 223, 14, "black", [1, 0]),
            ("Discovery Report", "box", 409, 223, 234, "black", [1, 0]),
            # An unmapped following run must not prevent the previous line's flush.
            ("Company", None, 30, 480, 100, "orange", [1, 0]),
        ]
    )
    result = translate(
        raw,
        unit,
        {
            "Assessment Phase": "評価フェーズ",
            "High": "高",
            "Level Assessment ": "レベル評価 ",
            "Discovery Report": "ディスカバリーレポート",
        },
    )
    assert [n.text_content() for n in result.xpath("//text")] == [
        "評価フェーズ",
        "高-レベル評価 – ディスカバリーレポート",
        "Company",
    ]
    assert result.xpath("//text/@transform") == [
        "matrix(1 0 0 1 64 188)",
        "matrix(1 0 0 1 64 223)",
        "matrix(1 0 0 1 30 480)",
    ]
    assert result.xpath("//tspan[@x]/@x")[:2] == ["0", "0"]
    assert not result.xpath("//foreignobject|//br")
    assert result.xpath("//path/@d|//image/@href|//svg/@width|//svg/@height") == html.fromstring(
        raw
    ).xpath("//path/@d|//image/@href|//svg/@width|//svg/@height")


def test_identical_translation_leaves_original_svg_structure_and_spacing_intact():
    raw, unit = slide(
        [
            ("Name", "box", 10, 10, 100, "black", [1, 0]),
            ("Tail", "box", 110, 10, 50, "black", [1, 0]),
        ]
    )
    result = translate(raw, unit, {})
    assert html.tostring(result) == html.tostring(html.fromstring(raw))


def test_distinct_textboxes_and_styles_are_not_joined():
    raw, unit = slide(
        [
            ("One", "box-a", 0, 10, 60, "black", [1, 0]),
            ("Two", "box-b", 60, 10, 60, "black", [1, 0]),
            ("Three", "box-b", 120, 10, 60, "orange", [1, 0]),
        ]
    )
    result = translate(raw, unit, {"One": "一", "Two": "二", "Three": "三"})
    assert [n.text_content() for n in result.xpath("//text")] == ["一", "二", "三"]
    assert result.xpath("//text/@fill") == ["black", "black", "orange"]


def test_rotated_runs_keep_their_original_transform():
    raw, unit = slide(
        [
            ("Rotated", "box", 10, 10, 60, "black", [0, 1]),
            ("Other", "box", 10, 30, 60, "black", [0, 1]),
        ]
    )
    result = translate(raw, unit, {"Rotated": "回転文字", "Other": "別の行"})
    assert len(result.xpath("//text")) == 2
    assert result.xpath("//text/@transform") == html.fromstring(raw).xpath("//text/@transform")


def test_large_tab_gap_preserves_separate_columns():
    raw, unit = slide(
        [
            ("Left", "box", 0, 10, 50, "black", [1, 0]),
            ("Right", "box", 400, 10, 50, "black", [1, 0]),
        ]
    )
    result = translate(raw, unit, {"Left": "左", "Right": "右"})
    assert len(result.xpath("//text")) == 2
