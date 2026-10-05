import json
import shutil
from pathlib import Path

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
)
from lxml import html

from docling_desk.preview.slides import SlideDocument, export_slide_layout, split_quicklook

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests/fixtures/documents/slide"


def test_positioned_extraction_keeps_table_and_chart_side_by_side(tmp_path):
    doc = DoclingDocument.load_from_json(SAMPLE / "document.json")
    before = doc.model_dump_json()
    export_slide_layout(doc, SAMPLE / "original.pptx", tmp_path, None)
    data = SlideDocument.model_validate_json((tmp_path / "slides.json").read_text())
    assert len(data.slides) == 2
    first = data.slides[0]
    assert first.width == 864 and first.height == 486
    table = next(b for b in first.blocks if b.kind == "table")
    chart = next(b for b in first.blocks if b.kind == "picture")
    assert table.x == 43.2 and table.width == 288
    assert chart.x == 374.4 and chart.x > table.x + table.width
    assert chart.image.startswith("data:image/png;")
    assert {"120", "180", "150", "450"} <= {c.text for c in table.cells}
    assert first.blocks[0].font_size == 28
    # Paragraphs sharing a PowerPoint text frame must not overlap one another.
    second = data.slides[1]
    assert len(second.blocks) == 2
    body = next(b for b in second.blocks if len(b.texts) == 3)
    assert len(body.refs) == 3 and "翻訳と実資料" in body.texts[2]
    assert doc.model_dump_json() == before


def test_source_slide_frames_share_assets_and_do_not_cross_page_boundaries(tmp_path):
    preview = SAMPLE / "quicklook/original.pptx.qlpreview"
    local = tmp_path / "quicklook"
    shutil.copytree(preview, local)
    paths = split_quicklook(local / "Preview.html", tmp_path, 2)
    assert sorted(paths) == [1, 2]
    first, second = [html.fromstring((tmp_path / paths[i]).read_text()) for i in [1, 2]]
    assert len(first.xpath('//div[@class="slide"]')) == 1
    assert (
        "表と図の比較" in first.text_content() and "日本語と出典の確認" not in first.text_content()
    )
    assert "日本語と出典の確認" in second.text_content()
    assert not first.xpath("//script|//iframe|//object|//embed")
    assert first.xpath("//img/@src") == ["Attachment1.png"]
    assert (local / "Attachment1.png").is_file()
    assert "zoom:1!important" in (local / "slide-layout.css").read_text()
    assert split_quicklook(local / "Preview.html", tmp_path, 3) == {}


def test_zero_size_notes_are_kept_outside_the_canvas(tmp_path):
    doc = DoclingDocument(name="notes")
    doc.add_page(page_no=1, size=Size(width=10972800, height=6172200))
    item = doc.add_text(
        label=DocItemLabel.TEXT,
        text="位置のないノート",
        prov=ProvenanceItem(
            page_no=1,
            charspan=(0, 9),
            bbox=BoundingBox(l=0, t=0, r=0, b=0, coord_origin=CoordOrigin.TOPLEFT),
        ),
    )
    export_slide_layout(doc, SAMPLE / "original.pptx", tmp_path, None)
    data = json.loads((tmp_path / "slides.json").read_text())
    assert data["slides"][0]["blocks"] == []
    assert data["slides"][0]["unplaced"] == [{"ref": item.self_ref, "text": "位置のないノート"}]
