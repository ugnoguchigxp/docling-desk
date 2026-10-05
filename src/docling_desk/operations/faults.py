"""Test-only failure injection. Normal runs leave DOCLING_FAULT unset."""

from __future__ import annotations

import os


class Fault(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(name)


def checkpoint(name: str) -> None:
    if os.environ.get("DOCLING_QUALITY_FAULT") == name:
        raise Fault(name)
    raw = os.environ.get("DOCLING_FAULT", "")
    mode, separator, target = raw.partition(":")
    if not separator or target != name:
        return
    if mode == "exit":
        os._exit(86)
    if mode == "raise":
        raise Fault(name)
