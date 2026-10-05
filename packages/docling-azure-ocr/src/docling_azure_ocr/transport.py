from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import SecretStr

from .config import OcrError, OcrProfile


def retry_after(value: str | None) -> float:
    if not value:
        return 2.0
    try:
        return max(1.0, float(value))
    except ValueError:
        try:
            return max(
                1.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            )
        except (ValueError, TypeError, OverflowError):
            raise OcrError("ocr_invalid_retry_after") from None


class AzureTransport:
    def __init__(self, profile: OcrProfile, key: SecretStr, client: httpx.Client | None = None):
        profile.check_credentials(key)
        self.profile, self.key = profile, key
        self.client = client or httpx.Client(follow_redirects=False, timeout=15)
        self.credential = None

    def close(self):
        self.client.close()
        if self.credential:
            self.credential.close()

    def headers(self) -> dict[str, str]:
        if not self.profile.enabled:
            raise OcrError("ocr_disabled")
        if self.profile.auth == "api_key":
            return {"Ocp-Apim-Subscription-Key": self.key.get_secret_value()}
        if self.credential is None:
            from azure.identity import ManagedIdentityCredential

            self.credential = ManagedIdentityCredential(client_id=self.profile.client_id)
        try:
            token = self.credential.get_token("https://cognitiveservices.azure.com/.default")
        except Exception:
            raise OcrError("ocr_auth_failed") from None
        return {"Authorization": "Bearer " + token.token}

    def check_operation(self, url: str) -> str:
        target, configured = urlsplit(url), urlsplit(self.profile.endpoint)
        path = f"/documentintelligence/documentModels/{self.profile.model}/analyzeResults/"
        suffix = target.path.removeprefix(path)
        from uuid import UUID

        try:
            UUID(suffix)
        except ValueError:
            raise OcrError("ocr_invalid_operation") from None
        if (
            target.scheme != "https"
            or target.hostname != configured.hostname
            or target.port not in (None, 443)
            or target.username
            or target.password
            or target.fragment
            or not target.path.startswith(path)
            or parse_qs(target.query) != {"api-version": [self.profile.api_version]}
        ):
            raise OcrError("ocr_invalid_operation")
        return url

    def submit(self, png: bytes, timeout: float) -> tuple[str, str | None]:
        # Obtain credentials before entering the uncertain POST interval.
        headers = {**self.headers(), "Content-Type": "image/png"}
        url = (
            self.profile.endpoint.rstrip("/")
            + f"/documentintelligence/documentModels/{self.profile.model}:analyze"
            + f"?api-version={self.profile.api_version}"
        )
        try:
            response = self.client.post(
                url, content=png, headers=headers, timeout=min(15, timeout), follow_redirects=False
            )
        except httpx.HTTPError:
            raise OcrError("submission_unknown") from None
        if response.status_code == 202:
            try:
                operation = self.check_operation(response.headers.get("Operation-Location", ""))
            except OcrError:
                raise OcrError("submission_unknown") from None
            return operation, response.headers.get("apim-request-id")
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            raise OcrError("submission_unknown")
        raise OcrError(f"ocr_http_{response.status_code}")

    def poll(self, url: str, timeout: float) -> tuple[dict | None, float]:
        self.check_operation(url)
        try:
            with self.client.stream(
                "GET", url, headers=self.headers(), timeout=min(15, timeout), follow_redirects=False
            ) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    return None, retry_after(response.headers.get("Retry-After"))
                if response.status_code != 200:
                    raise OcrError(f"ocr_http_{response.status_code}")
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > 32 * 1024**2:
                        raise OcrError("ocr_response_too_large")
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise OcrError("ocr_invalid_response") from None
                if not isinstance(value, dict):
                    raise OcrError("ocr_invalid_response")
                return value, retry_after(response.headers.get("Retry-After"))
        except httpx.HTTPError:
            return None, 2.0
