"""Synthetic PDFs for tests, written with reportlab (no PyMuPDF)."""

from __future__ import annotations

from collections.abc import Sequence
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas


def pdf_bytes(
    texts: Sequence[str] = (),
    *,
    pages: int | None = None,
    size: tuple[float, float] = A4,
    at: tuple[float, float] = (72, 72),
) -> bytes:
    """One page per text, or ``pages`` blank pages; text sits ``at`` from the top-left corner."""
    stream = BytesIO()
    document = canvas.Canvas(stream, pagesize=size)
    for number in range(len(texts) if pages is None else pages):
        if number < len(texts):
            document.drawString(at[0], size[1] - at[1], texts[number])
        document.showPage()
    document.save()
    return stream.getvalue()


def encrypted_pdf_bytes(user: str = "user", owner: str = "owner") -> bytes:
    """A one-page PDF that needs a password to open."""
    stream = BytesIO()
    document = canvas.Canvas(stream, encrypt=StandardEncryption(user, owner))
    document.drawString(72, 72, "secret")
    document.save()
    return stream.getvalue()
