from __future__ import annotations

import subprocess
from pathlib import Path

from docling_core.types.doc import DoclingDocument
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response

from docling_desk import config
from docling_desk.api.dependencies import job_detail, job_folder, json_job
from docling_desk.api.translation import translated_html, translation_folder
from docling_desk.documents.conversion import Job
from docling_desk.documents.diagrams import read_diagram
from docling_desk.documents.tables import TableView, project_tables
from docling_desk.documents.text import TEX_SUFFIXES, TEXT_DOCUMENT_SUFFIXES
from docling_desk.preview.diagrams import diagram_html
from docling_desk.preview.document_frames import interactive_html
from docling_desk.preview.editable_preview import ensure_pdf_preview
from docling_desk.preview.html_view import viewer_html
from docling_desk.preview.pdf_view import pdf_html, pdf_pages, pdf_thumbnail, render_pdf_page
from docling_desk.preview.sheets import preview_sheets, sheet_html, workbook_html
from docling_desk.preview.thumbnails import thumbnail
from docling_desk.storage import job_file, original_file, safe_path
from docling_desk.translation.source import slide_template, template_for

router = APIRouter()


@router.get("/view/{job_id}/diagram", response_class=HTMLResponse)
def diagram_view(job_id: str, unit: int = Query(default=1, ge=1)) -> HTMLResponse:
    folder = job_folder(job_id)
    job = job_detail(job_id)
    if not job.diagram:
        raise HTTPException(404)
    source = original_file(folder, Path(job.original_filename or job.filename).suffix.lower())
    if not safe_path(config.DATA, source) or not source.is_file():
        raise HTTPException(404)
    try:
        diagram = read_diagram(source)
        if unit > len(diagram.names):
            raise HTTPException(404)
        return HTMLResponse(
            diagram_html(job.id, job.filename, diagram, unit),
            headers={"Cache-Control": "private, no-store"},
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(422, "図を読み込めませんでした。原本を確認してください。") from exc


@router.get("/api/jobs/{job_id}/slides/{number}/thumbnail")
def slide_thumbnail(job_id: str, number: int) -> FileResponse:
    folder = job_folder(job_id)
    job = job_detail(job_id)
    if not job.filename.lower().endswith(".pptx") or job.state not in {"success", "partial"}:
        raise HTTPException(404)
    try:
        target = thumbnail(folder, number)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(404, "スライドのプレビューが見つかりません。") from exc
    except (RuntimeError, subprocess.SubprocessError) as exc:
        raise HTTPException(503, "サムネイルを生成できませんでした。") from exc
    return FileResponse(
        target, media_type="image/webp", headers={"Cache-Control": "private, no-cache"}
    )


def pdf_source(job_id: str) -> tuple[Job, Path]:
    folder = job_folder(job_id)
    job = job_detail(job_id)
    source = original_file(folder, ".pdf")
    suffix = Path(job.original_filename or job.filename).suffix.lower()
    if suffix in TEX_SUFFIXES and job.tex_preview == "tex-preview.pdf":
        source = folder / "tex-preview.pdf"
    elif suffix != ".pdf":
        raise HTTPException(404)
    if not job.preview or not source.is_file():
        raise HTTPException(404)
    return job, source


@router.get("/view/{job_id}/pdf", response_class=HTMLResponse)
def pdf_view(job_id: str) -> HTMLResponse:
    job, source = pdf_source(job_id)
    try:
        ensure_pdf_preview(source, job_folder(job_id))
        return HTMLResponse(pdf_html(job_id, job.filename, pdf_pages(source)))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, "PDFプレビューを読み込めませんでした。") from exc


@router.get("/view/{job_id}/pages/{number}", response_class=HTMLResponse)
def pdf_page_html(job_id: str, number: int, language: str = "original") -> HTMLResponse:
    job, source = pdf_source(job_id)
    try:
        ensure_pdf_preview(source, job_folder(job_id))
        if not 1 <= number <= len(pdf_pages(source)):
            raise HTTPException(404)
        if Path(job.original_filename or job.filename).suffix.lower() in TEX_SUFFIXES:
            raw = slide_template(job_folder(job_id), f"editable-preview/page-{number}.html")
            return HTMLResponse(interactive_html(raw, job_id, f"page-{number}"))
        raw = template_for(job_folder(job_id), job.model_dump(), number)
        return translated_html(job_id, f"page-{number}", raw, language)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, "ページのHTMLを生成できませんでした。") from exc


@router.get("/api/jobs/{job_id}/pdf/pages/{number}")
def pdf_page_image(
    job_id: str, number: int, scale: float = Query(default=1.5, ge=0.25, le=6)
) -> Response:
    _, source = pdf_source(job_id)
    try:
        image = render_pdf_page(source, number, scale)
    except IndexError as exc:
        raise HTTPException(404) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, "ページを描画できませんでした。") from exc
    return Response(
        image, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/api/jobs/{job_id}/pdf/pages/{number}/thumbnail")
def pdf_page_thumbnail(job_id: str, number: int) -> FileResponse:
    _, source = pdf_source(job_id)
    try:
        target = pdf_thumbnail(source, number)
    except IndexError as exc:
        raise HTTPException(404) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, "サムネイルを生成できませんでした。") from exc
    return FileResponse(
        target, media_type="image/webp", headers={"Cache-Control": "private, no-cache"}
    )


@router.get("/view/{job_id}/extracted", response_class=HTMLResponse)
def extracted_view(job_id: str) -> HTMLResponse:
    folder = job_folder(job_id)
    job = job_detail(job_id)
    if job.state not in {"success", "partial"} or not (folder / "document.json").is_file():
        raise HTTPException(409, "抽出完了後にHTMLを閲覧できます。")
    doc = DoclingDocument.load_from_json(folder / "document.json")
    return HTMLResponse(viewer_html(doc, job.filename, job.id))


def source_sheets(job_id: str) -> tuple[Job, list[dict]]:
    folder = job_folder(job_id)
    job = job_detail(job_id)
    if not job.filename.lower().endswith(".xlsx") or not job.preview:
        raise HTTPException(404)
    try:
        return job, preview_sheets(folder, job.preview)
    except (OSError, ValueError) as exc:
        raise HTTPException(404, "シートのプレビューが見つかりません。") from exc


@router.get("/view/{job_id}/workbook", response_class=HTMLResponse)
def workbook_view(job_id: str) -> HTMLResponse:
    job, sheets = source_sheets(job_id)
    return HTMLResponse(workbook_html(job_id, job.filename, sheets))


@router.get("/view/{job_id}/sheets/{number}", response_class=HTMLResponse)
def worksheet_view(job_id: str, number: int, language: str = "original") -> HTMLResponse:
    _, sheets = source_sheets(job_id)
    sheet = next((sheet for sheet in sheets if sheet["number"] == number), None)
    if sheet is None:
        raise HTTPException(404)
    raw = sheet_html(job_folder(job_id), job_id, sheet)
    return translated_html(job_id, f"sheet-{number}", raw, language)


@router.get("/view/{job_id}/word", response_class=HTMLResponse)
def word_view(job_id: str, language: str = "original") -> HTMLResponse:
    folder = job_folder(job_id)
    job = job_detail(job_id)
    if not job.filename.lower().endswith(".docx") or not job.preview:
        raise HTTPException(404)
    try:
        raw = template_for(folder, job.model_dump(), 1)
        return translated_html(job_id, "document-1", raw, language)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(422, "Wordプレビューを読み込めませんでした。") from exc


@router.get("/view/{job_id}/document", response_class=HTMLResponse)
def text_document_view(job_id: str, language: str = "original") -> HTMLResponse:
    folder = job_folder(job_id)
    job = job_detail(job_id)
    suffix = Path(job.original_filename or job.filename).suffix.lower()
    if suffix not in TEXT_DOCUMENT_SUFFIXES or not job.preview:
        raise HTTPException(404)
    try:
        raw = template_for(folder, job.model_dump(), 1)
        return translated_html(job_id, "document-1", raw, language)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(422, "文書プレビューを読み込めませんでした。") from exc


@router.get("/view/{job_id}/source", response_class=HTMLResponse)
def source_view(job_id: str, language: str = "original") -> HTMLResponse:
    response = text_document_view(job_id, language)
    # Add controls only after the common document sanitizer. Source is escaped
    # text and is never executed, including TeX commands and embedded HTML.
    raw = response.body.decode("utf-8")
    raw = raw.replace(
        "</head>",
        '<link rel="stylesheet" href="/static/source-viewer.css">'
        '<script src="/static/source-viewer.js" defer></script></head>',
        1,
    )
    return HTMLResponse(
        raw,
        headers={
            "Cache-Control": "no-store",
            "X-Translation-State": response.headers["X-Translation-State"],
        },
    )


@router.get("/view/{job_id}/slides/{number}", response_class=HTMLResponse)
def translated_slide(job_id: str, number: int, language: str = "original") -> HTMLResponse:
    folder = translation_folder(job_id)
    job = json_job(folder)
    if not (job.get("original_filename") or job["filename"]).lower().endswith(".pptx"):
        raise HTTPException(404)
    try:
        raw = template_for(folder, job, number)
        return translated_html(job_id, f"slide-{number}", raw, language)
    except ValueError as exc:
        # e.g. an old preview with no saved PDF to rebuild it from: say why, not "not found".
        raise HTTPException(404, str(exc)) from exc
    except (OSError, KeyError, StopIteration) as exc:
        raise HTTPException(404, "スライドが見つかりません。") from exc


@router.get("/api/jobs/{job_id}/tables", response_model=list[TableView])
def job_tables(job_id: str) -> list[TableView]:
    folder = job_folder(job_id)
    job = Job.model_validate_json((job_file(folder)).read_text())
    if job.state not in {"success", "partial"} or not (folder / "document.json").is_file():
        raise HTTPException(409, "抽出完了後に表を操作できます。")
    return project_tables(DoclingDocument.load_from_json(folder / "document.json"), job.filename)
