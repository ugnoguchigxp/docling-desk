"""Conditional-write behaviour against the real Azure SDK.

Run with Azurite (the local Storage emulator) and its connection string:
    DOCLING_TEST_AZURITE="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;..." \
        pytest tests/test_blob_azurite.py
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from docling_desk import config as desk_config
from docling_desk.blob_mirror import AzureBlobStore, Mirror

CONNECTION = os.environ.get("DOCLING_TEST_AZURITE", "")
pytestmark = pytest.mark.skipif(not CONNECTION, reason="DOCLING_TEST_AZURITE is not set")


def make(root: Path, key: str, data: bytes) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    old = time.time() - 60
    os.utime(path, (old, old))


@pytest.fixture
def store(monkeypatch):
    from azure.storage.blob import BlobServiceClient

    name = f"t{uuid.uuid4().hex[:12]}"
    service = BlobServiceClient.from_connection_string(CONNECTION)
    service.create_container(name)
    monkeypatch.setattr(desk_config, "BLOB_CONNECTION_STRING", CONNECTION)
    monkeypatch.setattr(desk_config, "BLOB_CONTAINER", name)
    monkeypatch.setattr(desk_config, "BLOB_PREFIX", "site")
    yield AzureBlobStore.from_config()
    service.delete_container(name)


def test_round_trip_conflict_delete_and_restore(store, tmp_path):
    local, other = tmp_path / "local", tmp_path / "restored"
    other.mkdir()
    mirror = Mirror(local, store, derived=True)
    make(local, "content/documents/x/original.pdf", b"pdf")
    make(local, "content/manifests/library.json", b"{}")
    make(local, "derived/documents/x/translations/en/1.json", b"{}")
    assert mirror.push().uploaded == 3
    assert mirror.push().unchanged == 3

    key = "content/documents/x/original.pdf"
    store.container.get_blob_client(f"site/{key}").upload_blob(b"theirs", overwrite=True)
    make(local, key, b"mine, edited")
    assert mirror.push().conflicts == [key]
    assert store.container.get_blob_client(f"site/{key}").download_blob().readall() == b"theirs"

    (local / "derived/documents/x/translations/en/1.json").unlink()
    assert mirror.push().deleted == 1

    restored = Mirror(other, store, derived=True)
    assert restored.pull().downloaded == 2
    assert not (other / "derived/documents/x/translations/en/1.json").exists()
    assert restored.push().unchanged == 2


def test_blob_removed_elsewhere_heals_and_lost_state_resends_nothing(store, tmp_path):
    local = tmp_path / "local"
    mirror = Mirror(local, store)
    key = "content/documents/x/original.pdf"
    make(local, key, b"pdf")
    assert mirror.push().uploaded == 1
    store.container.get_blob_client(f"site/{key}").delete_blob()
    make(local, key, b"pdf v2")
    assert mirror.push().uploaded == 1
    assert store.container.get_blob_client(f"site/{key}").download_blob().readall() == b"pdf v2"

    (local / "runtime/blob-sync.json").unlink()
    report = Mirror(local, store).push()
    assert report.unchanged == 1 and report.uploaded == 0 and not report.errors


def test_powerpoint_artifacts_and_binding_restore_without_reextracting(store, tmp_path):
    import json
    from unittest.mock import patch

    from docling_core.types.doc import DoclingDocument
    from test_powerpoint_recovery import ligature_sources

    from docling_desk.documents.conversion import Job, save_job
    from docling_desk.storage import document_folder
    from docling_desk.translation.source import file_digest, read_source, source_map

    root = tmp_path / "source"
    folder = document_folder(root, "b" * 32)
    folder.mkdir(parents=True)
    # Use a saved two-slide Docling fixture and generate a ligature PDF preview.
    from shutil import copyfile

    copyfile(Path("tests/fixtures/documents/slide/document.json"), folder / "document.json")
    source, pdf = ligature_sources(folder)
    original = root / "content/documents" / folder.name / "original.pptx"
    original.parent.mkdir(parents=True)
    source.replace(original)
    from docling_desk.preview.editable_preview import build_editable_preview

    preview = build_editable_preview(original, pdf, folder)
    from docling_desk.preview.slides import export_slide_layout

    export_slide_layout(
        DoclingDocument.load_from_json(folder / "document.json"), original, folder, preview
    )
    save_job(
        folder,
        Job(
            id=folder.name,
            filename="ligatures.pptx",
            state="success",
            pages=2,
            preview=preview,
            slide_layout=True,
        ),
    )
    first = source_map(folder)
    for path in root.rglob("*"):
        if path.is_file():
            old = time.time() - 60
            os.utime(path, (old, old))
    assert not Mirror(root, store, derived=True).push().errors
    other = tmp_path / "empty"
    assert Mirror(other, store, derived=True).pull().downloaded > 0
    file_digest.cache_clear()
    read_source.cache_clear()
    restored = document_folder(other, folder.name)
    assert json.loads((restored / "slides.json").read_text())["slides"][1]["preview"]
    with patch(
        "docling_desk.translation.source.build_source",
        side_effect=AssertionError("must reuse restored binding"),
    ):
        assert source_map(restored) == first
