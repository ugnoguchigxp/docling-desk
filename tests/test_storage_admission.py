"""Storage admission must follow the data volume, even when code lives elsewhere."""

from collections import namedtuple

from fastapi.testclient import TestClient

from docling_desk import config
from docling_desk.api import uploads
from docling_desk.app import create_app


def test_full_data_volume_rejected_before_creating_job_or_consuming_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    usage = namedtuple("Usage", "total used free")
    checked = []

    def disk_usage(path):
        checked.append(path)
        return usage(10 * 1024**3, 0, 0 if path == tmp_path else 10 * 1024**3)

    monkeypatch.setattr(uploads.shutil, "disk_usage", disk_usage)
    app = create_app()
    with TestClient(app) as client:
        for _ in range(4):
            response = client.post("/api/upload", files={"file": ("note.txt", b"text")})
            assert response.status_code == 507
        assert checked == [tmp_path] * 4
        assert not list(tmp_path.glob("*/job.json"))
        slots = app.state.slots
        assert all(slots.acquire(blocking=False) for _ in range(3))
        assert not slots.acquire(blocking=False)
        for _ in range(3):
            slots.release()
