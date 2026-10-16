"""Expose public OpenAPI argument contracts to the LLM; persistence stays in HTTP."""

from __future__ import annotations

import json
from pathlib import Path

from docling_desk.config import PROJECT_ROOT, RESOURCES
from docling_desk.viewer.document_tools import DOCUMENT_TOOLS


def read_contract(name: str = "knowledge-api.openapi.json") -> dict:
    candidates = [RESOURCES / "contracts" / name]
    if PROJECT_ROOT:
        candidates.insert(0, PROJECT_ROOT / "contracts" / name)
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if not path:
        raise RuntimeError(f"Contract is not installed: {name}")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def document_arguments(name: str) -> dict:
    contract = read_contract()
    method, route, identities, queries = DOCUMENT_TOOLS[name]
    operation = contract["paths"]["/api/v1" + route][method.lower()]
    schema = (
        {}
        if method == "GET"
        else (
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
    )
    properties = dict(schema.get("properties", {}))
    required = list(schema.get("required", []))
    for field in identities:
        properties[field] = {"type": "string", "format": "uuid"}
        required.append(field)
    for field in queries:
        parameter = next(
            p for p in operation["parameters"] if p["in"] == "query" and p["name"] == field
        )
        properties[field] = parameter["schema"]
        if parameter.get("required"):
            required.append(field)
    for parameter in operation.get("parameters", []):
        field = {"Idempotency-Key": "idempotency_key", "If-Match": "etag"}.get(parameter["name"])
        if field:
            properties[field] = parameter["schema"]
            if parameter.get("required"):
                required.append(field)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
