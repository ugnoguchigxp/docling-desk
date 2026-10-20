"""Native draw.io contracts and integration with the existing file library."""

import base64
import hashlib
import json
import zlib
from io import BytesIO
from unittest.mock import Mock
from urllib.parse import quote
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from lxml import html
from PIL import Image, PngImagePlugin

from docling_desk import config
from docling_desk.app import create_app
from docling_desk.documents import diagrams
from docling_desk.storage import document_folder, original_file


def model(label="日本語の図"):
    root = ET.Element("mxGraphModel")
    cells = ET.SubElement(root, "root")
    ET.SubElement(cells, "mxCell", id="0")
    ET.SubElement(cells, "mxCell", id="1", parent="0")
    cell = ET.SubElement(
        cells,
        "mxCell",
        id="2",
        parent="1",
        value=label,
        vertex="1",
        style="rounded=1;whiteSpace=wrap;html=1;",
    )
    ET.SubElement(
        cell, "mxGeometry", x="30", y="40", width="200", height="80", attrib={"as": "geometry"}
    )
    return ET.tostring(root, encoding="unicode")


def compressed(text):
    encoder = zlib.compressobj(wbits=-15)
    raw = quote(text, safe="!'()*-._~").encode()
    return base64.b64encode(encoder.compress(raw) + encoder.flush()).decode()


def document(packed=False):
    root = ET.Element("mxfile")
    for number in (1, 2):
        page = ET.SubElement(root, "diagram", id=f"p{number}", name=f"工程 {number}")
        value = model(f"日本語の図 {number}")
        if packed:
            page.text = compressed(value)
        else:
            page.append(ET.fromstring(value))
    return ET.tostring(root, encoding="utf-8")


def svg(raw):
    root = ET.Element("{http://www.w3.org/2000/svg}svg", content=raw.decode())
    ET.SubElement(root, "{http://www.w3.org/2000/svg}script").text = "alert(1)"
    return ET.tostring(root, encoding="utf-8")


def exported_html(raw):
    from html import escape

    data = escape(json.dumps({"xml": raw.decode()}), quote=True)
    return (
        '<!doctype html><div class="mxgraph" data-mxgraph="'
        + data
        + '"></div><script>alert(1)</script>'
    ).encode()


def png(raw):
    stream = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("mxfile", quote(raw.decode()))
    Image.new("RGB", (32, 32)).save(stream, format="PNG", pnginfo=metadata)
    return stream.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize(
    "name,wrap,packed",
    [
        ("example.drawio", lambda raw: raw, False),
        ("example.DRAWIO", lambda raw: raw, True),
        ("example.dio", lambda raw: raw, True),
        ("example.xml", lambda raw: raw, False),
        ("example.drawio.svg", svg, True),
        ("example.drawio.png", png, True),
        ("example.png", png, True),
        ("example.html", exported_html, True),
        ("example.htm", exported_html, False),
    ],
)
def test_upload_preview_and_original_without_extraction_or_duplicate_html(
    client, name, wrap, packed
):
    client.app.state.worker.submit = Mock(
        side_effect=AssertionError("diagram must not be extracted")
    )
    # A full conversion queue and unavailable Azure must not prevent a diagram.
    for _ in range(3):
        assert client.app.state.slots.acquire(blocking=False)
    raw = wrap(document(packed))
    folder_id = client.post("/api/folders", json={"name": "図"}).json()["id"]
    response = client.post(
        "/api/upload",
        files={"file": (name, raw)},
        data={"folder_id": folder_id, "ocr_provider": "azure_read"},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["diagram"] and job["state"] == "success" and job["pages"] == 2
    assert job["folder_id"] == folder_id and job["image"] is None and job["chunks"] == 0
    url = f"/view/{job['id']}/diagram"
    result = client.get(url + "?unit=2")
    assert result.status_code == 200
    tree = html.fromstring(result.text)
    data = json.loads(tree.xpath('//script[@id="diagramData"]/text()')[0])
    assert data["page"] == 1 and data["names"] == ["工程 1", "工程 2"]
    assert "日本語の図" in data["xml"]
    assert not tree.xpath('//script[not(@src) and not(@type="application/json")]')
    assert not tree.xpath("//iframe")
    assert "unsafe-eval" not in result.headers["content-security-policy"]
    assert "sandbox allow-scripts" in result.headers["content-security-policy"]
    assert "img-src data:" in result.headers["content-security-policy"]
    assert client.get(url + "?unit=3").status_code == 404
    suffix = "." + name.rsplit(".", 1)[1].lower()
    assert client.get(f"/files/{job['id']}/original{suffix}?download=true").content == raw
    folder = document_folder(config.DATA, job["id"])
    assert original_file(folder, suffix).read_bytes() == raw
    assert not list(folder.glob("*.html")) and not (folder / "document.json").exists()
    assert not list(folder.glob("rag*.jsonl"))
    listing = client.get("/api/library")
    assert listing.status_code == 200 and listing.json()["jobs"][0]["diagram"]


@pytest.mark.parametrize(
    "raw",
    [
        b"<root/>",
        b"<mxfile/>",
        b"garbage",
        b"<svg/>",
        b"<mxfile><diagram>not-base64</diagram></mxfile>",
        b'<!DOCTYPE mxfile [<!ENTITY x SYSTEM "file:///etc/passwd">]><mxfile>&x;</mxfile>',
        b'<mxfile><diagram id="same"><mxGraphModel><root/></mxGraphModel></diagram><diagram id="same"><mxGraphModel><root/></mxGraphModel></diagram></mxfile>',
    ],
)
def test_invalid_drawio_does_not_register_any_file(client, raw):
    response = client.post("/api/upload", files={"file": ("bad.drawio", raw)})
    assert response.status_code == 422, response.text
    assert client.get("/api/library").json()["jobs"] == []


def test_raw_graph_model_and_html_labels_are_data(client):
    value = model("</script><script>alert(1)</script>")
    job = client.post("/api/upload", files={"file": ("raw.xml", value.encode())}).json()
    response = client.get(f"/view/{job['id']}/diagram")
    tree = html.fromstring(response.text)
    assert len(tree.xpath('//script[@id="diagramData"]')) == 1
    payload = json.loads(tree.xpath('//script[@id="diagramData"]/text()')[0])
    assert "alert(1)" in payload["xml"]
    assert not tree.xpath('//script[text()="alert(1)"]')


def test_compressed_expansion_budget(tmp_path, monkeypatch):
    source = tmp_path / "bomb.drawio"
    source.write_bytes(document(True))
    # The compressed original fits; its expanded model exceeds the budget.
    monkeypatch.setattr(diagrams, "MAX_BYTES", len(source.read_bytes()) + 50)
    with pytest.raises(ValueError, match="上限"):
        diagrams.read_diagram(source)


def test_copy_move_rename_delete_preserve_drawio(client):
    raw = document(True)
    job = client.post("/api/upload", files={"file": ("diagram.drawio", raw)}).json()
    copied = client.post(
        "/api/library/operations",
        json={"action": "copy", "items": [{"kind": "file", "id": job["id"]}], "destination": None},
    ).json()
    identifier = copied["items"][0]["id"]
    folder = client.post("/api/folders", json={"name": "移動先"}).json()["id"]
    for action, changes in [("move", {"destination": folder}), ("rename", {"name": "別名.drawio"})]:
        result = client.post(
            "/api/library/operations",
            json={"action": action, "items": [{"kind": "file", "id": identifier}], **changes},
        )
        assert result.status_code == 200, result.text
    assert client.get(f"/view/{identifier}/diagram").status_code == 200
    assert client.get(f"/files/{identifier}/original.drawio").content == raw
    assert (
        client.post(
            "/api/library/operations",
            json={"action": "delete", "items": [{"kind": "file", "id": identifier}]},
        ).status_code
        == 200
    )
    assert client.get(f"/view/{identifier}/diagram").status_code == 404
    assert client.get(f"/view/{job['id']}/diagram").status_code == 200


def test_shared_viewer_assets_have_recorded_integrity(client):
    directory = config.RESOURCES / "static/vendor/drawio"
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest
    for name in ["viewer-static.min.js", "stencils.min.js"]:
        result = client.get("/static/vendor/drawio/" + name)
        assert result.status_code == 200 and result.content == (directory / name).read_bytes()
    assert client.get("/static/vendor/drawio/LICENSE").status_code == 404


def test_plain_png_stays_an_image_and_invalid_embedded_png_is_rejected(client):
    raw = BytesIO()
    Image.new("RGB", (32, 32)).save(raw, format="PNG")
    job = client.post("/api/upload", files={"file": ("plain.png", raw.getvalue())}).json()
    assert job["image"] and not job["diagram"]
    bad = client.post("/api/upload", files={"file": ("broken.drawio.png", b"broken")})
    assert bad.status_code == 422


def test_other_html_and_external_diagram_urls_are_rejected(client):
    for raw in [
        b"<html><body>ordinary</body></html>",
        b"""<div class="mxgraph" data-mxgraph='{"url":"https://example.test/graph.xml"}'></div>""",
    ]:
        response = client.post("/api/upload", files={"file": ("export.html", raw)})
        assert response.status_code == 422


@pytest.mark.parametrize("suffix", [".svg", ".png"])
def test_embedded_plain_xml_preserves_literal_percent_sequences(suffix, tmp_path):
    value = model("100%25 + A%20B")
    if suffix == ".svg":
        raw = svg(value.encode())
    else:
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("mxfile", value)
        stream = BytesIO()
        Image.new("RGB", (32, 32)).save(stream, format="PNG", pnginfo=metadata)
        raw = stream.getvalue()
    source = tmp_path / ("example" + suffix)
    source.write_bytes(raw)
    assert "100%25 + A%20B" in diagrams.read_diagram(source).xml
