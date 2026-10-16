"""Verify bundled guide examples, public MCP contracts and HTTP schemas offline."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
from pathlib import Path

from docling_desk.config import checkout_root


async def check(write: bool = False) -> dict:
    from fastapi import FastAPI

    from docling_desk.viewer.gateway import Gateway
    from docling_desk.viewer.mcp import create_mcp
    from docling_desk.viewer.tool_guides import ToolGuides, install_tool_guides
    from docling_desk.viewer.transfers import install_transfers

    root = checkout_root()
    with tempfile.TemporaryDirectory(prefix="docling-guide-check-") as temp:
        config = Path(temp) / "gateway.json"
        config.write_text(
            json.dumps(
                {
                    "public_url": "http://localhost",
                    "api_url": "http://localhost",
                    "api_token": "a" * 40,
                    "signing_key": "k" * 40,
                    "connector_token": "c" * 40,
                    "client_id": "guide-check",
                    "issuer": "guide-check",
                    "kid": "v1",
                    "users": {"synthetic": {"scopes": [{"collection_id": "documents"}]}},
                }
            )
        )
        gateway = Gateway(config)
        tools = await create_mcp(gateway).list_tools()
    snapshot = {
        t.name: {"inputSchema": t.inputSchema, "outputSchema": t.outputSchema} for t in tools
    }
    app = FastAPI(title="Docling Desk tool guides", version="1")
    install_tool_guides(app, None)
    contract = app.openapi()
    transfer_app = FastAPI(title="Docling Desk document transfers", version="1")
    install_transfers(transfer_app, None)
    for name, value in (
        ("mcp-tools.json", snapshot),
        ("tool-guides.openapi.json", contract),
        ("document-transfers.openapi.json", transfer_app.openapi()),
    ):
        path = root / "contracts" / name
        encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if write:
            path.write_text(encoded, encoding="utf-8")
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError(f"Contract differs: {name}. Review then run with --write.")
    catalog = ToolGuides()
    catalog.check_tools(tools)
    examples = validate_examples(catalog)
    return {
        "guides": len(catalog.entries),
        "tools": len(tools),
        "examples": examples,
        "catalog_revision": catalog.revision,
    }


def validate_examples(catalog) -> int:
    from jsonschema import Draft202012Validator, FormatChecker

    examples = 0
    for entry in catalog.entries:
        rendered = []
        for section in entry["sections"]:
            for block in re.findall(r"```json\n(.*?)\n```", catalog.texts[section["file"]], re.S):
                value = json.loads(block)
                if "name" in value and "arguments" in value:
                    rendered.append({"tool_name": value["name"], "arguments": value["arguments"]})
        if rendered != entry["examples"]:
            raise ValueError("Rendered guide examples differ from manifest")
        for example in entry["examples"]:
            schema = catalog.tools[example["tool_name"]]["inputSchema"]
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(
                example["arguments"]
            )
            examples += 1
        if entry["kind"] == "tool" and not entry["examples"]:
            raise ValueError("Tool guide requires a call example")
    return examples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Update reviewed contract snapshots")
    result = asyncio.run(check(parser.parse_args().write))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
