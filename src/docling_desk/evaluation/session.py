"""Start and stop the isolated evaluation server."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from docling_desk.evaluation.recording import ROOT


class IsolatedApp:
    def __init__(self, data: Path):
        self.data = data
        self.process: subprocess.Popen | None = None
        self.client: httpx.Client | None = None
        self.port = 0
        self.stderr: list[bytes] = []

    def __enter__(self) -> IsolatedApp:
        self.data.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "docling_desk.evaluation.server",
                "--data",
                str(self.data),
                "--port",
                str(self.port),
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        threading.Thread(target=self._drain, daemon=True).start()
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}",
            trust_env=False,
            timeout=30,
        )
        self._wait_until_ready()
        return self

    def _drain(self) -> None:
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            self.stderr.append(line)

    def _wait_until_ready(self) -> None:
        assert self.client and self.process
        deadline = time.monotonic() + 20
        last = ""
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                detail = b"".join(self.stderr).decode()[-2000:]
                raise RuntimeError(f"evaluation server exited: {detail}")
            try:
                response = self.client.get("/api/knowledge/status")
                if response.status_code == 200:
                    return
                last = response.text
            except httpx.HTTPError as exc:
                last = str(exc)
            time.sleep(0.05)
        detail = b"".join(self.stderr).decode()[-2000:]
        raise RuntimeError(f"evaluation server did not start: {last} {detail}")

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.client:
            self.client.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
