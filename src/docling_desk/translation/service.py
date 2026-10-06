"""Persist each complete unit, independent of browser lifetime and provider details."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import uniform
from time import monotonic
from uuid import uuid4

from docling_desk.translation.provider import (
    PROMPT_VERSION,
    TranslationError,
    TranslationProvider,
    configured_profile,
    provider_for,
)
from docling_desk.translation.schedule import Schedule, configured_schedule
from docling_desk.translation.source import source_map
from docling_desk.translation.store import (
    ACTIVE,
    LANGUAGES,
    LOCK,
    atomic_json,
    now,
    read_result,
    result_path,
    usable,
)

PROTECTED = re.compile(
    r"https?://[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|`[^`]*`|\r?\n|\d+(?:[.,:/-]\d+)*"
)


def prepare(unit: dict, limit: int) -> tuple[list[list[dict]], dict]:
    """Split only internally, with exact restoration of whitespace and protected literals."""
    parts, restoration = [], {}
    for segment in unit["segments"]:
        tokens = {}

        def protect(match):
            token = f"__DL_{segment['id']}_{len(tokens)}__"
            tokens[token] = match.group()
            return token

        protected = PROTECTED.sub(protect, segment["source_text"])
        # Tokens are indivisible, including at a batch boundary.
        atoms = re.findall(r"__DL_[A-Za-z0-9_]+?__|.", protected, re.S)
        pieces, current = [], ""
        for atom in atoms:
            if current and len(current) + len(atom) > limit:
                pieces.append(current)
                current = ""
            current += atom
        pieces.append(current)
        ids = []
        for index, piece in enumerate(pieces):
            identifier = f"{segment['id']}:p{index}"
            ids.append(identifier)
            leading = piece[: len(piece) - len(piece.lstrip())]
            trailing = piece[len(piece.rstrip()) :] if piece.rstrip() else ""
            text = piece.strip()
            restoration[identifier] = {
                "leading": leading,
                "trailing": trailing,
                "tokens": {k: v for k, v in tokens.items() if k in piece},
                "literal": piece if not text else None,
            }
            if text:
                parts.append({"id": identifier, "text": text})
        restoration[segment["id"]] = ids
    batches, batch, size = [], [], 0
    for part in parts:
        if batch and size + len(part["text"]) > limit:
            batches.append(batch)
            batch, size = [], 0
        batch.append(part)
        size += len(part["text"])
    if batch:
        batches.append(batch)
    return batches, restoration


def restore(unit: dict, translated: dict[str, str], restoration: dict) -> dict[str, str]:
    result = {}
    for segment in unit["segments"]:
        pieces = []
        for identifier in restoration[segment["id"]]:
            details = restoration[identifier]
            if details["literal"] is not None:
                pieces.append(details["literal"])
                continue
            if (
                identifier not in translated
                or not isinstance(translated[identifier], str)
                or not translated[identifier].strip()
            ):
                raise TranslationError("invalid_response", "訳文に欠落があります。")
            text = translated[identifier].strip()
            found = re.findall(r"__DL_[A-Za-z0-9_]+?__", text)
            if sorted(found) != sorted(details["tokens"]):
                raise TranslationError(
                    "invalid_response", "数値・URL・改行などの保護対象が変更されました。"
                )
            for token, literal in details["tokens"].items():
                text = text.replace(token, literal)
            pieces.append(details["leading"] + text + details["trailing"])
        result[segment["id"]] = "".join(pieces)
    return result


class TranslationManager:
    def __init__(self):
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translation")
        self.slots = threading.BoundedSemaphore(3)
        self.stopping = threading.Event()
        self.active_provider: TranslationProvider | None = None
        # These are shared by every document/language submitted to this worker.
        self.finished_at: float | None = None
        self.previous_interval = 0
        self.retry_not_before = 0.0
        self.cooldown_reason = "interval"

    def close(self) -> None:
        self.stopping.set()
        if self.active_provider and hasattr(self.active_provider, "cancel"):
            self.active_provider.cancel()
        self.worker.shutdown(wait=False, cancel_futures=False)

    def submit(
        self,
        folder: Path,
        language: str,
        unit_ids: list[str] | None,
        force: bool = False,
        provider: TranslationProvider | None = None,
        interval_seconds: int | None = None,
    ) -> dict:
        if language not in LANGUAGES:
            raise TranslationError("input", "翻訳先は英語か日本語を指定してください。")
        profile = provider.profile if provider else configured_profile()
        schedule = configured_schedule(interval_seconds)
        with LOCK:
            source = source_map(folder)
            ids = unit_ids if unit_ids is not None else [u["id"] for u in source["units"]]
            if (
                not ids
                or len(ids) != len(set(ids))
                or not set(ids).issubset({u["id"] for u in source["units"]})
            ):
                raise TranslationError("input", "翻訳対象の単位が不正です。")
            chosen = [u for u in source["units"] if u["id"] in ids]
            pending, reused = [], []
            for unit in chosen:
                record = read_result(folder, language, unit["id"])
                if record["state"] in ACTIVE:
                    if (
                        record.get("requested_source_hash") != unit["source_hash"]
                        or record.get("requested_config", {}).get("config_hash")
                        != profile.metadata()["config_hash"]
                    ):
                        raise TranslationError(
                            "conflict",
                            "この単位は別の設定で翻訳中です。完了後に再実行してください。",
                        )
                    reused.append(unit["id"])
                elif (
                    not force
                    and usable(record, unit)
                    and record["result"].get("config_hash") == profile.metadata()["config_hash"]
                ):
                    reused.append(unit["id"])
                else:
                    pending.append((unit, record))
            if not pending:
                return {"accepted": [], "reused": reused}
            provider = provider or provider_for(profile)
            if any(unit["segments"] for unit, _ in pending):
                provider.preflight()
            if self.stopping.is_set():
                raise TranslationError(
                    "not_configured", "翻訳処理は終了中です。再起動後に実行してください。"
                )
            if not self.slots.acquire(blocking=False):
                raise TranslationError(
                    "queue_full", "翻訳処理待ちは最大3件です。完了後に実行してください。"
                )
            attempt = uuid4().hex
            try:
                for unit, record in pending:
                    record.update(
                        schema_version=1,
                        unit_id=unit["id"],
                        target_language=language,
                        state="queued",
                        attempt_id=attempt,
                        requested_source_hash=unit["source_hash"],
                        requested_config=profile.metadata(),
                        updated_at=now(),
                        error=None,
                        error_code=None,
                        schedule=asdict(schedule),
                        wait_reason=None,
                        next_attempt_at=None,
                        retry_attempt=0,
                    )
                    atomic_json(result_path(folder, language, unit["id"]), record)
                self.worker.submit(
                    self._run,
                    folder,
                    language,
                    [u for u, _ in pending],
                    attempt,
                    provider,
                    schedule,
                )
            except Exception as exc:
                self.slots.release()
                for unit, _ in pending:
                    self._terminal(
                        folder,
                        language,
                        unit,
                        attempt,
                        "failed",
                        error="翻訳の保存または受付に失敗しました。",
                        error_code="storage",
                    )
                raise TranslationError("storage", "翻訳の保存または受付に失敗しました。") from exc
            return {"accepted": [u["id"] for u, _ in pending], "reused": reused}

    def _change(
        self, folder: Path, language: str, unit: dict, attempt: str, state: str, **updates
    ) -> bool:
        with LOCK:
            record = read_result(folder, language, unit["id"])
            if (
                record.get("attempt_id") != attempt
                or record.get("requested_source_hash") != unit["source_hash"]
            ):
                return False
            record.update(state=state, updated_at=now(), **updates)
            if state not in ACTIVE:
                record.update(wait_reason=None, next_attempt_at=None)
            atomic_json(result_path(folder, language, unit["id"]), record)
            return True

    def _run(
        self,
        folder: Path,
        language: str,
        units: list[dict],
        attempt: str,
        provider: TranslationProvider,
        schedule: Schedule,
    ) -> None:
        abort: TranslationError | None = None
        self.active_provider = provider
        try:
            for unit in units:
                if self.stopping.is_set():
                    self._terminal(
                        folder,
                        language,
                        unit,
                        attempt,
                        "interrupted",
                        error="サーバー停止で中断しました。再実行できます。",
                    )
                    continue
                if abort:
                    self._terminal(
                        folder,
                        language,
                        unit,
                        attempt,
                        "failed",
                        error=str(abort),
                        error_code=abort.code,
                    )
                    continue
                try:
                    if not self._change(folder, language, unit, attempt, "running"):
                        continue
                    batches, restoration = prepare(unit, provider.profile.max_input_chars)
                    translated = {}
                    for batch in batches:
                        response = self._translate(
                            folder, language, unit, attempt, provider, batch, schedule
                        )
                        if set(response) != {s["id"] for s in batch}:
                            raise TranslationError(
                                "invalid_response", "翻訳結果のIDが一致しません。"
                            )
                        translated.update(response)
                        if self.stopping.is_set():
                            raise TranslationError(
                                "interrupted", "サーバー停止で翻訳を中断しました。"
                            )
                    values = restore(unit, translated, restoration)
                    with LOCK:
                        current = next(
                            u for u in source_map(folder)["units"] if u["id"] == unit["id"]
                        )
                        if current["source_hash"] != unit["source_hash"]:
                            raise TranslationError(
                                "source_changed",
                                "翻訳中に原文が変更されました。再翻訳してください。",
                            )
                        result = {
                            "source_hash": unit["source_hash"],
                            **provider.profile.metadata(),
                            "prompt_version": PROMPT_VERSION,
                            "created_at": now(),
                            "translations": values,
                        }
                        if not self._change(
                            folder,
                            language,
                            unit,
                            attempt,
                            "completed",
                            result=result,
                            error=None,
                            error_code=None,
                        ):
                            raise TranslationError("conflict", "翻訳の保存先が更新されました。")
                except TranslationError as exc:
                    self._terminal(
                        folder,
                        language,
                        unit,
                        attempt,
                        "interrupted" if self.stopping.is_set() else "failed",
                        error=str(exc),
                        error_code=exc.code,
                    )
                    if exc.code in {
                        "authentication",
                        "model_unavailable",
                        "rate_limit",
                        "quota",
                        "configuration",
                        "not_configured",
                    }:
                        abort = exc
                except Exception:
                    # Never expose original content or credentials in public error messages.
                    self._terminal(
                        folder,
                        language,
                        unit,
                        attempt,
                        "failed",
                        error="翻訳データの処理または保存に失敗しました。",
                        error_code="storage",
                    )
        finally:
            self.active_provider = None
            self.slots.release()

    def _translate(self, folder, language, unit, attempt, provider, batch, schedule) -> dict:
        for retry in range(schedule.max_retries + 1):
            not_before = self.retry_not_before
            if self.finished_at is not None:
                not_before = max(
                    not_before,
                    self.finished_at + max(self.previous_interval, schedule.interval_seconds),
                )
            delay = max(0.0, not_before - monotonic())
            if delay > schedule.max_retry_wait_seconds and self.retry_not_before > monotonic():
                raise TranslationError(
                    "rate_limit",
                    "サービス指定の待機時間が上限を超えています。後で再実行してください。",
                )
            if delay > 0:
                next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                reason = self.cooldown_reason if self.retry_not_before > monotonic() else "interval"
                if not self._change(
                    folder,
                    language,
                    unit,
                    attempt,
                    "waiting",
                    wait_reason=reason,
                    next_attempt_at=next_attempt.isoformat(),
                    retry_attempt=retry,
                ):
                    raise TranslationError("conflict", "翻訳の保存先が更新されました。")
                if self.stopping.wait(delay):
                    raise TranslationError("interrupted", "サーバー停止で翻訳を中断しました。")
            if self.stopping.is_set():
                raise TranslationError("interrupted", "サーバー停止で翻訳を中断しました。")
            if not self._change(
                folder,
                language,
                unit,
                attempt,
                "running",
                wait_reason=None,
                next_attempt_at=None,
                retry_attempt=retry,
            ):
                raise TranslationError("conflict", "翻訳の保存先が更新されました。")
            try:
                return provider.translate(language, batch)
            except TranslationError as exc:
                if exc.retryable:
                    base = schedule.retry_seconds * 2**retry
                    delay = max(base, exc.retry_after or 0) + uniform(0, base * 0.1)
                    # Keep the cooldown even after retry exhaustion, so a new
                    # document/request cannot immediately hit the same service.
                    self.retry_not_before = max(self.retry_not_before, monotonic() + delay)
                    self.cooldown_reason = "rate_limit" if exc.code == "rate_limit" else "retry"
                if not exc.retryable or retry == schedule.max_retries:
                    raise
            finally:
                self.finished_at = monotonic()
                self.previous_interval = schedule.interval_seconds
        raise AssertionError("unreachable")

    def _terminal(self, *args, **kwargs) -> bool:
        # A full disk must not escape the worker and strand the remaining units.
        # Failed persistence keeps the previous atomic file intact; startup recovers
        # any active state that could not be updated while storage was unavailable.
        try:
            return self._change(*args, **kwargs)
        except (OSError, ValueError, KeyError):
            return False


def overview(folder: Path) -> dict:
    with LOCK:
        source = source_map(folder)
        units = []
        for unit in source["units"]:
            row = {k: unit[k] for k in ("id", "kind", "number", "mode", "excluded_count")}
            for key in ("preview_unavailable_reason", "unavailable_reason"):
                if unit.get(key):
                    row[key] = unit[key]
            row["segments_count"] = len(unit["segments"])
            row["languages"] = {}
            for language in sorted(LANGUAGES):
                record = read_result(folder, language, unit["id"])
                row["languages"][language] = {
                    "state": record["state"],
                    "available": usable(record, unit),
                    "stale": bool(record.get("result")) and not usable(record, unit),
                    "error": record.get("error"),
                    "updated_at": record.get("updated_at"),
                    "result_created_at": (record.get("result") or {}).get("created_at"),
                    "wait_reason": record.get("wait_reason"),
                    "next_attempt_at": record.get("next_attempt_at"),
                    "retry_attempt": record.get("retry_attempt", 0),
                    "interval_seconds": record.get("schedule", {}).get("interval_seconds"),
                }
            units.append(row)
        profile_info, configuration_error, scheduling = {}, None, {}
        try:
            profile = configured_profile()
            profile_info = profile.metadata()
            scheduling = asdict(configured_schedule())
            provider_for(profile).preflight()
        except TranslationError as exc:
            configuration_error = str(exc)
        return {
            "units": units,
            "unlocated_count": source["unlocated_count"],
            "profile": profile_info,
            "configuration_error": configuration_error,
            "scheduling": scheduling,
        }
