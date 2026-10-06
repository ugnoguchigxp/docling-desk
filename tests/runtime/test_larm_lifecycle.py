import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from docling_desk.knowledge.larm_lifecycle import install_larm_lifecycle


def test_auth_drain_and_resume():
    app = FastAPI()
    install_larm_lifecycle(app, "x" * 40)

    @app.get("/internal/v1/work")
    def work():
        return {"ok": True}

    with TestClient(app) as c:
        assert c.get("/internal/larm/activity").status_code == 401
        assert c.get("/internal/larm/activity", headers={"Authorization": "x" * 40}).status_code == 401
        headers = {"Authorization": "Bearer " + "x" * 40}
        assert c.post("/internal/larm/drain", headers=headers).json()["activeJobs"] == 0
        assert c.get("/internal/v1/work").status_code == 503
        assert c.post("/internal/larm/resume", headers=headers).status_code == 200
        assert c.get("/internal/v1/work").status_code == 200


def test_processing_remains_active_until_completion():
    app = FastAPI()
    install_larm_lifecycle(app, "x" * 40)
    started, finish = threading.Event(), threading.Event()

    @app.get("/internal/v1/work")
    def work():
        started.set()
        assert finish.wait(5)
        return {"ok": True}

    with TestClient(app) as c:
        thread = threading.Thread(target=lambda: c.get("/internal/v1/work"))
        thread.start()
        assert started.wait(5)
        headers = {"Authorization": "Bearer " + "x" * 40}
        assert c.post("/internal/larm/drain", headers=headers).json()["activeJobs"] == 1
        assert c.get("/internal/v1/work").status_code == 503
        finish.set()
        thread.join(5)
        assert not thread.is_alive()
        assert c.get("/internal/larm/activity", headers=headers).json()["activeJobs"] == 0
