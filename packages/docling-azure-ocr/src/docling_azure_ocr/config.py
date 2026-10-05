from __future__ import annotations

import importlib.util
import os
from typing import Literal, Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

DEFAULT_PROVIDER = "local"
API_VERSION = "2024-11-30"
MODEL = "prebuilt-read"
DEFAULT_PROFILE = "read-v1"
DEFAULT_TIER = "F0"
DOCUMENT_TIMEOUT = 1800
MAX_SUBMISSIONS = 100


class OcrError(RuntimeError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


class OcrProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["local", "azure_read", "disabled"] = DEFAULT_PROVIDER
    enabled: bool = False
    endpoint: str = ""
    auth: Literal["api_key", "managed_identity"] = "managed_identity"
    client_id: str | None = None
    api_version: Literal["2024-11-30"] = API_VERSION
    model: Literal["prebuilt-read"] = MODEL
    profile: Literal["read-v1", "read-full-page-v1"] = DEFAULT_PROFILE
    tier: Literal["F0", "S0"] = DEFAULT_TIER
    document_timeout: int = Field(default=DOCUMENT_TIMEOUT, ge=180, le=7200)
    max_submissions: int = Field(default=MAX_SUBMISSIONS, ge=1, le=1000)
    adapter_version: Literal["1"] = "1"

    @model_validator(mode="after")
    def endpoint_is_safe(self):
        if self.provider != "azure_read":
            return self
        if not self.enabled:
            raise ValueError("Azure OCRのエンドポイントが未設定です。")
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".cognitiveservices.azure.com")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Document IntelligenceのHTTPSエンドポイントが必要です。")
        return self

    def check_credentials(self, key: SecretStr | None):
        if self.provider != "azure_read":
            return
        if self.auth == "api_key" and (key is None or not key.get_secret_value()):
            raise OcrError("ocr_auth_missing", "Document IntelligenceのAPIキーが未設定です。")
        if self.auth == "managed_identity":
            try:
                available = importlib.util.find_spec("azure.identity") is not None
            except ModuleNotFoundError:
                available = False
            if not available:
                raise OcrError("ocr_auth_missing", "マネージドID用のazure-identityが必要です。")


def load_profile(provider: str | None = None, env: Mapping[str, str] | None = None) -> OcrProfile:
    values = os.environ if env is None else env
    endpoint = values.get("DOCLING_AZURE_OCR_ENDPOINT", "").strip().rstrip("/")
    # Connection settings enable the option; only an explicit job selection uses Azure.
    # A key selects API-key auth. Without a key, Azure VMs use ManagedIdentityCredential.
    return OcrProfile.model_validate(
        {
            "provider": provider or DEFAULT_PROVIDER,
            "enabled": bool(endpoint),
            "endpoint": endpoint,
            "auth": "api_key"
            if values.get("AZURE_DOCUMENT_INTELLIGENCE_API_KEY")
            else "managed_identity",
            "client_id": values.get("DOCLING_AZURE_OCR_CLIENT_ID") or None,
        }
    )


def api_key() -> SecretStr:
    return SecretStr(os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_API_KEY", ""))
