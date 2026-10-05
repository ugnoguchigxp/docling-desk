"""Synthetic embeddings for the evaluation server. Never opens a socket."""

from __future__ import annotations

import hashlib
import math
import time

from docling_desk.knowledge.embedding_azure import EmbeddingError


class SyntheticEmbedding:
    """Deterministic local vectors. Results are not semantic relevance."""

    def __init__(
        self, dimensions: int = 1024, delay_seconds: float = 0, fail_status: int | None = None
    ):
        self.dimensions = dimensions
        self.delay_seconds = delay_seconds
        self.fail_status = fail_status
        self.external_requests = 0
        self.local_requests = 0

    @property
    def profile(self) -> str:
        return f"synthetic:{self.dimensions}"

    @property
    def gate_key(self) -> str:
        return f"synthetic-gate:{self.dimensions}"

    def request(self, texts: list[str]) -> tuple[list[list[float]], float]:
        self.local_requests += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.fail_status:
            error = EmbeddingError(
                f"検証用embeddingが失敗しました（HTTP {self.fail_status}）。",
                self.fail_status == 429,
            )
            error.delay = 0
            raise error
        return [self._vector(text) for text in texts], 0.0

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        values: list[float] = []
        while len(values) < self.dimensions:
            digest = hashlib.sha256(digest).digest()
            for offset in range(0, len(digest), 4):
                chunk = digest[offset : offset + 4]
                if len(chunk) < 4:
                    break
                values.append((int.from_bytes(chunk, "big") / 2**32) - 0.5)
                if len(values) == self.dimensions:
                    break
        norm = math.hypot(*values) or 1.0
        return [value / norm for value in values]
