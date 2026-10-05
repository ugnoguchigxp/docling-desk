import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from docling_desk.translation.azure import AzureProvider, retry_after
from docling_desk.translation.provider import (
    Profile,
    TranslationError,
    configured_profile,
    provider_for,
)


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setenv("DOCLING_AZURE_API_KEY", "test-secret")
    return AzureProvider(
        Profile(
            provider="azure_openai",
            profile_id="azure-test",
            model="azure-model",
            deployment="translation-deploy",
            endpoint="https://example.openai.azure.com",
            auth_mode="api_key",
        )
    )


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "docling_desk.translation.azure.httpx.Client",
        lambda **options: original(**options, transport=httpx.MockTransport(handler)),
    )


def answer(rows=None, **extra):
    message = {"content": json.dumps({"translations": rows or [{"id": "a", "text": "日本語"}]})}
    message.update(extra)
    return {"choices": [{"finish_reason": "stop", "message": message}]}


def test_v1_request_uses_deployment_structured_output_and_no_sdk_retries(azure, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url == "https://example.openai.azure.com/openai/v1/chat/completions"
        assert request.headers["api-key"] == "test-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "translation-deploy" and payload["stream"] is False
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert "tools" not in payload
        assert json.loads(payload["messages"][1]["content"])["segments"] == [
            {"id": "a", "text": "English"}
        ]
        return httpx.Response(200, json=answer())

    transport(monkeypatch, handle)
    assert azure.translate("ja", [{"id": "a", "text": "English"}]) == {"a": "日本語"}
    assert len(requests) == 1
    assert "test-secret" not in json.dumps(azure.profile.metadata())


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "authentication", False),
        (403, "authentication", False),
        (404, "model_unavailable", False),
        (400, "configuration", False),
        (429, "rate_limit", True),
        (503, "transient", True),
        (302, "configuration", False),
    ],
)
def test_http_failures_are_classified_and_never_leak_response_body(
    azure, monkeypatch, status, code, retryable
):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"retry-after-ms": "90000", "retry-after": "60"},
            json={"error": {"message": "private-document test-secret", "code": "RateLimitReached"}},
        )

    transport(monkeypatch, handle)
    with pytest.raises(TranslationError) as failure:
        azure.translate("ja", [{"id": "a", "text": "English"}])
    assert failure.value.code == code and failure.value.retryable is retryable
    assert "private-document" not in str(failure.value) and "test-secret" not in str(failure.value)
    if retryable:
        assert failure.value.retry_after == 90
    assert len(calls) == 1


def test_permanent_quota_is_not_retried(azure, monkeypatch):
    transport(
        monkeypatch,
        lambda request: httpx.Response(429, json={"error": {"code": "insufficient_quota"}}),
    )
    with pytest.raises(TranslationError) as error:
        azure.translate("en", [{"id": "a", "text": "日本語"}])
    assert error.value.code == "quota" and not error.value.retryable


@pytest.mark.parametrize(
    "value",
    [
        answer([{"id": "wrong", "text": "日本語"}]),
        answer(tool_calls=[{"id": "tool"}]),
        answer(refusal="refused"),
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        {"choices": []},
    ],
)
def test_bad_incomplete_or_tool_output_is_not_saved(azure, monkeypatch, value):
    transport(monkeypatch, lambda request: httpx.Response(200, json=value))
    with pytest.raises(TranslationError) as error:
        azure.translate("ja", [{"id": "a", "text": "English"}])
    assert error.value.code == "invalid_response"


def test_retry_after_supports_dates_and_invalid_values():
    future = datetime.now(timezone.utc) + timedelta(seconds=90)
    assert 88 <= retry_after(httpx.Headers({"retry-after": format_datetime(future)})) <= 90
    assert retry_after(httpx.Headers({"retry-after": "invalid", "retry-after-ms": "2500"})) == 2.5
    assert retry_after(httpx.Headers({"retry-after": "nan", "retry-after-ms": "-1"})) is None


def test_configuration_and_cancel_fail_before_any_http(azure, monkeypatch):
    calls = []
    transport(monkeypatch, lambda request: calls.append(request))
    for endpoint in [
        "http://localhost",
        "https://user:secret@example.com",
        "https://example.com?key=secret",
        "https://example.com/wrong",
    ]:
        azure.profile = replace(azure.profile, endpoint=endpoint)
        with pytest.raises(TranslationError):
            azure.preflight()
    azure.profile = replace(azure.profile, endpoint="https://example.com/openai/v1/")
    azure.preflight()
    azure.cancel()
    with pytest.raises(TranslationError) as error:
        azure.translate("ja", [{"id": "a", "text": "English"}])
    assert error.value.code == "interrupted" and not calls


def test_configured_azure_does_not_inherit_luna_or_openai_credentials(monkeypatch):
    monkeypatch.setenv("DOCLING_TRANSLATION_PROVIDER", "azure_openai")
    monkeypatch.setenv("DOCLING_AZURE_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("DOCLING_AZURE_DEPLOYMENT", "my-deployment")
    monkeypatch.setenv("OPENAI_API_KEY", "not-an-azure-key")
    monkeypatch.delenv("DOCLING_AZURE_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    profile = configured_profile()
    assert profile.model == "my-deployment" and profile.auth_mode == "api_key"
    with pytest.raises(TranslationError) as error:
        provider_for(profile).preflight()
    assert error.value.code == "authentication"
