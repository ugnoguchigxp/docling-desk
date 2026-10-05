"""Scheduling settings are separate from the translation content/cache identity."""

from __future__ import annotations

import os
from dataclasses import dataclass

from docling_desk.translation.provider import TranslationError


@dataclass(frozen=True)
class Schedule:
    interval_seconds: int = 60
    max_retries: int = 2
    retry_seconds: int = 60
    max_retry_wait_seconds: int = 3600


def configured_schedule(interval_seconds: int | None = None) -> Schedule:
    try:
        schedule = Schedule(
            interval_seconds=(
                int(os.environ.get("DOCLING_TRANSLATION_INTERVAL_SECONDS", "60"))
                if interval_seconds is None
                else interval_seconds
            ),
            max_retries=int(os.environ.get("DOCLING_TRANSLATION_MAX_RETRIES", "2")),
            retry_seconds=int(os.environ.get("DOCLING_TRANSLATION_RETRY_SECONDS", "60")),
            max_retry_wait_seconds=int(
                os.environ.get("DOCLING_TRANSLATION_MAX_RETRY_WAIT_SECONDS", "3600")
            ),
        )
    except ValueError as exc:
        raise TranslationError("configuration", "翻訳の間隔・再試行設定が不正です。") from exc
    if (
        type(schedule.interval_seconds) is not int
        or not 0 <= schedule.interval_seconds <= 3600
        or not 0 <= schedule.max_retries <= 5
        or not 0 <= schedule.retry_seconds <= 3600
        or not 1 <= schedule.max_retry_wait_seconds <= 86400
    ):
        raise TranslationError("configuration", "翻訳の間隔・再試行設定が範囲外です。")
    return schedule
