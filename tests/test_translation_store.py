from pathlib import Path

import pytest

from docling_desk.translation.store import (
    atomic_json,
    interrupt_pending,
    read_result,
    result_path,
    summary,
)


def test_restart_keeps_previous_result_and_marks_interrupted(tmp_path):
    folder = tmp_path / ("a" * 32)
    original = {"unit_id": "slide-1", "state": "running", "result": {"translations": {"t1": "old"}}}
    path = result_path(folder, "en", "slide-1")
    atomic_json(path, original)
    interrupt_pending(tmp_path)
    value = read_result(folder, "en", "slide-1")
    assert value["state"] == "interrupted"
    assert value["result"] == original["result"]
    assert not list(folder.rglob("*.tmp"))


def test_result_paths_reject_traversal(tmp_path):
    for language, unit in [("../en", "slide-1"), ("en", "../slide-1"), ("en", "slide-0")]:
        with pytest.raises(ValueError):
            result_path(tmp_path, language, unit)


def test_atomic_replace_failure_keeps_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    atomic_json(path, {"old": True})
    previous = path.read_bytes()

    def failed_replace(self, destination):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", failed_replace)
    with pytest.raises(OSError):
        atomic_json(path, {"new": True})
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_translation_does_not_prevent_startup_or_library(tmp_path):
    folder = tmp_path / ("a" * 32)
    path = result_path(folder, "en", "slide-1")
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    interrupt_pending(tmp_path)
    assert summary(folder)["en"] == {"saved": 0, "active": 0, "failed": 1}
