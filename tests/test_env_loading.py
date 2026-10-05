from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def probe(tmp_path: Path, content: str | None, overrides: dict | None = None):
    project = tmp_path / "project with spaces"
    project.mkdir()
    package = project / "src/docling_desk"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'docling-desk'\n")
    (package / "__init__.py").write_text("")
    shutil.copyfile(ROOT / "src/docling_desk/config.py", package / "config.py")
    if content is not None:
        (project / ".env").write_text(content, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DOCLING_", "AZURE_")) and k != "PYTHONPATH"
    }
    env.update(overrides or {})
    code = """
import json
import os
import sys
sys.path.insert(0, sys.argv[1])
import docling_desk.config as settings
from docling_azure_ocr.config import api_key, load_profile
profile = load_profile("azure_read" if os.environ.get("DOCLING_AZURE_OCR_ENDPOINT") else None)
print(json.dumps({
    "provider": profile.provider, "enabled": profile.enabled, "auth": profile.auth,
    "endpoint": profile.endpoint, "data": str(settings.DATA),
    "key_loaded": api_key().get_secret_value() == "synthetic key # value",
    "literal_value": os.environ.get("ENV_LITERAL_TEST"),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(project / "src")],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "synthetic key" not in result.stdout + result.stderr
    return json.loads(result.stdout)


def test_project_env_is_loaded_from_other_working_directory(tmp_path):
    data = tmp_path / "document storage"
    loaded = probe(
        tmp_path,
        f"""# The same format accepted by uvicorn --env-file.
DOCLING_AZURE_OCR_ENDPOINT=https://test.cognitiveservices.azure.com
AZURE_DOCUMENT_INTELLIGENCE_API_KEY='synthetic key # value'
DOCLING_DATA_DIR="{data}"
""",
    )
    assert loaded == {
        "provider": "azure_read",
        "enabled": True,
        "auth": "api_key",
        "endpoint": "https://test.cognitiveservices.azure.com",
        "data": str(data),
        "key_loaded": True,
        "literal_value": None,
    }


def test_explicit_environment_wins_over_env_file(tmp_path):
    loaded = probe(
        tmp_path,
        "DOCLING_AZURE_OCR_ENDPOINT=https://test.cognitiveservices.azure.com\n",
        {"DOCLING_AZURE_OCR_ENDPOINT": ""},
    )
    assert loaded["provider"] == "local"
    assert loaded["enabled"] is False


def test_missing_env_keeps_local_defaults(tmp_path):
    loaded = probe(tmp_path, None)
    assert loaded["provider"] == "local"
    assert loaded["key_loaded"] is False


def test_env_contents_are_data_and_never_run_as_shell_commands(tmp_path):
    marker = tmp_path / "should not exist"
    expression = f"$(touch '{marker}')"
    loaded = probe(tmp_path, f'ENV_LITERAL_TEST="{expression}"\n')
    assert loaded["literal_value"] == expression
    assert not marker.exists()
