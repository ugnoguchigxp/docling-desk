"""OCR state adapters. Only the TypeScript service writes the knowledge database."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
from docling_azure_ocr.config import OcrError
from docling_azure_ocr.runtime import atomic_json

from docling_desk.operations.faults import checkpoint


class FileOcrLedger:
    """Local OCR/demo ledger. Azure knowledge jobs use HttpOcrLedger."""

    def __init__(self, folder: Path, max_submissions: int = 100, live=None):
        self.folder, self.max_submissions = folder, max_submissions
        self.live = live or (lambda: True)
        self.lock = threading.RLock()

    def check(self):
        if not self.live():
            raise OcrError("ocr_cancelled")

    def get(self, key: str) -> dict | None:
        self.check()
        path = self.folder / (key + ".json")
        return json.loads(path.read_text()) if path.is_file() else None

    def save(self, key: str, record: dict, expected_version: int) -> dict:
        with self.lock:
            self.check()
            old = self.get(key)
            if (old or {}).get("version", 0) != expected_version:
                raise OcrError("ocr_state_conflict")
            if record["state"] == "submitting" and (not old or old["state"] != "submitting"):
                total = sum(
                    json.loads(p.read_text()).get("attempt", 0) for p in self.folder.glob("*.json")
                )
                if total >= self.max_submissions:
                    raise OcrError("ocr_submission_limit")
            saved = {**record, "version": expected_version + 1}
            checkpoint("ocr_ledger_before_save")
            atomic_json(self.folder / (key + ".json"), saved)
            checkpoint("ocr_ledger_after_save")
            return saved


class HttpOcrLedger:
    def __init__(
        self,
        endpoint: str,
        token: str,
        job_id: str,
        run: int,
        source_revision: str,
        client: httpx.Client | None = None,
    ):
        self.url = endpoint.rstrip("/") + f"/internal/v1/jobs/{job_id}/ocr"
        self.token, self.run, self.source_revision = token, run, source_revision
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)

    def close(self):
        self.client.close()

    def call(self, key: str, **values):
        try:
            response = self.client.post(
                self.url,
                json={
                    "key": key,
                    "run": self.run,
                    "source_revision": self.source_revision,
                    **values,
                },
                headers={"Authorization": "Bearer " + self.token},
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise OcrError("ocr_state_unavailable") from None
        if response.status_code != 200:
            try:
                code = response.json().get("error", {}).get("code", "ocr_state_unavailable")
            except ValueError:
                code = "ocr_state_unavailable"
            raise OcrError(code if code.startswith("ocr_") else "ocr_cancelled")
        try:
            return response.json()["record"]
        except (ValueError, KeyError):
            raise OcrError("ocr_state_invalid") from None

    def get(self, key: str) -> dict | None:
        return self.call(key, action="get")

    def save(self, key: str, record: dict, expected_version: int) -> dict:
        return self.call(key, action="save", record=record, expected_version=expected_version)
