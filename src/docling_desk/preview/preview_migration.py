"""Upgrade saved previews without rerunning extraction or changing RAG."""

from __future__ import annotations

import json
from pathlib import Path

from docling_desk.documents.conversion import Job, office_preview, save_job
from docling_desk.preview.editable_preview import PDF_NOTICE, ensure_pdf_preview
from docling_desk.preview.slides import split_quicklook
from docling_desk.storage import document_folders, job_file, original_file


def rebuild_preview(folder: Path) -> dict:
    job = Job.model_validate_json((job_file(folder)).read_text())
    suffix = Path(job.filename).suffix.lower()
    if job.state not in {"success", "partial"} or suffix not in {".pdf", ".pptx"}:
        return {"id": job.id, "state": "skipped"}
    source = original_file(folder, suffix)
    if suffix == ".pdf":
        preview, notice = ensure_pdf_preview(source, folder), PDF_NOTICE
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
    return {"id": job.id, "state": "updated", "preview": preview}


if __name__ == "__main__":
    from docling_desk.config import DATA

    for folder in sorted(document_folders(DATA)):
        try:
            print(json.dumps(rebuild_preview(folder), ensure_ascii=False), flush=True)
        except (OSError, RuntimeError, ValueError) as exc:
            print(
                json.dumps(
                    {"id": folder.name, "state": "failed", "reason": str(exc)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
