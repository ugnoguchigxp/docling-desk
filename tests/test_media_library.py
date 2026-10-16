"""Image upload -> library metadata -> reusable Markdown original."""

import hashlib
from io import BytesIO
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from docling_desk import config
from docling_desk.app import create_app
from docling_desk.storage import document_cache, document_folder, original_file


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    with TestClient(create_app()) as client:
        yield client


def picture(format="PNG", size=(160, 90), **options):
    stream = BytesIO()
    Image.new("RGB", size, "#5588aa").save(stream, format=format, **options)
    return stream.getvalue()


def upload(client, raw=None, name="diagram.png", folder=None):
    response = client.post(
        "/api/upload",
        files={"file": (name, raw if raw is not None else picture())},
        data={"folder_id": folder or "", "ocr_provider": "azure_read"},
    )
    assert response.status_code == 202, response.text
    return response.json()


def endpoint(job):
    return "/api/v1/media/" + str(UUID(job["id"]))


def test_upload_prepares_thumbnail_without_a_separate_library_entry(client, monkeypatch):
    from docling_desk.documents import images

    job = upload(client, picture(size=(1600, 900)))
    folder = document_folder(config.DATA, job["id"])
    cached = document_cache(folder) / "thumbnails" / f"image-{job['image']['sha256']}.webp"
    assert cached.is_file(), "upload must finish generating the thumbnail before returning"
    raw = cached.read_bytes()
    modified = cached.stat().st_mtime_ns
    assert len(raw) <= 32_000
    with Image.open(BytesIO(raw)) as image:
        assert image.format == "WEBP"
        assert image.size == (480, 270)
    snapshot = client.get("/api/library").json()
    assert [item["id"] for item in snapshot["jobs"]] == [job["id"]]
    assert len(client.get("/api/v1/media").json()["items"]) == 1
    encode = Mock(side_effect=AssertionError("listing must reuse the prepared thumbnail"))
    monkeypatch.setattr(images, "encode_thumbnail", encode)
    response = client.get(endpoint(job) + "/thumbnail")
    assert response.status_code == 200
    assert response.content == raw
    assert cached.stat().st_mtime_ns == modified
    encode.assert_not_called()


def test_missing_thumbnail_is_regenerated(client):
    job = upload(client)
    folder = document_folder(config.DATA, job["id"])
    cached = document_cache(folder) / "thumbnails" / f"image-{job['image']['sha256']}.webp"
    cached.unlink(missing_ok=True)
    response = client.get(endpoint(job) + "/thumbnail")
    assert response.status_code == 200
    assert cached.read_bytes() == response.content


def test_thumbnail_failure_does_not_register_or_publish_upload(client, monkeypatch):
    from docling_desk.documents import images

    raw = picture()
    monkeypatch.setattr(images, "encode_thumbnail", Mock(side_effect=OSError("disk full")))
    response = client.post("/api/upload", files={"file": ("diagram.png", raw)})
    assert response.status_code == 500
    assert client.get("/api/library").json()["jobs"] == []
    for area in ("content", "derived", "runtime", "cache"):
        assert not list((config.DATA / area / "documents").glob("*"))
    reference = f"/api/wiki/images/{hashlib.sha256(raw).hexdigest()}.png"
    assert client.get(reference).status_code == 404


def test_images_skip_conversion_and_keep_reusable_originals(client):
    client.app.state.worker.submit = Mock(side_effect=AssertionError("image is not a conversion"))
    raw = picture()
    job = upload(client, raw)
    assert job["state"] == "success" and job["image"]["width"] == 160
    assert job["image"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert client.get(job["image"]["reference"]).content == raw
    assert client.get(endpoint(job) + "/content").content == raw
    assert client.get(f"/files/{job['id']}/original.png").content == raw
    thumbnail = client.get(endpoint(job) + "/thumbnail")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/webp"
    assert len(thumbnail.content) <= 32_000
    with Image.open(BytesIO(thumbnail.content)) as image:
        assert image.width <= 480 and image.height <= 360
        assert image.width / image.height == pytest.approx(160 / 90, abs=0.03)
    # Deleting a placement must not break the image pasted into another MD tab.
    assert (
        client.post(
            "/api/library/operations",
            json={"action": "delete", "items": [{"kind": "file", "id": job["id"]}]},
        ).status_code
        == 200
    )
    assert client.get(endpoint(job)).status_code == 404
    assert client.get(job["image"]["reference"]).content == raw


def test_metadata_conflicts_search_move_and_copy(client):
    folder = client.post("/api/folders", json={"name": "設計"}).json()["id"]
    job = upload(client, folder=folder)
    url = endpoint(job)
    value = client.get(url).json()
    fields = {
        "display_name": "構成図.png",
        "description": "認証基盤の構成",
        "tags": ["構成図", "認証"],
        "default_alt": "図 [概要]",
        "default_credit": "自社作成",
        "source_url": "",
    }
    assert client.patch(url, json=fields).status_code == 428
    result = client.patch(url, json=fields, headers={"If-Match": value["metadata_etag"]})
    assert result.status_code == 200, result.text
    assert result.json()["reference"] == value["reference"]
    assert (
        client.patch(url, json=fields, headers={"If-Match": value["metadata_etag"]}).status_code
        == 412
    )
    assert (
        client.get("/api/v1/media", params={"q": "認証"}).json()["items"][0]["display_name"]
        == "構成図.png"
    )
    assert client.get("/api/v1/media", params={"folder_id": "root"}).json()["items"] == []
    snapshot = client.get("/api/library").json()["jobs"][0]
    assert snapshot["image_metadata"]["tags"] == ["構成図", "認証"]
    copied = client.post(
        "/api/library/operations",
        json={"action": "copy", "items": [{"kind": "file", "id": job["id"]}], "destination": None},
    )
    assert copied.status_code == 200, copied.text
    copy_id = copied.json()["items"][0]["id"]
    assert client.get("/api/v1/media/" + str(UUID(copy_id))).json()["default_alt"] == "図 [概要]"
    moved = client.post(
        "/api/library/operations",
        json={"action": "move", "items": [{"kind": "file", "id": job["id"]}], "destination": None},
    )
    assert moved.status_code == 200
    assert client.get(url).json()["reference"] == value["reference"]
    assert client.get(url).json()["folder_path"] == []


def test_search_cursor_and_formats(client):
    upload(client)
    upload(client, picture("JPEG"), "photo.jpeg")
    first = client.get("/api/v1/media", params={"limit": 1}).json()
    assert len(first["items"]) == 1 and first["next_cursor"]
    second = client.get("/api/v1/media", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert second["items"][0]["asset_id"] != first["items"][0]["asset_id"]
    assert (
        client.get(
            "/api/v1/media", params={"cursor": first["next_cursor"], "q": "different"}
        ).status_code
        == 400
    )
    assert (
        client.get("/api/v1/media", params={"media_type": "image/jpeg"}).json()["items"][0][
            "media_type"
        ]
        == "image/jpeg"
    )
    assert client.get("/api/v1/media/not-an-id").status_code == 404


@pytest.mark.parametrize(
    "raw,name",
    [(b"broken", "broken.png"), (picture("JPEG"), "wrong.png"), (b"<svg/>", "drawing.svg")],
)
def test_rejects_invalid_or_mislabeled_images(client, raw, name):
    response = client.post("/api/upload", files={"file": (name, raw)})
    assert response.status_code in {415, 422}, response.text
    assert client.get("/api/library").json()["jobs"] == []


def test_exif_orientation_and_corrupt_original(client):
    exif = Image.Exif()
    exif[274] = 6
    job = upload(client, picture("JPEG", (160, 90), exif=exif), "rotated.jpg")
    assert (job["image"]["width"], job["image"]["height"]) == (90, 160)
    source = original_file(document_folder(config.DATA, job["id"]), ".jpg")
    source.write_bytes(b"corrupt")
    assert client.get(endpoint(job) + "/content").status_code == 409


def test_pixel_limit(client, monkeypatch):
    from docling_desk.documents import images

    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 100)
    response = client.post("/api/upload", files={"file": ("large.png", picture())})
    assert response.status_code == 422
    assert client.get("/api/library").json()["jobs"] == []
