"""Loopback evaluation server. The production app is not started and no provider flag is added there."""

from __future__ import annotations

import argparse
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from docling_desk.evaluation.provider import SyntheticEmbedding
from docling_desk.knowledge.api import create_router
from docling_desk.knowledge.service import Knowledge

AZURE_PREFIX = "DOCLING_AZURE_EMBEDDING_"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class OutboundGuard:
    def __init__(self) -> None:
        self.external_attempts = 0

    def install(self) -> None:
        guard = self
        connect = socket.socket.connect
        connect_ex = socket.socket.connect_ex

        def blocked(address) -> bool:
            if not isinstance(address, tuple) or not address:
                return False
            host = address[0]
            if isinstance(host, bytes):
                host = host.decode()
            host = str(host)
            if host in LOCAL_HOSTS or host.startswith("127."):
                return False
            guard.external_attempts += 1
            return True

        def guarded_connect(sock, address):
            if blocked(address):
                raise OSError("evaluation server blocked a non-local connection")
            return connect(sock, address)

        def guarded_connect_ex(sock, address):
            if blocked(address):
                return 1
            return connect_ex(sock, address)

        socket.socket.connect = guarded_connect
        socket.socket.connect_ex = guarded_connect_ex


GUARD = OutboundGuard()


def build_app(data: Path, ignored_azure_env: list[str]) -> FastAPI:
    provider = SyntheticEmbedding()
    knowledge = Knowledge(data, provider)
    knowledge.config_error = ""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        knowledge.close()

    app = FastAPI(title="Docling Desk evaluation server", lifespan=lifespan)
    app.state.knowledge = knowledge
    app.state.synthetic_provider = provider
    app.include_router(create_router(lambda: data))

    @app.get("/api/evaluation/provider")
    def provider_info():
        return {
            "provider_kind": "synthetic",
            "external_requests": GUARD.external_attempts,
            "outbound_connect_attempts": GUARD.external_attempts,
            "local_requests": provider.local_requests,
            "dimensions": provider.dimensions,
            "semantic_quality_claimed": False,
            "azure_env_ignored": ignored_azure_env,
            "production_env_flag": None,
        }

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Isolated loopback knowledge server")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    ignored = [key for key in list(os.environ) if key.startswith(AZURE_PREFIX)]
    for key in ignored:
        os.environ.pop(key, None)
    GUARD.install()
    data = args.data.resolve()
    data.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        build_app(data, ignored),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="error",
    )


if __name__ == "__main__":
    main()
