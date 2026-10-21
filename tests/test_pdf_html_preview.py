import hashlib

import pypdfium2 as pdfium
from lxml import html
from PIL import Image
from reportlab.pdfgen import canvas

from docling_desk.documents.conversion import Job, convert_job
from docling_desk.preview.editable_preview import build_editable_preview, editable_preview


def test_pdf_keeps_mixed_dimensions_rotation_and_a_selectable_text_layer(tmp_path):
    source = tmp_path / "original.pdf"
    doc = canvas.Canvas(str(source), pagesize=(300, 400))
    doc.drawString(30, 350, "First page")
    doc.rect(20, 250, 200, 80)
    doc.showPage()
    doc.setPageSize((600, 300))
    doc.drawString(30, 250, "Second page")
    doc.save()
    pdf = pdfium.PdfDocument(source)
    pdf[1].set_rotation(90)
    pdf.save(tmp_path / "rotated.tmp")
    pdf.close()
    (tmp_path / "rotated.tmp").replace(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    path = build_editable_preview(source, source, tmp_path)
    assert editable_preview(source, tmp_path) == path
    sizes = []
    for number, text in [(1, "First page"), (2, "Second page")]:
        tree = html.fromstring((tmp_path / f"editable-preview/page-{number}.html").read_text())
        page = tree.xpath('//div[@class="page-canvas"]')[0]
        sizes.append((float(page.get("data-width")), float(page.get("data-height"))))
        assert tree.xpath('//div[contains(@class,"pdf-text")]/text()') == [text]
        assert tree.xpath("//img/@src") == [f"page-{number}.webp"]
        assert not tree.xpath("//svg|//text|//script")
        with Image.open(tmp_path / f"editable-preview/page-{number}.webp") as image:
            assert image.size == (int(sizes[-1][0] * 2), int(sizes[-1][1] * 2))
    # The second page is displayed rotated; its text runs down the page.
    assert sizes == [(300, 400), (300, 600)]
    rotated = html.fromstring((tmp_path / "editable-preview/page-2.html").read_text()).xpath(
        '//div[contains(@class,"pdf-text")]/@style'
    )[0]
    assert "rotate(90.000deg)" in rotated
    assert before == hashlib.sha256(source.read_bytes()).hexdigest()


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
