"""Per-feature diagnostics: statuses, independence, secrecy, and no outbound calls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import shutil
import socket
import subprocess
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import docling_desk.app as web
from docling_desk import config as desk_config
from docling_desk.api import diagnostics

SECRET = b"s" * 40
SENTINELS = (
    "sk-sentinel-openai",
    "azure-sentinel-key",
    "embedding-sentinel-key",
    "https://sentinel.openai.azure.com",
    "sentinel-deployment",
)
ENV_NAMES = (
    "DOCLING_TRANSLATION_PROVIDER",
    "DOCLING_AZURE_ENDPOINT",
    "DOCLING_AZURE_DEPLOYMENT",
    "DOCLING_AZURE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "DOCLING_EXPLANATION_WEB",
    "BRAVE_SEARCH_API_KEY",
    "DOCLING_AZURE_OCR_ENDPOINT",
    "AZURE_DOCUMENT_INTELLIGENCE_API_KEY",
    "DOCLING_AZURE_EMBEDDING_ENDPOINT",
    "DOCLING_AZURE_EMBEDDING_KEY",
    "DOCLING_AZURE_EMBEDDING_DEPLOYMENT",
    "DOCLING_AZURE_EMBEDDING_MODEL_VERSION",
    "DOCLING_AZURE_EMBEDDING_DIMENSIONS",
)


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def token() -> str:
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"type": "access", "userId": "u1", "exp": time.time() + 300}).encode())
    signature = hmac.new(SECRET, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(signature)}"


@pytest.fixture
def healthy(monkeypatch, tmp_path):
    """A Linux host where every local prerequisite exists and no provider is set."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    frontend = tmp_path / "resources/static/frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setattr(desk_config, "RESOURCES", tmp_path / "resources")
    monkeypatch.setattr(desk_config, "DATA", data)
    monkeypatch.setattr(desk_config, "MODELS", tmp_path / "models")
    monkeypatch.setattr(desk_config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(diagnostics, "model_diagnosis", lambda *args: [])
    monkeypatch.setattr(diagnostics, "_platform", lambda: "linux")
    monkeypatch.setattr(diagnostics, "_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(diagnostics, "_powerpoint", lambda: True)
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: SimpleNamespace(free=desk_config.MIN_FREE * 2)
    )
    original = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *args: object() if name == "openai_codex" else original(name, *args),
    )
    return tmp_path


def features(**kwargs):
    report = diagnostics.build_report(**kwargs)
    return {item["id"]: item for item in report["features"]}


def status(report):
    return {key: item["status"] for key, item in report.items()}


def test_every_feature_is_reported_with_a_stable_shape(healthy):
    report = diagnostics.build_report()
    assert report["version"] == 1 and report["platform"] == "linux"
    ids = [item["id"] for item in report["features"]]
    assert ids == [
        "local_viewing",
        "conversion",
        "office_presentation",
        "office_documents",
        "tex_preview",
        "semantic_search",
        "translation",
        "explanation",
        "blob_storage",
    ]
    for item in report["features"]:
        assert set(item) == {
            "id",
            "label",
            "description",
            "optional",
            "depends_on",
            "status",
            "reasons",
            "recovery",
            "notes",
            "prerequisites",
        }
        assert item["status"] in {"available", "not_configured", "temporary_failure"}
        for check in item["prerequisites"]:
            assert set(check) == {"id", "label", "required", "status", "code", "recovery"}
            assert check["status"] in {"ok", "not_configured", "temporary_failure", "disabled"}
    by_id = {item["id"]: item for item in report["features"]}
    assert not by_id["local_viewing"]["optional"] and not by_id["conversion"]["optional"]
    assert by_id["office_presentation"]["depends_on"] == ["conversion"]


def test_a_complete_local_setup_leaves_only_cloud_features_unset(healthy):
    found = status(features())
    assert found == {
        "local_viewing": "available",
        "conversion": "available",
        "office_presentation": "available",
        "office_documents": "available",
        "tex_preview": "available",
        "semantic_search": "not_configured",
        "translation": "available",
        "explanation": "available",
        "blob_storage": "not_configured",
    }
    unset = features()["semantic_search"]
    assert unset["reasons"] == ["embedding_not_configured"]
    assert "Wikiと全文検索は使えます" in unset["recovery"][0]


def test_missing_models_stop_conversion_but_not_viewing_or_providers(healthy, monkeypatch):
    models = [{"code": "models_missing", "state": "not_configured", "recovery": "取得してください"}]
    monkeypatch.setattr(diagnostics, "model_diagnosis", lambda *args: models)
    found = features()
    assert found["conversion"]["status"] == "not_configured"
    assert found["conversion"]["reasons"] == ["models_missing"]
    assert found["conversion"]["recovery"] == ["取得してください"]
    for other in ("local_viewing", "translation", "explanation", "tex_preview"):
        assert found[other]["status"] == "available"


def test_an_unreadable_model_manifest_has_its_own_recovery_not_a_generic_error(
    healthy, monkeypatch
):
    def invalid(*args):
        raise ValueError("manifest_invalid")

    monkeypatch.setattr(diagnostics, "model_diagnosis", invalid)
    found = features()
    assert found["conversion"]["status"] == "not_configured"
    assert found["conversion"]["reasons"] == ["models_manifest_invalid"]
    assert "manifest" in found["conversion"]["recovery"][0]
    assert "diagnostic_error" not in json.dumps(found)
    assert found["local_viewing"]["status"] == "available"


def test_an_interrupted_model_swap_is_temporary_and_wins_over_a_missing_model(healthy, monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "model_diagnosis",
        lambda *args: [
            {"code": "models_missing", "state": "not_configured", "recovery": "取得"},
            {"code": "models_interrupted", "state": "temporary_failure", "recovery": "回復"},
        ],
    )
    conversion = features()["conversion"]
    assert conversion["status"] == "temporary_failure"
    assert conversion["reasons"] == ["models_interrupted"]


def test_office_and_tex_tools_are_diagnosed_independently(healthy, monkeypatch):
    monkeypatch.setattr(
        diagnostics, "_which", lambda name: None if name in {"libreoffice", "soffice"} else "x"
    )
    found = features()
    assert found["office_presentation"]["reasons"] == ["libreoffice_missing"]
    assert found["office_documents"]["status"] == "not_configured"
    assert status(found)["tex_preview"] == "available"
    assert found["conversion"]["status"] == "available"
    monkeypatch.setattr(diagnostics, "_which", lambda name: None if name == "tectonic" else "x")
    found = features()
    assert found["tex_preview"]["reasons"] == ["tectonic_missing"]
    assert "ソースは導入なしで閲覧できます" in found["tex_preview"]["recovery"][0]
    assert found["office_presentation"]["status"] == "available"
    monkeypatch.setattr(diagnostics, "_which", lambda name: None if name == "bwrap" else "x")
    assert features()["tex_preview"]["reasons"] == ["sandbox_missing"]


def test_missing_tesseract_only_affects_conversion_on_linux(healthy, monkeypatch):
    monkeypatch.setattr(diagnostics, "_which", lambda name: None if name == "tesseract" else "x")
    found = features()
    assert found["conversion"]["reasons"] == ["tesseract_missing"]
    assert found["local_viewing"]["status"] == "available"


def test_macos_checks_apple_tools_instead_of_linux_ones(healthy, monkeypatch):
    monkeypatch.setattr(diagnostics, "_platform", lambda: "darwin")
    monkeypatch.setattr(diagnostics, "_which", lambda name: None)
    monkeypatch.setattr(diagnostics, "_powerpoint", lambda: False)
    monkeypatch.setattr(diagnostics, "QUICKLOOK", healthy / "missing-qlmanage")
    monkeypatch.setattr(diagnostics, "SANDBOX_EXEC", healthy / "missing-sandbox-exec")
    found = features()
    assert found["conversion"]["status"] == "available"  # Apple OCR needs nothing installed
    assert found["office_presentation"]["reasons"] == ["powerpoint_missing"]
    assert found["office_documents"]["reasons"] == ["quicklook_missing"]
    assert found["tex_preview"]["reasons"] == ["tectonic_missing", "sandbox_missing"]


def test_other_platforms_are_reported_not_crashed(healthy, monkeypatch):
    monkeypatch.setattr(diagnostics, "_platform", lambda: "other")
    found = features()
    assert found["office_documents"]["reasons"] == ["platform_unsupported"]
    assert found["tex_preview"]["reasons"] == ["sandbox_missing"]


def test_low_disk_and_unwritable_data_are_temporary(healthy, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(free=1))
    found = features()
    assert found["local_viewing"]["status"] == "temporary_failure"
    assert found["conversion"]["reasons"] == ["disk_space"]
    assert found["translation"]["status"] == "available"
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: (_ for _ in ()).throw(OSError("unreadable"))
    )
    assert features()["local_viewing"]["reasons"] == ["disk_unreadable"]
    monkeypatch.setattr(desk_config, "DATA", healthy / "absent")
    assert "data_unwritable" in features()["local_viewing"]["reasons"]


def test_missing_frontend_and_incomplete_blob_restore_block_viewing(healthy):
    (healthy / "resources/static/frontend/index.html").unlink()
    runtime = desk_config.DATA / "runtime"
    runtime.mkdir()
    (runtime / "blob-restore.incomplete").write_text("", encoding="utf-8")
    viewing = features()["local_viewing"]
    assert viewing["status"] == "not_configured"
    assert viewing["reasons"] == ["frontend_missing", "blob_restore_incomplete"]


def test_azure_ocr_being_unset_never_blocks_conversion(healthy, monkeypatch):
    conversion = features()["conversion"]
    assert conversion["status"] == "available"
    assert conversion["notes"] == ["azure_ocr_not_configured"]
    monkeypatch.setenv("DOCLING_AZURE_OCR_ENDPOINT", "https://x.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_API_KEY", "k")
    conversion = features()["conversion"]
    assert conversion["notes"] == []


def test_semantic_search_distinguishes_unset_partial_and_complete(healthy, monkeypatch):
    monkeypatch.setenv("DOCLING_AZURE_EMBEDDING_ENDPOINT", "https://e.openai.azure.com")
    partial = features()["semantic_search"]
    assert partial["status"] == "not_configured" and partial["reasons"] == ["embedding_invalid"]
    monkeypatch.setenv("DOCLING_AZURE_EMBEDDING_KEY", "k")
    monkeypatch.setenv("DOCLING_AZURE_EMBEDDING_DEPLOYMENT", "d")
    monkeypatch.setenv("DOCLING_AZURE_EMBEDDING_MODEL_VERSION", "1")
    assert features()["semantic_search"]["status"] == "available"
    assert features()["translation"]["status"] == "available"


def test_translation_and_explanation_fail_separately_from_azure_features(healthy, monkeypatch):
    monkeypatch.delenv("CODEX_HOME")
    monkeypatch.setenv("HOME", str(healthy / "empty-home"))
    found = features()
    for name, prefix in (("translation", "translation"), ("explanation", "explanation")):
        assert found[name]["reasons"] == [f"{prefix}_authentication"]
        assert "OPENAI_API_KEY" in found[name]["recovery"][0]
    assert found["conversion"]["status"] == "available"
    assert found["semantic_search"]["status"] == "not_configured"
    monkeypatch.setenv("OPENAI_API_KEY", SENTINELS[0])
    assert status(features())["translation"] == "available"
    assert status(features())["explanation"] == "available"
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args: None)
    assert features()["translation"]["reasons"] == ["translation_not_configured"]


def test_azure_translation_needs_endpoint_deployment_and_key(healthy, monkeypatch):
    monkeypatch.setenv("DOCLING_TRANSLATION_PROVIDER", "azure_openai")
    incomplete = features()["translation"]
    assert incomplete["reasons"] == ["translation_configuration"]
    assert "DOCLING_AZURE_ENDPOINT" in incomplete["recovery"][0]
    monkeypatch.setenv("DOCLING_AZURE_ENDPOINT", SENTINELS[3])
    monkeypatch.setenv("DOCLING_AZURE_DEPLOYMENT", SENTINELS[4])
    keyless = features()["translation"]
    assert keyless["reasons"] == ["translation_authentication"]
    assert "AZURE_OPENAI_API_KEY" in keyless["recovery"][0]
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SENTINELS[1])
    assert features()["translation"]["status"] == "available"
    # Choosing Azure for translation does not touch explanation or conversion.
    assert features()["explanation"]["status"] == "available"


def test_invalid_numbers_and_unknown_providers_are_configuration_problems(healthy, monkeypatch):
    monkeypatch.setenv("DOCLING_TRANSLATION_TIMEOUT", "soon")
    assert features()["translation"]["reasons"] == ["translation_configuration"]
    monkeypatch.delenv("DOCLING_TRANSLATION_TIMEOUT")
    monkeypatch.setenv("DOCLING_TRANSLATION_PROVIDER", "other")
    assert features()["translation"]["reasons"] == ["translation_configuration"]
    monkeypatch.setenv("DOCLING_EXPLANATION_TIMEOUT", "0")
    explanation = features()["explanation"]
    assert explanation["reasons"] == ["explanation_configuration"]
    assert [c["id"] for c in explanation["prerequisites"]] == ["explanation_provider"]


def test_an_unset_brave_key_is_a_note_not_a_failure(healthy, monkeypatch):
    plain = features()["explanation"]
    assert plain["status"] == "available" and plain["notes"] == []
    monkeypatch.setenv("DOCLING_EXPLANATION_WEB", "brave")
    brave = features()["explanation"]
    assert brave["status"] == "available"
    assert brave["notes"] == ["brave_key_missing"]
    assert brave["reasons"] == [] and brave["recovery"] == []
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "k")
    assert features()["explanation"]["notes"] == []
    monkeypatch.setenv("DOCLING_EXPLANATION_WEB", "disabled")
    disabled = features()["explanation"]
    assert disabled["status"] == "available" and disabled["notes"] == []
    assert disabled["prerequisites"][-1]["status"] == "disabled"


def test_blob_is_unset_by_default_and_reports_sync_state_when_enabled(healthy, monkeypatch):
    blob = features()["blob_storage"]
    assert blob["status"] == "not_configured" and blob["optional"]
    assert blob["reasons"] == ["blob_not_configured"]
    monkeypatch.setattr(desk_config, "STORAGE_BACKEND", "azure-blob")

    def mirror(**state):
        return SimpleNamespace(
            status=lambda: {"conflicts": {}, "errors": [], "restore_incomplete": False, **state}
        )

    assert features(blob_mirror=mirror())["blob_storage"]["status"] == "available"
    assert features(blob_mirror=None)["blob_storage"]["reasons"] == ["blob_mirror_inactive"]
    conflicted = features(blob_mirror=mirror(conflicts={"a": "changed"}))["blob_storage"]
    assert conflicted["status"] == "temporary_failure"
    assert conflicted["reasons"] == ["blob_conflicts"]
    assert features(blob_mirror=mirror(errors=["x"]))["blob_storage"]["reasons"] == [
        "blob_sync_errors"
    ]
    assert features(blob_mirror=mirror(restore_incomplete=True))["blob_storage"]["reasons"] == [
        "blob_restore_incomplete"
    ]
    broken = SimpleNamespace(status=lambda: (_ for _ in ()).throw(OSError("secret path")))
    result = features(blob_mirror=broken)["blob_storage"]
    assert result["reasons"] == ["blob_status_unreadable"]
    assert "secret path" not in json.dumps(result)
    # Without a running server there is no sync state to read; only the setting is checked.
    assert features(runtime=False)["blob_storage"]["status"] == "available"


def test_one_failing_probe_does_not_hide_the_other_features(healthy, monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "tex_preview",
        lambda: (_ for _ in ()).throw(RuntimeError("/private/path/leak")),
    )
    found = features()
    assert found["tex_preview"]["status"] == "temporary_failure"
    assert found["tex_preview"]["reasons"] == ["diagnostic_error"]
    assert "/private/path/leak" not in json.dumps(found)
    assert found["conversion"]["status"] == "available"


def test_diagnosis_makes_no_network_calls_and_starts_no_process(healthy, monkeypatch):
    monkeypatch.setenv("DOCLING_TRANSLATION_PROVIDER", "azure_openai")
    monkeypatch.setenv("DOCLING_AZURE_ENDPOINT", SENTINELS[3])
    monkeypatch.setenv("DOCLING_AZURE_DEPLOYMENT", SENTINELS[4])
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SENTINELS[1])
    monkeypatch.setenv("DOCLING_AZURE_OCR_ENDPOINT", "https://x.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_DOCUMENT_INTELLIGENCE_API_KEY", "k")
    for name, value in (
        ("DOCLING_AZURE_EMBEDDING_ENDPOINT", "https://e.openai.azure.com"),
        ("DOCLING_AZURE_EMBEDDING_KEY", SENTINELS[2]),
        ("DOCLING_AZURE_EMBEDDING_DEPLOYMENT", "d"),
        ("DOCLING_AZURE_EMBEDDING_MODEL_VERSION", "1"),
        ("DOCLING_EXPLANATION_WEB", "brave"),
        ("BRAVE_SEARCH_API_KEY", "k"),
    ):
        monkeypatch.setenv(name, value)

    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostics must stay local")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    found = features()
    assert found["translation"]["status"] == "available"
    assert found["semantic_search"]["status"] == "available"


def test_endpoint_is_behind_the_login_while_health_stays_public(healthy, monkeypatch):
    monkeypatch.setattr(desk_config, "AUTH_MODE", "jwt")
    monkeypatch.setattr(desk_config, "AUTH_JWT_SECRET", SECRET)
    monkeypatch.setattr(desk_config, "AUTH_LOGIN_URL", "")
    client = TestClient(web.app, follow_redirects=False)
    refused = client.get("/api/diagnostics/features")
    assert refused.status_code == 401
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code in {200, 503}
    assert "features" not in ready.json() and "version" not in ready.json()
    allowed = client.get("/api/diagnostics/features", cookies={"mplm_access_token": token()})
    assert allowed.status_code == 200
    assert allowed.headers["cache-control"] == "no-store"
    assert [item["id"] for item in allowed.json()["features"]][:2] == [
        "local_viewing",
        "conversion",
    ]


def test_response_never_contains_credentials_or_internal_paths(healthy, monkeypatch):
    monkeypatch.setenv("DOCLING_TRANSLATION_PROVIDER", "azure_openai")
    monkeypatch.setenv("DOCLING_AZURE_ENDPOINT", SENTINELS[3])
    monkeypatch.setenv("DOCLING_AZURE_DEPLOYMENT", SENTINELS[4])
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SENTINELS[1])
    monkeypatch.setenv("OPENAI_API_KEY", SENTINELS[0])
    monkeypatch.setenv("DOCLING_AZURE_EMBEDDING_ENDPOINT", "https://e.openai.azure.com")
    monkeypatch.setenv("DOCLING_AZURE_EMBEDDING_KEY", SENTINELS[2])
    monkeypatch.setattr(desk_config, "STORAGE_BACKEND", "azure-blob")
    client = TestClient(web.app)
    response = client.get("/api/diagnostics/features")
    assert response.status_code == 200
    text = response.text
    for secret in (*SENTINELS, str(healthy), str(desk_config.DATA), "e.openai.azure.com"):
        assert secret not in text
    assert "/Users/" not in text and "/var/lib" not in text


def test_text_report_lists_hints_for_missing_features(healthy):
    report = diagnostics.build_report(runtime=False)
    text = diagnostics.format_text(report)
    assert "[利用可能] ローカル閲覧・全文検索" in text
    assert "[未設定] 意味検索" in text
    assert "(任意) Azure OCR" in text
