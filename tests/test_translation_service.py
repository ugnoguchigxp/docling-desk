import json
from dataclasses import replace
from threading import Event

import pytest
from conftest import wait_translation
from lxml import html

from docling_desk.translation.provider import TranslationError
from docling_desk.translation.service import TranslationManager, prepare, restore
from docling_desk.translation.source import source_map, template_for
from docling_desk.translation.store import read_result, usable


def active_stylesheet(folder):
    # Preview generation now prefers SVG HTML over the old Quick Look template.
    job = json.loads((folder / "job.json").read_text())
    tree = html.fromstring(template_for(folder, job, 1))
    url = tree.xpath('//link[@rel="stylesheet"]/@href')[0]
    return folder / url.removeprefix(f"/files/{folder.name}/")


def test_protected_literals_whitespace_and_long_nodes_survive_splitting():
    text = "  売上120件\nhttps://example.com/path  " + "長い文章" * 100
    unit = {"segments": [{"id": "t1", "source_text": text}]}
    batches, restoration = prepare(unit, 100)
    assert len(batches) > 1
    translated = {s["id"]: s["text"] for batch in batches for s in batch}
    assert restore(unit, translated, restoration) == {"t1": text}
    key = next(k for k, v in translated.items() if "__DL_" in v)
    translated[key] = "missing placeholders"
    with pytest.raises(TranslationError):
        restore(unit, translated, restoration)


def test_save_reuse_retry_and_provider_change(translation_document, fixed_provider):
    folder = translation_document()
    manager = TranslationManager()
    try:
        manager.submit(folder, "en", ["slide-1"], provider=fixed_provider)
        first = wait_translation(folder, "en", "slide-1")
        assert first["state"] == "completed"
        before = len(fixed_provider.calls)
        assert manager.submit(folder, "en", ["slide-1"], provider=fixed_provider)["accepted"] == []
        assert len(fixed_provider.calls) == before
        fixed_provider.error = ("authentication", "認証失敗")
        manager.submit(folder, "en", ["slide-1"], force=True, provider=fixed_provider)
        failed = wait_translation(folder, "en", "slide-1")
        assert failed["state"] == "failed" and failed["result"] == first["result"]
        assert usable(failed, source_map(folder)["units"][0])
        fixed_provider.error = None
        fixed_provider.profile = replace(
            fixed_provider.profile, provider="azure_openai", deployment="demo", model="azure-model"
        )
        manager.submit(folder, "en", ["slide-1"], provider=fixed_provider)
        azure = wait_translation(folder, "en", "slide-1")
        assert azure["state"] == "completed"
        assert azure["result"]["provider"] == "azure_openai"
        assert azure["result"]["config_hash"] != first["result"]["config_hash"]
    finally:
        manager.close()
        manager.worker.shutdown(wait=True)


def test_source_changes_prevent_saved_translation_display(translation_document, fixed_provider):
    folder = translation_document()
    manager = TranslationManager()
    try:
        manager.submit(folder, "ja", ["slide-1"], provider=fixed_provider)
        value = wait_translation(folder, "ja", "slide-1")
        css = active_stylesheet(folder)
        css.write_text(css.read_text() + "\nbody{color:blue}")
        assert not usable(value, source_map(folder)["units"][0])
        assert read_result(folder, "ja", "slide-1")["result"] == value["result"]
    finally:
        manager.close()
        manager.worker.shutdown(wait=True)


def test_active_deduplication_conflict_and_queue_limit(translation_document, fixed_provider):
    folder = translation_document()
    entered, release = Event(), Event()
    original_translate = fixed_provider.translate

    def blocked(language, segments):
        entered.set()
        assert release.wait(5)
        return original_translate(language, segments)

    fixed_provider.translate = blocked
    manager = TranslationManager()
    try:
        manager.submit(folder, "en", ["slide-1"], provider=fixed_provider)
        assert entered.wait(3)
        assert manager.submit(folder, "en", ["slide-1"], provider=fixed_provider) == {
            "accepted": [],
            "reused": ["slide-1"],
        }
        from conftest import FixedProvider

        different = FixedProvider(replace(fixed_provider.profile, revision="2"))
        with pytest.raises(TranslationError) as error:
            manager.submit(folder, "en", ["slide-1"], provider=different)
        assert error.value.code == "conflict"
        manager.submit(folder, "ja", ["slide-1"], provider=fixed_provider)
        manager.submit(folder, "en", ["slide-2"], provider=fixed_provider)
        with pytest.raises(TranslationError) as error:
            manager.submit(folder, "ja", ["slide-2"], provider=fixed_provider)
        assert error.value.code == "queue_full"
        assert read_result(folder, "ja", "slide-2")["state"] == "untranslated"
        release.set()
        assert wait_translation(folder, "en", "slide-2")["state"] == "completed"
    finally:
        release.set()
        manager.close()
        manager.worker.shutdown(wait=True)


def test_source_change_during_request_rejects_completion(translation_document, fixed_provider):
    folder = translation_document()
    original_translate = fixed_provider.translate

    def changed(language, segments):
        css = active_stylesheet(folder)
        css.write_text(css.read_text() + "\nbody{color:blue}")
        return original_translate(language, segments)

    fixed_provider.translate = changed
    manager = TranslationManager()
    try:
        manager.submit(folder, "en", ["slide-1"], provider=fixed_provider)
        record = wait_translation(folder, "en", "slide-1")
        assert record["state"] == "failed" and record["error_code"] == "source_changed"
        assert record["result"] is None
    finally:
        manager.close()
        manager.worker.shutdown(wait=True)


def test_retry_is_bounded_and_auth_failure_skips_remaining_units(
    translation_document, fixed_provider
):
    folder = translation_document()
    manager = TranslationManager()
    original_translate = fixed_provider.translate
    attempts = []

    def transient(language, segments):
        attempts.append(language)
        raise TranslationError("transient", "通信失敗", retryable=True)

    try:
        fixed_provider.translate = transient
        manager.submit(folder, "en", ["slide-1"], provider=fixed_provider)
        assert wait_translation(folder, "en", "slide-1")["error_code"] == "transient"
        assert len(attempts) == 2
        fixed_provider.translate = original_translate
        fixed_provider.error = ("authentication", "認証失敗")
        manager.submit(folder, "ja", None, provider=fixed_provider)
        assert wait_translation(folder, "ja", "slide-2")["error_code"] == "authentication"
        assert len(fixed_provider.calls) == 1
    finally:
        manager.close()
        manager.worker.shutdown(wait=True)
