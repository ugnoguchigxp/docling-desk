"""Admission slots: one owner, returned exactly once, only when the work has really ended."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import FixedProvider, wait_translation
from fastapi.testclient import TestClient

import docling_desk.explanation.service as explanation_service
from docling_desk import config
from docling_desk.api import uploads
from docling_desk.app import create_app
from docling_desk.execution import LeaseReleased, SlotLease, SlotPool
from docling_desk.explanation.provider import Profile
from docling_desk.explanation.store import ACTIVE, read_state, write_state
from docling_desk.storage import document_folders
from docling_desk.translation.provider import TranslationError
from docling_desk.translation.service import TranslationManager
from docling_desk.translation.store import read_result


def settle(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --- the shared component -------------------------------------------------


def test_pool_is_bounded_and_claims_are_leases():
    pool = SlotPool(2)
    first, second = pool.claim(), pool.claim()
    assert pool.claim() is None and pool.in_use == 2
    assert first.release() is True and first.release() is False
    assert pool.in_use == 1
    second.release()
    assert pool.in_use == 0
    with pytest.raises(ValueError):
        pool.release()  # A raw over-release is still an error, as with BoundedSemaphore.
    assert pool.acquire(blocking=False) and pool.in_use == 1


def test_work_that_raises_returns_its_slot_once():
    pool, executor = SlotPool(1), ThreadPoolExecutor(max_workers=1)
    try:
        lease = pool.claim()

        def boom():
            raise RuntimeError("work failed")

        future = lease.submit(executor, boom)
        with pytest.raises(RuntimeError):
            future.result(5)
        assert settle(lambda: pool.in_use == 0)
        # A late release (handler cleanup, stop request) cannot free another request's slot.
        other = pool.claim()
        assert lease.release() is False and lease.abandon() is False
        assert pool.in_use == 1
        other.release()
    finally:
        executor.shutdown(wait=True)


def test_submit_failure_returns_the_slot_and_nothing_is_accepted():
    pool, executor = SlotPool(1), ThreadPoolExecutor(max_workers=1)
    executor.shutdown()
    ran = []
    for _ in range(3):
        lease = pool.claim()
        assert lease is not None
        with pytest.raises(RuntimeError):
            lease.submit(executor, ran.append, 1)
        assert pool.in_use == 0 and lease.released
    assert ran == []


def test_queued_cancellation_returns_the_slot_once_and_work_never_runs():
    pool, executor = SlotPool(2), ThreadPoolExecutor(max_workers=1)
    gate, started, ran = threading.Event(), threading.Event(), []
    try:
        running = pool.claim()
        running.submit(executor, lambda: (started.set(), gate.wait(5)))
        assert started.wait(5)
        queued = pool.claim()
        queued.submit(executor, ran.append, "queued")
        assert pool.in_use == 2
        # Stop request on queued work: it never starts, so the slot comes back now.
        assert queued.request_stop() is True
        assert queued.stop_requested.is_set() and pool.in_use == 1
        assert queued.release() is False and queued.abandon() is False
        assert pool.in_use == 1
    finally:
        gate.set()
        executor.shutdown(wait=True)
    assert ran == [] and pool.in_use == 0


def test_stop_request_on_running_work_does_not_return_the_slot():
    pool, executor = SlotPool(1), ThreadPoolExecutor(max_workers=1)
    gate, started, cancelled = threading.Event(), threading.Event(), []
    try:
        lease = pool.claim()
        lease.submit(executor, lambda: (started.set(), gate.wait(5)))
        assert started.wait(5)
        # The provider ignores its cancel hook and keeps running.
        assert lease.request_stop(lambda: cancelled.append(True)) is False
        assert cancelled == [True] and lease.stop_requested.is_set()
        assert pool.in_use == 1 and not lease.released and pool.claim() is None
        gate.set()
        assert settle(lambda: pool.in_use == 0)
    finally:
        gate.set()
        executor.shutdown(wait=True)


def test_handler_cleanup_leaves_submitted_work_alone_but_frees_unsubmitted_claims():
    pool, executor = SlotPool(2), ThreadPoolExecutor(max_workers=1)
    gate, started = threading.Event(), threading.Event()
    try:
        submitted, never = pool.claim(), pool.claim()
        submitted.submit(executor, lambda: (started.set(), gate.wait(5)))
        assert started.wait(5)
        assert submitted.abandon() is False and pool.in_use == 2
        assert never.abandon() is True and pool.in_use == 1
        # A late submit on an abandoned claim is refused instead of running unowned.
        with pytest.raises(LeaseReleased):
            never.submit(executor, lambda: None)
        assert isinstance(never, SlotLease) and pool.in_use == 1
    finally:
        gate.set()
        executor.shutdown(wait=True)
    assert pool.in_use == 0


def test_concurrent_abandon_and_submit_never_free_a_slot_of_accepted_work():
    """Whichever wins, accepted work keeps its slot and refused work frees it once."""
    pool, executor = SlotPool(1), ThreadPoolExecutor(max_workers=1)
    try:
        for _ in range(200):
            lease = pool.claim()
            assert lease is not None
            gate, barrier = threading.Event(), threading.Barrier(2)
            outcome: dict = {}

            def submit():
                barrier.wait()
                try:
                    outcome["future"] = lease.submit(executor, gate.wait, 5)
                except LeaseReleased:
                    outcome["refused"] = True

            thread = threading.Thread(target=submit)
            thread.start()
            barrier.wait()
            freed = lease.abandon()
            thread.join(5)
            if "future" in outcome:
                assert freed is False and pool.in_use == 1  # still owned by the work
                gate.set()
                outcome["future"].result(5)
            else:
                assert outcome["refused"] and freed is True
            deadline = time.monotonic() + 5
            while pool.in_use and time.monotonic() < deadline:
                time.sleep(0.001)
            assert pool.in_use == 0
    finally:
        executor.shutdown(wait=True)


# --- upload conversion -----------------------------------------------------


@pytest.fixture
def upload_app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    config.DATA.mkdir()
    usage = uploads.shutil.disk_usage(config.DATA)
    monkeypatch.setattr(uploads.shutil, "disk_usage", lambda _: usage._replace(free=3 * 1024**3))
    return create_app()


def post_text(client, name="note.txt"):
    return client.post("/api/upload", files={"file": (name, b"text")})


def test_upload_submit_failure_rolls_back_and_never_consumes_a_slot(upload_app):
    with TestClient(upload_app) as client:
        slots = upload_app.state.slots

        def refuse(*args, **kwargs):
            raise RuntimeError("executor is shutting down")

        upload_app.state.worker.submit = refuse
        for _ in range(5):  # A leaked slot would turn the fourth into 429.
            assert post_text(client).status_code == 500
        assert slots.in_use == 0
        assert document_folders(config.DATA) == []
        assert client.get("/api/jobs").json() == []


def test_upload_unexpected_error_before_submit_returns_the_slot(upload_app, monkeypatch):
    monkeypatch.setattr(
        uploads, "register_upload", lambda *args, **kwargs: (_ for _ in ()).throw(KeyError("x"))
    )
    with TestClient(upload_app, raise_server_exceptions=False) as client:
        for _ in range(5):
            assert post_text(client).status_code == 500
        assert upload_app.state.slots.in_use == 0
        assert document_folders(config.DATA) == []


def test_upload_failure_after_acceptance_is_not_rolled_back_or_released_twice(
    upload_app, monkeypatch
):
    gate, calls = threading.Event(), []
    monkeypatch.setattr(uploads, "convert_job", lambda folder, job: (calls.append(1), gate.wait(5)))
    real_decorate, seen = uploads.decorate, []

    def decorate(*args, **kwargs):
        seen.append(1)
        if len(seen) == 2:
            raise OSError("library refresh failed after the job was queued")
        return real_decorate(*args, **kwargs)

    monkeypatch.setattr(uploads, "decorate", decorate)
    with TestClient(upload_app) as client:
        slots = upload_app.state.slots
        try:
            response = post_text(client)
            # The queued job is real, so the upload is reported as accepted.
            assert response.status_code == 202
            assert len(document_folders(config.DATA)) == 1
            assert settle(lambda: calls == [1]) and slots.in_use == 1
        finally:
            gate.set()
        assert settle(lambda: slots.in_use == 0)
        assert len(document_folders(config.DATA)) == 1


def test_lifespan_end_returns_queued_slots_and_a_restart_gets_a_fresh_pool(upload_app, monkeypatch):
    gate, started, calls = threading.Event(), threading.Event(), []

    def convert(folder, job):
        calls.append(job.id)
        started.set()
        gate.wait(5)

    monkeypatch.setattr(uploads, "convert_job", convert)
    try:
        with TestClient(upload_app) as client:
            slots = upload_app.state.slots
            assert post_text(client, "a.txt").status_code == 202
            assert started.wait(5)
            assert post_text(client, "b.txt").status_code == 202  # queued behind the first
            assert slots.in_use == 2
        # Shutdown cancelled the queued job: its slot is back, the running one keeps its own
        # until the conversion really stops.
        assert settle(lambda: slots.in_use == 1) and len(calls) == 1
        gate.set()
        assert settle(lambda: slots.in_use == 0)
    finally:
        gate.set()
    with TestClient(upload_app) as client:
        assert upload_app.state.slots is not slots and upload_app.state.slots.in_use == 0
        states = {job["state"] for job in client.get("/api/jobs").json()}
        assert states == {"failed"}
        assert len(calls) == 1  # The cancelled job was not started by the restart.


# --- translation -----------------------------------------------------------


def test_translation_submit_failure_leaves_no_accepted_request(translation_document):
    folder, provider = translation_document(), FixedProvider()
    manager = TranslationManager()
    manager.worker.shutdown(wait=True)
    try:
        for _ in range(5):
            with pytest.raises(TranslationError) as error:
                manager.submit(folder, "en", ["slide-1"], provider=provider)
            assert error.value.code == "storage"  # never queue_full: nothing leaked
            assert manager.slots.in_use == 0
        record = read_result(folder, "en", "slide-1")
        assert record["state"] == "failed" and record["error_code"] == "storage"
        assert provider.calls == []
    finally:
        manager.close()


def test_translation_slot_waits_for_a_provider_that_ignores_stop(translation_document):
    folder, provider = translation_document(), FixedProvider()
    entered, release = threading.Event(), threading.Event()
    translate = provider.translate

    def stubborn(language, segments):
        entered.set()
        assert release.wait(10)
        return translate(language, segments)

    provider.translate = stubborn
    manager = TranslationManager()
    try:
        manager.submit(folder, "en", ["slide-1"], provider=provider)
        assert entered.wait(5)
        manager.close()  # Stop request; the provider keeps running.
        assert manager.stopping.is_set() and manager.slots.in_use == 1
        release.set()
        record = wait_translation(folder, "en", "slide-1")
        assert settle(lambda: manager.slots.in_use == 0)
        assert record["state"] == "interrupted" and record["result"] is None
        assert len(provider.calls) == 1  # No resend of the request that was in flight.
    finally:
        release.set()
        manager.worker.shutdown(wait=True)


def test_translation_exception_returns_the_slot_once(translation_document):
    folder, provider = translation_document(), FixedProvider()
    provider.error = ("authentication", "認証エラー")
    manager = TranslationManager()
    try:
        manager.submit(folder, "en", ["slide-1"], provider=provider)
        assert wait_translation(folder, "en", "slide-1")["state"] == "failed"
        assert settle(lambda: manager.slots.in_use == 0)
        assert all(manager.slots.claim() for _ in range(3))
        assert manager.slots.claim() is None
    finally:
        manager.close()
        manager.worker.shutdown(wait=True)


# --- explanation -----------------------------------------------------------


class Stubborn:
    """A provider whose cancel hook does nothing and whose call can be held."""

    def __init__(self):
        self.profile = Profile(web_provider="disabled")
        self.calls = []
        self.entered, self.release = threading.Event(), threading.Event()

    def preflight(self):
        pass

    def cancel(self):
        pass

    def complete(self, task, payload, timeout):
        self.calls.append(task)
        self.entered.set()
        assert self.release.wait(10)
        return {
            "explanation": {
                "sections": [],
                "glossary": [],
                "supplements": [],
                "limitations": [],
            },
            "facts": [],
            "gaps": [],
            "search_plan": [],
        }


@pytest.fixture
def explanation(monkeypatch, translation_document):
    folder, provider = translation_document(), Stubborn()
    monkeypatch.setattr(explanation_service, "provider_for", lambda profile: provider)
    monkeypatch.setattr(explanation_service, "configured_profile", lambda: provider.profile)
    manager = explanation_service.ExplanationManager()
    yield folder, provider, manager
    provider.release.set()
    manager.close()
    manager.executor.shutdown(wait=True)


def test_explanation_submit_failure_marks_failed_and_keeps_every_slot(explanation):
    folder, provider, manager = explanation
    manager.executor.shutdown(wait=True)
    for _ in range(5):
        with pytest.raises(RuntimeError):
            manager.submit(folder, "slide-1", True)
        assert manager.slots.in_use == 0
    assert read_state(folder, "slide-1")["stage"] == "受付失敗"
    assert provider.calls == []


def test_explanation_checkpoint_read_failure_after_claim_returns_the_slot(explanation, monkeypatch):
    folder, provider, manager = explanation
    provider.release.set()
    manager.submit(folder, "slide-1")
    assert settle(lambda: read_state(folder, "slide-1")["state"] not in ACTIVE)
    assert settle(lambda: manager.slots.in_use == 0)
    state = read_state(folder, "slide-1")
    write_state(folder, {**state, "state": "interrupted", "resume_requested": True})

    def unreadable(*args, **kwargs):
        raise OSError("checkpoint unreadable")

    monkeypatch.setattr(explanation_service, "read_checkpoint", unreadable)
    for _ in range(4):
        with pytest.raises(OSError):
            manager.submit(folder, "slide-1", resume=True)
        assert manager.slots.in_use == 0


def test_explanation_stop_keeps_the_slot_until_the_provider_returns_and_old_attempt_is_refused(
    explanation,
):
    folder, provider, manager = explanation
    manager.submit(folder, "slide-1")
    assert provider.entered.wait(5) and manager.slots.in_use == 1
    manager.close()  # Stop request only: the provider ignores it.
    assert manager.slots.in_use == 1
    # A newer attempt takes over the unit while the old call is still in flight.
    state = read_state(folder, "slide-1")
    write_state(folder, {**state, "attempt_id": "newer-attempt"})
    provider.release.set()
    assert settle(lambda: manager.slots.in_use == 0)
    assert len(provider.calls) == 1  # The held request is not resent.
    after = read_state(folder, "slide-1")
    assert after["attempt_id"] == "newer-attempt"
    assert after["state"] in ACTIVE  # The old attempt did not overwrite or publish.
    assert not explanation_service.result(folder, "slide-1")["available"]


def test_explanation_limit_counts_only_accepted_work(explanation):
    folder, provider, manager = explanation
    manager.submit(folder, "slide-1")
    assert provider.entered.wait(5)
    assert manager.slots.claim() and manager.slots.claim()
    assert manager.slots.in_use == 3
    with pytest.raises(explanation_service.ExplanationError) as error:
        manager.submit(folder, "slide-2")
    assert error.value.code == "queue_full" and manager.slots.in_use == 3
