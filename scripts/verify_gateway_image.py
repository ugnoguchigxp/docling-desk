"""Probe a built gateway image with no network and a disposable transfer volume."""

from __future__ import annotations

import argparse
import subprocess
from uuid import uuid4

# All identities and credentials below are synthetic and remain inside the container.
PROBE = r"""
import json, os, pathlib, sys
from fastapi.testclient import TestClient
import httpx
from docling_desk.config import RESOURCES
from docling_desk.viewer.gateway import create_gateway
from docling_desk.viewer.tool_guides import ToolGuides
from docling_desk.operations.mcp_check import Connection

resources = RESOURCES / "integrations"
Connection.model_validate_json((resources / "eumenes/connection.example.json").read_text())
value = json.loads((resources / "documents/gateway.container.example.json").read_text())
value.update(api_token="r"*40, signing_key="s"*40, connector_token="h"*40)
value["api_clients"]["editor"].update(api_token="e"*40, signing_key="k"*40)
value["users"]["read-user"]["mcp_token"] = "a"*40
value["users"]["local-user"]["mcp_token"] = "w"*40
config = pathlib.Path("/tmp/gateway.json")
config.write_text(json.dumps(value))
transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"scopes":[{"collection_id":"documents"}],"actions":["read","write"]}))
receipt = pathlib.Path(value["transfer_dir"]).parent / "p5-receipt.json"
with TestClient(create_gateway(config, transport, mcp_enabled=True), base_url=value["public_url"]) as client:
    guides = ToolGuides()
    assert len(guides.entries) == 42
    for entry in guides.entries:
        response = client.get("/api/v1/tool-guides/"+entry["guide_id"], headers={"Authorization":"Bearer "+"a"*40})
        assert response.status_code == 200, response.text
    headers = {"Authorization":"Bearer "+"w"*40}
    if sys.argv[1] == "stage":
        response = client.post("/api/v1/document-transfers", headers=headers,
            files={"file":("synthetic.txt",b"container transfer")},
            data={"metadata":json.dumps({"title":"Synthetic","collection_id":"documents","source_kind":"document","language":"ja"})})
        assert response.status_code == 200, response.text
        receipt.write_text(json.dumps(response.json()))
        os.chmod(receipt,0o600)
    else:
        saved = json.loads(receipt.read_text())
        response = client.get("/api/v1/document-transfers/"+saved["transfer_id"], headers=headers)
        assert response.status_code == 200 and response.json()["sha256"] == saved["sha256"], response.text
        assert response.json()["state"] == "staged"
        assert pathlib.Path(value["transfer_dir"]).stat().st_mode & 0o077 == 0
print(json.dumps({"container_stage":sys.argv[1],"guide_count":len(guides.entries),"uid":os.getuid(),"private_transfer_volume":True}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="An already built gateway image")
    image = parser.parse_args().image
    volume = "docling-p5-" + uuid4().hex
    subprocess.run(["docker", "volume", "create", volume], check=True, stdout=subprocess.DEVNULL)
    try:
        for stage in ("stage", "restart"):
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--network",
                    "none",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:mode=1777",
                    "--mount",
                    f"type=volume,src={volume},dst=/var/lib/docling-gateway",
                    image,
                    "python",
                    "-",
                    stage,
                ],
                input=PROBE,
                text=True,
                check=True,
            )
    finally:
        subprocess.run(["docker", "volume", "rm", volume], check=True, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
