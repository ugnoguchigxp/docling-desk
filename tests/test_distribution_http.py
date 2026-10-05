import time

from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import config as desk_config


def test_upload_search_context_and_delete_do_not_call_out(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    with TestClient(web.app) as client:
        uploaded = client.post(
            "/api/upload",
            files={
                "file": (
                    "synthetic-note.txt",
                    "復元確認の本文です。\n".encode(),
                    "text/plain",
                )
            },
        )
        assert uploaded.status_code == 202, uploaded.text
        job_id = uploaded.json()["id"]
        state = "queued"
        for _ in range(100):
            job = client.get(f"/api/jobs/{job_id}")
            assert job.status_code == 200
            state = job.json()["state"]
            if state in {"success", "failed"}:
                break
            time.sleep(0.05)
        assert state == "success", job.text
        found = client.post(
            "/api/knowledge/search",
            json={"query": "復元確認の本文", "mode": "text"},
        )
        assert found.status_code == 202, found.text
        results = found.json()["output"]["results"]
        assert results
        context = client.post(
            "/api/knowledge/context",
            json={
                "retrieval_id": found.json()["id"],
                "chunk_ids": [results[0]["chunk_id"]],
            },
        )
        assert context.status_code == 200, context.text
        assert "復元確認の本文" in context.text
        removed = client.post(
            "/api/library/operations",
            json={"action": "delete", "items": [{"kind": "file", "id": job_id}]},
        )
        assert removed.status_code == 200, removed.text
        assert not (tmp_path / job_id).exists()
        again = client.post(
            "/api/knowledge/search",
            json={"query": "復元確認の本文", "mode": "text"},
        )
        assert again.status_code == 202
        assert again.json()["output"]["results"] == []


def test_second_startup_in_the_same_process_can_convert(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    for index in range(2):
        with TestClient(web.app) as client:
            uploaded = client.post(
                "/api/upload",
                files={"file": (f"note-{index}.txt", f"再起動{index}の本文です。\n".encode())},
            )
            assert uploaded.status_code == 202, uploaded.text
            job_id = uploaded.json()["id"]
            state = "queued"
            detail = ""
            for _ in range(100):
                job = client.get(f"/api/jobs/{job_id}")
                assert job.status_code == 200
                state = job.json()["state"]
                detail = job.text
                if state in {"success", "failed"}:
                    break
                time.sleep(0.05)
            assert state == "success", detail
