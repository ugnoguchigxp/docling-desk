"""Upload synthetic artifacts through actor-authenticated ingestion."""

import base64
import hmac
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from docling_desk.viewer.gateway import encoded  # noqa: E402

base = ROOT / ".cache/viewer-embedding"
config = json.loads((base / "config.json").read_text())
user = next(iter(config["users"]))
now = int(time.time())
head = encoded({"alg": "HS256", "typ": "JWT", "kid": config["kid"]})
claims = encoded(
    {
        "iss": config["issuer"],
        "aud": "docling-desk-api",
        "client_id": config["client_id"],
        "sub": user,
        "iat": now,
        "exp": now + 300,
        "scopes": config["users"][user]["scopes"],
    }
)
sig = (
    base64.urlsafe_b64encode(
        hmac.digest(config["signing_key"].encode(), f"{head}.{claims}".encode(), "sha256")
    )
    .decode()
    .rstrip("=")
)
headers = {
    "authorization": "Bearer " + config["api_token"],
    "x-knowledge-actor": f"{head}.{claims}.{sig}",
}
refs = {}
with httpx.Client(timeout=45) as client:
    for kind, suffix in [
        ("page", "pdf"),
        ("slide", "pptx"),
        ("sheet", "xlsx"),
        ("docx", "docx"),
        ("md", "md"),
        ("txt", "txt"),
    ]:
        path = base / "fixtures" / kind / f"original.{suffix}"
        response = client.post(
            config["api_url"] + "/api/v1/sources",
            headers={**headers, "idempotency-key": str(uuid4())},
            files={
                "file": (f"synthetic.{suffix}", path.read_bytes()),
                "metadata": (
                    None,
                    json.dumps({"title": f"Synthetic {suffix}", "collection_id": "synthetic"}),
                ),
            },
        )
        if response.status_code != 202:
            raise RuntimeError(f"Upload {suffix} failed: {response.status_code} {response.text}")
        registered = response.json()
        for _ in range(300):
            response = client.get(
                config["api_url"] + "/api/v1/sources/" + registered["source_id"], headers=headers
            )
            value = response.json()
            if value.get("fts_ready"):
                break
            if value.get("state") == "failed":
                raise RuntimeError("Fixture indexing failed " + suffix)
            time.sleep(0.1)
        else:
            raise RuntimeError("Index timeout " + suffix)
        refs[suffix] = {k: value[k] for k in ["source_id", "source_revision", "evidence_revision"]}
(base / "refs.json").write_text(json.dumps(refs))
print("Six versioned synthetic sources indexed")
