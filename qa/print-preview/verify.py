"""Check actual browser PDFs, including pagination, text and paper dimensions."""
import json
from pathlib import Path

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parent


class Page:
    def __init__(self, page):
        self.page = page
        self.width, self.height = page.get_size()

    def get_text(self):
        return self.page.get_textpage().get_text_bounded()

    def save_render(self, path):
        self.page.render().to_pil().save(path)


class Pdf:
    def __init__(self, path):
        self.document = pdfium.PdfDocument(path)
        self.pages = [Page(self.document[i]) for i in range(len(self.document))]

    def __enter__(self):
        return self.pages

    def __exit__(self, *_):
        self.document.close()

EXPECTED = {"pdf": 2, "slides": 2, "sheets": 2, "real-slides": 34, "long-text": 5}
results = {}
for name, count in EXPECTED.items():
    with Pdf(ROOT / f"{name}.pdf") as document:
        assert len(document) == count, (name, len(document), count)
        texts = [page.get_text() for page in document]
        assert all(text.strip() for text in texts), f"blank page: {name}"
        text = "".join(texts)
        assert "印刷・PDF保存" not in text
        if name == "long-text":
            assert "PRINT_FINAL_LINE" in texts[-1]
            assert all(f"印刷の確認 {n}:" in text for n in range(1, 181))
        sizes = [[round(p.width, 2), round(p.height, 2)] for p in document]
        if name in {"pdf", "sheets", "long-text"}:
            assert all(abs(w - 595.28) < 1.5 and abs(h - 841.89) < 1.5 for w, h in sizes)
        for number in ([0, 14] if name == "real-slides" else [0]):
            document[number].save_render(ROOT / f"{name}-{number + 1}.render.png")
        results[name] = {"pages": len(document), "sizes_pt": sizes, "text_characters": len(text)}
    if name != "long-text":
        with Pdf(ROOT / f"{name}-a4.pdf") as document:
            assert len(document) == 1
            assert document[0].get_text().strip()
            assert abs(document[0].width - 841.89) < 1.5
            assert abs(document[0].height - 595.28) < 1.5

for name in ["word", "markdown"]:
    with Pdf(ROOT / f"{name}.pdf") as document:
        assert len(document) == 1, (name, len(document))
        text = "".join(p.get_text() for p in document)
        assert text.strip() and "120" in text
        assert "印刷・PDF保存" not in text
        document[0].save_render(ROOT / f"{name}-1.render.png")
        results[name] = {"pages": len(document), "text_characters": len(text)}

for name in ["real-sheets", "without-preview", "native-slides"]:
    with Pdf(ROOT / f"{name}.pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert text.strip()
        assert "印刷・PDF保存" not in text
        if name == "real-sheets":
            assert "Requirement" in text and "Closed" in document[-1].get_text()
            assert len(text) > 25000
        if name == "native-slides":
            assert len(document) == 2
            assert all(page.get_text().strip() for page in document)
        if name != "real-sheets":
            assert "120" in text
        results[name] = {"pages": len(document), "text_characters": len(text)}

report = {"passed": True, "pdf_checks": results,
          "method": "Same printable DOM and shadow roots, exported through Chromium print-to-PDF; print-button invocation stubbed.",
          "native_browser": {"browser": "Brave", "print_dialog_verified": ["pdf", "docx", "xlsx", "pptx"], "saved_pdf": "native-slides.pdf"},
          "unverified": ["physical printers", "Safari print dialog"]}
(ROOT / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
