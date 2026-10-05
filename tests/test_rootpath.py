from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import config as desk_config
from docling_desk.web.rootpath import prefix_text


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    monkeypatch.setattr(desk_config, "ROOT_PATH", "/assessment")
    return TestClient(web.app, follow_redirects=False)


def test_prefix_text_targets_only_app_paths():
    html = (
        '<link href="/static/a.css"><img data-url="/api/jobs/1/x"><a href="https://x.test/static/">'
    )
    out = prefix_text(html, "/assessment")
    assert 'href="/assessment/static/a.css"' in out
    assert 'data-url="/assessment/api/jobs/1/x"' in out
    assert "https://x.test/static/" in out
    css = prefix_text("a{background:url(/static/x.png)}", "/assessment")
    assert css == "a{background:url(/assessment/static/x.png)}"
    bundle = prefix_text(
        'import("/static/frontend/assets/a.js");fetch("/api/x")', "/assessment", bundle=True
    )
    assert bundle == 'import("/assessment/static/frontend/assets/a.js");fetch("/api/x")'


def test_index_publishes_the_prefix_and_prefixes_assets(client):
    page = client.get("/").text
    assert '<meta name="docling-base" content="/assessment">' in page
    assert 'src="/assessment/static/frontend/assets/' in page
    assert 'href="/static/' not in page


def test_static_scripts_call_prefixed_endpoints(client):
    script = client.get("/static/slides.js").text
    assert "/assessment/api/jobs/" in script
    assert "`/api/jobs/" not in script


def test_vendor_files_are_not_rewritten(client, monkeypatch):
    plain = TestClient(web.app).get("/static/vendor/ag-grid-community.min.js").content
    assert client.get("/static/vendor/ag-grid-community.min.js").content == plain


def test_no_prefix_means_no_change(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_config, "DATA", tmp_path)
    page = TestClient(web.app).get("/").text
    assert "docling-base" not in page


def test_login_redirect_returns_to_the_prefixed_address(client, monkeypatch):
    monkeypatch.setattr(desk_config, "AUTH_MODE", "jwt")
    monkeypatch.setattr(desk_config, "AUTH_JWT_SECRET", b"s" * 40)
    monkeypatch.setattr(desk_config, "AUTH_LOGIN_URL", "https://site.example/login")
    response = client.get("/?mode=wiki", headers={"Accept": "text/html"})
    assert response.status_code == 302
    assert "%2Fassessment%2F%3Fmode%3Dwiki" in response.headers["location"]


def test_document_text_is_not_rewritten_but_urls_are():
    html = (
        "<p>call \"/api/users\" or '/files/x' in code</p>"
        '<img src="/files/a/b.png"><script src="/static/x.js"></script>'
        "<style>a{background:url(/static/y.png)}</style>"
    )
    out = prefix_text(html, "/assessment", kind="text/html")
    assert '"/api/users"' in out and "'/files/x'" in out
    assert 'src="/assessment/files/a/b.png"' in out
    assert 'src="/assessment/static/x.js"' in out
    assert "url(/assessment/static/y.png)" in out


def test_partial_responses_and_validators_are_handled(client):
    ranged = client.get("/static/slides.js", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100
    full = client.get("/static/slides.js")
    assert "etag" not in full.headers
    assert int(full.headers["content-length"]) == len(full.content)
