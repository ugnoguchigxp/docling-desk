"""Isolated, cancellable structured Codex decisions. The app executes web actions."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from docling_desk.explanation.provider import (
    INSTRUCTIONS,
    SCHEMAS,
    TASKS,
    ExplanationError,
    Profile,
)
from docling_desk.translation.codex import FEATURES, CodexProvider, classify_error


class CodexExplanationProvider:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.process: subprocess.Popen | None = None
        self.cancelled = threading.Event()
        self.usage: dict | None = None

    def preflight(self) -> None:
        if importlib.util.find_spec("openai_codex") is None:
            raise ExplanationError("not_configured", "Codex SDKが見つかりません。")
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        if not (home / "auth.json").is_file() and not os.environ.get("OPENAI_API_KEY"):
            raise ExplanationError("authentication", "Codexの認証を確認してください。")

    def cancel(self) -> None:
        self.cancelled.set()
        if self.process:
            CodexProvider._stop(self.process)

    def complete(self, task: str, payload: dict, timeout: float) -> dict:
        self.usage = None
        if self.cancelled.is_set():
            raise ExplanationError("interrupted", "解説処理を中断しました。")
        self.preflight()
        with tempfile.TemporaryDirectory(prefix="docling-explanation-") as runtime:
            root = Path(runtime)
            home, work = root / "home", root / "work"
            home.mkdir(mode=0o700)
            work.mkdir()
            auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
            if auth.is_file():
                shutil.copyfile(auth, home / "auth.json")
                (home / "auth.json").chmod(0o600)
            env = {
                k: v
                for k, v in os.environ.items()
                if k
                in {
                    "PATH",
                    "HOME",
                    "TMPDIR",
                    "LANG",
                    "LC_ALL",
                    "OPENAI_API_KEY",
                    "HTTPS_PROXY",
                    "HTTP_PROXY",
                    "NO_PROXY",
                    "SSL_CERT_FILE",
                }
            }
            env["CODEX_HOME"] = str(home)
            self.process = subprocess.Popen(
                [sys.executable, "-m", "docling_desk.explanation.codex", "--worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=work,
                start_new_session=True,
            )
            process = self.process
            try:
                if self.cancelled.is_set():
                    raise ExplanationError("interrupted", "解説処理を中断しました。")
                stdout, _ = process.communicate(
                    json.dumps(
                        {
                            "task": task,
                            "payload": payload,
                            "model": self.profile.model,
                            "work": str(work),
                            "instructions": INSTRUCTIONS,
                            "task_instructions": TASKS[task],
                            "schema": SCHEMAS[task].model_json_schema(),
                        },
                        ensure_ascii=False,
                    ),
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise ExplanationError(
                    "timeout", "解説の応答がタイムアウトしました。", retryable=True
                ) from exc
            finally:
                CodexProvider._stop(process)
                self.process = None
            if self.cancelled.is_set() and (process.returncode != 0 or not stdout.strip()):
                raise ExplanationError("interrupted", "解説処理を中断しました。")
            if process.returncode != 0 or len(stdout.encode()) > self.profile.output_bytes:
                raise ExplanationError("truncated", "解説の応答を確定できませんでした。")
            try:
                value = json.loads(stdout)
            except ValueError as exc:
                raise ExplanationError(
                    "invalid_response", "解説の応答を読み込めませんでした。"
                ) from exc
            if not isinstance(value, dict):
                raise ExplanationError("invalid_response", "解説の応答形式が不正です。")
            if value.get("error"):
                error = classify_error(value["error"])
                raise ExplanationError(
                    error.code, str(error).replace("翻訳", "解説"), error.retryable
                )
            if isinstance(value.get("response"), dict):
                self.usage = value.get("usage")
                value = value["response"]
            return value


def worker() -> None:
    from typing import cast

    from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox
    from openai_codex.types import JsonObject

    payload = json.load(sys.stdin)
    overrides = tuple(
        f"features.{name}={str(enabled).lower()}" for name, enabled in FEATURES.items()
    ) + (
        'web_search="disabled"',
        'cli_auth_credentials_store="file"',
        "mcp_servers={}",
        "project_doc_max_bytes=0",
        # Built-in provider IDs are reserved in SDK 0.160.0. An equivalent named
        # provider uses the existing OpenAI auth/default endpoint, with no retries.
        'model_provider="explanation"',
        'model_providers.explanation.name="OpenAI explanation"',
        'model_providers.explanation.wire_api="responses"',
        "model_providers.explanation.requires_openai_auth=true",
        "model_providers.explanation.request_max_retries=0",
        "model_providers.explanation.stream_max_retries=0",
    )
    try:
        with Codex(CodexConfig(cwd=payload["work"], config_overrides=overrides)) as codex:
            if codex.account().account is None:
                if os.environ.get("OPENAI_API_KEY"):
                    codex.login_api_key(os.environ["OPENAI_API_KEY"])
                else:
                    raise RuntimeError("authentication required")
            thread = codex.thread_start(
                model=payload["model"],
                cwd=payload["work"],
                ephemeral=True,
                sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all,
                base_instructions=payload.get("instructions", INSTRUCTIONS),
                developer_instructions=payload.get("task_instructions", TASKS[payload["task"]]),
            )
            result = thread.run(
                json.dumps(payload["payload"], ensure_ascii=False, separators=(",", ":")),
                output_schema=cast(
                    JsonObject, payload.get("schema", SCHEMAS[payload["task"]].model_json_schema())
                ),
            )
            if result.status.value != "completed" or not result.final_response:
                raise RuntimeError("explanation did not complete")
            if any(
                getattr(item.root, "type", "") not in {"userMessage", "agentMessage", "reasoning"}
                for item in result.items
            ):
                raise RuntimeError("unexpected tool use")
            print(
                json.dumps(
                    {
                        "response": json.loads(result.final_response),
                        "usage": result.usage.model_dump(mode="json") if result.usage else None,
                    },
                    ensure_ascii=False,
                )
            )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))


if __name__ == "__main__":
    worker()
