import re

from fastapi.testclient import TestClient

import docling_desk.app as app
import docling_desk.web.frontend as delivery


def test_react_bundle_and_legacy_routes():
    client = TestClient(app.app)
    for route in ["/", "/ui/"]:
        response = client.get(route)
        assert response.status_code == 200
        assert "/static/frontend/assets/" in response.text
        assert "/static/app.js" not in response.text
        assert response.headers["cache-control"] == "no-cache"
        assert "script-src 'self'" in response.headers["content-security-policy"]
    match = re.search(r'src="(/static/frontend/assets/[^"]+)"', response.text)
    assert match is not None
    asset = match.group(1)
    assert client.get(asset).headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "ag-grid-react" in client.get("/static/frontend/licenses.txt").text
    legacy = client.get("/legacy/")
    assert "/static/app.js" in legacy.text
    assert "/static/library.js" in legacy.text


def test_explicit_ui_rollback(monkeypatch):
    monkeypatch.setenv("DOCLING_LEGACY_UI", "1")
    client = TestClient(app.app)
    assert "/static/app.js" in client.get("/").text
    assert "/static/frontend/assets/" in client.get("/ui/").text


def test_asset_escape_missing_bundle_and_unknown_extensions(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "BUNDLE", tmp_path / "bundle")
    delivery.BUNDLE.mkdir()
    (tmp_path / "outside.js").write_text("private")
    (delivery.BUNDLE / "private.json").write_text("{}")
    (delivery.BUNDLE / "escape.js").symlink_to(tmp_path / "outside.js")
    client = TestClient(app.app)
    assert client.get("/ui/").status_code == 503
    for path in ["../outside.js", "private.json", "missing.js", "escape.js"]:
        assert client.get("/static/frontend/" + path.replace("../", "%2E%2E/")).status_code == 404


def test_frontend_keeps_existing_origin_check():
    client = TestClient(app.app)
    response = client.post(
        "/api/folders", headers={"origin": "http://evil.example"}, json={"name": "no"}
    )
    assert response.status_code == 403


def test_print_shell_is_passive_and_does_not_relax_the_application_policy():
    client = TestClient(app.app)
    shell = client.get("/static/frontend/print.html")
    assert shell.status_code == 200
    assert "<script" not in shell.text
    policy = shell.headers["content-security-policy"]
    assert "script-src 'none'" in policy
    assert "sandbox allow-same-origin allow-modals" in policy
    assert "font-src 'self' data:" in policy
    assert "allow-same-origin" not in client.get("/").headers["content-security-policy"]
    assert "style-src 'self';" in client.get("/").headers["content-security-policy"]
