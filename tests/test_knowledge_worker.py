from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient

from docling_desk.knowledge.worker import create_worker

TOKEN = "private-worker-token-" * 3


@pytest.fixture
def worker(tmp_path):
    return TestClient(create_worker(tmp_path, TOKEN)), tmp_path


def uploaded(root: Path, raw: bytes, suffix: str = ".txt") -> dict:
    source, revision = str(uuid4()), str(uuid4())
    folder = root / "sources" / source / revision
    folder.mkdir(parents=True)
    (folder / f"original{suffix}").write_bytes(raw)
    return {
        "source_id": source,
        "source_revision": revision,
        "suffix": suffix,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def test_worker_requires_token_and_rejects_arbitrary_paths(worker):
    client, root = worker
    payload = uploaded(root, "合成資料です。".encode())
    assert client.post("/internal/v1/validate", json=payload).status_code == 401
    payload["source_id"] = "../../data"
    assert (
        client.post(
            "/internal/v1/validate", json=payload, headers={"Authorization": f"Bearer {TOKEN}"}
        ).status_code
        == 422
    )
    assert client.get("/api/upload").status_code == 404
    assert client.get("/files/unknown/original.txt").status_code == 404


def test_validation_checks_hash_and_pdf_page_limit(worker):
    client, root = worker
    payload = uploaded(root, b"valid")
    payload["sha256"] = "a" * 64
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/internal/v1/validate", json=payload, headers=headers).status_code == 422
    pdf = pymupdf.open()
    for _ in range(101):
        pdf.new_page()
    payload = uploaded(root, pdf.tobytes(), ".pdf")
    pdf.close()
    assert client.post("/internal/v1/validate", json=payload, headers=headers).status_code == 422


def test_real_text_conversion_returns_rag_and_preserves_original(worker):
    client, root = worker
    raw = "これは合成されたナレッジです。\n\n在庫連携の時刻を確認してください。".encode()
    payload = uploaded(root, raw)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    checked = client.post("/internal/v1/validate", json=payload, headers=headers)
    assert checked.status_code == 200
    assert checked.json()["sha256"] == payload["sha256"]
    request = {
        **payload,
        "job_id": str(uuid4()),
        "run": 1,
        "profile": "local-v1",
        "filename": "合成資料.txt",
    }
    result = client.post("/internal/v1/extract", json=request, headers=headers)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["source_id"] == payload["source_id"]
    assert data["source_revision"] == payload["source_revision"]
    assert data["job_id"] == request["job_id"]
    assert data["source_sha256"] == payload["sha256"]
    assert "在庫連携" in "\n".join(item["text"] for item in data["chunks"])
    assert all(item["pages"] == [] for item in data["chunks"])
    parents = {item["id"] for item in data["contexts"]}
    assert all(item["parent_id"] in parents for item in data["chunks"])
    folder = root / "sources" / payload["source_id"] / payload["source_revision"]
    assert (folder / "original.txt").read_bytes() == raw
    assert (
        json.loads((folder / "attempts" / f"{request['job_id']}-1" / "job.json").read_text())[
            "state"
        ]
        == "success"
    )
    assert not (root / "library.json").exists()


def test_invalid_office_file_and_symlink_are_rejected(worker, tmp_path):
    client, root = worker
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = uploaded(root, b"not a zip", ".pptx")
    assert client.post("/internal/v1/validate", json=payload, headers=headers).status_code == 422
    payload = uploaded(root, b"hello")
    path = root / "sources" / payload["source_id"] / payload["source_revision"] / "original.txt"
    other = tmp_path / "another.txt"
    other.write_text("hello")
    path.unlink()
    path.symlink_to(other)
    assert client.post("/internal/v1/validate", json=payload, headers=headers).status_code == 422


def test_ocr_profile_is_read_from_json_body(worker):
    client, _ = worker
    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = client.post("/internal/v1/ocr-profile", json={"provider": "local"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["profile"] == "local-v1"
    assert response.json()["ocr_profile"]["provider"] == "local"
    assert (
        client.post(
            "/internal/v1/ocr-profile", json={"provider": "invalid"}, headers=headers
        ).status_code
        == 422
    )


@pytest.mark.parametrize("configured_endpoint", ["", "https://other.cognitiveservices.azure.com"])
def test_minimal_azure_settings_and_queued_job_connection_check(
    worker, monkeypatch, configured_endpoint
):
    client, root = worker
    headers = {"Authorization": f"Bearer {TOKEN}"}
    endpoint = "https://test.cognitiveservices.azure.com"
    monkeypatch.setenv("DOCLING_AZURE_OCR_ENDPOINT", endpoint)
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_API_KEY", "synthetic-secret")
    monkeypatch.delenv("DOCLING_AZURE_OCR_ENABLED", raising=False)
    monkeypatch.delenv("DOCLING_AZURE_OCR_AUTH", raising=False)
    checked = client.post(
        "/internal/v1/ocr-profile", json={"provider": "azure_read"}, headers=headers
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["ocr_profile"]["auth"] == "api_key"
    assert "synthetic-secret" not in checked.text
    request = {
        **uploaded(root, b"synthetic text"),
        **checked.json(),
        "job_id": str(uuid4()),
        "run": 1,
        "filename": "synthetic.txt",
    }
    monkeypatch.setenv("DOCLING_AZURE_OCR_ENDPOINT", configured_endpoint)
    result = client.post("/internal/v1/extract", json=request, headers=headers)
    assert result.status_code == 422
    assert result.json()["detail"] == "ocr_disabled"


def test_viewer_reads_only_one_attempt_and_never_reextracts(worker, monkeypatch):
    import base64

    client, root = worker
    raw = b"Synthetic viewer evidence. Literal /static/example.css stays unchanged."
    source = uploaded(root, raw)
    request = {
        **source,
        "job_id": str(uuid4()),
        "run": 1,
        "profile": "local-v1",
        "filename": "synthetic.txt",
    }
    headers = {"Authorization": f"Bearer {TOKEN}"}
    extraction = client.post("/internal/v1/extract", json=request, headers=headers)
    assert extraction.status_code == 200 and extraction.json()["viewer_ready"]

    def forbidden(*args, **kwargs):
        raise AssertionError("viewing must never extract or OCR")

    monkeypatch.setattr("docling_desk.knowledge.worker.convert_job", forbidden)
    view = {k: v for k, v in request.items() if k not in {"profile", "filename", "sha256"}}
    view.update(resource="manifest", prefix="/viewer/session/" + "x" * 43 + "/")
    manifest = client.post("/internal/v1/viewer", json=view, headers=headers)
    assert manifest.status_code == 200 and manifest.json()["kind"] == "document"
    assert "ocr_profile" not in manifest.json()["job"]
    result = client.post(
        "/internal/v1/viewer", json={**view, "resource": "view/document"}, headers=headers
    )
    assert result.status_code == 200, result.text
    html = base64.b64decode(result.json()["body_base64"]).decode()
    assert "Synthetic viewer evidence" in html and "/viewer/session/" in html
    assert "Literal /static/example.css stays unchanged." in html
    assert "/files/" not in html
    for path in [
        "file/job.json",
        "file/ocr-private/operations.json",
        "file/../original.txt",
        "file/original.txt",
        "view/pages/9",
    ]:
        assert client.post(
            "/internal/v1/viewer", json={**view, "resource": path}, headers=headers
        ).status_code in {404, 422}
    assert (
        client.post("/internal/v1/viewer", json={**view, "run": 2}, headers=headers).status_code
        == 404
    )
    folder = (
        root
        / "sources"
        / source["source_id"]
        / source["source_revision"]
        / "attempts"
        / f"{request['job_id']}-1"
    )
    (folder / "outside.svg").symlink_to(
        root / "sources" / source["source_id"] / source["source_revision"] / "original.txt"
    )
    assert (
        client.post(
            "/internal/v1/viewer", json={**view, "resource": "file/outside.svg"}, headers=headers
        ).status_code
        == 404
    )
    (folder.parent.parent / "original.txt").unlink()
    assert client.post("/internal/v1/viewer", json=view, headers=headers).status_code == 404


def test_viewer_rewrites_svg_links_without_inserting_tokens_in_text(tmp_path):
    import base64

    from docling_desk.documents.conversion import Job
    from docling_desk.viewer.render import render_resource

    job = Job(id=str(uuid4()), filename="synthetic.txt", state="success", preview="preview.html")
    (tmp_path / "job.json").write_text(job.model_dump_json())
    (tmp_path / "preview.html").write_text(
        f'<svg><image xlink:href="/files/{job.id}/plot.svg"></image>'
        "<text>Literal /static/example.css</text></svg>"
    )
    prefix = "/viewer/session/" + "x" * 43 + "/"
    result = render_resource(tmp_path, "file/preview.html", prefix)
    html = base64.b64decode(result["body_base64"]).decode()
    assert f'xlink:href="{prefix}file/plot.svg"' in html
    assert "Literal /static/example.css" in html
