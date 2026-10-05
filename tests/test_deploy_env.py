"""Check the real Compose config resolution using only synthetic credentials."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def compose_project(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI not installed")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True)
    if version.returncode:
        pytest.skip("Docker Compose CLI not installed")
    project = tmp_path / "VM project"
    (project / "deploy").mkdir(parents=True)
    (project / ".knowledge-api").mkdir()
    shutil.copyfile(ROOT / "deploy/compose.knowledge.yml", project / "deploy/compose.knowledge.yml")
    shutil.copyfile(ROOT / "compose.yaml", project / "compose.yaml")
    (project / ".knowledge-api/container.env").write_text(
        "KNOWLEDGE_WORKER_TOKEN=synthetic-token\nKNOWLEDGE_CLIENTS_B64=synthetic-clients\n"
    )
    (project / ".knowledge-api/worker.env").write_text("KNOWLEDGE_WORKER_TOKEN=synthetic-token\n")
    return project


def resolved(project, compose_file):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DOCLING_", "AZURE_", "KNOWLEDGE_", "COMPOSE_"))
    }
    result = subprocess.run(
        ["docker", "compose", "-f", compose_file, "config", "--format", "json"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["services"]


def test_demo_uses_minimal_root_env(compose_project):
    (compose_project / ".env").write_text(
        "DOCLING_AZURE_OCR_ENDPOINT=https://vm.cognitiveservices.azure.com\nAZURE_DOCUMENT_INTELLIGENCE_API_KEY=synthetic-key\n"
    )
    env = resolved(compose_project, "compose.yaml")["docling"]["environment"]
    assert env["DOCLING_AZURE_OCR_ENDPOINT"] == "https://vm.cognitiveservices.azure.com"
    assert env["AZURE_DOCUMENT_INTELLIGENCE_API_KEY"] == "synthetic-key"
    assert "DOCLING_AZURE_OCR_AUTH" not in env
    assert "DOCLING_AZURE_OCR_ENABLED" not in env


@pytest.mark.parametrize("override", [False, True])
def test_knowledge_processor_receives_root_ocr_env_but_api_does_not(compose_project, override):
    (compose_project / ".env").write_text(
        "DOCLING_AZURE_OCR_ENDPOINT=https://root.cognitiveservices.azure.com\nAZURE_DOCUMENT_INTELLIGENCE_API_KEY=synthetic-root-key\n"
    )
    if override:
        (compose_project / ".knowledge-api/worker.env").write_text(
            "KNOWLEDGE_WORKER_TOKEN=synthetic-token\nDOCLING_AZURE_OCR_ENDPOINT=https://worker.cognitiveservices.azure.com\nAZURE_DOCUMENT_INTELLIGENCE_API_KEY=synthetic-worker-key\n"
        )
    services = resolved(compose_project, "deploy/compose.knowledge.yml")
    worker, api = services["processor"]["environment"], services["api"]["environment"]
    source = "worker" if override else "root"
    assert worker["DOCLING_AZURE_OCR_ENDPOINT"] == f"https://{source}.cognitiveservices.azure.com"
    assert worker["AZURE_DOCUMENT_INTELLIGENCE_API_KEY"] == f"synthetic-{source}-key"
    assert "AZURE_DOCUMENT_INTELLIGENCE_API_KEY" not in api
    assert "DOCLING_AZURE_OCR_ENDPOINT" not in api
    assert "KNOWLEDGE_CLIENTS_B64" not in worker
    assert worker["KNOWLEDGE_API_INTERNAL_URL"] == "http://api:18766"
    assert worker["KNOWLEDGE_WORKER_TOKEN"] == api["KNOWLEDGE_WORKER_TOKEN"]


def test_vm_managed_identity_worker_settings_work_without_root_env(compose_project):
    (compose_project / ".knowledge-api/worker.env").write_text(
        "KNOWLEDGE_WORKER_TOKEN=synthetic-token\nDOCLING_AZURE_OCR_ENDPOINT=https://vm.cognitiveservices.azure.com\nDOCLING_AZURE_OCR_CLIENT_ID=synthetic-assigned-id\n"
    )
    env = resolved(compose_project, "deploy/compose.knowledge.yml")["processor"]["environment"]
    assert env["DOCLING_AZURE_OCR_ENDPOINT"] == "https://vm.cognitiveservices.azure.com"
    assert env["DOCLING_AZURE_OCR_CLIENT_ID"] == "synthetic-assigned-id"
    assert not env.get("AZURE_DOCUMENT_INTELLIGENCE_API_KEY")
