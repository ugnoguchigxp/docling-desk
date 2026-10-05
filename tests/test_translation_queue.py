from dataclasses import replace
from threading import Event

import pytest
from conftest import wait_translation

import docling_desk.translation.service as service
from docling_desk.translation.provider import TranslationError
from docling_desk.translation.schedule import configured_schedule
from docling_desk.translation.service import TranslationManager, overview
from docling_desk.translation.source import source_map
from docling_desk.translation.store import interrupt_pending, read_result, summary


class Clock:
    def __init__(self):
        self.value = 100.0
        self.delays = []

    def wait(self, delay):
        self.delays.append(delay)
        self.value += delay
        return False


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(service, "monotonic", lambda: value.value)
    monkeypatch.setattr(service, "uniform", lambda a, b: 0)
    return value


def shutdown(manager):
    manager.close()
    manager.worker.shutdown(wait=True)


def test_one_minute_between_all_units_splits_and_documents(
    translation_document, fixed_provider, clock, monkeypatch
):
    folder, other = translation_document(), translation_document()
    source = source_map(folder)
    # Force multiple requests inside a page, without touching its original file.
    source["units"][0]["segments"][0]["source_text"] = "文" * 150
    monkeypatch.setattr(service, "source_map", lambda path: source)
    fixed_provider.profile = replace(fixed_provider.profile, max_input_chars=50)
    manager = TranslationManager()
    monkeypatch.setattr(manager.stopping, "wait", clock.wait)
    calls = []
    translate = fixed_provider.translate

    def timed(language, segments):
        calls.append(clock.value)
        result = translate(language, segments)
        clock.value += 7  # The interval begins after completion.
        return result

    fixed_provider.translate = timed
    try:
        manager.submit(folder, "ja", None, provider=fixed_provider, interval_seconds=60)
        assert wait_translation(folder, "ja", "slide-2")["state"] == "completed"
        first_calls = len(calls)
        assert first_calls >= 4
        assert all(b - a == 67 for a, b in zip(calls, calls[1:]))
        assert set(clock.delays) == {60}
        # A new document asking for zero cannot bypass the preceding cooldown.
        manager.submit(other, "en", ["slide-2"], provider=fixed_provider, interval_seconds=0)
        assert wait_translation(other, "en", "slide-2")["state"] == "completed"
        assert calls[first_calls] - calls[first_calls - 1] == 67
        record = read_result(folder, "ja", "slide-1")
        assert record["schedule"]["interval_seconds"] == 60
        assert record["next_attempt_at"] is None and record["wait_reason"] is None
        # Pacing is not part of the content hash/cache identity.
        assert (
            manager.submit(folder, "ja", None, provider=fixed_provider, interval_seconds=0)[
                "accepted"
            ]
            == []
        )
    finally:
        shutdown(manager)


def test_fifo_requests_never_overlap(translation_document, fixed_provider, monkeypatch):
    folder, other = translation_document(), translation_document()
    manager = TranslationManager()
    entered, release = Event(), Event()
    translate = fixed_provider.translate
    active = 0
    order = []

    def blocked(language, segments):
        nonlocal active
        active += 1
        assert active == 1
        order.append(language)
        if len(order) == 1:
            entered.set()
            assert release.wait(3)
        value = translate(language, segments)
        active -= 1
        return value

    fixed_provider.translate = blocked
    try:
        manager.submit(folder, "ja", ["slide-1"], provider=fixed_provider)
        assert entered.wait(3)
        manager.submit(other, "en", ["slide-1"], provider=fixed_provider)
        manager.submit(folder, "en", ["slide-2"], provider=fixed_provider)
        assert read_result(other, "en", "slide-1")["state"] == "queued"
        release.set()
        assert wait_translation(folder, "en", "slide-2")["state"] == "completed"
        assert order == ["ja", "en", "en"]
    finally:
        release.set()
        shutdown(manager)


def test_rate_limit_wait_and_bounded_retries_preserve_previous_result(
    translation_document, fixed_provider, clock, monkeypatch
):
    folder = translation_document()
    manager = TranslationManager()
    waiting = []

    def wait(delay):
        row = read_result(folder, "ja", "slide-1")
        waiting.append((delay, row["state"], row["wait_reason"], row["next_attempt_at"]))
        assert summary(folder)["ja"]["active"] == 1
        assert overview(folder)["units"][0]["languages"]["ja"]["state"] == "waiting"
        return clock.wait(delay)

    monkeypatch.setattr(manager.stopping, "wait", wait)
    try:
        manager.submit(folder, "ja", ["slide-1"], provider=fixed_provider)
        original = wait_translation(folder, "ja", "slide-1")["result"]
        attempts = []

        def limited(language, segments):
            attempts.append(clock.value)
            raise TranslationError("rate_limit", "制限中", True, retry_after=90)

        fixed_provider.translate = limited
        manager.submit(folder, "ja", ["slide-1"], force=True, provider=fixed_provider)
        failed = wait_translation(folder, "ja", "slide-1")
        assert attempts == [100, 190]  # Test retry limit is one, header is honored.
        assert waiting[0][:3] == (90, "waiting", "rate_limit") and waiting[0][3]
        assert failed["state"] == "failed" and failed["result"] == original
        assert failed["next_attempt_at"] is None
        # Exhaustion still leaves the provider-wide cooldown in effect.
        assert manager.retry_not_before == 280
    finally:
        shutdown(manager)


def test_backoff_increases_and_long_retry_hint_is_not_shortened(
    translation_document, fixed_provider, clock, monkeypatch
):
    monkeypatch.setenv("DOCLING_TRANSLATION_MAX_RETRIES", "2")
    monkeypatch.setenv("DOCLING_TRANSLATION_RETRY_SECONDS", "10")
    folder = translation_document()
    manager = TranslationManager()
    monkeypatch.setattr(manager.stopping, "wait", clock.wait)
    calls = []

    def failing(language, segments):
        calls.append(clock.value)
        raise TranslationError("transient", "一時エラー", True)

    fixed_provider.translate = failing
    try:
        manager.submit(folder, "en", ["slide-1"], provider=fixed_provider)
        assert wait_translation(folder, "en", "slide-1")["state"] == "failed"
        assert calls == [100, 110, 130] and clock.delays == [10, 20]
        clock.value = 200

        def long_hint(language, segments):
            calls.append(clock.value)
            raise TranslationError("rate_limit", "長い制限", True, retry_after=7200)

        fixed_provider.translate = long_hint
        manager.submit(folder, "ja", None, provider=fixed_provider)
        assert wait_translation(folder, "ja", "slide-2")["error_code"] == "rate_limit"
        assert calls == [100, 110, 130, 200]
        assert clock.delays == [10, 20]  # Never retry sooner than the server hint.
    finally:
        shutdown(manager)


def test_close_interrupts_interval_wait_and_queued_units(
    translation_document, fixed_provider, monkeypatch
):
    folder = translation_document()
    manager = TranslationManager()
    entered = Event()
    wait = manager.stopping.wait

    def observed(delay):
        entered.set()
        return wait(delay)

    monkeypatch.setattr(manager.stopping, "wait", observed)
    try:
        manager.submit(folder, "ja", None, provider=fixed_provider, interval_seconds=60)
        assert entered.wait(3)
        assert read_result(folder, "ja", "slide-2")["state"] == "waiting"
        manager.close()
        assert wait_translation(folder, "ja", "slide-2")["state"] == "interrupted"
        assert len(fixed_provider.calls) == 1
    finally:
        shutdown(manager)


def test_restart_recovers_waiting_state_without_losing_result(translation_document):
    from docling_desk.translation.store import atomic_json, result_path

    folder = translation_document()
    atomic_json(
        result_path(folder, "ja", "slide-1"),
        {
            "unit_id": "slide-1",
            "state": "waiting",
            "result": {"saved": "keep"},
            "wait_reason": "rate_limit",
            "next_attempt_at": "future",
        },
    )
    interrupt_pending(folder.parent)
    value = read_result(folder, "ja", "slide-1")
    assert value["state"] == "interrupted" and value["result"] == {"saved": "keep"}
    assert value["next_attempt_at"] is None


def test_default_interval_and_invalid_scheduling(monkeypatch):
    monkeypatch.delenv("DOCLING_TRANSLATION_INTERVAL_SECONDS")
    assert configured_schedule().interval_seconds == 60
    for value in [-1, 3601, True, 1.5]:
        with pytest.raises(TranslationError):
            configured_schedule(value)
    monkeypatch.setenv("DOCLING_TRANSLATION_INTERVAL_SECONDS", "bad")
    with pytest.raises(TranslationError):
        configured_schedule()
