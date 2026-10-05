from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import config as desk_config
from docling_desk.web.auth import TokenError, verify_token

SECRET = b"s" * 40


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def token(claims=None, *, secret=SECRET, alg="HS256"):
    claims = {"type": "access", "userId": "u1", "exp": time.time() + 300, **(claims or {})}
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = hmac.new(secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    monkeypatch.setattr(desk_config, "AUTH_MODE", "jwt")
    monkeypatch.setattr(desk_config, "AUTH_JWT_SECRET", SECRET)
    monkeypatch.setattr(desk_config, "AUTH_LOGIN_URL", "https://site.example/login")
    return TestClient(web.app, follow_redirects=False)


def test_default_mode_needs_no_login(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    assert TestClient(web.app).get("/api/library").status_code == 200


def test_requests_without_a_valid_token_are_refused(client):
    refused = client.get("/api/library")
    assert refused.status_code == 401
    assert refused.json()["login_url"] == "https://site.example/login"
    page = client.get("/", headers={"Accept": "text/html"})
    assert page.status_code == 302
    assert page.headers["location"].startswith("https://site.example/login?next=")
    for bad in (
        token(secret=b"x" * 40),
        token({"exp": time.time() - 3600}),
        token({"type": "refresh"}),
        token(alg="none"),
        "garbage",
    ):
        assert client.get("/api/library", cookies={"mplm_access_token": bad}).status_code == 401


def test_cookie_and_bearer_tokens_are_accepted(client):
    good = token()
    assert client.get("/api/library", cookies={"mplm_access_token": good}).status_code == 200
    assert (
        client.get("/api/library", headers={"Authorization": f"Bearer {good}"}).status_code == 200
    )


def test_health_and_static_stay_public(client):
    assert client.get("/health/live").status_code == 200


def test_verify_token_checks_issuer_and_audience():
    good = token({"iss": "site", "aud": ["a", "b"]})
    assert verify_token(good, SECRET, issuer="site", audience="b")["userId"] == "u1"
    with pytest.raises(TokenError):
        verify_token(good, SECRET, issuer="other")
    with pytest.raises(TokenError):
        verify_token(good, SECRET, audience="c")
    with pytest.raises(TokenError):
        verify_token(token({"exp": None}), SECRET)
