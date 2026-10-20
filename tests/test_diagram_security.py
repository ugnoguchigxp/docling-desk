"""Scene validation limits: everything is rejected before any file is written."""

import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from docling_desk import config
from docling_desk.api.diagrams import router
from docling_desk.app import create_app
from docling_desk.documents import diagram_models as models
from docling_desk.documents import diagram_store as store

FIXTURE = Path(__file__).parent / "fixtures" / "diagrams" / "basic.excalidraw"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def good() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def encode(value) -> bytes:
    return json.dumps(value).encode()


def image(mime="image/png", body=PNG, prefix=None) -> dict:
    prefix = prefix or f"data:{mime};base64,"
    return {"id": "f", "mimeType": mime, "dataURL": prefix + base64.b64encode(body).decode()}


def with_element(**fields) -> dict:
    value = good()
    value["elements"][0].update(fields)
    return value


def with_file(entry) -> dict:
    value = good()
    value["files"]["f1"] = entry
    return value


def with_key(key, replacement) -> dict:
    value = good()
    value[key] = replacement
    return value


def nested(depth: int) -> dict:
    leaf: dict = {}
    for _ in range(depth):
        leaf = {"a": leaf}
    return leaf


def duplicate_ids() -> dict:
    value = good()
    value["elements"][1]["id"] = "a1"
    return value


def test_fixture_scene_is_valid():
    scene = models.validate_scene(FIXTURE.read_bytes())
    assert scene["elements"][0]["customField"] == {"keep": [3, 2, 1]}
    minimal = {"type": "excalidraw", "version": 2, "elements": []}
    assert models.validate_scene(encode(minimal)) == minimal


REJECTED = {
    "svg image": with_file(image("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>")),
    "mime and data url differ": with_file(
        image("image/png", PNG, prefix="data:image/jpeg;base64,")
    ),
    "declared jpeg but png bytes": with_file(image("image/jpeg", PNG)),
    "declared png but jpeg bytes": with_file(image("image/png", b"\xff\xd8\xff\xe0" + b"0" * 8)),
    "declared webp but riff wave": with_file(image("image/webp", b"RIFF\x00\x00\x00\x00WAVE")),
    "unknown mime": with_file(image("application/pdf", b"%PDF-1.4")),
    "data url not base64": with_file(
        {"mimeType": "image/png", "dataURL": "data:image/png;base64,@@@"}
    ),
    "data url missing": with_file({"mimeType": "image/png"}),
    "file entry not an object": with_file("x"),
    "image over 4 MiB": with_file(image("image/png", PNG + b"\x00" * (4 * 1024 * 1024))),
    "javascript link": with_element(link="javascript:alert(1)"),
    "mixed case javascript link": with_element(link="JaVaScRiPt:alert(1)"),
    "tab inside scheme": with_element(link="java\tscript:alert(1)"),
    "leading space link": with_element(link=" javascript:alert(1)"),
    "data link": with_element(link="data:text/html,<script>alert(1)</script>"),
    "relative link": with_element(link="/etc/passwd"),
    "link not a string": with_element(link=5),
    "embeddable": with_element(type="embeddable"),
    "iframe": with_element(type="iframe"),
    "magicframe": with_element(type="magicframe"),
    "unknown type": with_element(type="hologram"),
    "missing type": {"type": "excalidraw", "version": 2, "elements": [{"id": "a"}]},
    "empty id": with_element(id=""),
    "numeric id": with_element(id=3),
    "duplicate ids": duplicate_ids(),
    "element not an object": with_key("elements", [1]),
    "huge x": with_element(x=1_000_001),
    "huge negative y": with_element(y=-1e9),
    "huge width": with_element(width=10**400),
    "huge point": with_element(points=[[0, 0], [2_000_000, 1]]),
    "point not a pair": with_element(points=[[0]]),
    "points not a list": with_element(points="x"),
    "coordinate as string": with_element(x="1"),
    "coordinate as bool": with_element(x=True),
    "wrong type": with_key("type", "excalidraw-library"),
    "version not int": with_key("version", "2"),
    "version float": with_key("version", 2.5),
    "elements not a list": with_key("elements", {}),
    "appState not an object": with_key("appState", []),
    "files not an object": with_key("files", []),
    "too many elements": with_key(
        "elements",
        [{"id": f"e{i}", "type": "rectangle"} for i in range(models.MAX_ELEMENTS + 1)],
    ),
    "nesting over 100": with_key("appState", nested(100)),
}


@pytest.mark.parametrize("name", REJECTED)
def test_scene_is_rejected(name):
    with pytest.raises(HTTPException) as caught:
        models.validate_scene(encode(REJECTED[name]))
    assert caught.value.status_code == 422, name
    assert isinstance(caught.value.detail, str)


def test_exact_limits_are_accepted():
    value = good()
    value["elements"] = [
        {"id": f"e{i}", "type": "rectangle", "x": 1_000_000, "y": -1_000_000}
        for i in range(models.MAX_ELEMENTS)
    ]
    value["appState"] = nested(97)
    models.validate_scene(encode(value))
    value = good()
    value["elements"][0]["link"] = "mailto:a@example.com"
    value["elements"][1]["link"] = "http://example.com"
    value["elements"][2]["link"] = ""
    value["files"]["f1"] = image("image/png", PNG + b"\x00" * (4 * 1024 * 1024 - len(PNG)))
    models.validate_scene(encode(value))
    for mime, body in (
        ("image/jpeg", b"\xff\xd8\xff\xe0" + b"0" * 8),
        ("image/gif", b"GIF89a" + b"0" * 8),
        ("image/gif", b"GIF87a" + b"0" * 8),
        ("image/webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "),
    ):
        models.validate_scene(encode(with_file(image(mime, body))))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"excalidraw","version":2,"elements":[{"id":"a","type":"rectangle","x":NaN}]}',
        b'{"type":"excalidraw","version":2,"elements":[],"x":Infinity}',
        b'{"type":"excalidraw","version":2,"elements":[],"x":-Infinity}',
        b'{"type":"excalidraw","version":2,"elements":[{"id":"a","type":"rectangle","x":1e999}]}',
        b'{"type":"excalidraw","version":2,"elements":[],"x":1e999}',
        b"\xef\xbb\xbf{}",
        b"\xff\xfe",
        b"",
        b"[]",
        b"null",
        b'{"type":"excalidraw","version":2,"elements":[',
    ],
)
def test_malformed_json_and_non_finite_numbers_are_rejected(raw):
    with pytest.raises(HTTPException) as caught:
        models.validate_scene(raw)
    assert caught.value.status_code == 422


def test_pathological_nesting_does_not_crash_the_parser():
    with pytest.raises(HTTPException) as caught:
        models.validate_scene(b"[" * 200_000 + b"]" * 200_000)
    assert caught.value.status_code == 422
    head = b'{"type":"excalidraw","version":2,"elements":[],"appState":'
    deep = head + b'{"a":' * 5000 + b"1" + b"}" * 5000 + b"}"
    with pytest.raises(HTTPException) as caught:
        models.validate_scene(deep)
    assert caught.value.status_code == 422


def test_total_size_is_413_not_422():
    head = b'{"type":"excalidraw","version":2,"elements":[],"pad":"'
    raw = head + b"a" * models.MAX_SCENE_BYTES + b'"}'
    with pytest.raises(HTTPException) as caught:
        models.validate_scene(raw)
    assert caught.value.status_code == 413


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    app = create_app()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


CREATE = {"If-None-Match": "*", "Idempotency-Key": "k"}


@pytest.mark.parametrize("name", ["svg image", "javascript link", "embeddable", "huge x"])
def test_http_rejections_never_reach_storage(client, tmp_path, name):
    identifier = uuid4().hex
    body = {"name": "x", "folder_id": None, "scene": REJECTED[name]}
    response = client.put(f"/api/diagrams/{identifier}", json=body, headers=CREATE)
    assert response.status_code == 422
    assert not (tmp_path / "content" / "documents" / identifier).exists()
    created = client.put(
        f"/api/diagrams/{identifier}",
        json={**body, "scene": good()},
        headers={**CREATE, "Idempotency-Key": "k2"},
    )
    etag = created.json()["etag"]
    save = client.post(
        f"/api/diagrams/{identifier}/revisions",
        content=encode(REJECTED[name]),
        headers={"If-Match": etag, "Idempotency-Key": "s", "Content-Type": "application/json"},
    )
    assert save.status_code == 422
    draft = client.post(
        f"/api/diagrams/{identifier}/drafts",
        content=encode(REJECTED[name]),
        headers={"If-Match": etag, "Content-Type": "application/json"},
    )
    assert draft.status_code == 422
    info = client.get(f"/api/diagrams/{identifier}").json()
    assert info["revision_number"] == 1 and info["draft"] is None


def test_http_nan_and_nesting_limits(client):
    identifier = uuid4().hex
    headers = {**CREATE, "Content-Type": "application/json"}
    url = f"/api/diagrams/{identifier}"
    nan = b'{"name":"x","folder_id":null,"scene":{"type":"excalidraw","version":2,"elements":[],"n":NaN}}'
    assert client.put(url, content=nan, headers=headers).status_code == 422
    deep = b'{"name":"x","folder_id":null,"scene":' + b"[" * 500_000 + b"]" * 500_000 + b"}"
    assert client.put(url, content=deep, headers=headers).status_code == 422
    scene = {"type": "excalidraw", "version": 2, "elements": [], "appState": nested(99)}
    body = {"name": "x", "folder_id": None, "scene": scene}
    assert client.put(url, json=body, headers=headers).status_code == 422
    scene["appState"] = nested(98)
    assert client.put(url, json=body, headers=headers).status_code == 201


def test_http_oversized_scene_is_413(client):
    scene = {
        "type": "excalidraw",
        "version": 2,
        "elements": [],
        "pad": "a" * (models.MAX_SCENE_BYTES + 10),
    }
    response = client.put(
        f"/api/diagrams/{uuid4().hex}",
        json={"name": "x", "folder_id": None, "scene": scene},
        headers=CREATE,
    )
    assert response.status_code == 413


def test_http_upload_validates_like_the_api(client, tmp_path):
    for name in ("svg image", "javascript link", "embeddable"):
        files = {"file": ("a.excalidraw", encode(REJECTED[name]), "text/plain")}
        assert client.post("/api/upload", files=files).status_code == 422, name
    assert client.get("/api/library").json()["jobs"] == []
    assert not any((tmp_path / "content" / "documents").glob("*"))


def test_store_writes_nothing_for_rejected_input(tmp_path):
    with pytest.raises(HTTPException):
        store.create(tmp_path, uuid4().hex, "x", None, encode(REJECTED["svg image"]), "k")
    assert not (tmp_path / "content").exists()
    assert not (tmp_path / "derived").exists()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"excalidraw","version":2,"elements":[{"id":"\\ud800","type":"rectangle"}]}',
        b'{"type":"excalidraw","version":2,"elements":[],"appState":{"\\udfff":1}}',
        b'{"type":"excalidraw","version":2,"elements":[],"x":"a\\ude00b"}',
    ],
)
def test_lone_surrogates_are_rejected(raw):
    with pytest.raises(HTTPException) as caught:
        models.validate_scene(raw)
    assert caught.value.status_code == 422
    models.validate_scene(b'{"type":"excalidraw","version":2,"elements":[],"x":"\\ud83d\\ude00"}')


def test_http_lone_surrogates_in_envelope_are_422(client):
    scene = '{"type":"excalidraw","version":2,"elements":[]}'
    headers = {**CREATE, "Content-Type": "application/json"}
    for body in (
        '{"name":"\\ud800","folder_id":null,"scene":' + scene + "}",
        '{"name":"x","folder_id":"\\ud800","scene":' + scene + "}",
        '{"name":"x","folder_id":null,"scene":{"type":"excalidraw","version":2,"elements":[],"a":"\\ud800"}}',
    ):
        response = client.put(f"/api/diagrams/{uuid4().hex}", content=body, headers=headers)
        assert response.status_code == 422, body
    assert client.get("/api/library").json()["jobs"] == []
