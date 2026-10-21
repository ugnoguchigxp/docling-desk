"""The upload-name boundary shared with frontend/src/lib/formats.ts.

Both sides read tests/fixtures/format-contract/cases.json; neither generates
the other. The frontend test (formats.test.ts) checks the same names against
its own table, so a name accepted on one side and refused on the other fails
one of the two.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from docling_desk import config
from docling_desk.app import create_app
from docling_desk.documents.diagram_store import SUFFIX as EXCALIDRAW_SUFFIX
from docling_desk.documents.diagrams import DIAGRAM_SUFFIXES, diagram_filename, png_has_diagram
from docling_desk.documents.images import IMAGE_SUFFIXES
from docling_desk.documents.text import (
    MARKDOWN_SUFFIXES,
    SUPPORTED_LABEL,
    SUPPORTED_SUFFIXES,
    TEX_SUFFIXES,
)

CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures" / "format-contract" / "cases.json").read_text("utf-8")
)

FAMILIES = {
    "pdf": {".pdf"},
    "pptx": {".pptx"},
    "xlsx": {".xlsx"},
    "docx": {".docx"},
    "markdown": MARKDOWN_SUFFIXES,
    "text": {".txt", ".text"},
    "tex": TEX_SUFFIXES,
}


def family_of(name: str) -> str | None:
    """How the server files an accepted name; the order is the upload route's."""
    suffix = Path(name).suffix.lower()
    if suffix == EXCALIDRAW_SUFFIX:
        return "excalidraw"
    if diagram_filename(name):
        return "drawio"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return next((family for family, suffixes in FAMILIES.items() if suffix in suffixes), None)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    # No lifespan: no worker, so an accepted name stops at 503 before any work.
    return TestClient(create_app())


@pytest.mark.parametrize("case", CONTRACT["names"], ids=lambda case: case["name"])
def test_upload_refuses_exactly_the_names_the_contract_refuses(client, case):
    response = client.post(
        "/api/upload", files={"file": (case["name"], b"\x00", "application/octet-stream")}
    )
    if case["family"] is None:
        assert response.status_code == 415, case
        assert SUPPORTED_LABEL in response.json()["detail"]
    else:
        assert response.status_code != 415, (case, response.json())


@pytest.mark.parametrize("case", CONTRACT["names"], ids=lambda case: case["name"])
def test_server_families_match_the_contract(case):
    accepted = Path(case["name"]).suffix.lower() in (
        SUPPORTED_SUFFIXES | IMAGE_SUFFIXES | DIAGRAM_SUFFIXES | {EXCALIDRAW_SUFFIX}
    )
    assert accepted == (case["family"] is not None)
    if accepted:
        assert family_of(case["name"]) == case["family"]


@pytest.mark.parametrize("case", CONTRACT["content_cases"], ids=lambda case: case["name"])
def test_a_png_is_a_diagram_by_content_not_by_name(tmp_path, case):
    path = tmp_path / "original.png"
    info = PngImagePlugin.PngInfo()
    for key, value in case["png_text"].items():
        info.add_text(key, value)
    Image.new("RGB", (2, 2), "white").save(path, "PNG", pnginfo=info)
    assert png_has_diagram(path) is case["diagram"], case["note"]


def test_the_refusal_label_names_every_accepted_family():
    assert "Excalidraw" in SUPPORTED_LABEL
