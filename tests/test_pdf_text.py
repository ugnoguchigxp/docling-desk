"""Positioned text read from PDFium: lines, paragraph blocks, rotation and colour."""

from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docling.utils.locks import pypdfium2_lock
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from docling_desk.preview.pdf_text import group_blocks, read_page


def read(path: Path, number: int = 1, rotation: int | None = None):
    with pypdfium2_lock, pdfium.PdfDocument(path) as document:
        page = document[number - 1]
        if rotation is not None:
            page.set_rotation(rotation)
        return read_page(page)


def draw(path: Path, paint, size=(400, 300)):
    page = canvas.Canvas(str(path), pagesize=size)
    paint(page)
    page.showPage()
    page.save()
    return path


def test_lines_come_back_top_down_in_page_points_with_size_and_colour(tmp_path):
    def paint(page):
        page.setFont("Helvetica", 12)
        page.setFillColorRGB(1, 0, 0)
        page.drawString(40, 250, "Red heading")
        page.setFillColorRGB(0, 0, 0)
        page.setFont("Helvetica", 20)
        page.drawString(40, 200, "Large")

    lines = read(draw(tmp_path / "a.pdf", paint))
    first, second = sorted(lines, key=lambda line: line.y0)
    assert (first.text, first.size, first.color, first.rotation) == (
        "Red heading",
        12,
        (255, 0, 0),
        0,
    )
    assert (second.text, second.size, second.color) == ("Large", 20, (0, 0, 0))
    # PDF baseline 250 from the bottom of a 300pt page is 50pt from the top.
    assert first.x0 == pytest.approx(40, abs=1.5) and 38 < first.y0 < 52 < first.y1 < 66
    assert second.y0 > first.y1


def test_scaled_text_matrix_gives_the_rendered_size(tmp_path):
    def paint(page):
        page.scale(2, 2)
        page.setFont("Helvetica", 6)
        page.drawString(20, 100, "Scaled up")

    assert read(draw(tmp_path / "scaled.pdf", paint))[0].size == pytest.approx(12)


def test_wrapped_paragraph_becomes_one_block_and_other_text_stays_apart(tmp_path):
    def paint(page):
        page.setFont("Helvetica", 12)
        for index, text in enumerate(["first line of text", "second line of text", "third line"]):
            page.drawString(40, 250 - 14 * index, text)
        page.drawString(40, 150, "separate paragraph")  # a clear gap
        page.setFont("Helvetica", 24)
        page.drawString(40, 100, "Different size")
        page.setFont("Helvetica", 12)
        page.drawString(200, 250, "second column")  # same baseline, far right

    blocks = group_blocks(read(draw(tmp_path / "b.pdf", paint)))
    assert [b.text for b in blocks] == [
        "first line of text\nsecond line of text\nthird line",
        "second column",
        "separate paragraph",
        "Different size",
    ]
    assert blocks[0].pitch == pytest.approx(14, abs=0.1) and blocks[0].size == 12


def test_list_items_and_table_cells_are_separate_blocks(tmp_path):
    def paint(page):
        page.setFont("Helvetica", 12)
        for index, text in enumerate(["- one item", "- another item", "1. numbered"]):
            page.drawString(40, 250 - 14 * index, text)
        page.drawString(40, 150, "Cell A")
        page.drawString(160, 150, "Cell B")

    blocks = group_blocks(read(draw(tmp_path / "c.pdf", paint)))
    assert [b.text for b in blocks] == [
        "- one item",
        "- another item",
        "1. numbered",
        "Cell A",
        "Cell B",
    ]


def test_one_line_with_a_wide_gap_is_split_into_runs(tmp_path):
    def paint(page):
        page.setFont("Helvetica", 12)
        page.drawString(40, 250, "left part")
        page.drawString(300, 250, "right part")

    assert [line.text for line in read(draw(tmp_path / "gap.pdf", paint))] == [
        "left part",
        "right part",
    ]


def test_japanese_text_is_read_without_a_font_file(tmp_path):
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))

    def paint(page):
        page.setFont("HeiseiKakuGo-W5", 14)
        page.drawString(40, 200, "日本語のテキスト行")

    (line,) = read(draw(tmp_path / "ja.pdf", paint))
    assert line.text == "日本語のテキスト行"
    assert line.x1 - line.x0 == pytest.approx(14 * 9, abs=1)


@pytest.mark.parametrize("page_rotation,expected", [(90, 90), (180, 180), (270, 270)])
def test_page_rotation_turns_the_reading_direction(tmp_path, page_rotation, expected):
    def paint(page):
        page.setFont("Helvetica", 12)
        page.drawString(40, 250, "Upright on the unrotated page")

    path = draw(tmp_path / "r.pdf", paint)
    (line,) = read(path, rotation=page_rotation)
    assert line.rotation == expected and line.text == "Upright on the unrotated page"
    # The box is in the displayed page: a 90 degree page is 300 wide and 400 tall.
    assert (line.x1 - line.x0 < line.y1 - line.y0) == (expected in (90, 270))
    assert 0 <= line.x0 < line.x1 <= (300 if expected in (90, 270) else 400) + 1


def test_rotated_text_is_never_merged_and_off_axis_text_is_dropped(tmp_path):
    def paint(page):
        page.setFont("Helvetica", 12)
        page.saveState()
        page.translate(300, 100)
        page.rotate(90)
        page.drawString(0, 0, "Sideways one")
        page.drawString(0, -14, "Sideways two")
        page.restoreState()
        page.saveState()
        page.translate(100, 100)
        page.rotate(30)
        page.drawString(0, 0, "Diagonal")
        page.restoreState()
        page.drawString(40, 250, "Level")

    blocks = group_blocks(read(draw(tmp_path / "t.pdf", paint)))
    assert sorted((b.text, b.rotation) for b in blocks) == [
        ("Level", 0),
        ("Sideways one", 270),
        ("Sideways two", 270),
    ]
    sideways = next(b for b in blocks if b.text == "Sideways one")
    assert sideways.x1 - sideways.x0 < sideways.y1 - sideways.y0


def test_blank_and_image_only_pages_have_no_text(tmp_path):
    assert read(draw(tmp_path / "blank.pdf", lambda page: None)) == []
