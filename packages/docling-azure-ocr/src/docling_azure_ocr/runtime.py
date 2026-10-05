from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Protocol

from PIL import Image

from .config import OcrError, OcrProfile
from .normalization import normalize, png_bytes
from .transport import AzureTransport


class Ledger(Protocol):
    def get(self, key: str) -> dict | None: ...
    def save(self, key: str, record: dict, expected_version: int) -> dict: ...


def fault_exit(name: str) -> None:
    """Crash only when a test sets DOCLING_FAULT. Production leaves the variable unset."""
    raw = os.environ.get("DOCLING_FAULT", "")
    mode, separator, target = raw.partition(":")
    if separator and target == name and mode == "exit":
        os._exit(86)


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class OcrRuntime:
    def __init__(
        self,
        profile: OcrProfile,
        ledger: Ledger,
        cache: Path,
        boundary: str,
        source_sha256: str,
        transport: AzureTransport | None = None,
        local_reader=None,
        allow_resubmit: bool = False,
    ):
        self.profile, self.ledger, self.cache = profile, ledger, cache
        self.boundary, self.source_sha256 = boundary, source_sha256
        self.transport, self.local_reader = transport, local_reader
        self.allow_resubmit = allow_resubmit
        self.deadline = time.monotonic() + (
            profile.document_timeout if profile.provider == "azure_read" else 300
        )
        self.lock = threading.RLock()
        self.evidence: dict[str, dict] = {}
        self.error: OcrError | None = None
        self.response_bytes = 0

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise OcrError("ocr_document_timeout")
        return remaining

    def read(self, image: Image.Image, target: str, metadata: dict) -> dict:
        with self.lock:
            try:
                return self._read(image, target, metadata)
            except OcrError as exc:
                self.error = exc
                raise

    def _read(self, image: Image.Image, target: str, metadata: dict) -> dict:
        self.remaining()
        if self.profile.provider == "disabled":
            raise OcrError("ocr_disabled")
        if self.profile.provider == "azure_read" and not self.profile.enabled:
            raise OcrError("ocr_disabled")
        png, size = png_bytes(image, self.profile)
        identity = {
            "boundary": self.boundary,
            "source_sha256": self.source_sha256,
            "input_sha256": hashlib.sha256(png).hexdigest(),
            "target": target,
            "provider": self.profile.provider,
            "endpoint": self.profile.endpoint,
            "model": self.profile.model,
            "api_version": self.profile.api_version,
            "profile": self.profile.profile,
            "adapter_version": self.profile.adapter_version,
        }
        if metadata.get("page_size"):
            page_size = metadata["page_size"]
            metadata = {
                **metadata,
                "image_to_page": [
                    page_size["width"] / size[0],
                    0,
                    0,
                    0,
                    page_size["height"] / size[1],
                    0,
                ],
            }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        record = self.ledger.get(key)
        if record is None:
            record = self.ledger.save(
                key,
                {
                    "key": key,
                    "state": "prepared",
                    "attempt": 0,
                    "metadata": {
                        **identity,
                        "image_size": list(size),
                        "original_image_size": list(image.size),
                        **metadata,
                    },
                    "operation": None,
                    "response_sha256": None,
                    "error_code": None,
                },
                0,
            )

        def update(**values):
            nonlocal record
            assert record is not None
            record = self.ledger.save(key, {**record, **values}, record["version"])

        path = self.cache / (key + ".json")
        # Even a cache hit checks the live job through the ledger before using any text.
        if path.is_file() and record["state"] in ("completed", "submitted", "polling"):
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if len(raw) > 32 * 1024**2 or (
                record.get("response_sha256") and digest != record["response_sha256"]
            ):
                raise OcrError("ocr_cache_invalid")
            try:
                result = json.loads(raw)
                words = normalize(result, size, self.profile)
            except (ValueError, OcrError):
                raise OcrError("ocr_cache_invalid") from None
            if record["state"] != "completed":
                update(state="completed", response_sha256=digest, error_code=None)
            self.response_bytes += len(raw)
            if self.response_bytes > 200 * 1024**2:
                raise OcrError("ocr_response_too_large")
            return self.remember(target, key, record, words, result)
        if record["state"] == "completed":
            if not self.allow_resubmit:
                raise OcrError(
                    "ocr_cache_missing", "保存済みOCR結果がありません。再送の明示許可が必要です。"
                )
            update(state="prepared", operation=None, response_sha256=None, error_code=None)
        if record["state"] in ("submitting", "submission_unknown"):
            if not self.allow_resubmit:
                update(state="submission_unknown", error_code="submission_unknown")
                raise OcrError(
                    "submission_unknown",
                    "Azureの受付が不明です。重複課金の可能性を確認して再送してください。",
                )
            update(state="prepared", operation=None, error_code=None)
        if record["state"] == "failed":
            # A known operation must be resumed by GET, never replaced by another POST.
            update(state="submitted" if record.get("operation") else "prepared", error_code=None)
        if self.profile.provider == "local":
            if self.local_reader is None:
                raise OcrError("ocr_local_unavailable")
            result = self.local_reader(png, size, self.profile)
        else:
            if self.transport is None:
                raise OcrError("ocr_transport_missing")
            if record["state"] == "prepared":
                self.transport.headers()  # Authentication failures happen before send intent.
                update(state="submitting", attempt=record["attempt"] + 1)
                fault_exit("ocr_before_post")
                try:
                    operation, request_id = self.transport.submit(png, self.remaining())
                except OcrError as exc:
                    update(
                        state="submission_unknown"
                        if exc.code == "submission_unknown"
                        else "failed",
                        error_code=exc.code,
                    )
                    raise
                update(state="submitted", operation=operation, request_id=request_id)
            page_deadline = time.monotonic() + min(180, self.remaining())
            result = None
            attempts = 0
            while time.monotonic() < page_deadline:
                update(state="polling")
                result, delay = self.transport.poll(
                    record["operation"], min(self.remaining(), page_deadline - time.monotonic())
                )
                attempts += 1
                state = result.get("status") if result else "running"
                if state == "succeeded":
                    break
                if state not in ("notStarted", "running"):
                    update(state="failed", error_code="ocr_analysis_failed")
                    raise OcrError("ocr_analysis_failed")
                if (
                    delay >= min(self.remaining(), page_deadline - time.monotonic())
                    or attempts >= 90
                ):
                    raise OcrError("ocr_page_timeout")
                time.sleep(delay)
            else:
                raise OcrError("ocr_page_timeout")
        if not isinstance(result, dict):
            raise OcrError("ocr_invalid_response")
        words = normalize(result, size, self.profile)
        raw = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        self.response_bytes += len(raw)
        if len(raw) > 32 * 1024**2 or self.response_bytes > 200 * 1024**2:
            raise OcrError("ocr_response_too_large")
        # Save success before committing completed; a crash here is recovered without POST.
        atomic_json(path, result)
        update(state="completed", response_sha256=hashlib.sha256(raw).hexdigest(), error_code=None)
        return self.remember(target, key, record, words, result)

    def remember(
        self, target: str, key: str, record: dict, words: list[dict], result: dict
    ) -> dict:
        value = {
            **record["metadata"],
            "cache_key": key,
            "words": words,
            "operation_id": (record.get("operation") or "").split("/")[-1].split("?")[0],
            "request_id": record.get("request_id"),
            "response_sha256": record["response_sha256"],
        }
        if value.get("coordinate_frame") == "embedded_image_pixels":
            lines = result["analyzeResult"]["pages"][0].get("lines", [])
            if lines and all(isinstance(line.get("content"), str) for line in lines):
                value["text"] = "\n".join(line["content"] for line in lines)
            else:
                content = result["analyzeResult"].get("content")
                value["text"] = (
                    content if isinstance(content, str) else " ".join(w["text"] for w in words)
                )
        self.evidence[target] = value
        return value
