"""Read positioned text from a PDF page with PDFium and group it into blocks.

Callers hold ``pypdfium2_lock`` for the whole read. All coordinates returned
here are points in the displayed page (top-left origin, after the page's own
rotation and crop box), so they line up with a rendered page image.
"""

from __future__ import annotations

import ctypes
import math
import re
from dataclasses import dataclass, field
from statistics import median

import pypdfium2.raw as pdfium_raw

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
UNMAPPED = {0, 0xFFFE, 0xFFFF}
BULLET = re.compile(r"^\s*(?:[•・●○◦▪■□▶\-–—*]|\d{1,3}[.)）]|[（(]\d{1,3}[)）])\s")
# Axis-aligned reading directions only; other angles are not placed.
ANGLE_TOLERANCE = 3.0
# A horizontal jump wider than this many font sizes starts a new text run.
COLUMN_GAP = 2.5


@dataclass
class TextLine:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    color: tuple[int, int, int] | None
    rotation: int = 0


@dataclass
class TextBlock:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    pitch: float
    color: tuple[int, int, int] | None
    rotation: int = 0
    lines: list[TextLine] = field(default_factory=list)


def page_transform(page, width: float, height: float) -> tuple[float, ...]:
    """Affine matrix from PDF user space to the displayed page, in points."""
    scale, unit = 100, 1000.0

    def device(x: float, y: float) -> tuple[float, float]:
        dx, dy = ctypes.c_int(), ctypes.c_int()
        pdfium_raw.FPDF_PageToDevice(
            page.raw,
            0,
            0,
            round(width * scale),
            round(height * scale),
            0,
            x,
            y,
            ctypes.byref(dx),
            ctypes.byref(dy),
        )
        return dx.value / scale, dy.value / scale

    ox, oy = device(0, 0)
    ex, ey = device(unit, 0)
    fx, fy = device(0, unit)
    return (
        (ex - ox) / unit,
        (ey - oy) / unit,
        (fx - ox) / unit,
        (fy - oy) / unit,
        ox,
        oy,
    )


def _apply(matrix: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _snap(angle: float) -> int | None:
    degrees = math.degrees(angle) % 360
    nearest = round(degrees / 90) % 4
    delta = abs((degrees - nearest * 90 + 180) % 360 - 180)
    return nearest * 90 if delta <= ANGLE_TOLERANCE else None


@dataclass
class _Char:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    ox: float
    oy: float
    size: float
    angle: float
    index: int


def _read_chars(textpage, matrix: tuple[float, ...]) -> list[_Char | None]:
    """Characters in PDFium's reading order; ``None`` marks a line break."""
    count = textpage.count_chars()
    chars: list[_Char | None] = []
    high = 0
    x, y = ctypes.c_double(), ctypes.c_double()
    text_matrix = pdfium_raw.FS_MATRIX()
    for index in range(count):
        code = pdfium_raw.FPDFText_GetUnicode(textpage, index)
        if code in (10, 13):
            if chars and chars[-1] is not None:
                chars.append(None)
            high = 0
            continue
        if 0xD800 <= code < 0xDC00:
            high = code
            continue
        if 0xDC00 <= code < 0xE000:
            if not high:
                continue
            code, high = 0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00), 0
        high = 0
        if code in UNMAPPED or code < 32 or 0x7F <= code < 0xA0:
            continue
        left, bottom, right, top = textpage.get_charbox(index, loose=True)
        x0, y0 = _apply(matrix, left, bottom)
        x1, y1 = _apply(matrix, right, top)
        pdfium_raw.FPDFText_GetCharOrigin(textpage, index, ctypes.byref(x), ctypes.byref(y))
        ox, oy = _apply(matrix, x.value, y.value)
        pdfium_raw.FPDFText_GetMatrix(textpage, index, ctypes.byref(text_matrix))
        scale = math.sqrt(abs(text_matrix.a * text_matrix.d - text_matrix.b * text_matrix.c))
        size = pdfium_raw.FPDFText_GetFontSize(textpage, index) * (scale or 1.0)
        chars.append(
            _Char(
                chr(code),
                min(x0, x1),
                min(y0, y1),
                max(x0, x1),
                max(y0, y1),
                ox,
                oy,
                size,
                pdfium_raw.FPDFText_GetCharAngle(textpage, index),
                index,
            )
        )
    return chars


def _fill_color(textpage, index: int) -> tuple[int, int, int] | None:
    red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
    ok = pdfium_raw.FPDFText_GetFillColor(
        textpage,
        index,
        ctypes.byref(red),
        ctypes.byref(green),
        ctypes.byref(blue),
        ctypes.byref(alpha),
    )
    if not ok or alpha.value == 0:
        return None
    return red.value, green.value, blue.value


def _direction(run: list[_Char], matrix: tuple[float, ...]) -> int | None:
    first, last = run[0], run[-1]
    dx, dy = last.ox - first.ox, last.oy - first.oy
    if math.hypot(dx, dy) > 0.5 * max(first.size, 1.0):
        return _snap(math.atan2(dy, dx))
    # Single glyphs report a clockwise angle in user space.
    a, b, c, d, _, _ = matrix
    ux, uy = math.cos(run[0].angle), -math.sin(run[0].angle)
    return _snap(math.atan2(b * ux + d * uy, a * ux + c * uy))


def _along(char: _Char, rotation: int) -> tuple[float, float]:
    """Start and end of a glyph along the reading direction."""
    if rotation == 0:
        return char.x0, char.x1
    if rotation == 180:
        return -char.x1, -char.x0
    if rotation == 90:
        return char.y0, char.y1
    return -char.y1, -char.y0


def _across(char: _Char, rotation: int) -> float:
    return (char.y0 + char.y1) / 2 if rotation in (0, 180) else (char.x0 + char.x1) / 2


def _line_text(run: list[_Char]) -> str:
    text = "".join(char.text for char in run)
    return "".join(LIGATURES.get(c, c) for c in text).strip()


def _split_runs(chars: list[_Char | None], matrix: tuple[float, ...]) -> list[tuple[int, list]]:
    """Split PDFium lines into runs that share one reading direction and position."""
    runs: list[tuple[int, list[_Char]]] = []
    line: list[_Char] = []

    def flush() -> None:
        nonlocal line
        if not line:
            return
        rotation = _direction([c for c in line if c.text.strip()] or line, matrix)
        if rotation is not None:
            current: list[_Char] = []
            for char in line:
                if current and char.text.strip():
                    previous = next((c for c in reversed(current) if c.text.strip()), None)
                    if previous is not None:
                        _, end = _along(previous, rotation)
                        start, _ = _along(char, rotation)
                        size = max(previous.size, char.size, 1.0)
                        shifted = abs(_across(char, rotation) - _across(previous, rotation))
                        if start - end > COLUMN_GAP * size or start - end < -size or shifted > size:
                            runs.append((rotation, current))
                            current = []
                current.append(char)
            runs.append((rotation, current))
        line = []

    for char in chars:
        if char is None:
            flush()
        else:
            line.append(char)
    flush()
    return runs


def page_lines(textpage, matrix: tuple[float, ...]) -> list[TextLine]:
    chars = _read_chars(textpage, matrix)
    lines = []
    for rotation, run in _split_runs(chars, matrix):
        visible = [c for c in run if c.text.strip()]
        text = _line_text(run)
        if not visible or not text:
            continue
        sizes = [c.size for c in visible if c.size > 0]
        lines.append(
            TextLine(
                text,
                min(c.x0 for c in visible),
                min(c.y0 for c in visible),
                max(c.x1 for c in visible),
                max(c.y1 for c in visible),
                median(sizes) if sizes else max(c.y1 - c.y0 for c in visible),
                _fill_color(textpage, visible[0].index),
                rotation,
            )
        )
    return lines


def _mergeable(above: TextLine, below: TextLine) -> bool:
    size = max(above.size, below.size, 1.0)
    gap = below.y0 - above.y1
    if not -0.4 * size <= gap <= 0.45 * size:
        return False
    ratio = above.size / below.size if below.size else 1.0
    if not 0.8 <= ratio <= 1.25:
        return False
    if BULLET.match(below.text):
        return False
    if min(above.x1, below.x1) - max(above.x0, below.x0) <= 0:
        return False
    tolerance = 1.5 * size
    return (
        abs(above.x0 - below.x0) <= tolerance
        or abs(above.x1 - below.x1) <= tolerance
        or abs((above.x0 + above.x1) / 2 - (below.x0 + below.x1) / 2) <= tolerance
    )


def group_blocks(lines: list[TextLine]) -> list[TextBlock]:
    """Paragraph-like blocks, in reading order (top to bottom, then left to right)."""
    flat = [line for line in lines if line.rotation == 0]
    order = sorted(range(len(flat)), key=lambda i: (flat[i].y0, flat[i].x0))
    parent = list(range(len(flat)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    below_taken: set[int] = set()
    for position, i in enumerate(order):
        above = flat[i]
        for j in order[position + 1 :]:
            below = flat[j]
            if below.y0 - above.y1 > 0.45 * max(above.size, 1.0):
                break
            if j in below_taken or not _mergeable(above, below):
                continue
            below_taken.add(j)
            parent[find(j)] = find(i)
            break
    groups: dict[int, list[TextLine]] = {}
    for i in order:
        groups.setdefault(find(i), []).append(flat[i])
    blocks = [_block(group) for group in groups.values()]
    blocks.extend(_block([line]) for line in lines if line.rotation != 0)
    blocks.sort(key=lambda b: (round(b.y0), b.x0))
    return blocks


def _block(group: list[TextLine]) -> TextBlock:
    group = sorted(group, key=lambda line: line.y0)
    tops = [b.y0 - a.y0 for a, b in zip(group, group[1:], strict=False)]
    size = median(line.size for line in group)
    rotation = group[0].rotation
    extent = (
        max(line.y1 for line in group) - min(line.y0 for line in group)
        if rotation in (0, 180)
        else max(line.x1 for line in group) - min(line.x0 for line in group)
    )
    return TextBlock(
        "\n".join(line.text for line in group),
        min(line.x0 for line in group),
        min(line.y0 for line in group),
        max(line.x1 for line in group),
        max(line.y1 for line in group),
        size,
        median(tops) if tops else extent,
        next((line.color for line in group if line.color), None),
        rotation,
        group,
    )


def read_page(page) -> list[TextLine]:
    """Positioned lines of one page; the caller holds the PDFium lock."""
    width, height = page.get_size()
    textpage = page.get_textpage()
    try:
        return page_lines(textpage, page_transform(page, width, height))
    finally:
        textpage.close()
