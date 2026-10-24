from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import cli
from docling_desk import config as desk_config
from docling_desk.web import auth

TOKEN = "larm-shared-credential-0123456789"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    monkeypatch.setattr(desk_config, "AUTH_MODE", "larm")
    monkeypatch.setattr(desk_config, "LARM_TOKEN", TOKEN)
    monkeypatch.setattr(desk_config, "ALLOWED_HOSTS", ["testserver"])
    auth._keys.clear()
    auth._tickets.clear()
    # The session cookie is Secure, so the browser side of the tests uses https.
    return TestClient(web.app, base_url="https://testserver", follow_redirects=False)


def configured(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Import config in a fresh process with exactly these auth variables."""
    clean = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LARM_", "DOCLING_AUTH", "DOCLING_ALLOWED"))
    }
    script = (
        "import json; from docling_desk import config; "
        "print(json.dumps({'mode': config.AUTH_MODE, 'token': len(config.LARM_TOKEN)}))"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        env={**clean, "DOCLING_ENV_FILE": os.devnull, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_larm_credential_enables_its_login_without_any_other_setting():
    cases = [
        ({"LARM_API_TOKEN": TOKEN}, "larm"),
        ({"LARM_API_TOKEN": "", "LARM_CONTROL_TOKEN": TOKEN}, "larm"),
        ({}, "none"),
        ({"LARM_API_TOKEN": TOKEN, "DOCLING_AUTH_MODE": "none"}, "none"),
    ]
    for env, mode in cases:
        result = configured(env)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["mode"] == mode


def test_unsafe_auth_configurations_refuse_to_start():
    short = configured({"LARM_API_TOKEN": "short"})
    assert short.returncode != 0 and "24文字以上" in short.stderr
    assert "short" not in short.stderr.replace("LARM_API_TOKEN", "")
    exposed = configured({"DOCLING_ALLOWED_HOSTS": "localhost,docs.example.com"})
    assert exposed.returncode != 0 and "docs.example.com" in exposed.stderr
    allowed = configured(
        {"DOCLING_ALLOWED_HOSTS": "docs.example.com", "DOCLING_AUTH_ALLOW_INSECURE": "1"}
    )
    assert allowed.returncode == 0, allowed.stderr
    # With a login in place, a public Host is the expected deployment.
    protected = configured({"DOCLING_ALLOWED_HOSTS": "docs.example.com", "LARM_API_TOKEN": TOKEN})
    assert protected.returncode == 0, protected.stderr


def test_only_the_shared_bearer_is_accepted(client):
    assert client.get("/api/library", headers=BEARER).status_code == 200
    for headers in ({}, {"Authorization": "Bearer wrong-credential-0123456789"}):
        refused = client.get("/api/library", headers=headers)
        assert refused.status_code == 401
        assert refused.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in refused.text
    assert client.get("/health/live").status_code == 200


def test_a_bearer_caller_may_use_its_own_host_and_origin(client):
    gateway = {"Host": "gateway.internal:18790", "Origin": "https://eumenes.example"}
    assert client.get("/api/library", headers={**BEARER, **gateway}).status_code == 200
    assert client.post("/auth/ui-tickets", headers={**BEARER, **gateway}).status_code == 200
    # Without the credential the Host allow-list still applies.
    assert client.get("/api/library", headers=gateway).status_code in {400, 401}


def test_a_forwarded_prefix_is_taken_only_from_a_bearer_caller(client):
    prefixed = {**BEARER, "X-Forwarded-Prefix": "/apps/docling/"}
    ticket = client.post("/auth/ui-tickets", headers=prefixed).json()
    assert ticket["url"].startswith("/apps/docling/auth/exchange?ticket=")
    page = client.get("/", headers={**prefixed, "Accept": "text/html"})
    assert page.status_code == 200
    assert '<meta name="docling-base" content="/apps/docling">' in page.text
    bad = client.get("/api/library", headers={**BEARER, "X-Forwarded-Prefix": "/a/../b"})
    assert bad.status_code == 400
    # A browser session cannot move the app below another prefix.
    login = client.get(client.post("/auth/ui-tickets", headers=BEARER).json()["url"])
    client.cookies.set(auth.LARM_COOKIE, login.cookies[auth.LARM_COOKIE])
    plain = client.get("/", headers={"X-Forwarded-Prefix": "/elsewhere", "Accept": "text/html"})
    assert "/elsewhere" not in plain.text


def test_a_ticket_becomes_a_browser_session_exactly_once(client):
    url = client.post("/auth/ui-tickets", headers=BEARER).json()["url"]
    login = client.get(url + "&next=/?mode=wiki")
    assert login.status_code == 303 and login.headers["location"] == "/?mode=wiki"
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=none" in cookie
    assert TOKEN not in login.headers["set-cookie"]
    assert client.get(url).status_code == 401  # spent
    session = {auth.LARM_COOKIE: login.cookies[auth.LARM_COOKIE]}
    assert client.get("/api/library", cookies=session).status_code == 200
    # A cookie is sent by the browser on its own: updates need a same-site Origin.
    assert client.post("/auth/ui-tickets", cookies=session).status_code == 403
    assert (
        client.post(
            "/auth/ui-tickets", cookies=session, headers={"Origin": "http://testserver"}
        ).status_code
        == 404  # a browser session cannot mint further logins
    )


def test_tickets_expire_and_never_redirect_elsewhere(client, monkeypatch):
    for target in ("//evil.example/x", "https://evil.example/", "/\\evil.example"):
        url = client.post("/auth/ui-tickets", headers=BEARER).json()["url"]
        login = client.get(url + "&next=" + quote(target, safe=""))
        assert login.status_code == 303 and login.headers["location"] == "/"
    url = client.post("/auth/ui-tickets", headers=BEARER).json()["url"]
    later = time.monotonic() + auth.TICKET_SECONDS + 1
    monkeypatch.setattr(auth.time, "monotonic", lambda: later)
    assert client.get(url).status_code == 401
    assert client.get("/auth/exchange?ticket=made-up").status_code == 401


def test_a_new_credential_ends_existing_sessions(client, monkeypatch):
    login = client.get(client.post("/auth/ui-tickets", headers=BEARER).json()["url"])
    session = {auth.LARM_COOKIE: login.cookies[auth.LARM_COOKIE]}
    monkeypatch.setattr(desk_config, "LARM_TOKEN", "rotated-larm-credential-987654321")
    assert client.get("/api/library", cookies=session).status_code == 401


def test_the_session_slides_while_used_but_not_on_every_request(client, monkeypatch):
    fresh = auth._session_value(int(time.time()) + auth.LARM_SESSION_SECONDS)
    quiet = client.get("/api/library", cookies={auth.LARM_COOKIE: fresh})
    assert quiet.status_code == 200 and "set-cookie" not in quiet.headers
    aging = auth._session_value(int(time.time()) + auth.LARM_SESSION_SECONDS // 2)
    renewed = client.get("/api/library", cookies={auth.LARM_COOKIE: aging})
    assert "max-age=1209600" in renewed.headers["set-cookie"].lower()
    expired = auth._session_value(int(time.time()) - 1)
    assert client.get("/api/library", cookies={auth.LARM_COOKIE: expired}).status_code == 401
    forged = fresh[:-2] + ("AA" if not fresh.endswith("AA") else "BB")
    assert client.get("/api/library", cookies={auth.LARM_COOKIE: forged}).status_code == 401


def test_the_session_key_is_private_and_stable(client, tmp_path):
    first = auth._local_secret()
    key = tmp_path / "runtime" / "keys" / "session.key"
    assert key.stat().st_mode & 0o777 == 0o600
    auth._keys.clear()
    assert auth._local_secret() == first


def test_tickets_exist_only_in_larm_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    plain = TestClient(web.app)
    assert plain.post("/auth/ui-tickets").status_code == 404
    assert plain.get("/auth/exchange?ticket=x").status_code == 404


def test_app_pages_refuse_foreign_frames_but_viewers_keep_their_sandbox(client):
    for path in ("/", "/api/library"):
        response = client.get(path, headers=BEARER)
        assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "SAMEORIGIN"
    view = client.get("/view/" + "0" * 32 + "/pages/1", headers=BEARER)
    assert "frame-ancestors" not in view.headers.get("content-security-policy", "")


def test_the_terminal_shows_a_one_time_login(monkeypatch, capsys, client):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    cli.announce_auth("0.0.0.0", 28765)
    shown = capsys.readouterr()
    assert TOKEN not in shown.out + shown.err
    url = shown.out.split(": ", 1)[1].strip()
    assert url.startswith("http://127.0.0.1:28765/auth/exchange?ticket=")
    assert client.get(url.removeprefix("http://127.0.0.1:28765")).status_code == 303


def test_a_present_but_unused_credential_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(desk_config, "AUTH_MODE", "none")
    monkeypatch.setattr(desk_config, "AUTH_MODE_EXPLICIT", True)
    monkeypatch.setattr(desk_config, "LARM_TOKEN", TOKEN)
    cli.announce_auth("127.0.0.1", 28765)
    assert "使用しません" in capsys.readouterr().err


def test_diagnostics_name_the_login_without_the_credential(client):
    from docling_desk.api.diagnostics import build_report, format_text

    report = build_report(runtime=False)
    assert report["auth"] == {"mode": "larm", "explicit": False, "larm_credential": True}
    assert TOKEN not in json.dumps(report, ensure_ascii=False) + format_text(report)
    assert format_text(report).startswith("認証: LARM_API_TOKEN")


def test_a_ticket_issued_below_a_prefix_keeps_the_browser_there(client):
    prefixed = {**BEARER, "X-Forwarded-Prefix": "/apps/docling"}
    url = client.post("/auth/ui-tickets", headers=prefixed).json()["url"]
    # The gateway strips the prefix; the browser itself carries no Bearer.
    login = client.get(url.removeprefix("/apps/docling") + "&next=/?mode=wiki")
    assert login.headers["location"] == "/apps/docling/?mode=wiki"
    assert "path=/apps/docling" in login.headers["set-cookie"].lower()
    session = {auth.LARM_COOKIE: login.cookies[auth.LARM_COOKIE]}
    page = client.get("/", cookies=session, headers={"Accept": "text/html"})
    assert '<meta name="docling-base" content="/apps/docling">' in page.text


def test_malformed_session_cookies_are_refused_without_errors(client):
    good = auth._session_value(int(time.time()) + 60)
    for value in (
        "v1.99.\xe9\xe9",
        "v2.\xb2.." + good.rsplit(".", 1)[1],
        "v2." + "9" * 5000 + "..x",
        "v2.99.@@.x",
        good.replace("v2.", "v1.", 1),
        "v2.99." + auth._b64encode(b"/a/../b") + ".x",
    ):
        assert auth.session_expiry(value) is None
    assert auth.session_expiry(good) == (int(good.split(".")[1]), "")


def test_an_unauthenticated_server_listens_only_on_loopback(monkeypatch, capsys):
    monkeypatch.setattr(desk_config, "AUTH_MODE", "none")
    monkeypatch.setattr(desk_config, "AUTH_ALLOW_INSECURE", False)
    monkeypatch.delenv("DOCLING_CONTAINER", raising=False)
    assert cli.exposed_without_login("0.0.0.0") is True
    assert cli.exposed_without_login("127.0.0.1") is False
    assert cli.main(["--host", "0.0.0.0"]) == 2
    assert "0.0.0.0" in capsys.readouterr().err
    monkeypatch.setenv("DOCLING_CONTAINER", "1")
    assert cli.exposed_without_login("0.0.0.0") is False
    monkeypatch.delenv("DOCLING_CONTAINER")
    monkeypatch.setattr(desk_config, "AUTH_MODE", "larm")
    assert cli.exposed_without_login("0.0.0.0") is False


def test_skipped_migration_is_reported_by_readiness(client):
    web.app.state.migration_skipped = ["a" * 32]
    try:
        body = client.get("/health/ready").json()
    finally:
        web.app.state.migration_skipped = []
    assert body["warnings"] == ["migration_skipped:1"]
