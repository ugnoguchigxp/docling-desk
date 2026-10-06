"""Deterministic browser server on a private copy of all saved documents."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import docling_desk.config as settings
def browser_source() -> Path:
    """Layout baselines may use an explicit saved copy. CI starts from repository fixtures."""
    if os.environ.get("UI_SYNTHETIC") == "1":
        from qa.frontend_migration import synthetic_data

        source = ROOT / ".cache/frontend-migration-synthetic/data"
        synthetic_data.materialize(source, synthetic=True)
        (source / "library.json").write_text('{"folders":{},"files":{}}', encoding="utf-8")
        return source
    source = ROOT / ".cache/frontend-migration-source/data"
    if not source.is_dir():
        raise SystemExit(
            "Browser fixture cache is missing. Run pnpm visual:prepare for saved layout "
            "baselines, or set UI_SYNTHETIC=1 to start from repository fixtures."
        )
    return source


port = int(os.environ.get("DOCLING_UI_QA_PORT", "8876"))
base = ROOT / f".cache/frontend-migration-data-{port}"
if base.exists():
    shutil.rmtree(base)
shutil.copytree(browser_source(), base)
# Browser data shares only immutable snapshots, never the live data directory.
settings.DATA = base
if os.environ.get("UI_SYNTHETIC") == "1":
    from urllib.parse import quote

    from docling_desk.wiki_batch.repository import Repository

    workspace = base / "synthetic-wiki-workspace"
    fixture = json.loads((ROOT / "tests/fixtures/wiki_batch/legacy-snapshot.json").read_text())
    record = fixture["snapshot"]["page"]
    appendix = "\n\n## 書き出し原本\n"
    for label, filename in (
        ("原本Markdown", "原本 メモ.md"),
        ("原本CSV", "下位/目次 一覧.csv"),
        ("添付ファイル", "下位/添付 ファイル.bin"),
        ("欠落原本", "missing.md"),
    ):
        appendix += f"[{label}](../../../../sources/notion/{quote('日本語 フォルダー/' + filename)})\n\n"
    for field, name in (("original_path", "original"), ("ja_path", "ja")):
        file = workspace / record[field]
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(fixture[name] + appendix)
    (workspace / "manifests").mkdir()
    (workspace / "manifests/pages.jsonl").write_text(json.dumps(record) + "\n")
    originals = workspace / "sources/notion/日本語 フォルダー"
    (originals / "下位").mkdir(parents=True)
    (originals / "原本 メモ.md").write_bytes("# 書き出し原本\r\n\r\n[CSV](下位/目次%20一覧.csv)\r\n".encode())
    (originals / "下位/目次 一覧.csv").write_bytes("title,page_path\r\n原本,../原本%20メモ.md\r\n".encode())
    (originals / "下位/添付 ファイル.bin").write_bytes(b"\x00\xffsynthetic-attachment\r\n")
    Repository(workspace, base).sync()
os.environ["DOCLING_TRANSLATION_INTERVAL_SECONDS"] = "0"
os.environ["DOCLING_TRANSLATION_RETRY_SECONDS"] = "0"
from conftest import FixedProvider
from test_explanation import Fixed

import docling_desk.app as app
import docling_desk.explanation.service as explanation_service
import docling_desk.web.frontend as frontend_delivery
import docling_desk.translation.service as translation_service
from docling_desk.translation.store import read_result

# Optional immutable build for long comparisons while other work changes the
# live deployment. Normal operation tests continue to use the current build.
if bundle := os.environ.get("DOCLING_UI_QA_BUNDLE"):
    frontend_delivery.BUNDLE = Path(bundle).resolve()

if baseline := os.environ.get("DOCLING_UI_QA_BASELINE"):
    from fastapi.responses import FileResponse

    baseline_index = Path(baseline).resolve() / "index.html"

    @app.app.get("/review-baseline/")
    def review_baseline():
        return FileResponse(baseline_index, headers={"Cache-Control": "no-cache"})

translation = FixedProvider()
translation_service.provider_for = lambda profile: translation
translation_service.configured_profile = lambda: translation.profile
explanation = Fixed()
explanation_service.provider_for = lambda profile: explanation
explanation_service.configured_profile = lambda: explanation.profile
# Synthetic smoke fixtures cover creation and downloaded saved versions without LLM access.
for index, kind in enumerate(["slide", "sheet", "page"], start=7):
    folder = base / (str(index) * 32)
    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(ROOT / "tests/fixtures/documents" / kind, folder)
    job = json.loads((folder / "job.json").read_text())
    job["id"] = folder.name
    job["filename"] = {"slide": "QA.pptx", "sheet": "QA.xlsx", "page": "QA.pdf"}[kind]
    (folder / "job.json").write_text(json.dumps(job))
# Keep fixture ownership deterministic and out of the user's library folders.
(base / "library.json").unlink(missing_ok=True)
# Save deterministic translation/explanation fixtures before a browser reads them.
tm = translation_service.TranslationManager()
em = explanation_service.ExplanationManager()
for index in [7, 8, 9]:
    folder = base / (str(index) * 32)
    source = translation_service.source_map(folder)
    for lang in ["en", "ja"]:
        tm.submit(folder, lang, None, interval_seconds=0)
        deadline = time.monotonic() + 15
        while any(
            read_result(folder, lang, u["id"])["state"] in {"queued", "waiting", "running"}
            for u in source["units"]
        ):
            if time.monotonic() > deadline:
                raise RuntimeError("translation seed timed out")
            time.sleep(0.01)
    # UI fixtures contain complete saved versions. They do not depend on the
    # current LLM orchestration or its call budget, and never restart generation.
    shutil.copytree(
        ROOT / "qa/frontend-migration/fixtures/explanations" / folder.name,
        folder / "explanations",
        dirs_exist_ok=True,
    )
tm.close()
em.close()
em.executor.shutdown(wait=True)


# No conversion/model process for uploads in this UI-only lane.
def fixed_convert(folder, job):
    if Path(job.filename).suffix.lower() in {".md", ".markdown", ".txt", ".text"}:
        from docling_desk.documents.conversion import convert_job

        convert_job(folder, job)
        return
    kind = {".pdf": "page", ".pptx": "slide", ".xlsx": "sheet"}[Path(job.filename).suffix.lower()]
    source = ROOT / "tests/fixtures/documents" / kind
    for item in source.iterdir():
        if item.name == "job.json" or item.name.startswith("original"):
            continue
        if item.is_dir():
            shutil.copytree(item, folder / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, folder / item.name)
    template = json.loads((source / "job.json").read_text())
    for key in [
        "preview",
        "slide_layout",
        "pages",
        "tables",
        "pictures",
        "chunks",
        "search_chunks",
        "rag_policy",
    ]:
        setattr(job, key, template[key])
    job.state = "success"
    app.save_job(folder, job)


from docling_desk.api import uploads

uploads.convert_job = fixed_convert


if os.environ.get("UI_SYNTHETIC") == "1":
    # This lane checks browser image loading, caching and concurrency. Native
    # Office/WebKit rendering belongs to renderer tests, not this UI-only server.
    from PIL import Image

    import docling_desk.api.preview as preview_api
    from docling_desk.preview.thumbnails import encode_thumbnail, slide_source

    synthetic_image = (
        ROOT / "tests/fixtures/documents/slide/quicklook"
        / "original.pptx.qlpreview/Attachment1.png"
    )
    synthetic_thumbnail = base / "synthetic-slide-thumbnail.webp"
    with Image.open(synthetic_image) as image:
        synthetic_thumbnail.write_bytes(encode_thumbnail(image))

    def fixed_thumbnail(folder: Path, number: int) -> Path:
        slide_source(folder, number)  # Preserve page validation and 404 behavior.
        return synthetic_thumbnail

    preview_api.thumbnail = fixed_thumbnail


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app.app, host="127.0.0.1", port=port, access_log=False)
