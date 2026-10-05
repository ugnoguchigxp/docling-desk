"""Prepare private QA config and run gateway, worker and fixture API."""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
ROOT = Path(__file__).resolve().parents[2]
for _ in range(100):
    try:
        if httpx.get("http://127.0.0.1:18880/health").is_success:
            break
    except httpx.HTTPError:
        pass
    time.sleep(0.3)
sys.path.insert(0, str(ROOT))

base = ROOT / ".cache/viewer-embedding"
base.mkdir(exist_ok=True)
# Isolated Open WebUI accounts. Credentials are private and never committed.
users = (
    json.loads((base / "accounts.json").read_text()) if (base / "accounts.json").exists() else []
)
for name in [] if users else ["Alice", "Bob"]:
    email = name.lower() + "@viewer.invalid"
    password = secrets.token_urlsafe(24)
    response = httpx.post(
        "http://127.0.0.1:18880/api/v1/auths/" + ("signup" if not users else "add"),
        headers={"authorization": "Bearer " + users[0]["token"]} if users else {},
        json={
            "name": name,
            "email": email,
            "password": password,
            **({"role": "user"} if users else {}),
        },
        timeout=30,
    )
    response.raise_for_status()
    user = response.json()
    user.update(email=email, password=password)
    users.append(user)
config = {
    "api_url": "http://127.0.0.1:18866",
    "public_url": "http://127.0.0.1:18868",
    "client_id": "viewer-qa",
    "issuer": "viewer-qa",
    "kid": "v1",
    "api_token": secrets.token_urlsafe(32),
    "signing_key": secrets.token_urlsafe(32),
    "connector_token": secrets.token_urlsafe(32),
    "users": {
        u["id"]: {
            "scopes": [{"collection_id": "synthetic" if i == 0 else "other"}],
            "mcp_token": secrets.token_urlsafe(32),
        }
        for i, u in enumerate(users)
    },
}
path = base / "config.json"
path.write_text(json.dumps(config))
path.chmod(0o600)
(base / "accounts.json").write_text(json.dumps(users))
(base / "accounts.json").chmod(0o600)
clients = [
    {
        "id": config["client_id"],
        "token": config["api_token"],
        "issuer": config["issuer"],
        "keys": {"v1": config["signing_key"]},
        "mode": "actor",
        "scopes": [{"collection_id": "synthetic"}, {"collection_id": "other"}],
        "actions": ["read", "write"],
    }
]
(base / "clients.json").write_text(json.dumps(clients))
(base / "clients.json").chmod(0o600)
worker_token = secrets.token_urlsafe(32)
env = {
    **os.environ,
    "VIEWER_CONFIG_FILE": str(path),
    "KNOWLEDGE_ARTIFACT_ROOT": str(base / "artifacts"),
    "KNOWLEDGE_STATE_DIR": str(base / "state"),
    "KNOWLEDGE_CLIENTS_FILE": str(base / "clients.json"),
    "KNOWLEDGE_WORKER_TOKEN": worker_token,
    "KNOWLEDGE_WORKER_URL": "http://127.0.0.1:18867",
}
commands = [
    [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "uvicorn",
        "knowledge_worker:app_factory",
        "--factory",
        "--port",
        "18867",
    ],
    ["bun", "qa/viewer-embedding/api.ts"],
    [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "uvicorn",
        "viewer_gateway:app_factory",
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        "18868",
    ],
]
processes = []
for i, command in enumerate(commands):
    log = (base / f"server-{i}.log").open("w")
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
    processes.append(process)
(base / "pids.json").write_text(json.dumps([p.pid for p in processes]))
for port in [18866, 18867, 18868]:
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health/live").is_success:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError(f"QA service {port} not ready")
# Install the native Tool and its administrator valves on isolated Open WebUI.
headers = {"authorization": "Bearer " + users[0]["token"]}
source = (ROOT / "integrations/open-webui/docling_desk_tools.py").read_text()
result = httpx.post(
    "http://127.0.0.1:18880/api/v1/tools/create",
    headers=headers,
    json={
        "id": "docling_viewer",
        "name": "Docling Desk Viewer",
        "content": source,
        "meta": {"description": "Synthetic viewer QA"},
        "access_control": None,
    },
    timeout=60,
)
if result.status_code not in [200, 400]:
    result.raise_for_status()
if result.status_code == 400:
    result = httpx.post(
        "http://127.0.0.1:18880/api/v1/tools/id/docling_viewer/update",
        headers=headers,
        json={
            "id": "docling_viewer",
            "name": "Docling Desk Viewer",
            "content": source,
            "meta": {"description": "Synthetic viewer QA"},
            "access_control": None,
        },
        timeout=60,
    )
    result.raise_for_status()
result = httpx.post(
    "http://127.0.0.1:18880/api/v1/tools/id/docling_viewer/valves/update",
    headers=headers,
    json={
        "GATEWAY_URL": "http://host.docker.internal:18868",
        "CONNECTOR_TOKEN": config["connector_token"],
    },
    timeout=30,
)
result.raise_for_status()
print("Isolated QA services and Open WebUI tool started", flush=True)
try:
    while all(p.poll() is None for p in processes):
        time.sleep(0.5)
finally:
    for process in processes:
        process.terminate()
