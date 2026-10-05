import hashlib
import json
from io import BytesIO

import pymupdf
from fontTools.ttLib import TTFont
from lxml import html
from reportlab.pdfgen import canvas

from docling_desk.documents.conversion import Job, convert_job
from docling_desk.preview.editable_preview import (
    build_editable_preview,
    editable_preview,
    outline_font,
)


def test_pdf_retains_text_vectors_mixed_dimensions_and_rotation(tmp_path):
    source = tmp_path / "original.pdf"
    doc = canvas.Canvas(str(source), pagesize=(300, 400))
    doc.drawString(30, 350, "First page")
    doc.rect(20, 250, 200, 80)
    doc.showPage()
    doc.setPageSize((600, 300))
    doc.drawString(30, 250, "Second page")
    doc.save()
    pdf = pymupdf.open(source)
    pdf[1].set_rotation(90)
    pdf.saveIncr()
    pdf.close()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    path = build_editable_preview(source, source, tmp_path)
    assert editable_preview(source, tmp_path) == path
    pages = json.loads((tmp_path / "editable-preview/text-layout.json").read_text())["pages"]
    assert [(p["width"], p["height"]) for p in pages] == [(300, 400), (300, 600)]
    for number, text in [(1, "First page"), (2, "Second page")]:
        tree = html.fromstring((tmp_path / f"editable-preview/page-{number}.html").read_text())
        assert "".join(tree.xpath("//tspan/text()")) == text
        assert not tree.xpath("//img")
        assert tree.xpath("//text/@font-family")[0].startswith("pdf-outline-")
    assert before == hashlib.sha256(source.read_bytes()).hexdigest()


def test_unembedded_font_outline_becomes_real_unicode_font():
    # The exact glyph geometry is carried in a font; the document text stays text.
    font = outline_font({ord("A"): ("M0 0 L.3 1 L.6 0 Z", 0.7)})
    with TTFont(BytesIO(font)) as face:
        name = face.getBestCmap()[ord("A")]
        assert face["glyf"][name].numberOfContours == 1
        assert face["hmtx"][name][0] == round(0.7 * 2048)


def test_new_pdf_ingestion_produces_html_and_keeps_extraction_separate(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from docling.datamodel.base_models import ConversionStatus
    from docling_core.types.doc import DoclingDocument

    import docling_desk.documents.conversion as service

    source = tmp_path / "original.pdf"
    doc = canvas.Canvas(str(source), pagesize=(300, 400))
    doc.drawString(30, 350, "Fresh upload")
    doc.save()
    document = DoclingDocument.load_from_json(Path("tests/fixtures/documents/page/document.json"))
    monkeypatch.setattr(
        service,
        "converter",
        lambda: SimpleNamespace(
            convert=lambda *a, **k: SimpleNamespace(
                status=ConversionStatus.SUCCESS, document=document, errors=[]
            )
        ),
    )
    job = Job(id=tmp_path.name, filename="new.pdf")
    convert_job(tmp_path, job)
    assert job.state == "success", job.error
    assert job.preview == "editable-preview/Preview.html"
    assert not list(tmp_path.glob("page-*.png"))
    assert (tmp_path / "document.json").is_file() and (tmp_path / "rag.jsonl").is_file()
