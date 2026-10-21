"""Open PDFs through PDFium under Docling's shared lock.

PDFium is not safe for concurrent use. Every caller that touches a document
holds ``docling.utils.locks.pypdfium2_lock``, the same lock Docling's own PDF
backend takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from docling.utils.locks import pypdfium2_lock

from docling_desk.config import MAX_PAGES

ENCRYPTED_MESSAGE = "暗号化PDF、空のPDF、またはページ数の上限を超えるPDFです。"


@dataclass(frozen=True)
class PdfInfo:
    sizes: tuple[tuple[float, float], ...]

    @property
    def pages(self) -> int:
        return len(self.sizes)


def open_pdf(path: Path) -> pdfium.PdfDocument:
    """Open a PDF; a document that needs a password is refused as a ValueError."""
    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        if exc.err_code == pdfium_raw.FPDF_ERR_PASSWORD:
            raise ValueError(ENCRYPTED_MESSAGE) from exc
        raise


def check_pdf_limits(path: Path, max_pages: int) -> int:
    """Page count of a PDF that opens without a password and has at most ``max_pages`` pages.

    Reads no page, so a large document costs only its cross-reference table. A PDF
    that opens with an empty password (owner-password-only protection) is accepted,
    as it can be read; only one that asks for a user password is refused.
    """
    with pypdfium2_lock:
        document = open_pdf(path)
        try:
            count = len(document)
        finally:
            document.close()
    if count > max_pages:
        raise ValueError(ENCRYPTED_MESSAGE)
    return count


def inspect_pdf(path: Path, *, max_pages: int | None = None) -> PdfInfo:
    """Page sizes (points, after rotation); refuses password-protected PDFs.

    ``max_pages`` is checked before any page is loaded.
    """
    with pypdfium2_lock:
        document = open_pdf(path)
        try:
            if max_pages is not None and len(document) > max_pages:
                raise ValueError(ENCRYPTED_MESSAGE)
            sizes = []
            for index in range(len(document)):
                page = document[index]
                try:
                    sizes.append(page.get_size())
                finally:
                    page.close()
            return PdfInfo(tuple(sizes))
        finally:
            document.close()


def checked_pdf(path: Path) -> PdfInfo:
    """Sizes of a non-empty PDF within ``MAX_PAGES`` that needs no password."""
    info = inspect_pdf(path, max_pages=MAX_PAGES)
    if not info.pages:
        raise ValueError(ENCRYPTED_MESSAGE)
    return info
