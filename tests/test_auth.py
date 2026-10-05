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
    expired = client.get(
        "/api/library", cookies={"mplm_access_token": token({"exp": time.time() - 3600})}
    )
    assert expired.status_code == 401
    assert expired.json()["login_url"] == "https://site.example/login"


def test_unacceptable_tokens_are_rejected_without_a_login_redirect(client):
    for bad in (
        token(secret=b"x" * 40),
        token({"type": "refresh"}),
        token(alg="none"),
        "garbage",
        "e30." * 2 + "e30",
    ):
        refused = client.get("/api/library", cookies={"mplm_access_token": bad})
        assert refused.status_code == 403
        assert "login_url" not in refused.json()
        page = client.get("/", cookies={"mplm_access_token": bad}, headers={"Accept": "text/html"})
        assert page.status_code == 403  # no redirect: logging in again cannot fix it


def test_non_finite_expiry_and_deep_nesting_are_rejected(client):
    for claims in ('{"exp": NaN}', '{"exp": Infinity}'):
        head = b64(b'{"alg":"HS256"}')
        body = b64(claims.encode())
        sig = hmac.new(SECRET, f"{head}.{body}".encode(), hashlib.sha256).digest()
        with pytest.raises(TokenError):
            verify_token(f"{head}.{body}.{b64(sig)}", SECRET)
    deep = b64(b"[" * 100000)
    assert (
        client.get("/api/library", headers={"Authorization": f"Bearer {deep}.x.y"}).status_code
        == 403
    )


def test_a_stale_cookie_does_not_hide_a_valid_bearer_token(client):
    stale = token({"exp": time.time() - 3600})
    response = client.get(
        "/api/library",
        cookies={"mplm_access_token": stale},
        headers={"Authorization": f"Bearer {token()}"},
    )
    assert response.status_code == 200


def test_verified_token_is_reissued_as_a_cross_site_session_cookie(client):
    first = client.get("/api/library", cookies={"mplm_access_token": token()})
    cookie = first.headers["set-cookie"].lower()
    assert "docling_session=" in cookie
    assert "samesite=none" in cookie and "secure" in cookie and "httponly" in cookie
    # The own cookie alone authenticates (sandboxed viewer sub-requests send only it),
    # and is not re-issued every time.
    good = token()
    again = client.get("/api/library", cookies={"docling_session": good})
    assert again.status_code == 200 and "set-cookie" not in again.headers


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


def test_return_parameter_name_and_format_are_configurable(client, monkeypatch):
    monkeypatch.setattr(desk_config, "AUTH_RETURN_PARAM", "redirect")
    monkeypatch.setattr(desk_config, "AUTH_RETURN_FORMAT", "path")
    monkeypatch.setattr(desk_config, "AUTH_LOGIN_URL", "/login")
    monkeypatch.setattr(desk_config, "ROOT_PATH", "/assessment")
    page = client.get("/?mode=wiki&source=a", headers={"Accept": "text/html"})
    assert page.headers["location"] == "/login?redirect=%2Fassessment%2F%3Fmode%3Dwiki%26source%3Da"
    api = client.get("/api/library").json()
    assert (api["login_url"], api["return_param"], api["return_format"]) == (
        "/login",
        "redirect",
        "path",
    )


def test_default_return_parameter_is_an_absolute_next(client):
    page = client.get("/?a=1", headers={"Accept": "text/html"})
    assert page.headers["location"].startswith("https://site.example/login?next=http")
    assert client.get("/api/library").json()["return_format"] == "url"


def test_a_stale_own_cookie_does_not_block_the_login_redirect(client):
    expired = token({"exp": time.time() - 3600})
    response = client.get(
        "/",
        cookies={"mplm_access_token": expired, "docling_session": token(secret=b"x" * 40)},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 302


def test_the_own_cookie_is_short_lived(client):
    long_lived = token({"exp": time.time() + 86400})
    first = client.get("/api/library", cookies={"mplm_access_token": long_lived})
    assert "max-age=900" in first.headers["set-cookie"].lower()
    short = client.get("/api/library", cookies={"mplm_access_token": token()})
    assert "max-age=900" not in short.headers["set-cookie"].lower()  # never beyond the token
