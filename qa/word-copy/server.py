"""Serve isolated real extraction fixtures without sending anything to an LLM."""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import docling_desk.config as settings
base = ROOT / ".cache/word-copy-qa/data"
base.mkdir(parents=True, exist_ok=True)
settings.DATA = base
os.environ["DOCLING_EXPLANATION_WEB"] = "disabled"
os.environ["DOCLING_TRANSLATION_INTERVAL_SECONDS"] = "0"
import docling_desk.app as app
import docling_desk.explanation.service as explanation_service
import docling_desk.translation.service as translation_service
from conftest import FixedProvider
from docling_desk.documents.conversion import Job, convert_job, save_job
from test_explanation import Fixed
from test_word import make_docx

for kind, ident, source in [
    ("slide", "7" * 32, ROOT / "data/bdfa6f3986324d1ba7b72e4d51a85390"),
    ("sheet", "8" * 32, ROOT / "tests/fixtures/documents/sheet"),
    ("page", "9" * 32, ROOT / "data/3bd19983f06e497b868d1994a6771a21"),
]:
    folder = base / ident
    if not folder.exists():
        shutil.copytree(
            source,
            folder,
            ignore=shutil.ignore_patterns(
                "translations", "explanations", "translation-source.json", "thumbnails"
            ),
        )
        job = Job.model_validate_json((folder / "job.json").read_text())
        job.id, job.filename = (
            ident,
            "QA." + {"slide": "pptx", "sheet": "xlsx", "page": "pdf"}[kind],
        )
        job.original_filename, job.folder_id = None, None
        save_job(folder, job)
word = base / ("d" * 32)
if not word.exists():
    word.mkdir()
    make_docx(word / "original.docx")
    job = Job(id=word.name, filename="QA.docx", synthetic=True)
    convert_job(word, job)
    if job.state != "success":
        raise RuntimeError(job.error or "Word preview failed")
translation = FixedProvider()
translation_service.provider_for = lambda profile: translation
translation_service.configured_profile = lambda: translation.profile
explanation = Fixed()
explanation_service.provider_for = lambda profile: explanation
explanation_service.configured_profile = lambda: explanation.profile

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app.app, host="127.0.0.1", port=8889, access_log=False)
