import pypdfium2 as pdfium
import pytest
from pdf_samples import encrypted_pdf_bytes, pdf_bytes
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas

from docling_desk.preview.pdfium_access import check_pdf_limits, inspect_pdf


def test_limits_check_counts_pages_without_loading_any(tmp_path, monkeypatch):
    path = tmp_path / "many.pdf"
    path.write_bytes(pdf_bytes(pages=12))

    def no_page(self, index):
        raise AssertionError("a page was loaded")

    monkeypatch.setattr(pdfium.PdfDocument, "__getitem__", no_page)
    assert check_pdf_limits(path, 12) == 12
    with pytest.raises(ValueError, match="上限"):
        check_pdf_limits(path, 11)


def test_a_pdf_that_needs_a_user_password_is_refused(tmp_path):
    path = tmp_path / "locked.pdf"
    path.write_bytes(encrypted_pdf_bytes())
    with pytest.raises(ValueError, match="暗号化"):
        check_pdf_limits(path, 10)
    with pytest.raises(ValueError, match="暗号化"):
        inspect_pdf(path)


def test_owner_password_only_protection_is_readable_and_accepted(tmp_path):
    # Opens with the empty user password, exactly as it did under needs_pass.
    path = tmp_path / "owner.pdf"
    page = canvas.Canvas(str(path), encrypt=StandardEncryption("", "owner"))
    page.drawString(72, 72, "readable")
    page.save()
    assert check_pdf_limits(path, 10) == 1
    assert inspect_pdf(path).pages == 1
