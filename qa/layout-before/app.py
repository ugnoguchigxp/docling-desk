from __future__ import annotations

import shutil
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from docling_core.types.doc import DoclingDocument
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from docling_desk.preview.html_view import viewer_html
from docling_desk.preview.pdf_view import pdf_html, pdf_pages, render_pdf_page
from docling_desk.documents.conversion import Job, convert_job, save_job, validate_input
from docling_desk.config import DATA, MAX_BYTES, MIN_FREE, ROOT
from docling_desk.preview.sheets import preview_sheets, sheet_html, workbook_html
from docling_desk.documents.tables import TableView, project_tables, table_csv

WORKER = ThreadPoolExecutor(max_workers=1)
SLOTS = threading.BoundedSemaphore(3)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    DATA.mkdir(exist_ok=True)
    for path in DATA.glob("*/job.json"):
        job = Job.model_validate_json(path.read_text())
        if job.state in {"queued", "running"}:
            job.state, job.error = (
                "failed",
                "前回のサーバー停止で処理が中断されました。再アップロードしてください。",
            )
            save_job(path.parent, job)
    yield
    WORKER.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Docling local comparison", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


@app.middleware("http")
async def local_only(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method == "POST":
        size = request.headers.get("content-length", "0")
        if size.isdigit() and int(size) > MAX_BYTES + 1024 * 1024:
            return JSONResponse({"detail": "上限50 MiBを超えています。"}, status_code=413)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != "http" or parsed.netloc != request.headers.get("host"):
                return JSONResponse(
                    {"detail": "他サイトからのアップロードは受け付けません。"}, status_code=403
                )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/view/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src data:; font-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'none'; sandbox allow-scripts allow-downloads"
        )
        if request.url.path.endswith("/workbook"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self'; frame-src 'self'; "
                "object-src 'none'; base-uri 'none'; sandbox allow-scripts"
            )
        if "/sheets/" in request.url.path:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; object-src 'none'; base-uri 'none'; sandbox allow-scripts"
            )
        if request.url.path.endswith("/pdf"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; img-src 'self'; "
                "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
                "form-action 'none'; sandbox allow-scripts"
            )
    elif request.url.path.startswith("/files/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; sandbox"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
        )
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static/index.html")


@app.get("/static/{name}")
def static(name: str) -> FileResponse:
    if name not in {
        "app.js",
        "style.css",
        "tables.js",
        "slides.js",
        "rag.js",
        "table-common.js",
        "table-ui.css",
        "inline-tables.js",
        "extracted-view.css",
        "sheets.js",
        "sheets.css",
        "sheet-frame.js",
        "pdf-viewer.js",
        "pdf-viewer.css",
    }:
        raise HTTPException(404)
    return FileResponse(ROOT / "src/docling_desk/resources/static" / name)


@app.get("/static/vendor/{name}")
def vendor(name: str) -> FileResponse:
    if name not in {"tabulator.min.js", "tabulator.min.css"}:
        raise HTTPException(404)
    return FileResponse(ROOT / "static/vendor" / name)


@app.get("/api/jobs", response_model=list[Job])
def jobs() -> list[Job]:
    return sorted(
        (Job.model_validate_json(p.read_text()) for p in DATA.glob("*/job.json")),
        key=lambda j: j.created,
        reverse=True,
    )


@app.get("/api/jobs/{job_id}", response_model=Job)
def job_detail(job_id: str) -> Job:
    return Job.model_validate_json(job_folder(job_id).joinpath("job.json").read_text())


def pdf_source(job_id: str) -> tuple[Job, Path]:
    folder = job_folder(job_id)
    job = job_detail(job_id)
    source = folder / "original.pdf"
    if not job.filename.lower().endswith(".pdf") or not job.preview or not source.is_file():
        raise HTTPException(404)
    return job, source


@app.get("/view/{job_id}/pdf", response_class=HTMLResponse)
def pdf_view(job_id: str) -> HTMLResponse:
    job, source = pdf_source(job_id)
    try:
        return HTMLResponse(pdf_html(job_id, job.filename, pdf_pages(source)))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, "PDFプレビューを読み込めませんでした。") from exc


@app.get("/api/jobs/{job_id}/pdf/pages/{number}")
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


@app.get("/view/{job_id}/extracted", response_class=HTMLResponse)
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


@app.get("/view/{job_id}/workbook", response_class=HTMLResponse)
def workbook_view(job_id: str) -> HTMLResponse:
    job, sheets = source_sheets(job_id)
    return HTMLResponse(workbook_html(job_id, job.filename, sheets))


@app.get("/view/{job_id}/sheets/{number}", response_class=HTMLResponse)
def worksheet_view(job_id: str, number: int) -> HTMLResponse:
    _, sheets = source_sheets(job_id)
    sheet = next((sheet for sheet in sheets if sheet["number"] == number), None)
    if sheet is None:
        raise HTTPException(404)
    return HTMLResponse(sheet_html(job_folder(job_id), job_id, sheet))


@app.get("/api/jobs/{job_id}/tables", response_model=list[TableView])
def job_tables(job_id: str) -> list[TableView]:
    folder = job_folder(job_id)
    job = Job.model_validate_json((folder / "job.json").read_text())
    if job.state not in {"success", "partial"} or not (folder / "document.json").is_file():
        raise HTTPException(409, "抽出完了後に表を操作できます。")
    return project_tables(DoclingDocument.load_from_json(folder / "document.json"), job.filename)


@app.get("/api/jobs/{job_id}/tables/{table_index}/csv")
def export_csv(
    job_id: str,
    table_index: int,
    rows: list[int] = Query(default=[]),
    columns: list[int] = Query(default=[-1]),
    headers: int = 0,
) -> Response:
    tables = job_tables(job_id)
    if not 0 <= table_index < len(tables):
        raise HTTPException(404)
    try:
        content = table_csv(tables[table_index], rows, columns, headers)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="table-{table_index + 1}.csv"'},
    )


def job_folder(job_id: str) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise HTTPException(404)
    folder = DATA / job_id
    if not (folder / "job.json").is_file():
        raise HTTPException(404)
    return folder


def run_job(folder: Path, job: Job) -> None:
    try:
        convert_job(folder, job)
    finally:
        SLOTS.release()


@app.post("/api/upload", response_model=Job, status_code=202)
async def upload(file: UploadFile = File(...)) -> Job:
    filename = Path((file.filename or "").replace("\\", "/")).name
    if Path(filename).suffix.lower() not in {".pdf", ".pptx", ".xlsx"}:
        raise HTTPException(415, "PDF / PPTX / XLSXを選んでください。")
    if shutil.disk_usage(ROOT).free < MIN_FREE:
        raise HTTPException(507, "空き容量が2 GiB未満です。処理を開始しません。")
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, "処理待ちは最大3件です。完了後に追加してください。")
    job = Job(id=uuid4().hex, filename=filename, synthetic=filename.startswith("synthetic-"))
    folder = DATA / job.id
    source = folder / ("original" + Path(filename).suffix.lower())
    try:
        folder.mkdir(parents=True)
        total = 0
        with source.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise HTTPException(413, "上限50 MiBを超えています。")
                stream.write(chunk)
        validate_input(source)
        save_job(folder, job)
        WORKER.submit(run_job, folder, job)
        return job.model_copy(deep=True)
    except (OSError, RuntimeError) as exc:
        SLOTS.release()
        if folder.is_dir():
            shutil.rmtree(folder)
        raise HTTPException(
            500,
            "ローカル保存または処理キューの開始に失敗しました。空き容量とログを確認してください。",
        ) from exc
    except (ValueError, HTTPException) as exc:
        SLOTS.release()
        shutil.rmtree(folder)  # Only this request's newly created incomplete upload.
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()


@app.get("/files/{job_id}/{relative:path}")
def artifact(job_id: str, relative: str, download: bool = False) -> FileResponse:
    folder = job_folder(job_id)
    target = (folder / relative).resolve()
    if (
        not target.is_relative_to(folder.resolve())
        or not target.is_file()
        or target.name in {"job.tmp", "job.json"}
    ):
        raise HTTPException(404)
    return FileResponse(target, filename=target.name if download else None)
