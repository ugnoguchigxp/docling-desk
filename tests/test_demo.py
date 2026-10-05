import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import config as desk_config
from docling_desk.documents.conversion import Job, save_job


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    data = tmp_path_factory.mktemp("jobs")
    desk_config.DATA = data
    with TestClient(web.app) as client:
        yield client


def test_bad_input_and_cross_site_rejected(client):
    assert client.post("/api/upload", files={"file": ("a.exe", b"x")}).status_code == 415
    assert (
        client.post("/api/upload", files={"file": ("broken.xlsx", b"not zip")}).status_code == 422
    )
    assert (
        client.post(
            "/api/upload",
            files={"file": ("a.pdf", b"%PDF-")},
            headers={"Origin": "https://attacker.example"},
        ).status_code
        == 403
    )
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400


def test_artifacts_are_confined_and_active_content_blocked(client):
    folder = desk_config.DATA / ("a" * 32)
    folder.mkdir()
    save_job(folder, Job(id="a" * 32, filename="test.pdf"))
    (folder / "extracted.html").write_text("<script>alert(1)</script>")
    result = client.get("/files/" + "a" * 32 + "/extracted.html")
    assert result.status_code == 200
    assert "default-src 'none'" in result.headers["content-security-policy"]
    assert "sandbox" in result.headers["content-security-policy"]
    assert client.get("/files/" + "a" * 32 + "/%2e%2e%2f%2e%2e%2fapp.py").status_code == 404
    assert client.get("/files/" + "a" * 32 + "/job.json").status_code == 404


def test_actual_synthetic_extraction():
    """Check independently known values and provenance in actual converter results."""
    samples = select_extraction_samples(Path(__file__).resolve().parent / "fixtures/documents")
    for folder, job in samples.values():
        assert job.state == "success", job.error
        document = json.loads((folder / "document.json").read_text())
        assert "合成" in (folder / "text.txt").read_text()
        assert job.tables >= 1
        cells = [
            cell["text"] for table in document["tables"] for cell in table["data"]["table_cells"]
        ]
        assert {"120", "180", "150", "450"}.issubset(set(cells))
        chunks = [json.loads(line) for line in (folder / "rag.jsonl").read_text().splitlines()]
        assert any(("180" in c["text"] for c in chunks))
        assert all((c["source"] == job.filename and len(c["source_sha256"]) == 64 for c in chunks))
        assert all((c["refs"] for c in chunks))
        assert job.preview is not None
        assert (folder / job.preview).is_file()
    pdf_folder, pdf_job = samples["synthetic-report.pdf"]
    assert pdf_job.pages == 2
    assert pdf_job.pictures >= 1
    elements = json.loads((pdf_folder / "elements.json").read_text())
    assert any((e["label"] == "table" and e["provenance"] for e in elements))
    assert any((2 in e["pages"] and "表1" in e["text"] for e in elements))


EXTRACTION_FILENAMES = (
    "synthetic-report.pdf",
    "synthetic-slides.pptx",
    "synthetic-sheet.xlsx",
)


def select_extraction_samples(root: Path) -> dict[str, tuple[Path, Job]]:
    """The table and provenance check uses these three files, not every synthetic fixture."""
    found: dict[str, tuple[Path, Job]] = {}
    if root.is_dir():
        for path in root.glob("*/job.json"):
            job = Job.model_validate_json(path.read_text())
            if not job.synthetic or job.filename not in EXTRACTION_FILENAMES:
                continue
            current = found.get(job.filename)
            if current is None or job.created > current[1].created:
                found[job.filename] = (path.parent, job)
    missing = [name for name in EXTRACTION_FILENAMES if name not in found]
    if missing:
        raise AssertionError("抽出検査の対象資料がありません: " + ", ".join(missing))
    for name, (folder, job) in found.items():
        if job.state != "success" or not (folder / "document.json").is_file():
            raise AssertionError(f"抽出検査の対象資料が壊れています: {name}")
    return found


def test_extraction_selection_rejects_a_missing_or_broken_sample(tmp_path):
    with pytest.raises(AssertionError, match="対象資料がありません"):
        select_extraction_samples(tmp_path)
    for name in EXTRACTION_FILENAMES:
        folder = tmp_path / name
        folder.mkdir()
        save_job(
            folder,
            Job(id="b" * 32, filename=name, synthetic=True, state="success"),
        )
        (folder / "document.json").write_text("{}", encoding="utf-8")
    broken = tmp_path / "synthetic-sheet.xlsx"
    save_job(
        broken,
        Job(id="c" * 32, filename="synthetic-sheet.xlsx", synthetic=True, state="failed"),
    )
    with pytest.raises(AssertionError, match="壊れています"):
        select_extraction_samples(tmp_path)
