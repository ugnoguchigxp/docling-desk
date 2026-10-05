import json
import random
import shutil
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops

import docling_desk.app as web
import docling_desk.preview.thumbnails as thumbnails
from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, save_job

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests/fixtures/documents/slide"


def copy_slides(folder):
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SAMPLE / "quicklook", folder / "quicklook")
    shutil.copyfile(SAMPLE / "slides.json", folder / "slides.json")
    return folder


@pytest.mark.skipif(thumbnails.sys.platform != "darwin", reason="Native WebKit renderer uses macOS")
def test_native_thumbnails_match_pages_and_reuse_cached_images(tmp_path, monkeypatch):
    folder = copy_slides(tmp_path / "document")
    before = (folder / "slides.json").read_bytes()
    legacy = folder / "thumbnails" / ("slide-1-" + "a" * 16 + ".png")
    legacy.parent.mkdir()
    Image.new("RGB", (240, 135), "white").save(legacy)
    unrelated = legacy.parent / "original.png"
    unrelated.write_bytes(b"keep")
    calls = []
    run = thumbnails.subprocess.run

    def counted(command, **kwargs):
        if command[0] != "/usr/bin/xcrun":
            calls.append(command)
        return run(command, **kwargs)

    monkeypatch.setattr(thumbnails.subprocess, "run", counted)
    with ThreadPoolExecutor(max_workers=3) as worker:
        paths = list(worker.map(lambda _: thumbnails.thumbnail(folder, 1), range(3)))
    assert len(set(paths)) == 1 and len(calls) == 1
    assert not legacy.exists() and unrelated.read_bytes() == b"keep"
    with Image.open(paths[0]) as encoded:
        assert encoded.format == "WEBP" and paths[0].suffix == ".webp"
        image = encoded.convert("RGB")
    assert image.size == (112, 63)
    assert paths[0].stat().st_size <= 2000
    assert len(image.getcolors(112 * 63)) > 100  # A rendered slide, not a blank placeholder.
    second = Image.open(thumbnails.thumbnail(folder, 2)).convert("RGB")
    assert ImageChops.difference(image, second).getbbox() is not None
    assert len(calls) == 2
    assert thumbnails.thumbnail(folder, 1) == paths[0] and len(calls) == 2
    assert (folder / "slides.json").read_bytes() == before
    asset = folder / "quicklook/original.pptx.qlpreview/Attachment1.png"
    asset.touch()
    updated = thumbnails.thumbnail(folder, 1)
    assert updated != paths[0] and len(calls) == 3
    assert not paths[0].exists()
    assert len(list((folder / "thumbnails").glob("slide-1-*"))) == 1
    assert list((folder / "thumbnails").glob("*.png")) == [unrelated]


def test_detailed_thumbnail_and_oversized_cache_stay_under_two_kb(tmp_path):
    # High-entropy content exceeds the target with fixed quality alone.
    image = Image.frombytes("RGB", (240, 240), random.Random(42).randbytes(240 * 240 * 3))
    path = tmp_path / "cached.webp"
    image.save(path, format="WEBP", quality=45, method=6)
    assert path.stat().st_size > 2000
    encoded = thumbnails.encode_thumbnail(image)
    assert len(encoded) <= 2000
    with Image.open(BytesIO(encoded)) as result:
        assert result.format == "WEBP" and result.width <= 112 and result.height <= 160
    thumbnails.compact_cached_thumbnail(path)
    assert path.stat().st_size <= 2000 and not path.with_suffix(".tmp").exists()
    before = path.read_bytes()
    thumbnails.compact_cached_thumbnail(path)
    assert path.read_bytes() == before


def test_thumbnail_sources_cannot_leave_document_folder(tmp_path):
    folder = copy_slides(tmp_path / "document")
    with pytest.raises(ValueError):
        thumbnails.thumbnail(folder, 999)
    doc = json.loads((folder / "slides.json").read_text())
    outside = tmp_path / "outside.html"
    outside.write_text("<p>outside</p>")
    doc["slides"][0]["preview"] = "../outside.html"
    (folder / "slides.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        thumbnails.thumbnail(folder, 1)
    doc["slides"][0]["preview"] = str(
        SAMPLE / "quicklook/original.pptx.qlpreview/slide-layout-1.html"
    )
    (folder / "slides.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        thumbnails.thumbnail(folder, 1)


@pytest.mark.skipif(thumbnails.sys.platform != "darwin", reason="Native WebKit renderer uses macOS")
def test_thumbnail_route_returns_images_and_rejects_other_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    ident = "d" * 32
    folder = copy_slides(tmp_path / ident)
    save_job(folder, Job(id=ident, filename="slides.pptx", state="success", slide_layout=True))
    client = TestClient(web.app)
    response = client.get(f"/api/jobs/{ident}/slides/1/thumbnail")
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    assert response.content.startswith(b"RIFF") and response.content[8:12] == b"WEBP"
    assert client.get(f"/api/jobs/{ident}/slides/1/thumbnail").content == response.content
    assert client.get(f"/api/jobs/{ident}/slides/99/thumbnail").status_code == 404
    assert client.get("/api/jobs/not-a-job/slides/1/thumbnail").status_code == 404
    save_job(folder, Job(id=ident, filename="slides.pdf", state="success"))
    assert client.get(f"/api/jobs/{ident}/slides/1/thumbnail").status_code == 404
    save_job(folder, Job(id=ident, filename="slides.pptx", state="running"))
    assert client.get(f"/api/jobs/{ident}/slides/1/thumbnail").status_code == 404
