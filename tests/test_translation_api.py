from conftest import wait_translation
from fastapi.testclient import TestClient
from lxml import html

import docling_desk.app as web
import docling_desk.translation.service as translation_service
from docling_desk import config as desk_config
from docling_desk.storage import document_folder, original_file


def test_translation_api_persists_switches_and_downloads_without_provider_calls(
    translation_document, fixed_provider, monkeypatch
):
    folder = translation_document()
    monkeypatch.setattr(desk_config, "DATA", folder.parent)
    monkeypatch.setattr(translation_service, "provider_for", lambda profile: fixed_provider)
    with TestClient(web.app) as client:
        folder = document_folder(desk_config.DATA, folder.name)
        url = f"/api/jobs/{folder.name}/translations"
        assert client.get(url).status_code == 200
        assert (
            client.post(url, json={"target_language": "en", "unit_ids": ["slide-1"]}).status_code
            == 202
        )
        saved = wait_translation(folder, "en", "slide-1")
        assert saved["state"] == "completed"
        calls = len(fixed_provider.calls)
        translated = client.get(f"/view/{folder.name}/slides/1?language=en")
        assert translated.status_code == 200 and "EN " in translated.text
        assert translated.headers["x-translation-state"] == "translated"
        assert "sandbox" in translated.headers["content-security-policy"]
        assert client.get(f"/view/{folder.name}/slides/1?language=original").status_code == 200
        assert len(fixed_provider.calls) == calls
        download = client.get(f"{url}/en/slide-1?download=true")
        assert "attachment" in download.headers["content-disposition"]
        assert download.json()["available"] is True
        assert (
            client.post(
                url, json={"target_language": "en", "endpoint": "https://example.com"}
            ).status_code
            == 422
        )
        assert (
            client.post(url, json={"target_language": "en", "unit_ids": ["../slide-1"]}).status_code
            == 422
        )
        assert client.post(url, json={"target_language": "fr"}).status_code == 422
        assert client.get(f"/view/{folder.name}/slides/1?language=fr").status_code == 422


def test_pdf_uses_translatable_page_html_and_unchanged_original(
    translation_document, fixed_provider, monkeypatch
):
    folder = translation_document("page")
    original = original_file(folder, ".pdf").read_bytes()
    monkeypatch.setattr(desk_config, "DATA", folder.parent)
    monkeypatch.setattr(translation_service, "provider_for", lambda profile: fixed_provider)
    with TestClient(web.app) as client:
        folder = document_folder(desk_config.DATA, folder.name)
        url = f"/api/jobs/{folder.name}/translations"
        assert (
            client.post(url, json={"target_language": "ja", "unit_ids": ["page-2"]}).status_code
            == 202
        )
        assert wait_translation(folder, "ja", "page-2")["state"] == "completed"
        result = client.get(f"{url}/ja/page-2").json()
        assert result["mode"] == "replace" and result["available"]
        page = client.get(f"/view/{folder.name}/pages/2?language=ja")
        assert page.status_code == 200 and "JA " in page.text
        assert page.headers["x-translation-state"] == "translated"
        native = client.get(f"/view/{folder.name}/pages/2").text
        assert html.fromstring(page.text).xpath("//path/@d") == html.fromstring(native).xpath(
            "//path/@d"
        )
        assert original_file(folder, ".pdf").read_bytes() == original


def test_untranslated_sheet_falls_back_without_changing_structure(
    translation_document, monkeypatch
):
    folder = translation_document("sheet")
    monkeypatch.setattr(desk_config, "DATA", folder.parent)
    with TestClient(web.app) as client:
        folder = document_folder(desk_config.DATA, folder.name)
        first = client.get(f"/view/{folder.name}/sheets/1").text
        second = client.get(f"/view/{folder.name}/sheets/1?language=en")
        assert second.status_code == 200
        assert [(n.tag, dict(n.attrib)) for n in html.fromstring(first).iter()] == [
            (n.tag, dict(n.attrib)) for n in html.fromstring(second.text).iter()
        ]


def test_azure_not_configured_is_explicit_and_does_not_fallback(translation_document, monkeypatch):
    folder = translation_document()
    monkeypatch.setattr(desk_config, "DATA", folder.parent)
    monkeypatch.setenv("DOCLING_TRANSLATION_PROVIDER", "azure_openai")
    with TestClient(web.app) as client:
        folder = document_folder(desk_config.DATA, folder.name)
        response = client.post(
            f"/api/jobs/{folder.name}/translations", json={"target_language": "en"}
        )
        assert response.status_code == 503 and "Azure" in response.text
        assert not list(folder.glob("translations/*/*.json"))


def test_translation_interval_is_validated_and_saved(
    translation_document, fixed_provider, monkeypatch
):
    from docling_desk.translation.store import read_result

    folder = translation_document()
    monkeypatch.setattr(desk_config, "DATA", folder.parent)
    monkeypatch.setattr(translation_service, "provider_for", lambda profile: fixed_provider)
    with TestClient(web.app) as client:
        folder = document_folder(desk_config.DATA, folder.name)
        url = f"/api/jobs/{folder.name}/translations"
        for value in [-1, 3601, True, 0.5, "60"]:
            assert (
                client.post(
                    url, json={"target_language": "ja", "interval_seconds": value}
                ).status_code
                == 422
            )
        response = client.post(
            url,
            json={
                "target_language": "ja",
                "unit_ids": ["slide-1"],
                "interval_seconds": 60,
            },
        )
        assert response.status_code == 202
        assert wait_translation(folder, "ja", "slide-1")["state"] == "completed"
        assert read_result(folder, "ja", "slide-1")["schedule"]["interval_seconds"] == 60
        assert client.get(url).json()["units"][0]["languages"]["ja"]["interval_seconds"] == 60
