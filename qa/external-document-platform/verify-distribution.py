"""Build from sdist, inspect editor assets and run the installed gateway only."""

import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="document-platform-dist-") as temporary:
    root = Path(temporary)
    sdist_dir = root / "sdist"
    wheel_dir = root / "wheel"
    sdist_dir.mkdir()
    wheel_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from setuptools.build_meta import build_sdist; import sys; build_sdist(sys.argv[1])",
            str(sdist_dir),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with tarfile.open(next(sdist_dir.glob("*.tar.gz"))) as archive:
        assert any(
            name.endswith("contracts/knowledge-api.openapi.json") for name in archive.getnames()
        )
        archive.extractall(sdist_dir, filter="data")
    source = next(path for path in sdist_dir.iterdir() if path.is_dir())
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
            str(wheel_dir),
        ],
        cwd=source,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    installed = root / "installed"
    with zipfile.ZipFile(next(wheel_dir.glob("*.whl"))) as archive:
        names = set(archive.namelist())
        prefix = "docling_desk/resources/static/frontend/"
        editor = archive.read(prefix + "editor.html").decode()
        for asset in re.findall(r'/static/frontend/(assets/[^"?#]+)', editor):
            assert prefix + asset in names
        assert "docling_desk/resources/contracts/knowledge-api.openapi.json" in names
        assert "platejs" in archive.read(prefix + "licenses.txt").decode()
        archive.extractall(installed)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DOCLING_", "KNOWLEDGE_", "AZURE_", "OPENAI_", "PYTHONPATH"))
    }
    env["DOCLING_ENV_FILE"] = os.devnull
    probe = """
import json, pathlib, sys, re
sys.path.insert(0,sys.argv[1])
from docling_desk import config
from docling_desk.viewer.gateway import create_gateway
from docling_desk.viewer.tool_contract import document_arguments
from fastapi.testclient import TestClient
assert config.PROJECT_ROOT is None
assert pathlib.Path(config.__file__).is_relative_to(pathlib.Path(sys.argv[1]))
assert 'base_sha256' in document_arguments('edit_document_draft')['properties']
assert 'idempotency_key' in document_arguments('commit_document_draft')['required']
private=pathlib.Path(sys.argv[2])/'gateway.json'
private.write_text(json.dumps({'api_url':'http://api.test','public_url':'http://testserver','client_id':'test','issuer':'test','kid':'v1','api_token':'a'*40,'signing_key':'k'*40,'connector_token':'c'*40,'users':{'user':{'scopes':[{'collection_id':'docs'}]}}}))
app=create_gateway(private)
with TestClient(app) as client:
    response=client.get('/editor')
    assert response.status_code==200
    for asset in re.findall(r'(?:src|href)="(/static/frontend/assets/[^"?#]+)',response.text):
        assert client.get(asset).status_code==200
print(json.dumps({'sdist':True,'wheel_editor_assets':True,'mcp_contract':True,'installed_gateway':True,'editor_licenses':True}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe, str(installed), str(root)],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())
    (ROOT / "qa/external-document-platform/distribution-results.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print("Installed editor and MCP contract verified")
