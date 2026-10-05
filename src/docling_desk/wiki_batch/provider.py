"""Explicit Responses transport, durable responses and provider wait deadlines."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from docling_desk.translation.azure import retry_after

from .files import FatalProviderError, NeedsReview, RetryLater, Stopped


@dataclass
class Runtime:
    now: object = lambda: int(time.time() * 1000)
    sleep: object = lambda ms: time.sleep(ms / 1000)
    stopping: object = lambda: False
    log: object = print


def wait_until(store, runtime, target):
    announced = False
    while runtime.now() < target():
        if runtime.stopping() or store.setting("paused") == "1":
            raise Stopped("翻訳バッチを停止しました。")
        if not announced:
            stamp = datetime.fromtimestamp(target() / 1000, ZoneInfo("Asia/Tokyo"))
            runtime.log(f"待機：{stamp:%Y-%m-%d %H:%M:%S}（日本時間）まで")
            announced = True
        runtime.sleep(min(1000, target() - runtime.now()))
    if runtime.stopping() or store.setting("paused") == "1":
        raise Stopped("翻訳バッチを停止しました。")


def config_from_env():
    required = [
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_LUNA_DEPLOYMENT",
        "AZURE_OPENAI_SOL_DEPLOYMENT",
    ]
    values = {k: os.environ.get(k, "").strip() for k in required}
    if not all(values.values()):
        raise ValueError("Azure接続先・APIキー・下訳／検証のデプロイ名を設定してください。")
    parsed = urlsplit(values[required[0]])
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Azure接続先は認証情報を含まないHTTPS URLにしてください。")
    return {
        "endpoint": f"https://{parsed.netloc}/openai/v1/responses",
        "apiKey": values[required[1]],
        "draft": values[required[2]],
        "verify": values[required[3]],
    }


class AzureClient:
    def __init__(self, store, config, runtime=None, transport=None):
        self.store, self.config, self.runtime = store, config, runtime or Runtime()
        self.models = {"draft": config["draft"], "verify": config["verify"]}
        self.transport = transport

    def scrub(self, message):
        text = str(message)
        for secret in (
            self.config["apiKey"],
            self.config["endpoint"],
            "https://" + urlsplit(self.config["endpoint"]).netloc,
        ):
            text = text.replace(secret, "[非表示]")
        return text[:800]

    def request(self, role, request, call_key, job_id):
        cached = self.store.db.execute(
            "SELECT response FROM attempts WHERE call_key=? AND status='success' ORDER BY started_at DESC LIMIT 1",
            (call_key,),
        ).fetchone()
        if cached:
            return cached[0]
        previous = self.store.db.execute(
            "SELECT count(*),min(started_at) FROM attempts WHERE call_key=? AND status<>'discarded'",
            (call_key,),
        ).fetchone()
        if previous[0] >= 6 or (
            previous[1] is not None and self.runtime.now() - previous[1] > 86400000
        ):
            raise NeedsReview("Azureへの再試行上限に達しました。")
        wait_until(self.store, self.runtime, lambda: self.store.number("blocked_until"))
        now, attempt_id = self.runtime.now(), str(uuid4())
        self.store.db.execute(
            "INSERT INTO attempts(id,job_id,call_key,role,started_at,status,reserved) VALUES(?,?,?,?,?,'pending',?)",
            (attempt_id, job_id, call_key, role, now, request["maxOutputTokens"]),
        )
        try:
            started = time.monotonic()
            with httpx.Client(
                timeout=180, follow_redirects=False, transport=self.transport
            ) as client:
                with client.stream(
                    "POST",
                    self.config["endpoint"],
                    headers={"api-key": self.config["apiKey"]},
                    json={
                        "model": self.config[role],
                        "instructions": request["instructions"],
                        "input": request["input"],
                        "max_output_tokens": request["maxOutputTokens"],
                        "store": False,
                    },
                ) as response:
                    delay = retry_after(response.headers)
                    status = response.status_code
                    for header, setting in (
                        ("x-ratelimit-limit-tokens", "observed_tpm"),
                        ("x-ratelimit-limit-requests", "observed_rpm"),
                    ):
                        raw_limit = response.headers.get(header, "")
                        if raw_limit.isdigit() and int(raw_limit) > 0:
                            previous_limit = self.store.number(setting)
                            self.store.set(
                                setting,
                                min(previous_limit, int(raw_limit))
                                if previous_limit
                                else raw_limit,
                            )
                    if not 200 <= status < 300:
                        message = f"Azure HTTP {status}"
                        self.store.db.execute(
                            "UPDATE attempts SET status='error',finished_at=?,error=? WHERE id=?",
                            (self.runtime.now(), message, attempt_id),
                        )
                        if status == 429 or status == 408 or status >= 500:
                            deadline = self.runtime.now() + max(
                                int((delay or 0) * 1000), min(300000, 5000 * 2 ** previous[0])
                            )
                            self.store.set(
                                "blocked_until", max(self.store.number("blocked_until"), deadline)
                            )
                            raise RetryLater(message, deadline)
                        if status in {401, 403, 404}:
                            raise FatalProviderError(message)
                        raise NeedsReview(message)
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > 180:
                            raise httpx.ReadTimeout("Azure応答の制限時間に達しました。")
                        raw.extend(chunk)
                        if len(raw) > 2 * 1024 * 1024:
                            raise NeedsReview("Azureの応答が上限を超えました。")
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get("status") != "completed":
                raise NeedsReview("Azureの応答が未完了または不正です。")
            text = body.get("output_text")
            if text is None:
                if not isinstance(body.get("output"), list):
                    raise NeedsReview("Azureの出力形式が不正です。")
                text = "\n".join(
                    content["text"]
                    for item in body["output"]
                    if isinstance(item, dict) and item.get("type") == "message"
                    for content in item.get("content", [])
                    if isinstance(content, dict)
                    and content.get("type") == "output_text"
                    and isinstance(content.get("text"), str)
                )
            if not isinstance(text, str) or not text.strip():
                raise NeedsReview("Azureの応答に本文がありません。")
            self.store.db.execute(
                "UPDATE attempts SET status='success',finished_at=?,response=?,usage=? WHERE id=?",
                (self.runtime.now(), text, json.dumps(body.get("usage", {})), attempt_id),
            )
            return text
        except (RetryLater, FatalProviderError, NeedsReview):
            self.store.db.execute(
                "UPDATE attempts SET status='error',finished_at=? WHERE id=? AND status='pending'",
                (self.runtime.now(), attempt_id),
            )
            raise
        except (ValueError, TypeError, KeyError) as exc:
            self.store.db.execute(
                "UPDATE attempts SET status='error',finished_at=?,error=? WHERE id=?",
                (self.runtime.now(), "Azure応答を解析できません", attempt_id),
            )
            raise NeedsReview("Azure応答を解析できません。") from exc
        except httpx.HTTPError as exc:
            message = self.scrub(exc)
            deadline = max(self.runtime.now() + min(300000, 5000 * 2 ** previous[0]), now + 210000)
            self.store.db.execute(
                "UPDATE attempts SET status='unknown',finished_at=?,error=? WHERE id=?",
                (self.runtime.now(), message, attempt_id),
            )
            self.store.set("blocked_until", max(self.store.number("blocked_until"), deadline))
            raise RetryLater(message, deadline) from exc
