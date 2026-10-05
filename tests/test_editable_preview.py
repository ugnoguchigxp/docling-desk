import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import reportlab
from fontTools.ttLib import TTFont
from lxml import html
from pptx import Presentation
from pptx.util import Pt
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as ReportlabFont
from reportlab.pdfgen import canvas

from docling_desk.documents.conversion import office_preview
from docling_desk.preview.editable_preview import (
    NOTICE,
    build_editable_preview,
    editable_preview,
    source_textboxes,
    translated_slide_html,
    web_font,
)
from docling_desk.preview.native_preview import build_native_preview
from docling_desk.preview.slides import split_quicklook
from docling_desk.translation.source import html_segments, slide_template
from docling_desk.translation.view import apply_text


def sources(folder, pdf_count=2, size=(960, 540)):
    source, pdf = folder / "original.pptx", folder / "powerpoint-rendered.pdf"
    deck = Presentation()
    deck.slide_width, deck.slide_height = Pt(960), Pt(540)
    labels = ["Material", "Vendor & <review>"]
    for label in labels:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        shape = slide.shapes.add_textbox(Pt(100), Pt(100), Pt(200), Pt(80))
        shape.text = label
    deck.save(source)
    font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    pdfmetrics.registerFont(ReportlabFont("EditablePreviewVera", str(font_path)))
    document = canvas.Canvas(str(pdf), pagesize=size)
    for label in labels[:pdf_count]:
        document.setFillColorRGB(0.5, 0.5, 0.9)
        document.rect(100, 360, 200, 80, fill=1, stroke=1)
        document.setFillColorRGB(0, 0, 0)
        document.setFont("EditablePreviewVera", 14)
        document.drawString(107, 420, label)
        document.showPage()
    document.save()
    return source, pdf


def test_preview_keeps_real_text_vector_paths_and_source_ids(tmp_path, monkeypatch):
    source, pdf = sources(tmp_path)
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, pdf)]
    preview = build_editable_preview(source, pdf, tmp_path)
    assert editable_preview(source, tmp_path) == preview
    frames = split_quicklook(tmp_path / preview, tmp_path, 2)
    for number, text in [(1, "Material"), (2, "Vendor & <review>")]:
        tree = html.fromstring((tmp_path / frames[number]).read_text())
        assert tree.xpath("//text/tspan/text()") == [text]
        assert tree.xpath("//path")
        assert not tree.xpath("//img")
        assert tree.xpath("//text/@data-source-id") == ["shape-2"]
        assert not tree.xpath("//script|//review")
    layout = json.loads((tmp_path / "editable-preview/text-layout.json").read_text())
    assert layout["pages"][0]["textboxes"][0]["source_text"] == "Material"
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, pdf)]

    def no_quicklook(*args, **kwargs):
        raise AssertionError("Editable native rendering must take priority")

    monkeypatch.setattr("docling_desk.documents.conversion.subprocess.run", no_quicklook)
    assert office_preview(source, tmp_path) == (preview, NOTICE)


def test_whole_textbox_can_be_replaced_with_japanese_without_changing_graphics(tmp_path):
    source, pdf = sources(tmp_path)
    build_editable_preview(source, pdf, tmp_path)
    directory = tmp_path / "editable-preview"
    original = (directory / "slide-1.svg").read_bytes()
    translated = translated_slide_html(directory, 1, {"shape-2": "品目\n日本語の本文"})
    tree = html.fromstring(translated)
    assert not tree.xpath("//text")
    assert tree.xpath("//foreignobject//div/text()") == ["品目\n日本語の本文"]
    original_paths = html.fromstring(original).xpath("//path/@d")
    assert tree.xpath("//path/@d") == original_paths
    assert (directory / "slide-1.svg").read_bytes() == original


def test_existing_translation_pipeline_keeps_graphics_and_native_text_anchor(tmp_path):
    source, pdf = sources(tmp_path)
    preview = build_editable_preview(source, pdf, tmp_path)
    frames = split_quicklook(tmp_path / preview, tmp_path, 2)
    raw = slide_template(tmp_path, frames[2])
    segments = html_segments(raw, html.fromstring(raw).xpath('//div[@class="slide"]'))
    assert len(segments) == 1
    assert segments[0]["source_text"] == "Vendor & <review>"
    assert segments[0]["locator"]["slot"] == "text"
    translated = apply_text(raw, {"segments": segments}, {segments[0]["id"]: "仕入先"})
    tree = html.fromstring(translated)
    assert tree.xpath("//tspan/text()") == ["仕入先"]
    assert tree.xpath("//path/@d") == html.fromstring(raw).xpath("//path/@d")
    assert tree.xpath("//text/@transform") == html.fromstring(raw).xpath("//text/@transform")
    assert tree.xpath("//text/@style") == html.fromstring(raw).xpath("//text/@style")
    assert tree.xpath("//tspan/@x") == [html.fromstring(raw).xpath("//tspan/@x")[0].split()[0]]
    stale = dict(segments[0], source_text="different source")
    with pytest.raises(ValueError):
        apply_text(raw, {"segments": [stale]}, {stale["id"]: "仕入先"})


def test_unmapped_svg_text_uses_natural_japanese_glyph_advances(tmp_path):
    source, pdf = sources(tmp_path)
    preview = build_editable_preview(source, pdf, tmp_path)
    frames = split_quicklook(tmp_path / preview, tmp_path, 2)
    raw = slide_template(tmp_path, frames[1])
    tree = html.fromstring(raw)
    svg = tree.xpath("//svg")[0]
    page = json.loads(svg.get("data-text-layout"))
    page["textboxes"] = []
    svg.set("data-text-layout", json.dumps(page))
    raw = html.tostring(tree, encoding="unicode")
    segments = html_segments(raw, tree.xpath('//div[@class="slide"]'))
    translated = apply_text(raw, {"segments": segments}, {segments[0]["id"]: "品目"})
    result = html.fromstring(translated)
    assert result.xpath("//tspan/@x") == [tree.xpath("//tspan/@x")[0].split()[0]]
    assert result.xpath("//text/@transform") == tree.xpath("//text/@transform")
    assert result.xpath("//text/@style") == tree.xpath("//text/@style")
    assert result.xpath("//path/@d") == tree.xpath("//path/@d")


def test_group_offsets_and_scaling_are_retained_for_translation_targets():
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    child = group.shapes.add_textbox(Pt(10), Pt(20), Pt(200), Pt(80))
    child.text = "Nested label"
    group.left, group.top, group.width, group.height = Pt(100), Pt(200), Pt(400), Pt(160)
    box = source_textboxes(slide)[0]
    assert box["shape_path"] == [group.shape_id, child.shape_id]
    assert box["bbox"] == pytest.approx([100, 200, 500, 360])


def test_pdf_subset_font_unicode_mapping_can_be_restored():
    path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    with TTFont(path) as font:
        glyph = font.getBestCmap()[ord("A")]
        glyph_id = font.getGlyphID(glyph)
        font["cmap"].tables = []
        del font["OS/2"]
        del font["post"]
        content = BytesIO()
        font.save(content)
    restored = web_font(content.getvalue(), {ord("A"): glyph_id})
    with TTFont(BytesIO(restored)) as font:
        assert font.getBestCmap()[ord("A")] == glyph
        assert "OS/2" in font
        assert font["post"].formatType == 3


@pytest.mark.parametrize("pdf_count,size", [(1, (960, 540)), (2, (540, 960))])
def test_wrong_pdf_rejected_before_registration(tmp_path, pdf_count, size):
    source, pdf = sources(tmp_path, pdf_count, size)
    with pytest.raises(ValueError):
        build_editable_preview(source, pdf, tmp_path)
    assert not (tmp_path / "editable-preview").exists()


@pytest.mark.parametrize("change", ["source", "pdf", "asset", "outside"])
def test_stale_or_incomplete_editable_cache_is_rejected(tmp_path, change):
    source, pdf = sources(tmp_path)
    build_editable_preview(source, pdf, tmp_path)
    if change in {"source", "pdf"}:
        target = source if change == "source" else pdf
        target.write_bytes(target.read_bytes() + b"changed")
    elif change == "asset":
        (tmp_path / "editable-preview/slide-2.svg").unlink()
    else:
        path = tmp_path / "editable-preview/manifest.json"
        data = json.loads(path.read_text())
        data["assets"] = ["../outside.svg"]
        path.write_text(json.dumps(data))
    assert editable_preview(source, tmp_path) is None


def test_reference_image_cache_is_never_the_default_preview(tmp_path, monkeypatch):
    source, pdf = sources(tmp_path)
    reference = build_native_preview(source, pdf, tmp_path)
    monkeypatch.setattr(
        "docling_desk.documents.conversion.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1),
    )
    assert office_preview(source, tmp_path)[0] != reference
