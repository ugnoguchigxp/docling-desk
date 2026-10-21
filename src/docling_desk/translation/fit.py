"""Estimate the font size at which translated text fits the box of its source text.

The browser decides the real line breaks. This only needs to be close enough to
shrink long translations into their source box; widths are typical proportional
sans-serif advances in em, and CJK characters are full width.
"""

from __future__ import annotations

import math
import unicodedata

MIN_SCALE = 0.55
SHRINK = 0.94
NARROW = set(".,;:'!|iljI`")


def advance(char: str) -> float:
    """Approximate advance of one character, in em."""
    if unicodedata.combining(char):
        return 0.0
    width = unicodedata.east_asian_width(char)
    if width in {"W", "F"}:
        return 1.0
    if width == "H":
        return 0.5
    if char.isspace():
        return 0.3
    if char in NARROW:
        return 0.28
    if char.isdigit():
        return 0.56
    if char.isupper():
        return 0.66
    if width == "A":
        return 0.6
    return 0.52


def is_wide(char: str) -> bool:
    return unicodedata.east_asian_width(char) in {"W", "F"}


def line_count(text: str, width: float) -> int:
    """Lines needed at a width given in em, breaking at spaces and between wide characters."""
    width = max(width, 0.1)
    total = 0
    for paragraph in text.split("\n"):
        lines, used, word = 1, 0.0, 0.0
        units: list[float] = []
        for char in paragraph:
            if char.isspace():
                if word:
                    units.append(word)
                    word = 0.0
                units.append(-advance(char))  # a negative unit is a breakable space
            elif is_wide(char):
                if word:
                    units.append(word)
                    word = 0.0
                units.append(advance(char))
            else:
                word += advance(char)
        if word:
            units.append(word)
        for unit in units:
            if unit < 0:
                used += -unit if used else 0.0
                continue
            if used + unit > width and used > 0:
                lines += 1
                used = 0.0
            if unit > width:
                lines += math.ceil(unit / width) - 1
                used = unit % width
            else:
                used += unit
        total += lines
    return total


def fitted_size(text: str, width: float, height: float, size: float, line_height: float) -> float:
    """The largest size up to ``size`` (down to ``MIN_SCALE`` of it) whose lines fit the height."""
    current = size
    while current > size * MIN_SCALE:
        if line_count(text, width / current) * line_height * current <= height + 0.5:
            return current
        current = max(size * MIN_SCALE, current * SHRINK)
    return size * MIN_SCALE
