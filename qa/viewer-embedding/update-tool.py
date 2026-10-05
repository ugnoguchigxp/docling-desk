import json
from pathlib import Path

import httpx
root = Path(__file__).resolve().parents[2]
base = root / ".cache/viewer-embedding"
users = json.loads((base / "accounts.json").read_text())
config = json.loads((base / "config.json").read_text())
headers = {"authorization": "Bearer " + users[0]["token"]}
r = httpx.post(
    "http://127.0.0.1:18880/api/v1/tools/id/docling_viewer/update",
    headers=headers,
    json={
        "id": "docling_viewer",
        "name": "Docling Desk Viewer",
        "content": (root / "integrations/open-webui/docling_desk_tools.py").read_text(),
        "meta": {"description": "Synthetic viewer QA"},
        "access_control": None,
    },
    timeout=60,
)
r.raise_for_status()
r = httpx.post(
    "http://127.0.0.1:18880/api/v1/tools/id/docling_viewer/valves/update",
    headers=headers,
    json={
        "GATEWAY_URL": "http://host.docker.internal:18868",
        "CONNECTOR_TOKEN": config["connector_token"],
    },
)
r.raise_for_status()
print("Latest native tool installed in isolated QA")
