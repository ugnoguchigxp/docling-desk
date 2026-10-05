from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("DOCLING_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

from docling_desk.translation.provider import Profile, TranslationError

SAMPLES = {
    "slide": "eb9e9df049b44b0193760203c3fb0f36",
    "sheet": "536cfca7d87b49bfa9be5f4ddf218efb",
    "page": "cdb93ad1297f406899ff983f8b97054b",
}


@pytest.fixture(autouse=True)
def fast_translation_schedule(monkeypatch):
    # Real pacing is verified with a virtual clock in the queue tests.
    monkeypatch.setenv("DOCLING_TRANSLATION_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("DOCLING_TRANSLATION_RETRY_SECONDS", "0")
    monkeypatch.setenv("DOCLING_TRANSLATION_MAX_RETRIES", "1")


@pytest.fixture
def translation_document(tmp_path):
    def create(kind="slide"):
        folder = tmp_path / (str(len(list(tmp_path.iterdir())) + 1) * 32)
        source = Path(__file__).resolve().parent / "fixtures" / "documents" / kind
        shutil.copytree(
            source,
            folder,
            ignore=shutil.ignore_patterns(
                "translations", "translation-source.json", "thumbnails", "explanations"
            ),
        )
        job = json.loads((folder / "job.json").read_text())
        job["id"] = folder.name
        (folder / "job.json").write_text(json.dumps(job))
        return folder

    return create


class FixedProvider:
    def __init__(self, profile=None):
        self.profile = profile or Profile()
        self.calls = []
        self.error = None

    def preflight(self):
        pass

    def cancel(self):
        pass

    def translate(self, language, segments):
        self.calls.append((language, segments))
        if self.error:
            raise TranslationError(*self.error)
        return {s["id"]: f"{language.upper()} {s['text']}" for s in segments}


@pytest.fixture
def fixed_provider():
    return FixedProvider()


def wait_translation(folder, language, unit_id):
    from docling_desk.translation.store import read_result

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = read_result(folder, language, unit_id)
        if result["state"] not in {"queued", "running", "waiting"}:
            return result
        time.sleep(0.01)
    raise AssertionError("translation did not complete")
