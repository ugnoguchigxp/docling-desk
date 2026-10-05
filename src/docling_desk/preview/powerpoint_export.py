"""Export an isolated copy through PowerPoint's native PDF renderer on macOS."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZipFile

import lxml.etree as etree  # ty: ignore[unresolved-import]  # Installed lxml binary extension.
import pymupdf
from pptx import Presentation

from docling_desk.config import RESOURCES as ROOT
from docling_desk.preview.hashing import digest
from docling_desk.preview.office_linux import convert_office

EXPORT_LOCK = threading.Lock()
SCRIPT = ROOT / "scripts/export-powerpoint.applescript"
POWERPOINT = Path("/Applications/Microsoft PowerPoint.app")
WORKSPACE = (
    Path.home() / "Library/Containers/com.microsoft.Powerpoint/Data/Documents/docling-preview"
)


def validate_export(source: Path, pdf: Path) -> None:
    deck = Presentation(str(source))
    width, height = deck.slide_width, deck.slide_height
    if width is None or height is None:
        raise ValueError("PowerPointのページ寸法がありません。")
    with pymupdf.open(pdf) as document:
        if document.needs_pass or len(document) != len(deck.slides):
            raise ValueError("PowerPointから書き出したPDFのページ数が原本と一致しません。")
        if any(
            abs(page.rect.width - width / 12700) > 0.1
            or abs(page.rect.height - height / 12700) > 0.1
            for page in document
        ):
            raise ValueError("PowerPointから書き出したPDFのページ寸法が原本と一致しません。")


def cached_export(source: Path, folder: Path) -> Path | None:
    pdf = folder / "powerpoint-rendered.pdf"
    # Accept a verified earlier native export, including the previous renderer.
    for manifest in (folder / "powerpoint-export.json", folder / "editable-preview/manifest.json"):
        try:
            data = json.loads(manifest.read_text())
            if data["source_sha256"] == digest(source) and data["pdf_sha256"] == digest(pdf):
                validate_export(source, pdf)
                return pdf
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            continue
    return None


def prepare_copy(source: Path, target: Path) -> None:
    # PowerPoint's Save-as-PDF omits hidden slides even with print-hidden enabled.
    # Change only the visibility flags in the disposable copy, retaining every
    # other package entry byte-for-byte, including unsupported Office objects.
    with ZipFile(source) as original, ZipFile(target, "w") as copy:
        for entry in original.infolist():
            content = original.read(entry.filename)
            if entry.filename.startswith("ppt/slides/slide") and entry.filename.endswith(".xml"):
                root = etree.fromstring(content)
                if root.get("show") in {"0", "false"}:
                    root.set("show", "1")
                    content = etree.tostring(
                        root, xml_declaration=True, encoding="UTF-8", standalone=True
                    )
            copy.writestr(entry, content)


def export_powerpoint(source: Path, folder: Path) -> Path:
    with EXPORT_LOCK:
        if cached := cached_export(source, folder):
            return cached
        if sys.platform == "linux":
            original_hash = digest(source)
            target = folder / "powerpoint-rendered.pdf"
            with TemporaryDirectory(prefix="powerpoint-linux-", dir=folder) as work:
                working = Path(work) / "preview.pptx"
                prepare_copy(source, working)
                output = convert_office(
                    working,
                    Path(work),
                    'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}',
                )
                validate_export(source, output)
                if digest(source) != original_hash:
                    raise ValueError("書き出し中に原本が変更されました。")
                output.replace(target)
            temporary = folder / "powerpoint-export.tmp"
            temporary.write_text(
                json.dumps(
                    {
                        "renderer": "LibreOffice Impress",
                        "source_sha256": original_hash,
                        "pdf_sha256": digest(target),
                    },
                    indent=2,
                )
            )
            temporary.replace(folder / "powerpoint-export.json")
            return target
        if sys.platform != "darwin" or not POWERPOINT.is_dir():
            raise RuntimeError(
                "PPTXの高再現プレビューには、このMacにMicrosoft PowerPointが必要です。"
            )
        original_hash = digest(source)
        target = folder / "powerpoint-rendered.pdf"
        # A unique disposable copy prevents reuse or closing of user-opened decks.
        # Work inside PowerPoint's own sandbox so every upload does not require
        # granting PowerPoint access to a new document-store directory.
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="export-", dir=WORKSPACE) as work:
            working = Path(work) / f"preview-{uuid4().hex}.pptx"
            prepare_copy(source, working)
            output = working.with_suffix(".pdf")
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/osascript",
                        str(SCRIPT),
                        str(working.resolve()),
                        str(output.resolve()),
                        working.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=150,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("PowerPointのPDF書き出しが時間内に完了しませんでした。") from exc
            if result.returncode != 0 or not output.is_file():
                detail = getattr(result, "stderr", "").strip()[-600:]
                raise RuntimeError(
                    "PowerPointのPDF書き出しに失敗しました。macOSの自動操作許可とPowerPointの状態を確認してください。"
                    + (f" {detail}" if detail else "")
                )
            validate_export(source, output)
            if digest(source) != original_hash:
                raise ValueError("書き出し中に原本が変更されました。")
            temporary_pdf = target.with_suffix(".tmp")
            try:
                shutil.copyfile(output, temporary_pdf)
                temporary_pdf.replace(target)
            finally:
                temporary_pdf.unlink(missing_ok=True)
        data = {
            "renderer": "Microsoft PowerPoint",
            "source_sha256": original_hash,
            "pdf_sha256": digest(target),
        }
        temporary = folder / "powerpoint-export.tmp"
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(folder / "powerpoint-export.json")
        return target
