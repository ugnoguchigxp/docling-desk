from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from docling_desk.operations.mcp_check import Connection, main, probe

ROOT = Path(__file__).resolve().parents[1]


def test_example_schema_and_gateway_configuration_match_runtime():
    from docling_desk.viewer.gateway import GatewayConfig

    path = ROOT / "integrations/eumenes"
    data = json.loads((path / "connection.example.json").read_text())
    assert Connection.model_validate(data).gateway_url == "http://127.0.0.1:18768"
    assert (
        json.loads((path / "connection.schema.json").read_text()) == Connection.model_json_schema()
    )
    for name in ("gateway.example.json", "gateway.container.example.json"):
        config = GatewayConfig.model_validate_json(
            (ROOT / "integrations/documents" / name).read_text()
        )
        assert config.users["read-user"].actions == ["read"]
        assert config.users["local-user"].actions == ["read", "write"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/a",
        "http://user:secret@localhost",
        "http://localhost/mcp/",
        "http://localhost?token=secret",
        "http://localhost/#secret",
    ],
)
def test_connection_rejects_credentials_or_non_origin_urls(url):
    with pytest.raises(ValidationError):
        Connection(schema_version=1, gateway_url=url, mcp_token_env="EXAMPLE_TOKEN")


def test_probe_requires_secret_environment_before_connecting(monkeypatch):
    import asyncio

    monkeypatch.delenv("DOCLING_TEST_PROBE_TOKEN", raising=False)
    settings = Connection(
        schema_version=1, gateway_url="http://127.0.0.1:1", mcp_token_env="DOCLING_TEST_PROBE_TOKEN"
    )
    with pytest.raises(ValueError, match="environment_missing"):
        asyncio.run(probe(settings))


def test_cli_failure_does_not_print_private_settings(tmp_path, monkeypatch, capsys):
    path = tmp_path / "private.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gateway_url": "http://user:DO_NOT_PRINT@localhost",
                "mcp_token_env": "EXAMPLE_TOKEN",
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["mcp_check", "--connection", str(path)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert "DO_NOT_PRINT" not in captured.err and str(path) not in captured.err
