import json

import pytest

import docling_desk.translation.codex as module
from docling_desk.translation.codex import FEATURES, CodexProvider, classify_error
from docling_desk.translation.provider import Profile, TranslationError


def test_codex_uses_isolated_home_and_cancellable_process(tmp_path, monkeypatch):
    home = tmp_path / "user-home"
    home.mkdir()
    (home / "auth.json").write_text("{}")
    (home / "config.toml").write_text("do-not-copy")
    monkeypatch.setenv("CODEX_HOME", str(home))
    calls = []

    class Process:
        pid = 9999999
        returncode = 0

        def __init__(self, command, **kwargs):
            calls.append(kwargs)
            assert kwargs["cwd"] != str(tmp_path)
            runtime_home = __import__("pathlib").Path(kwargs["env"]["CODEX_HOME"])
            assert (runtime_home / "auth.json").is_file()
            assert not (runtime_home / "config.toml").exists()

        def communicate(self, payload, timeout):
            assert json.loads(payload)["model"] == "gpt-6-luna"
            return json.dumps({"translations": [{"id": "a", "text": "English"}]}), ""

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", Process)
    assert CodexProvider(Profile()).translate("en", [{"id": "a", "text": "日本語"}]) == {
        "a": "English"
    }
    assert calls[0]["start_new_session"] is True
    assert FEATURES["shell_tool"] is False and FEATURES["apps"] is False


def test_authentication_preflight_and_error_normalization(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(TranslationError, match="ログイン"):
        CodexProvider(Profile()).preflight()
    assert classify_error("401 unauthorized").code == "authentication"
    assert classify_error("429 quota").code == "rate_limit"
    assert classify_error("429 rate limit reached for model").code == "rate_limit"
    assert classify_error("network disconnected").retryable is True


def test_timeout_terminates_private_process_group(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    terminated = []

    class Process:
        pid = 1234567
        returncode = None

        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, payload, timeout):
            raise module.subprocess.TimeoutExpired("worker", timeout)

        def wait(self, timeout):
            self.returncode = -15
            return -15

    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: terminated.append((pid, sig)))
    provider = CodexProvider(Profile())
    with pytest.raises(TranslationError) as error:
        provider.translate("en", [{"id": "a", "text": "原文"}])
    assert error.value.code == "timeout"
    assert terminated and terminated[0][0] == 1234567
    assert provider.process is None


def test_cancelled_provider_cannot_start_new_process(monkeypatch):
    provider = CodexProvider(Profile())
    provider.cancel()
    with pytest.raises(TranslationError) as error:
        provider.translate("en", [{"id": "a", "text": "原文"}])
    assert error.value.code == "interrupted"
