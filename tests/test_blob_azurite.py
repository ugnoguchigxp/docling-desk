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
