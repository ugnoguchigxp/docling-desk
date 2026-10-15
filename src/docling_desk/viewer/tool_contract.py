"""Expose public OpenAPI argument contracts to the LLM; persistence stays in HTTP."""

from __future__ import annotations

import json
from pathlib import Path

from docling_desk.config import PROJECT_ROOT, RESOURCES
from docling_desk.viewer.document_tools import DOCUMENT_TOOLS


def document_arguments(name: str) -> dict:
    candidates = [RESOURCES / "contracts/knowledge-api.openapi.json"]
    if PROJECT_ROOT:
        candidates.insert(0, PROJECT_ROOT / "contracts/knowledge-api.openapi.json")
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if not path:
        raise RuntimeError("Document OpenAPI contract is not installed")
    contract = json.loads(Path(path).read_text(encoding="utf-8"))
    method, route, identities, queries = DOCUMENT_TOOLS[name]
    operation = contract["paths"]["/api/v1" + route][method.lower()]
    schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    properties = dict(schema.get("properties", {}))
    required = list(schema.get("required", []))
    for field in identities:
        properties[field] = {"type": "string", "format": "uuid"}
        required.append(field)
    for field in queries:
        properties[field] = (
            {"type": "integer", "minimum": 0}
            if field in {"offset", "limit"}
            else {"type": "string", "format": "uuid"}
        )
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
