from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zipfile import BadZipFile, ZipFile

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.backend_options import MarkdownBackendOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import (
    OcrMacOptions,
    PdfPipelineOptions,
    TesseractCliOcrOptions,
)
from docling.document_converter import DocumentConverter, MarkdownFormatOption, PdfFormatOption
from docling_azure_ocr.config import OcrError, OcrProfile
from docling_azure_ocr.options import AzureReadOcrOptions
from docling_azure_ocr.runtime import OcrRuntime
from docling_core.transforms.serializer.plain_text import PlainTextDocSerializer, PlainTextParams
from docling_core.types.doc import DocItem, ImageRefMode, PictureItem, TableItem, TextItem
from pydantic import BaseModel, Field

from docling_desk.config import MAX_BYTES, MAX_PAGES, MODELS
from docling_desk.documents.ocr_profiles import (
    OCR_META,
    enrich_office_images,
    enrich_pdf_evidence,
    load_profile,
    make_runtime,
)
from docling_desk.documents.pagination import export_paginated_html
from docling_desk.documents.rag import export_rag
from docling_desk.documents.text import (
    MARKDOWN_SUFFIXES,
    SUPPORTED_LABEL,
    SUPPORTED_SUFFIXES,
    TEXT_DOCUMENT_SUFFIXES,
    TEXT_SUFFIXES,
    plain_document,
    read_text_input,
    text_preview,
)
from docling_desk.operations.backup import exclusive_write
from docling_desk.operations.faults import checkpoint
from docling_desk.preview.editable_preview import NOTICE as EDITABLE_NOTICE
from docling_desk.preview.editable_preview import (
    PDF_NOTICE,
    build_editable_preview,
    editable_preview,
    ensure_pdf_preview,
)
from docling_desk.preview.office_linux import office_html
from docling_desk.preview.powerpoint_export import export_powerpoint
from docling_desk.preview.slides import export_slide_layout
from docling_desk.storage import data_root, job_file, original_file, record_artifacts

LOGGER = logging.getLogger(__name__)


class Job(BaseModel):
    id: str
    filename: str
    original_filename: str | None = None
    folder_id: str | None = None
    state: Literal["queued", "running", "success", "partial", "failed"] = "queued"
    created: float = Field(default_factory=time.time)
    duration: float | None = None
    pages: int = 0
    tables: int = 0
    pictures: int = 0
    chunks: int = 0
    search_chunks: int = 0
    rag_policy: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    preview: str | None = None
    slide_layout: bool = False
    synthetic: bool = False
    ocr_profile: dict | None = None
    error_code: str | None = None


class Element(BaseModel):
    ref: str
    label: str
    text: str
    pages: list[int]
    provenance: list[dict[str, object]]
    parent: str | None
    captions: list[str]


def save_job(folder: Path, job: Job) -> None:
    record_artifacts(folder, job.model_dump())
    target = job_file(folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(job.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(job_file(folder))


def validate_input(path: Path) -> None:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"{SUPPORTED_LABEL}のみ対応しています。")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("上限50 MiBを超えています。")
    if path.suffix.lower() in TEXT_DOCUMENT_SUFFIXES:
        read_text_input(path)
        return
    if path.suffix.lower() == ".pdf":
        with path.open("rb") as stream:
            header = stream.read(1024)
        if not header.lstrip().startswith(b"%PDF-"):
            raise ValueError("PDFヘッダーが見つかりません。")
        return
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(x.file_size for x in entries) > 250 * 1024**2:
                raise ValueError("Office展開サイズの上限250 MiBまたは10000エントリを超えています。")
            names = archive.namelist()
            expected = {
                ".pptx": "ppt/presentation.xml",
                ".xlsx": "xl/workbook.xml",
                ".docx": "word/document.xml",
            }[path.suffix.lower()]
            if expected not in names:
                raise ValueError("拡張子に対応するOffice構造が見つかりません。")
    except BadZipFile as exc:
        raise ValueError("Office ZIP形式として読み込めません。") from exc


@lru_cache(maxsize=1)
def converter() -> DocumentConverter:
    options = PdfPipelineOptions(
        artifacts_path=MODELS,
        enable_remote_services=False,
        do_ocr=True,
        ocr_options=(
            OcrMacOptions(lang=["ja-JP", "en-US"])
            if sys.platform == "darwin"
            else TesseractCliOcrOptions(lang=["jpn", "eng"])
        ),
        do_table_structure=True,
        generate_page_images=True,
        generate_picture_images=True,
        images_scale=1.5,
        document_timeout=300,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=4),
    )

    return DocumentConverter(
        allowed_formats=[
            InputFormat.PDF,
            InputFormat.PPTX,
            InputFormat.XLSX,
            InputFormat.DOCX,
            InputFormat.MD,
        ],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.MD: MarkdownFormatOption(
                backend_options=MarkdownBackendOptions(
                    fetch_images=False, enable_local_fetch=False, enable_remote_fetch=False
                )
            ),
        },
    )


def job_converter(profile: OcrProfile, runtime: OcrRuntime) -> DocumentConverter:
    if profile.provider == "local":
        return converter()
    from docling.datamodel.pipeline_options import OcrMode

    options = PdfPipelineOptions(
        artifacts_path=MODELS,
        enable_remote_services=profile.provider == "azure_read",
        allow_external_plugins=profile.provider == "azure_read",
        do_ocr=profile.provider != "disabled",
        ocr_options=AzureReadOcrOptions(
            runtime=runtime,
            mode=OcrMode.FULL_PAGE if profile.profile == "read-full-page-v1" else OcrMode.DEFAULT,
        )
        if profile.provider == "azure_read"
        else TesseractCliOcrOptions(lang=["jpn", "eng"]),
        do_table_structure=True,
        generate_page_images=True,
        generate_picture_images=True,
        images_scale=1.5,
        document_timeout=profile.document_timeout,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=4),
    )
    return DocumentConverter(
        allowed_formats=[
            InputFormat.PDF,
            InputFormat.PPTX,
            InputFormat.XLSX,
            InputFormat.DOCX,
            InputFormat.MD,
        ],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.MD: MarkdownFormatOption(
                backend_options=MarkdownBackendOptions(
                    fetch_images=False, enable_local_fetch=False, enable_remote_fetch=False
                )
            ),
        },
    )


def make_sheet_preview(preview: Path) -> None:
    """Replace Quick Look's script-driven tabs with static local sheet frames."""
    raw = preview.read_text(encoding="utf-8")
    sheets = re.findall(r'<div class="TabHeader">(.*?)</div><a href="([^"]+)">', raw, re.S)
    if not sheets:
        return  # Single-sheet Quick Look previews are already static.
    sections = []
    for label, href in sheets:
        if Path(href).name != href or not href.endswith(".html"):
            continue
        sections.append(
            f'<section><h2>{html.escape(html.unescape(label))}</h2><a href="{html.escape(href)}">このシートを大きく開く</a><iframe src="{html.escape(href)}" title="{html.escape(label)}" sandbox></iframe></section>'
        )
    preview.write_text(
        '<!doctype html><html lang="ja"><meta charset="utf-8"><style>body{font-family:sans-serif;margin:12px}h2{font-size:16px}a{font-size:12px}iframe{display:block;width:100%;height:700px;border:1px solid #ddd;margin:12px 0 24px}</style>'
        + "".join(sections)
        + "</html>",
        encoding="utf-8",
    )


def office_preview(source: Path, folder: Path) -> tuple[str | None, str]:
    if source.suffix.lower() == ".pptx":
        try:
            preview = editable_preview(source, folder)
            if not preview:
                pdf = export_powerpoint(source, folder)
                preview = build_editable_preview(source, pdf, folder)
            notice = EDITABLE_NOTICE
            try:
                renderer_name = json.loads((folder / "powerpoint-export.json").read_text()).get(
                    "renderer"
                )
            except (OSError, ValueError, AttributeError):
                renderer_name = None
            if renderer_name == "LibreOffice Impress":
                notice = (
                    "LibreOfficeの描画から生成したHTML/SVGです。文字はテキスト、図形はベクターとして保持します。"
                    "Microsoft Officeとはフォント・配置が異なることがあります。"
                )
            return preview, notice
        except (OSError, RuntimeError, ValueError) as exc:
            LOGGER.exception("High-fidelity preview failed for %s", source)
            return None, f"高再現HTMLプレビューを作成できませんでした。{exc}"
    if sys.platform != "darwin":
        try:
            return office_html(source, folder), (
                "LibreOfficeのHTML表示です。Microsoft Officeとはフォント・配置・図形が異なることがあります。"
            )
        except (OSError, RuntimeError, ValueError) as exc:
            LOGGER.exception("LibreOffice preview failed for %s", source)
            return None, f"LibreOfficeの原本プレビューを生成できませんでした。{exc}"
    preview_dir = folder / "quicklook"
    preview_dir.mkdir(exist_ok=True)
    try:
        result = subprocess.run(
            ["/usr/bin/qlmanage", "-p", "-o", str(preview_dir), str(source)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (
            None,
            "Mac Quick Lookの原本プレビューを生成できませんでした。原本をダウンロードしてOfficeで比較してください。",
        )
    preview = preview_dir / (source.name + ".qlpreview") / "Preview.html"
    if result.returncode != 0 or not preview.is_file():
        return (
            None,
            "Mac Quick Lookの原本プレビューを生成できませんでした。原本をダウンロードしてOfficeで比較してください。",
        )
    if source.suffix == ".xlsx":
        make_sheet_preview(preview)
    return (
        preview.relative_to(folder).as_posix(),
        "原本側はMac Quick LookのHTML表示です。Officeでの表示とフォント・図形・グラフ・シート範囲が異なることがあります。",
    )


def convert_job(folder: Path, job: Job, *, ocr_runtime: OcrRuntime | None = None) -> None:
    with exclusive_write(data_root(folder), "convert"):
        _convert_job(folder, job, ocr_runtime=ocr_runtime)


def _convert_job(folder: Path, job: Job, *, ocr_runtime: OcrRuntime | None = None) -> None:
    started = time.monotonic()
    job.state = "running"
    save_job(folder, job)
    source = original_file(folder, Path(job.filename).suffix.lower())
    runtime = ocr_runtime
    try:
        validate_input(source)
        profile = OcrProfile.model_validate(job.ocr_profile) if job.ocr_profile else load_profile()
        job.ocr_profile = profile.model_dump(mode="json")
        if runtime is None:
            runtime = make_runtime(
                profile, folder, hashlib.sha256(source.read_bytes()).hexdigest(), job.id
            )
        source_text = read_text_input(source) if source.suffix in TEXT_DOCUMENT_SUFFIXES else ""
        if source.suffix in TEXT_SUFFIXES:
            doc = plain_document(source, source_text)
            job.state = "success"
        else:
            result = (
                converter().convert_string(source_text, format=InputFormat.MD, name=source.name)
                if source.suffix in MARKDOWN_SUFFIXES
                else job_converter(profile, runtime).convert(
                    source, raises_on_error=False, max_num_pages=MAX_PAGES, max_file_size=MAX_BYTES
                )
            )
            if runtime.error:
                raise runtime.error
            if result.status not in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
                errors = "; ".join(error.error_message for error in result.errors)
                raise ValueError(f"Docling: {result.status.value}: {errors}")
            doc = result.document
            job.state = (
                "partial" if result.status == ConversionStatus.PARTIAL_SUCCESS else "success"
            )
            if result.errors:
                job.warnings.extend(error.error_message for error in result.errors)
        if source.suffix == ".pdf" and profile.provider == "azure_read":
            enrich_pdf_evidence(doc, runtime)
        if source.suffix in {".pptx", ".docx", ".xlsx"} and profile.provider != "disabled":
            job.warnings.extend(enrich_office_images(doc, runtime))
        job.pages, job.tables, job.pictures = len(doc.pages), len(doc.tables), len(doc.pictures)
        doc.save_as_json(folder / "document.json", image_mode=ImageRefMode.EMBEDDED)
        checkpoint("convert_after_document")
        export_paginated_html(doc, job.filename, folder / "extracted.html")
        (folder / "text.txt").write_text(
            source_text
            if source.suffix in TEXT_SUFFIXES
            else PlainTextDocSerializer(
                doc=doc, params=PlainTextParams(blocked_meta_names={OCR_META})
            )
            .serialize()
            .text,
            encoding="utf-8",
        )
        (folder / "document.md").write_text(
            doc.export_to_markdown(
                image_mode=ImageRefMode.PLACEHOLDER, blocked_meta_names={OCR_META}
            ),
            encoding="utf-8",
        )
        elements: list[Element] = []
        for item, _ in doc.iterate_items():
            if not isinstance(item, DocItem):
                continue
            text = item.export_to_markdown(doc=doc) if isinstance(item, TableItem) else ""
            if not isinstance(item, (TableItem, PictureItem)):
                if isinstance(item, TextItem):
                    text = item.text
            captions = (
                [caption.cref for caption in item.captions]
                if isinstance(item, (TableItem, PictureItem))
                else []
            )
            elements.append(
                Element(
                    ref=item.self_ref,
                    label=item.label.value,
                    text=text,
                    pages=sorted({p.page_no for p in item.prov}),
                    provenance=[p.model_dump(mode="json") for p in item.prov],
                    parent=item.parent.cref if item.parent else None,
                    captions=captions,
                )
            )
        (folder / "elements.json").write_text(
            json.dumps([x.model_dump() for x in elements], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        policy = export_rag(doc, job.filename, job.id, source, folder)
        checkpoint("convert_after_rag")
        job.chunks, job.search_chunks = policy.context_chunks, policy.search_chunks
        job.rag_policy = policy.version
        if source.suffix in TEXT_DOCUMENT_SUFFIXES:
            job.preview = text_preview(doc, source, source_text, folder)
            if source.suffix in MARKDOWN_SUFFIXES:
                job.warnings.append(
                    "Markdownを整形して表示します。リンク先や外部画像は読み込みません。"
                )
        elif source.suffix == ".pdf":
            job.preview = ensure_pdf_preview(source, folder)
            job.warnings.append(PDF_NOTICE)
        else:
            job.preview, warning = office_preview(source, folder)
            job.warnings.append(warning)
            if not job.preview:
                job.state = "partial"
            if source.suffix == ".pptx":
                export_slide_layout(doc, source, folder, job.preview)
                job.slide_layout = True
        job.warnings.append(
            "抽出HTMLは読み順・表・図を再構成した表示です。原本レイアウトや図と本文の意味関係の完全な保持は保証しません。"
        )
        if source.suffix == ".xlsx":
            job.warnings.append(
                "XLSXの数式再計算は行いません。保存済み値・表領域・画像の抽出で、セル装飾や全グラフの意味抽出は保証しません。"
            )
    except Exception as exc:  # One error boundary: preserve failed job status for the UI.
        if isinstance(exc, OcrError):
            LOGGER.error("OCR failed for %s: %s", job.id, exc.code)
            job.error_code = exc.code
        else:
            LOGGER.exception("Conversion failed for %s", job.id)
        job.state, job.error = "failed", str(exc)
    finally:
        if runtime and runtime.transport:
            runtime.transport.close()
    job.duration = round(time.monotonic() - started, 2)
    checkpoint("convert_before_job_success")
    save_job(folder, job)
