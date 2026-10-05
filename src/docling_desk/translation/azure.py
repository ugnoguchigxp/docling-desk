"""Azure v1 HTTP adapter; pacing and retries belong to the shared translation queue."""

from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic
from urllib.parse import urlsplit

import httpx

from docling_desk.translation.provider import (
    INSTRUCTIONS,
    Profile,
    TranslationError,
    validate_response,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


def retry_after(headers: httpx.Headers) -> float | None:
    delays = []
    for key, scale in (
        ("retry-after-ms", 0.001),
        ("x-ms-retry-after-ms", 0.001),
        ("retry-after", 1),
    ):
        value = headers.get(key)
        if value is None:
            continue
        try:
            delay = float(value) * scale
        except ValueError:
            if key != "retry-after":
                continue
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                delay = (stamp - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(delay) and delay >= 0:
            delays.append(delay)
    return max(delays) if delays else None


class AzureProvider:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.cancelled = threading.Event()
        # Capture the selected credentials at acceptance; never persist them.
        self.key = (
            os.environ.get("DOCLING_AZURE_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY") or ""
        )

    def cancel(self) -> None:
        self.cancelled.set()

    def preflight(self) -> None:
        try:
            endpoint = urlsplit(self.profile.endpoint)
        except ValueError as exc:
            raise TranslationError("configuration", "Azureの接続先URLが不正です。") from exc
        if (
            self.profile.api_mode != "v1"
            or self.profile.auth_mode != "api_key"
            or endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or endpoint.path.rstrip("/") not in {"", "/openai/v1"}
            or not self.profile.deployment
        ):
            raise TranslationError(
                "configuration",
                "AzureのHTTPS接続先・デプロイ名・v1/APIキー設定を確認してください。",
            )
        if not self.key:
            raise TranslationError(
                "authentication",
                "DOCLING_AZURE_API_KEYまたはAZURE_OPENAI_API_KEYを設定してください。",
            )

    def translate(self, language: str, segments: list[dict[str, str]]) -> dict[str, str]:
        self.preflight()
        if self.cancelled.is_set():
            raise TranslationError("interrupted", "翻訳処理を中断しました。")
        endpoint = self.profile.endpoint.rstrip("/")
        if not endpoint.endswith("/openai/v1"):
            endpoint += "/openai/v1"
        payload = {
            "model": self.profile.deployment,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"target_language": language, "segments": segments}, ensure_ascii=False
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "translation", "strict": True, "schema": SCHEMA},
            },
            "stream": False,
        }
        deadline = monotonic() + self.profile.timeout
        try:
            # httpx does not automatically retry or follow redirects. Avoid leaking
            # credentials through redirects or including response bodies in errors.
            with httpx.Client(timeout=self.profile.timeout, follow_redirects=False) as client:
                with client.stream(
                    "POST",
                    endpoint + "/chat/completions",
                    headers={"api-key": self.key},
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        self._error(response)
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if self.cancelled.is_set():
                            raise TranslationError("interrupted", "翻訳処理を中断しました。")
                        if monotonic() > deadline:
                            raise TranslationError("timeout", "Azure翻訳がタイムアウトしました。")
                        raw.extend(chunk)
                        if len(raw) > self.profile.max_output_chars * 8 + 16384:
                            raise TranslationError(
                                "invalid_response", "Azureの応答が上限を超えました。"
                            )
            value = json.loads(raw)
            choice = value["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") != "stop" or message.get("refusal"):
                raise TranslationError(
                    "invalid_response", "Azureの翻訳結果が未完了または拒否されました。"
                )
            if message.get("tool_calls") or message.get("function_call"):
                raise TranslationError(
                    "invalid_response", "Azureの翻訳結果にツール実行が含まれています。"
                )
            content = message["content"]
            if not isinstance(content, str) or len(content) > self.profile.max_output_chars:
                raise TranslationError(
                    "invalid_response", "Azureの翻訳結果の形式または長さが不正です。"
                )
            return validate_response(json.loads(content), {s["id"] for s in segments})
        except httpx.TimeoutException as exc:
            raise TranslationError("timeout", "Azure翻訳がタイムアウトしました。") from exc
        except httpx.HTTPError as exc:
            raise TranslationError("transient", "Azure翻訳との通信に失敗しました。", True) from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise TranslationError("invalid_response", "Azureの翻訳結果を読み取れません。") from exc

    @staticmethod
    def _error(response: httpx.Response) -> None:
        status = response.status_code
        delay = retry_after(response.headers)
        if status in {401, 403}:
            raise TranslationError(
                "authentication", "AzureのAPIキーとアクセス権を確認してください。"
            )
        if status == 429:
            # Read only a bounded error body to distinguish permanent quota failures.
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 16384:
                    break
            try:
                code = json.loads(raw).get("error", {}).get("code", "")
            except (ValueError, AttributeError):
                code = ""
            if isinstance(code, str) and code in {
                "insufficient_quota",
                "quota_exceeded",
                "billing_hard_limit_reached",
            }:
                raise TranslationError("quota", "Azureの利用枠または課金設定を確認してください。")
            raise TranslationError("rate_limit", "Azureのレート制限に達しました。", True, delay)
        if status in {408, 500, 502, 503, 504}:
            raise TranslationError("transient", "Azure翻訳が一時的に利用できません。", True, delay)
        if status == 404:
            raise TranslationError(
                "model_unavailable", "Azureのデプロイ名と接続先を確認してください。"
            )
        raise TranslationError("configuration", "Azureの翻訳リクエスト設定を確認してください。")
