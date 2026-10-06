"""Private runtime activity/drain adapter, independent of document semantics."""
import re
import secrets
import threading
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse


def install_larm_lifecycle(app: FastAPI, larm_token: str) -> None:
    lifecycle_lock = threading.Lock()
    lifecycle = {"activeJobs": 0, "draining": False}
    boot_id = str(uuid4())

    @app.middleware("http")
    async def track_activity(request: Request, call_next):
        tracked = request.url.path.startswith("/internal/v1/")
        if tracked:
            with lifecycle_lock:
                if lifecycle["draining"]:
                    return JSONResponse({"detail": "processor_draining"}, status_code=503)
                lifecycle["activeJobs"] += 1
        try:
            return await call_next(request)
        finally:
            if tracked:
                with lifecycle_lock:
                    lifecycle["activeJobs"] -= 1

    def lifecycle_auth(authorization: Annotated[str | None, Header()] = None) -> None:
        match = re.fullmatch(r"Bearer (\S+)", authorization or "", re.IGNORECASE)
        supplied = match.group(1) if match else ""
        if len(larm_token) < 32 or not secrets.compare_digest(supplied.encode(), larm_token.encode()):
            raise HTTPException(401, "unauthenticated")

    def lifecycle_status() -> dict:
        with lifecycle_lock:
            return {"bootId": boot_id, "activeJobs": lifecycle["activeJobs"]}

    @app.get("/internal/larm/activity", dependencies=[Depends(lifecycle_auth)])
    def larm_activity() -> dict:
        return lifecycle_status()

    @app.post("/internal/larm/drain", dependencies=[Depends(lifecycle_auth)])
    def larm_drain() -> dict:
        with lifecycle_lock:
            lifecycle["draining"] = True
        return lifecycle_status()

    @app.post("/internal/larm/resume", dependencies=[Depends(lifecycle_auth)])
    def larm_resume() -> dict:
        with lifecycle_lock:
            lifecycle["draining"] = False
        return lifecycle_status()
