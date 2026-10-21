"""Previews and translations saved by the SVG renderer keep working after the page-image renderer."""

import json
import threading

import pytest
from conftest import FixedProvider, wait_translation
from fastapi.testclient import TestClient
from lxml import html
from test_editable_preview import sources as sources_for_deck

import docling_desk.app as web
from docling_desk import config
from docling_desk.preview.editable_preview import (
    RENDERER_VERSION,
    build_editable_preview,
    ensure_pdf_preview,
)
from docling_desk.storage import document_folder
from docling_desk.translation import service
from docling_desk.translation import source as sources
from docling_desk.translation.provider import TranslationError
from docling_desk.translation.source import LEGACY_NAME, legacy_pairs, source_map, template_for
from docling_desk.translation.store import LOCK


def downgrade(folder):
    """Leave what the SVG renderer wrote: version 3 and a page that is not an image."""
    directory = folder / "editable-preview"
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["version"] = 3
    (directory / "manifest.json").write_text(json.dumps(manifest))
    for page in directory.glob("page-*.html"):
        page.write_text("<html><body><svg><text>old svg</text></svg></body></html>")
    return directory


def test_pdf_preview_of_the_svg_renderer_is_regenerated_on_first_use(translation_document):
    folder = translation_document("page")
    source = folder / "original.pdf"
    original = source.read_bytes()
    ensure_pdf_preview(source, folder)
    directory = downgrade(folder)
    ensure_pdf_preview(source, folder)
    assert json.loads((directory / "manifest.json").read_text())["version"] == RENDERER_VERSION
    assert "old svg" not in (directory / "page-1.html").read_text()
    assert (directory / "page-1.webp").is_file() and not list(folder.glob(".editable-preview-*"))
    assert source.read_bytes() == original


def test_powerpoint_preview_of_the_svg_renderer_is_rebuilt_from_the_saved_pdf(tmp_path):
    source, pdf = sources_for_deck(tmp_path)
    job = {"filename": "deck.pptx", "preview": "editable-preview/Preview.html"}
    build_editable_preview(source, pdf, tmp_path)
    (tmp_path / "slides.json").write_text(
        json.dumps(
            {
                "slides": [
                    {"number": n, "preview": f"editable-preview/page-{n}.html"} for n in (1, 2)
                ]
            }
        )
    )
    downgrade(tmp_path)
    raw = template_for(tmp_path, job, 2)
    assert html.fromstring(raw).xpath('//div[contains(@class,"pdf-text")]/text()') == [
        "Vendor & <review>"
    ]
    assert "old svg" not in raw
    manifest = json.loads((tmp_path / "editable-preview/manifest.json").read_text())
    assert manifest["version"] == RENDERER_VERSION


def test_old_saved_translation_is_listed_as_pairs_and_can_be_redone(
    translation_document, monkeypatch
):
    folder = translation_document("page")
    monkeypatch.setattr(config, "DATA", folder.parent)
    monkeypatch.setattr(service, "provider_for", lambda profile: FixedProvider())
    # A binding and a translation as the SVG renderer left them.
    (folder / "translation-source.json").write_text(
        json.dumps(
            {
                "document_id": folder.name,
                "dependencies": {"renderer_version": 3},
                "units": [
                    {
                        "id": "page-1",
                        "source_hash": "old-hash",
                        "segments": [
                            {"id": "t00001", "source_text": "Old source A", "locator": {}},
                            {"id": "t00002", "source_text": "Old source B", "locator": {}},
                        ],
                    }
                ],
            }
        )
    )
    saved = folder / "translations/en/page-1.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(
        json.dumps(
            {
                "unit_id": "page-1",
                "state": "completed",
                "result": {
                    "source_hash": "old-hash",
                    "translations": {"t00001": "旧訳A", "t00002": "旧訳B"},
                },
            }
        )
    )
    before = saved.read_bytes()
    with TestClient(web.app) as client:
        folder = document_folder(config.DATA, folder.name)
        saved = folder / "translations/en/page-1.json"
        base = f"/api/jobs/{folder.name}/translations"
        row = client.get(base).json()["units"][0]["languages"]["en"]
        assert row["stale"] is True and row["available"] is False
        result = client.get(base + "/en/page-1").json()
        assert result["available"] is False and result["legacy"] is True
        assert result["pairs"] == [
            {"source": "Old source A", "translation": "旧訳A"},
            {"source": "Old source B", "translation": "旧訳B"},
        ]
        assert "texts" not in result
        # The old source text is kept once and the saved translation is untouched.
        legacy = json.loads((folder / LEGACY_NAME).read_text())
        assert legacy["bindings"]["old-hash"]["unit_id"] == "page-1"
        assert source_map(folder)["dependencies"]["renderer_version"] == RENDERER_VERSION
        assert (folder / LEGACY_NAME).read_text() == json.dumps(
            legacy, ensure_ascii=False, indent=2
        )
        assert saved.read_bytes() == before
        # The page shows its original text until the unit is translated again.
        page = client.get(f"/view/{folder.name}/pages/1?language=en")
        assert page.headers["X-Translation-State"] == "unavailable"
        assert not html.fromstring(page.text).xpath("//*[@data-translated]")
        # Translating again places the new translation over the page image.
        assert (
            client.post(base, json={"target_language": "en", "unit_ids": ["page-1"]}).status_code
            == 202
        )
        assert wait_translation(folder, "en", "page-1")["state"] == "completed"
        again = client.get(base + "/en/page-1").json()
        assert again["available"] is True and "legacy" not in again
        page = client.get(f"/view/{folder.name}/pages/1?language=en")
        assert page.headers["X-Translation-State"] == "translated"
        blocks = html.fromstring(page.text).xpath('//div[@data-translated="true"]')
        assert blocks and all(b.text.startswith("EN ") for b in blocks)
        assert html.fromstring(page.text).xpath("//img/@src")


def test_a_translation_unrelated_to_the_archived_binding_gets_no_pairs(
    translation_document, monkeypatch
):
    folder = translation_document("page")
    monkeypatch.setattr(config, "DATA", folder.parent)
    source_map(folder)
    saved = folder / "translations/en/page-1.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(
        json.dumps(
            {
                "unit_id": "page-1",
                "state": "completed",
                "result": {"source_hash": "unknown", "translations": {"t00001": "x"}},
            }
        )
    )
    with TestClient(web.app) as client:
        result = client.get(f"/api/jobs/{folder.name}/translations/en/page-1").json()
    assert result["available"] is False and "pairs" not in result


def test_only_documents_shown_by_the_page_renderer_depend_on_it(translation_document):
    assert source_map(translation_document("sheet"))["dependencies"]["renderer_version"] == 3
    # A deck whose preview is not the page-image one keeps its saved translations valid.
    assert source_map(translation_document("slide"))["dependencies"]["renderer_version"] == 3
    assert source_map(translation_document("page"))["dependencies"]["renderer_version"] == 4


def old_binding(folder, source_hash, text):
    (folder / "translation-source.json").write_text(
        json.dumps(
            {
                "document_id": folder.name,
                "dependencies": {"renderer_version": 3},
                "units": [
                    {
                        "id": "page-1",
                        "source_hash": source_hash,
                        "segments": [{"id": "t00001", "source_text": text, "locator": {}}],
                    }
                ],
            }
        )
    )


def old_translation(folder, source_hash, text):
    saved = folder / "translations/en/page-1.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(
        json.dumps(
            {
                "unit_id": "page-1",
                "state": "completed",
                "result": {"source_hash": source_hash, "translations": {"t00001": text}},
            }
        )
    )


def test_every_older_generation_stays_listed_after_a_rollback_and_a_second_upgrade(
    translation_document,
):
    folder = translation_document("page")
    old_binding(folder, "first", "First generation")
    source_map(folder)
    # Rolled back to the old code, which wrote its own binding, then upgraded again.
    old_binding(folder, "second", "Second generation")
    source_map(folder)
    bindings = json.loads((folder / LEGACY_NAME).read_text())["bindings"]
    assert set(bindings) == {"first", "second"}
    old_translation(folder, "second", "二度目")
    assert legacy_pairs(
        folder, "page-1", json.loads((folder / "translations/en/page-1.json").read_text())
    ) == [{"source": "Second generation", "translation": "二度目"}]
    old_translation(folder, "first", "一度目")
    record = json.loads((folder / "translations/en/page-1.json").read_text())
    assert legacy_pairs(folder, "page-1", record) == [
        {"source": "First generation", "translation": "一度目"}
    ]
    # A hash that belongs to another unit is not paired.
    assert legacy_pairs(folder, "page-2", record) is None


def test_a_failed_archive_is_logged_and_the_new_binding_is_still_written(
    translation_document, monkeypatch, caplog
):
    folder = translation_document("page")
    old_binding(folder, "old-hash", "Old")
    real = sources.atomic_json

    def fail_on_archive(path, value):
        if path.name == LEGACY_NAME:
            raise OSError("disk full")
        real(path, value)

    monkeypatch.setattr(sources, "atomic_json", fail_on_archive)
    result = source_map(folder)
    assert result["dependencies"]["renderer_version"] == RENDERER_VERSION
    assert json.loads((folder / "translation-source.json").read_text())["units"]
    assert not (folder / LEGACY_NAME).exists()
    assert "Could not archive" in caplog.text
    # A malformed earlier binding cannot block it either.
    old_binding(folder, "other", "Old")
    (folder / "translation-source.json").write_text(
        json.dumps({"dependencies": {"renderer_version": 3}, "units": [{"id": "page-1"}]})
    )
    assert source_map(folder)["units"]


@pytest.mark.parametrize("entry", ["source_map", "overview"])
def test_previews_are_generated_outside_the_translation_lock(
    translation_document, monkeypatch, entry
):
    folder = translation_document("page")
    held = []
    real = sources.ensure_pdf_preview

    def probe(source, target):
        def other_thread():
            free = LOCK.acquire(timeout=2)
            if free:
                LOCK.release()
            held.append(not free)

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
        return real(source, target)

    monkeypatch.setattr(sources, "ensure_pdf_preview", probe)
    if entry == "source_map":
        source_map(folder)
    else:
        monkeypatch.setattr(
            service,
            "configured_profile",
            lambda: (_ for _ in ()).throw(TranslationError("not_configured", "x")),
        )
        service.overview(folder)
    assert held == [False]


def test_old_deck_preview_with_no_saved_pdf_is_unavailable_not_read(
    translation_document, monkeypatch
):
    folder = translation_document("slide")
    monkeypatch.setattr(config, "DATA", folder.parent)
    source, pdf = sources_for_deck(folder)
    build_editable_preview(source, pdf, folder)
    downgrade(folder)
    pdf.unlink()
    job = json.loads((folder / "job.json").read_text())
    job["preview"] = "editable-preview/Preview.html"
    (folder / "job.json").write_text(json.dumps(job))
    slides = json.loads((folder / "slides.json").read_text())
    for slide in slides["slides"]:
        slide["preview"] = f"editable-preview/page-{slide['number']}.html"
    (folder / "slides.json").write_text(json.dumps(slides))
    units = source_map(folder)["units"]
    assert all(u["mode"] == "panel" and u["segments"] for u in units)
    assert all(
        "保存済みの描画PDF" in u["preview_unavailable_reason"]
        or "読み込めません" in u["preview_unavailable_reason"]
        for u in units
    )
    with TestClient(web.app) as client:
        response = client.get(f"/view/{folder.name}/slides/1")
    assert response.status_code == 404 and "保存済みの描画PDF" in response.json()["detail"]
    assert "old svg" not in response.text
