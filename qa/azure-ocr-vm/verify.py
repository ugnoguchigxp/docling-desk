"""Verify VM Compose wiring and Linux amd64 OCR with synthetic documents only."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "docling-ocr-vm-qa:20261004"
OUTPUT = ROOT / "qa/azure-ocr-vm" / datetime.now().strftime("%Y%m%d-%H%M%S")
OUTPUT.mkdir(parents=True)
PRIVATE = OUTPUT / "private"
PRIVATE.mkdir(mode=0o700)
TOKEN = "synthetic-worker-token-" * 3
(PRIVATE / "worker.env").write_text(f"KNOWLEDGE_WORKER_TOKEN={TOKEN}\n")
(PRIVATE / "container.env").write_text(
    f"KNOWLEDGE_WORKER_TOKEN={TOKEN}\nKNOWLEDGE_CLIENTS_B64=synthetic-clients\n"
)
for path in PRIVATE.glob("*.env"):
    path.chmod(0o600)
ROOT_ENV = dotenv_values(ROOT / ".env")
assert ROOT_ENV.get("AZURE_DOCUMENT_INTELLIGENCE_API_KEY")
with socket.socket() as reservation:
    reservation.bind(("127.0.0.1", 0))
    PORT = reservation.getsockname()[1]
ENV = os.environ.copy()
ENV.update(
    DOCLING_PORT=str(PORT),
    KNOWLEDGE_WORKER_ENV=str(PRIVATE / "worker.env"),
    KNOWLEDGE_CONTAINER_ENV=str(PRIVATE / "container.env"),
)
DEMO_PROJECT, WORKER_PROJECT = "ocr-vm-demo-" + uuid4().hex[:8], "ocr-vm-worker-" + uuid4().hex[:8]
(DEMO_OVERRIDE := PRIVATE / "demo.yml").write_text(
    yaml.safe_dump({"services": {"docling": {"image": IMAGE, "platform": "linux/amd64"}}})
)
(WORKER_OVERRIDE := PRIVATE / "worker.yml").write_text(
    yaml.safe_dump({"services": {"processor": {"image": IMAGE, "platform": "linux/amd64"}}})
)
DEMO = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / ".env"),
    "-p",
    DEMO_PROJECT,
    "-f",
    str(ROOT / "compose.yaml"),
    "-f",
    str(DEMO_OVERRIDE),
]
WORKER = [
    "docker",
    "compose",
    "-p",
    WORKER_PROJECT,
    "-f",
    str(ROOT / "deploy/compose.knowledge.yml"),
    "-f",
    str(WORKER_OVERRIDE),
]
REPORT = {
    "verified_at": datetime.now().isoformat(),
    "platform": "linux/amd64",
    "cases": [],
    "azure_vm_actual_host": "not_tested",
}


def command(args, **kwargs):
    result = subprocess.run(args, cwd=ROOT, env=ENV, capture_output=True, text=True, **kwargs)
    with (PRIVATE / "commands.log").open("a") as log:
        log.write(result.stdout + result.stderr)
    assert result.returncode == 0, "Container command failed; inspect private commands.log locally."
    return result.stdout


def persist():
    (OUTPUT / "verification.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))


try:
    expected = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (
            "service.py",
            "rag.py",
            "pagination.py",
            "settings.py",
            "knowledge_worker.py",
            "ocr_profiles.py",
        )
    }
    probe = """import hashlib, json, pathlib, sys
from docling_azure_ocr.config import load_profile
expected = json.loads(sys.argv[1])
assert all(hashlib.sha256(pathlib.Path('/app', name).read_bytes()).hexdigest() == digest for name, digest in expected.items())
assert not pathlib.Path('/app/.env').exists()
assert load_profile().provider == 'local'
print(json.dumps({'code_matches_checkout': True, 'env_excluded_from_image': True}))
"""
    REPORT["image_id"] = command(
        ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"]
    ).strip()
    REPORT["image_checks"] = json.loads(
        command(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "--entrypoint",
                "python",
                IMAGE,
                "-c",
                probe,
                json.dumps(expected),
            ]
        )
    )
    processor_probe = """import json, os
from pathlib import Path
from fastapi.testclient import TestClient
from docling_desk.knowledge.worker import create_worker
client = TestClient(create_worker(Path('/var/lib/docling/data'), os.environ['KNOWLEDGE_WORKER_TOKEN']))
response = client.post('/internal/v1/ocr-profile', json={'provider':'azure_read'}, headers={'Authorization': 'Bearer ' + os.environ['KNOWLEDGE_WORKER_TOKEN']})
assert response.status_code == 200
profile = response.json()['ocr_profile']
assert profile['auth'] == 'api_key' and profile['enabled']
assert os.environ['AZURE_DOCUMENT_INTELLIGENCE_API_KEY'] not in response.text
print(json.dumps({'profile_status':200,'auth':profile['auth'],'enabled':profile['enabled'],'api_key_excluded_from_response':True}))
"""
    REPORT["knowledge_processor"] = json.loads(
        command(
            WORKER
            + [
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "processor",
                "-c",
                processor_probe,
            ]
        )
    )
    print(
        "PASS: current OCR code in amd64 image; .env excluded; processor loads root OCR connection",
        flush=True,
    )
    command(DEMO + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180"])
    with httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=45) as client:
        assert client.get("/health/ready").json()["status"] == "ready"
        assert client.get("/api/ocr").json()["azure_available"] is True
        sources = ROOT / "qa/azure-ocr-live/20261004-090448"
        for label, filename in [
            ("pptx", "synthetic-azure-image.pptx"),
            ("docx-table", "synthetic-azure-docx-table.docx"),
            ("pdf-image-only", "synthetic-azure-image.pdf"),
        ]:
            with (sources / filename).open("rb") as stream:
                response = client.post(
                    "/api/upload",
                    data={"ocr_provider": "azure_read"},
                    files={"file": (filename, stream)},
                )
            assert response.status_code == 202
            job = response.json()
            deadline = time.monotonic() + 360
            while job["state"] in ("queued", "running") and time.monotonic() < deadline:
                time.sleep(1)
                job = client.get("/api/jobs/" + job["id"]).json()
            assert job["state"] == "success", (label, job["state"], job.get("error_code"))
            exports = {}
            for name in (
                "text.txt",
                "document.md",
                "extracted.html",
                "rag-index.jsonl",
                "rag.jsonl",
            ):
                result = client.get(f"/files/{job['id']}/{name}")
                assert result.status_code == 200
                exports[name] = result.text
            for name in ("text.txt", "document.md", "extracted.html"):
                assert "73921" in exports[name] and "120" in exports[name]
                assert "response_sha256" not in exports[name]
                assert "cognitiveservices.azure.com" not in exports[name]
            for name in ("rag-index.jsonl", "rag.jsonl"):
                records = [json.loads(line) for line in exports[name].splitlines()]
                text = "".join(r["text"] for r in records).replace(" ", "")
                assert all(token in text for token in ("画像OCR", "在庫", "73921", "120", "2026"))
                assert all("response_sha256" not in r["text"] for r in records)
                assert any(r["ocr_evidence"] for r in records)
            case = {
                "case": label,
                "state": job["state"],
                "duration_seconds": job["duration"],
                "japanese_text_in_rag": True,
                "ocr_evidence_preserved": True,
                "metadata_not_in_text": True,
            }
            REPORT["cases"].append(case)
            (OUTPUT / (label + "-text.txt")).write_text(exports["text.txt"])
            persist()
            print("PASS: actual Azure OCR in Linux amd64 container: " + label, flush=True)
    REPORT["status"] = "passed"
    persist()
finally:
    # Only the unique verification projects and their generated volumes are removed.
    for compose in (DEMO, WORKER):
        result = subprocess.run(
            compose + ["down", "-v", "--remove-orphans"],
            cwd=ROOT,
            env=ENV,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, "Verification container cleanup failed."
    key = ROOT_ENV["AZURE_DOCUMENT_INTELLIGENCE_API_KEY"]
    assert all(key not in p.read_text(errors="ignore") for p in OUTPUT.rglob("*") if p.is_file())
    print("Report: " + str(OUTPUT / "verification.json"), flush=True)
