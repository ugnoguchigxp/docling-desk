from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from lxml import html
from PIL import Image
from reportlab.pdfgen import canvas

import docling_desk.app as web
import docling_desk.preview.pdf_view as pdf_view
from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, save_job
from docling_desk.storage import document_cache, document_folder, original_file, source_folder


@pytest.fixture
def pdf_client(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    job_id = "f" * 32
    folder = tmp_path / job_id
    folder.mkdir()
    source = folder / "original.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(300, 400))
    pdf.drawString(20, 350, "First page")
    pdf.showPage()
    pdf.setPageSize((600, 300))
    pdf.drawString(20, 250, "Landscape page")
    pdf.save()
    save_job(
        folder, Job(id=job_id, filename="<script>report</script>.pdf", preview="original.html")
    )
    with TestClient(web.app) as client:
        yield client, job_id, original_file(document_folder(tmp_path, job_id), ".pdf")


def test_pdf_viewer_uses_original_dimensions_and_blocks_document_scripts(pdf_client):
    client, job_id, source = pdf_client
    original = source.read_bytes()
    response = client.get(f"/view/{job_id}/pdf")
    assert response.status_code == 200
    assert 'data-width="400.0" data-height="533.3333333333334"' in response.text
    assert 'data-width="800.0" data-height="400.0"' in response.text
    assert "&lt;script&gt;report&lt;/script&gt;.pdf" in response.text
    assert "<script>report" not in response.text
    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "sandbox allow-scripts" in policy
    assert "allow-same-origin" not in policy
    assert client.get("/static/pdf-viewer.js").status_code == 200
    assert client.get("/static/pdf-viewer.css").status_code == 200
    assert not html.fromstring(response.text).xpath('//figure[@class="pdf-page"]/img')
    page = client.get(f"/view/{job_id}/pages/1")
    assert page.status_code == 200
    assert html.fromstring(page.text).xpath("//text/tspan/text()") == ["First page"]
    assert "font-src 'self' data:" in page.headers["Content-Security-Policy"]
    assert "sandbox allow-scripts" in page.headers["Content-Security-Policy"]
    assert "allow-same-origin" not in page.headers["Content-Security-Policy"]
    assert html.fromstring(page.text).xpath("//script/@src") == [
        "/static/document-frame.js",
        "/static/file-drop.js",
    ]
    assert client.get(f"/view/{job_id}/pages/0").status_code == 404
    assert client.get(f"/view/{job_id}/pages/3").status_code == 404
    assert client.get(f"/view/{job_id}/pages/1?language=fr").status_code == 422
    assert source.read_bytes() == original


def test_pdf_thumbnails_are_small_webp_cached_and_replaced(pdf_client, monkeypatch):
    client, job_id, source = pdf_client
    original = source.read_bytes()
    images = []
    for number in (1, 2):
        response = client.get(f"/api/jobs/{job_id}/pdf/pages/{number}/thumbnail")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        with Image.open(BytesIO(response.content)) as image:
            assert image.format == "WEBP" and image.width <= 112 and image.height <= 160
            assert image.size == ((112, 149) if number == 1 else (112, 56))
        assert len(response.content) <= 2000
        images.append(response.content)
    assert images[0] != images[1]
    assert not list(document_cache(source_folder(source)).glob("thumbnails/*.png"))
    old_path = next(document_cache(source_folder(source)).glob("thumbnails/page-1-*.webp"))
    real_document = pdf_view.pdfium.PdfDocument
    with monkeypatch.context() as patch:

        def unexpected_render(*args):
            raise AssertionError("Cached thumbnails must not rerender the PDF")

        patch.setattr(pdf_view.pdfium, "PdfDocument", unexpected_render)
        assert client.get(f"/api/jobs/{job_id}/pdf/pages/1/thumbnail").content == images[0]
    assert pdf_view.pdfium.PdfDocument is real_document
    source.touch()
    assert client.get(f"/api/jobs/{job_id}/pdf/pages/1/thumbnail").status_code == 200
    assert not old_path.exists()
    assert len(list(document_cache(source_folder(source)).glob("thumbnails/page-1-*.webp"))) == 1
    for number in (0, 3):
        assert client.get(f"/api/jobs/{job_id}/pdf/pages/{number}/thumbnail").status_code == 404
    assert source.read_bytes() == original


def test_pdf_render_resolution_limits_and_invalid_pages(pdf_client):
    client, job_id, source = pdf_client
    original = source.read_bytes()
    for scale in (1, 2):
        response = client.get(f"/api/jobs/{job_id}/pdf/pages/2?scale={scale}")
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "image/png"
        with Image.open(BytesIO(response.content)) as image:
            assert image.size == (600 * scale, 300 * scale)
    for number in (0, 3):
        assert client.get(f"/api/jobs/{job_id}/pdf/pages/{number}").status_code == 404
    for scale in (0, 7, "nan", "inf"):
        assert client.get(f"/api/jobs/{job_id}/pdf/pages/1?scale={scale}").status_code == 422
    assert client.get(f"/view/{'e' * 32}/pdf").status_code == 404
    assert source.read_bytes() == original


def test_pdf_render_caps_large_pages_and_reports_unreadable_sources(pdf_client):
    client, job_id, source = pdf_client
    pdf = canvas.Canvas(str(source), pagesize=(3000, 5000))
    pdf.drawString(20, 20, "Large page")
    pdf.save()
    response = client.get(f"/api/jobs/{job_id}/pdf/pages/1?scale=6")
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        assert max(image.size) <= 8192
        assert image.width * image.height <= 12_010_000  # PDFium rounds dimensions up.
    source.write_bytes(b"%PDF-broken")
    assert client.get(f"/view/{job_id}/pdf").status_code == 422
    assert client.get(f"/api/jobs/{job_id}/pdf/pages/1").status_code == 422
