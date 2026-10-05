from dataclasses import replace

import pytest

from docling_desk.translation.provider import (
    Profile,
    TranslationError,
    configured_profile,
    provider_for,
    validate_response,
)


def test_response_contract_rejects_missing_duplicate_and_extra_ids():
    assert validate_response({"translations": [{"id": "a", "text": "word"}]}, {"a"}) == {
        "a": "word"
    }
    for value in [
        {"translations": []},
        {"translations": [{"id": "a", "text": "word"}] * 2},
        {"translations": [{"id": "b", "text": "word"}]},
        {"translations": [{"id": "a", "text": ""}]},
    ]:
        with pytest.raises(TranslationError):
            validate_response(value, {"a"})


def test_configuration_hash_changes_for_azure_model_and_deployment(monkeypatch):
    initial = Profile()
    azure = replace(
        initial,
        provider="azure_openai",
        model="azure-model",
        deployment="translation-deployment",
        endpoint="https://example.invalid",
    )
    assert initial.metadata()["config_hash"] != azure.metadata()["config_hash"]
    assert "endpoint" not in azure.metadata()
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    assert initial.metadata()["config_hash"] == Profile().metadata()["config_hash"]
    monkeypatch.setenv("DOCLING_TRANSLATION_PROVIDER", "azure_openai")
    with pytest.raises(TranslationError, match="Azure"):
        provider_for(configured_profile()).preflight()


def test_invalid_settings_are_reported(monkeypatch):
    monkeypatch.setenv("DOCLING_TRANSLATION_TIMEOUT", "wrong")
    with pytest.raises(TranslationError):
        configured_profile()
