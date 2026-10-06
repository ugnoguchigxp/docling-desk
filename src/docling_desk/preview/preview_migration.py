"""Upgrade saved previews without rerunning extraction or changing RAG."""

from __future__ import annotations

import json
from pathlib import Path

from docling_desk.documents.conversion import Job, office_preview, save_job
from docling_desk.preview.editable_preview import (
    NOTICE,
    PDF_NOTICE,
    PDF_RENDER_LOCK,
    build_editable_preview,
    editable_preview,
    ensure_pdf_preview,
)
from docling_desk.preview.powerpoint_export import cached_export, validate_export
from docling_desk.preview.slides import split_quicklook
from docling_desk.storage import document_folders, job_file, original_file
from docling_desk.translation.source import source_map


def rebuild_preview(folder: Path, *, saved_pdf_only: bool = False) -> dict:
    job = Job.model_validate_json((job_file(folder)).read_text())
    suffix = Path(job.original_filename or job.filename).suffix.lower()
    if job.state not in {"success", "partial"} or suffix not in {".pdf", ".pptx"}:
        return {"id": job.id, "state": "skipped"}
    source = original_file(folder, suffix)
    if suffix == ".pdf":
        preview, notice = ensure_pdf_preview(source, folder), PDF_NOTICE
    else:
        if saved_pdf_only:
            # Repair can run on a host with no Office renderer. Never re-extract.
            pdf = folder / "powerpoint-rendered.pdf"
            if not pdf.is_file():
                return {
                    "id": job.id,
                    "state": "failed",
                    "reason": "保存済みの描画PDFがありません。",
                }
            # A known mismatch is never accepted; legacy PDFs without a hash
            # manifest must at least agree on every slide's count and dimensions.
            manifests = [
                folder / "powerpoint-export.json",
                folder / "editable-preview/manifest.json",
            ]
            if any(path.is_file() for path in manifests) and cached_export(source, folder) is None:
                return {
                    "id": job.id,
                    "state": "failed",
                    "reason": "保存済みPDFが原本と一致しません。",
                }
            validate_export(source, pdf)
            with PDF_RENDER_LOCK:
                preview = editable_preview(source, folder) or build_editable_preview(
                    source, pdf, folder
                )
            notice = NOTICE
        else:
            preview, notice = office_preview(source, folder)
        if not preview:
            return {"id": job.id, "state": "failed", "reason": notice}
        path = folder / "slides.json"
        data = json.loads(path.read_text())
        frames = split_quicklook(folder / preview, folder, len(data["slides"]))
        if len(frames) != len(data["slides"]):
            raise ValueError("スライド番号を新しいHTMLと対応付けできません。")
        for slide in data["slides"]:
            slide["preview"] = frames[slide["number"]]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temporary.replace(path)
    # Preserve folder moves and other metadata written while native export runs.
    job = Job.model_validate_json((job_file(folder)).read_text())
    job.preview = preview
    job.warnings = [
        w for w in job.warnings if not w.startswith(("原本側は", "PDFの描画を", "高再現HTML"))
    ]
    job.warnings.append(notice)
    save_job(folder, job)
    source_map(folder)
    return {"id": job.id, "state": "updated", "preview": preview}


if __name__ == "__main__":
    import argparse

    from docling_desk.config import DATA

    parser = argparse.ArgumentParser(description="保存済み本文を再抽出せず、原本プレビューを修復")
    parser.add_argument(
        "--saved-pdf-only", action="store_true", help="保存済みPDFだけを使い、Officeを書き出さない"
    )
    parser.add_argument("--id", action="append", dest="ids", help="対象の資料ID（省略時は全資料）")
    args = parser.parse_args()
    for folder in sorted(document_folders(DATA)):
        if args.ids and folder.name not in args.ids:
            continue
        try:
            print(
                json.dumps(
                    rebuild_preview(folder, saved_pdf_only=args.saved_pdf_only), ensure_ascii=False
                ),
                flush=True,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(
                json.dumps(
                    {"id": folder.name, "state": "failed", "reason": str(exc)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
