"""Office extraction in a child process, so a document that never finishes can be stopped.

Docling bounds PDF work with ``document_timeout``; its Office backends have no such
limit and run in the single conversion worker. A thread cannot be stopped, so PPTX,
XLSX and DOCX are converted in a spawned process that is killed at the deadline.
The child uses the same default Office pipeline the in-process converter used.
"""

from __future__ import annotations

import json
import multiprocessing
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling_core.types.doc import DoclingDocument, ImageRefMode

OFFICE_SUFFIXES = {".pptx", ".xlsx", ".docx"}
DEFAULT_TIMEOUT = 300


def office_timeout() -> int:
    value = int(os.environ.get("DOCLING_OFFICE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
    if value < 1:
        raise ValueError("DOCLING_OFFICE_TIMEOUT_SECONDS は1以上にしてください。")
    return value


@dataclass
class OfficeError:
    error_message: str


@dataclass
class OfficeResult:
    """The parts of a Docling ConversionResult that conversion reads."""

    status: ConversionStatus
    document: DoclingDocument
    errors: list[OfficeError] = field(default_factory=list)


def _convert(source: str, document: str, report: str, max_pages: int, max_bytes: int) -> None:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter(
        allowed_formats=[InputFormat.PPTX, InputFormat.XLSX, InputFormat.DOCX]
    )
    result = converter.convert(
        Path(source), raises_on_error=False, max_num_pages=max_pages, max_file_size=max_bytes
    )
    if result.status in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
        result.document.save_as_json(Path(document), image_mode=ImageRefMode.EMBEDDED)
    Path(report).write_text(
        json.dumps(
            {
                "status": result.status.value,
                "errors": [error.error_message for error in result.errors],
            }
        ),
        encoding="utf-8",
    )


def run_isolated(target: Callable[..., object], args: tuple, timeout: float) -> int | None:
    """Run ``target(*args)`` in a spawned process; None when it was killed at the deadline."""
    # spawn, never fork: the server holds threads and locks a forked child would inherit.
    process = multiprocessing.get_context("spawn").Process(target=target, args=args, daemon=True)
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.kill()
        process.join(10)
        return None
    return process.exitcode


def convert_office(source: Path, max_pages: int, max_bytes: int) -> OfficeResult:
    timeout = office_timeout()
    with TemporaryDirectory(prefix="docling-office-convert-") as work:
        document = Path(work) / "document.json"
        report = Path(work) / "report.json"
        code = run_isolated(
            _convert, (str(source), str(document), str(report), max_pages, max_bytes), timeout
        )
        if code is None:
            raise ValueError(f"Office文書の抽出が{timeout}秒以内に完了しませんでした。")
        if code != 0 or not report.is_file():
            raise ValueError("Office文書の抽出処理が異常終了しました。")
        outcome = json.loads(report.read_text(encoding="utf-8"))
        status = ConversionStatus(outcome["status"])
        errors = [OfficeError(message) for message in outcome["errors"]]
        if status not in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
            return OfficeResult(status, DoclingDocument(name=source.stem), errors)
        return OfficeResult(status, DoclingDocument.load_from_json(document), errors)
