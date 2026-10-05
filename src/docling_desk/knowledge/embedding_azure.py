"""Azure v1 embeddings, with no implicit SDK retries and a durable shared cooldown."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from docling_desk.knowledge.store import Store


class EmbeddingError(Exception):
    def __init__(self, message: str, retryable: bool = False, delay: float = 0):
        super().__init__(message)
        self.retryable = retryable
        self.delay = delay


@dataclass(frozen=True)
class AzureEmbedding:
    endpoint: str
    key: str
    deployment: str
    version: str
    dimensions: int | None = None

    @classmethod
    def from_env(cls) -> AzureEmbedding | None:
        endpoint = os.environ.get("DOCLING_AZURE_EMBEDDING_ENDPOINT", "").rstrip("/")
        key = os.environ.get("DOCLING_AZURE_EMBEDDING_KEY", "")
        deployment = os.environ.get("DOCLING_AZURE_EMBEDDING_DEPLOYMENT", "")
        version = os.environ.get("DOCLING_AZURE_EMBEDDING_MODEL_VERSION", "")
        if not any((endpoint, key, deployment, version)):
            return None
        try:
            parsed = urlsplit(endpoint)
            parsed.port  # Reject invalid/out-of-range ports before creating the service.
            if not parsed.hostname or any(c.isspace() or ord(c) < 32 for c in endpoint):
                raise ValueError
        except ValueError:
            raise EmbeddingError("Azure embeddingの接続先が不正です。") from None
        if (
            not all((endpoint, key, deployment, version))
            or parsed.scheme != "https"
            or not parsed.netloc
        ):
            raise EmbeddingError(
                "Azure embeddingの接続先・キー・デプロイ名・モデル版を設定してください。"
            )
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise EmbeddingError("Azure embeddingの接続先が不正です。")
        dimensions = os.environ.get("DOCLING_AZURE_EMBEDDING_DIMENSIONS", "")
        if dimensions and (
            not dimensions.isascii()
            or not dimensions.isdigit()
            or len(dimensions) > 4
            or not 1 <= int(dimensions) <= 3072
        ):
            raise EmbeddingError("embeddingの次元数は1〜3072で指定してください。")
        return cls(endpoint, key, deployment, version, int(dimensions) if dimensions else None)

    @property
    def url(self) -> str:
        base = self.endpoint.removesuffix("/openai/v1")
        return base + "/openai/v1/embeddings"

    @property
    def profile(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [self.url, self.deployment, self.version, self.dimensions, "cl100k_base"]
            ).encode()
        ).hexdigest()

    @property
    def gate_key(self) -> str:
        # Changing dimensions or model-version cache keys must not bypass the same quota.
        return hashlib.sha256((self.url + "|" + self.deployment).encode()).hexdigest()

    def request(self, texts: list[str]) -> tuple[list[list[float]], float]:
        body = {"model": self.deployment, "input": texts, "encoding_format": "float"}
        if self.dimensions:
            body["dimensions"] = self.dimensions
        try:
            with httpx.Client(
                timeout=httpx.Timeout(60, connect=10), follow_redirects=False
            ) as client:
                response = client.post(self.url, headers={"api-key": self.key}, json=body)
        except httpx.HTTPError:
            raise EmbeddingError("Azure embeddingとの通信が終了しませんでした。", True) from None
        delay = retry_delay(response.headers)
        if response.status_code != 200:
            error = EmbeddingError(
                f"Azure embeddingの要求に失敗しました（HTTP {response.status_code}）。",
                response.status_code == 429 or response.status_code >= 500,
            )
            error.delay = delay
            raise error
        try:
            data = response.json()["data"]
            if not isinstance(data, list) or any(
                not isinstance(row, dict) or type(row.get("index")) is not int for row in data
            ):
                raise ValueError
            data = sorted(data, key=lambda row: row["index"])
            if [row["index"] for row in data] != list(range(len(texts))):
                raise ValueError
            vectors = [row["embedding"] for row in data]
            if any(not isinstance(v, list) for v in vectors):
                raise ValueError
            sizes = {len(v) for v in vectors}
            if len(sizes) != 1 or not next(iter(sizes)):
                raise ValueError
            if self.dimensions and sizes != {self.dimensions}:
                raise ValueError
            for vector in vectors:
                if (
                    any(
                        isinstance(v, bool)
                        or not isinstance(v, (int, float))
                        or not math.isfinite(v)
                        for v in vector
                    )
                    or not math.isfinite(math.hypot(*vector))
                    or not math.hypot(*vector)
                ):
                    raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise EmbeddingError("Azure embeddingの応答形式が不正です。", delay=delay) from None
        norms = [math.hypot(*vector) for vector in vectors]
        return [
            [v / norm for v in vector] for vector, norm in zip(vectors, norms, strict=True)
        ], delay


def retry_delay(headers) -> float:
    delays = [0.0]
    for name, factor in (("retry-after-ms", 0.001), ("retry-after", 1)):
        value = headers.get(name)
        if not value:
            continue
        try:
            delay = float(value) * factor
        except ValueError:
            try:
                delay = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(delay):
            delays.append(max(0, delay))
    return max(delays)


def paced_request(
    store: Store,
    provider: AzureEmbedding,
    texts: list[str],
    stop: threading.Event,
    cancelled=lambda: False,
) -> list[list[float]]:
    owner = uuid4().hex
    while True:
        if stop.is_set() or cancelled():
            raise EmbeddingError("処理を中止しました。")
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT until FROM gate WHERE profile=?", (provider.gate_key,)
            ).fetchone()
            now = time.time()
            wait = max(0, row[0] - now) if row else 0
            if not wait:
                db.execute(
                    "INSERT INTO gate VALUES(?,?,?) ON CONFLICT(profile) DO UPDATE SET owner=excluded.owner,until=excluded.until",
                    (provider.gate_key, owner, now + 180),
                )
        if not wait:
            break
        stop.wait(min(wait, 1))
    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(10):
            with store.connection() as db:
                db.execute(
                    "UPDATE gate SET until=? WHERE profile=? AND owner=?",
                    (time.time() + 180, provider.gate_key, owner),
                )

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    delay = 0
    try:
        if stop.is_set() or cancelled():
            raise EmbeddingError("処理を中止しました。")
        vectors, delay = provider.request(texts)
        return vectors
    except EmbeddingError as error:
        delay = getattr(error, "delay", 0)
        raise
    finally:
        heartbeat_stop.set()
        thread.join()
        with store.connection() as db:
            # Every attempt, including errors and cancellation during communication, cools down.
            db.execute(
                "UPDATE gate SET owner=NULL,until=? WHERE profile=? AND owner=?",
                (time.time() + max(15, delay), provider.gate_key, owner),
            )
