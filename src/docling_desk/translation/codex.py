"""Run the pinned Python Codex SDK in a cancellable, isolated child process."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from docling_desk.translation.provider import (
    INSTRUCTIONS,
    Profile,
    TranslationError,
    validate_response,
)

FEATURES = {
    name: False
    for name in (
        "shell_tool",
        "unified_exec",
        "apps",
        "plugins",
        "hooks",
        "browser_use",
        "browser_use_external",
        "computer_use",
        "image_generation",
        "view_image",
        "multi_agent",
        "multi_agent_v2",
        "memories",
        "skill_search",
        "sleep_tool",
        "code_mode",
        "code_mode_host",
        "js_repl",
        "workspace_dependencies",
        "remote_plugin",
        "recommended_plugins",
        "unbounded_connection_retries",
    )
}
FEATURES["skip_host_skill_discovery"] = True


def classify_error(message: str) -> TranslationError:
    value = message.lower()
    if any(
        word in value
        for word in ("401", "403", "unauthorized", "authentication", "not logged", "refresh token")
    ):
        return TranslationError("authentication", "Codexの認証を確認してください。")
    if any(word in value for word in ("429", "quota", "usage limit", "rate limit")):
        return TranslationError(
            "rate_limit", "翻訳サービスの利用上限に達しました。時間を置いて再実行してください。"
        )
    if any(word in value for word in ("model", "not supported", "not found")):
        return TranslationError(
            "model_unavailable", "指定したモデルを利用できません。モデル設定を確認してください。"
        )
    if any(word in value for word in ("refusal", "content filter", "blocked")):
        return TranslationError("refused", "翻訳サービスがこの内容の処理を拒否しました。")
    if any(word in value for word in ("connect", "network", "503", "502", "stream disconnected")):
        return TranslationError("transient", "翻訳サービスとの通信に失敗しました。", retryable=True)
    return TranslationError(
        "provider_error", "翻訳サービスの処理に失敗しました。接続設定を確認してください。"
    )


class CodexProvider:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.process: subprocess.Popen | None = None
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()
        if self.process:
            self._stop(self.process)

    def preflight(self) -> None:
        if importlib.util.find_spec("openai_codex") is None:
            raise TranslationError(
                "not_configured", "専用環境にCodex SDKをインストールしてください。"
            )
        auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
        if not auth.is_file() and not os.environ.get("OPENAI_API_KEY"):
            raise TranslationError(
                "authentication", "Codexにログインするか、OPENAI_API_KEYを設定してください。"
            )

    def translate(self, language: str, segments: list[dict[str, str]]) -> dict[str, str]:
        if self.cancelled.is_set():
            raise TranslationError("interrupted", "翻訳処理を中断しました。")
        self.preflight()
        with tempfile.TemporaryDirectory(prefix="docling-translation-") as runtime:
            directory = Path(runtime)
            home, work = directory / "home", directory / "work"
            home.mkdir(mode=0o700)
            work.mkdir()
            source = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
            if source.is_file():
                shutil.copyfile(source, home / "auth.json")
                (home / "auth.json").chmod(0o600)
            # Do not expose user config, repository context, or unrelated credentials to the runtime.
            env = {
                key: value
                for key, value in os.environ.items()
                if key
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
            payload = json.dumps(
                {
                    "model": self.profile.model,
                    "language": language,
                    "segments": segments,
                    "work": str(work),
                },
                ensure_ascii=False,
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "docling_desk.translation.codex", "--worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=work,
                start_new_session=True,
            )
            self.process = process
            try:
                if self.cancelled.is_set():
                    raise TranslationError("interrupted", "翻訳処理を中断しました。")
                stdout, _ = process.communicate(payload, timeout=self.profile.timeout)
            except subprocess.TimeoutExpired as exc:
                self._stop(process)
                raise TranslationError(
                    "timeout", "翻訳がタイムアウトしました。再実行できます。"
                ) from exc
            finally:
                self._stop(process)
                self.process = None
            try:
                value = json.loads(stdout)
            except ValueError as exc:
                raise TranslationError(
                    "invalid_response", "Codexの翻訳結果を読み込めませんでした。"
                ) from exc
            if not isinstance(value, dict):
                raise TranslationError("invalid_response", "Codexの翻訳結果の形式が不正です。")
            if value.get("error"):
                raise classify_error(value["error"])
            if process.returncode != 0 or len(stdout) > self.profile.max_output_chars:
                raise TranslationError(
                    "truncated", "翻訳結果を確定できませんでした。出力上限を確認してください。"
                )
            return validate_response(value, {s["id"] for s in segments})

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        # The SDK runtime is a descendant in this private process group.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)


def worker() -> None:
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
        'model_provider="openai"',
    )
    schema: JsonObject = {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["id", "text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["translations"],
        "additionalProperties": False,
    }
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
                base_instructions=INSTRUCTIONS,
                developer_instructions="Return only the translation schema. Never call a tool.",
            )
            result = thread.run(
                json.dumps(
                    {"target_language": payload["language"], "segments": payload["segments"]},
                    ensure_ascii=False,
                ),
                output_schema=schema,
            )
            if result.status.value != "completed" or not result.final_response:
                raise RuntimeError("translation did not complete")
            # Tool use is never an accepted translation result.
            if any(
                getattr(item.root, "type", "") not in {"userMessage", "agentMessage", "reasoning"}
                for item in result.items
            ):
                raise RuntimeError("unexpected tool use")
            print(result.final_response)
    except Exception as exc:
        # Internal error text is only sent to the parent, which maps it to a safe public message.
        print(json.dumps({"error": str(exc)}))


if __name__ == "__main__":
    worker()
