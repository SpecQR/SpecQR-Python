"""Portable, bounded SVG/PNG/RGBA rendering using only the standard library.

Geometry, color semantics and stored-DEFLATE PNG layout are adapted from
SpecQR JavaScript 15ad15e5 (MIT, Copyright 2026 SpecQR contributors).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from html import escape
import re
import struct
from urllib.parse import quote
import zlib
from collections.abc import Sequence
from .errors import InvalidColorError, InvalidInputError

RASTER_PIXEL_BUDGET = 4 * 1024 * 1024
SVG_CHARACTER_BUDGET = 8 * 1024 * 1024
DATA_URL_CHARACTER_BUDGET = 32 * 1024 * 1024
MAX_GEOMETRY_INTEGER = 2**53 - 1
Matrix = tuple[tuple[bool, ...], ...]

@dataclass(frozen=True, slots=True)
class Pixels:
    width: int
    height: int
    pixels: bytes


def parse_color(value: str, *, strict: bool = True) -> tuple[int, int, int, int] | None:
    if not isinstance(value, str):
        raise InvalidColorError("color must be a string")
    text = value.strip().lower()
    named = {"black": (0, 0, 0, 255), "white": (255, 255, 255, 255),
             "transparent": (0, 0, 0, 0)}
    if text in named:
        return named[text]
    if re.fullmatch(r"#[0-9a-f]{3,4}|#[0-9a-f]{6}(?:[0-9a-f]{2})?", text):
        h = text[1:]
        if len(h) in (3, 4):
            values = [int(c * 2, 16) for c in h]
        else:
            values = [int(h[i:i + 2], 16) for i in range(0, len(h), 2)]
        if len(values) == 3:
            values.append(255)
        return tuple(values)  # type: ignore[return-value]
    if strict:
        raise InvalidColorError('color must be hex, "black", "white", or "transparent"')
    return None


def contrast_ratio(foreground: tuple[int, int, int, int], background: tuple[int, int, int, int]) -> float:
    def luminance(color: tuple[int, int, int, int]) -> float:
        v = [c / 255 for c in color[:3]]
        v = [x / 12.92 if x <= 0.03928 else ((x + .055) / 1.055)**2.4 for x in v]
        return sum(a * b for a, b in zip(v, (.2126, .7152, .0722)))
    a, b = luminance(foreground), luminance(background)
    return (max(a, b) + .05) / (min(a, b) + .05)


def _matrix(matrix: Sequence[Sequence[bool]]) -> Matrix:
    if not isinstance(matrix, Sequence) or isinstance(matrix, (str, bytes, bytearray)):
        raise InvalidInputError("matrix must be a square sequence of boolean rows")
    n = len(matrix)
    if not 1 <= n <= 177:
        raise InvalidInputError("matrix size must be 1..177")
    for row in matrix:
        if not isinstance(row, Sequence) or len(row) != n or any(type(x) is not bool for x in row):
            raise InvalidInputError("matrix must be square and contain only bool values")
    return tuple(tuple(row) for row in matrix)


def _geometry(matrix: Sequence[Sequence[bool]], margin: int, scale: int, *, raster: bool) -> tuple[Matrix, int]:
    for name, value, lower in (("margin", margin, 0), ("scale", scale, 1)):
        if type(value) is not int or not lower <= value <= MAX_GEOMETRY_INTEGER:
            raise InvalidInputError(f"{name} must be an integer from {lower} to {MAX_GEOMETRY_INTEGER}")
    mat = _matrix(matrix)
    dimension = (len(mat) + margin * 2) * scale
    if dimension > MAX_GEOMETRY_INTEGER:
        raise InvalidInputError("render dimension exceeds the safe integer bound")
    if raster and dimension * dimension > RASTER_PIXEL_BUDGET:
        raise InvalidInputError("render geometry exceeds the 4 Mi-pixel budget")
    return mat, dimension


def to_pixels(matrix: Sequence[Sequence[bool]], *, margin: int = 4, scale: int = 8,
              foreground: str = "#000000", background: str = "#ffffff") -> Pixels:
    mat, dimension = _geometry(matrix, margin, scale, raster=True)
    fg, bg = bytes(parse_color(foreground)), bytes(parse_color(background))  # type: ignore[arg-type]
    blank = bg * dimension
    lines = [blank] * (margin * scale)
    for row in mat:
        line = bg * (margin * scale) + b"".join((fg if bit else bg) * scale for bit in row) + bg * (margin * scale)
        lines.extend([line] * scale)
    lines.extend([blank] * (margin * scale))
    return Pixels(dimension, dimension, b"".join(lines))


def to_svg(matrix: Sequence[Sequence[bool]], *, margin: int = 4, scale: int = 8,
           foreground: str = "#000000", background: str = "#ffffff") -> str:
    mat, dimension = _geometry(matrix, margin, scale, raster=False)
    if not isinstance(foreground, str) or not isinstance(background, str):
        raise InvalidColorError("SVG colors must be strings")
    for value in (foreground, background):
        if any(not (c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF
                    or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
            raise InvalidColorError("SVG colors must contain valid XML characters")
    # Validate and budget before escaping/copying arbitrary CSS input.
    if len(foreground) + len(background) > SVG_CHARACTER_BUDGET // 6:
        raise InvalidInputError("SVG color strings exceed rendering budget")
    bound = sum(sum(row) for row in mat) * (7 + len(str(dimension)) * 2 + len(str(scale)) * 3)
    bound += 512 + (len(foreground) + len(background)) * 6 + len(str(dimension)) * 4
    if bound > SVG_CHARACTER_BUDGET:
        raise InvalidInputError("SVG output exceeds the 8 Mi-character budget")
    path = "".join(f"M{(x + margin) * scale},{(y + margin) * scale}h{scale}v{scale}h-{scale}z"
                   for y, row in enumerate(mat) for x, dark in enumerate(row) if dark)
    fg, bg = escape(foreground, quote=True), escape(background, quote=True)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{dimension}" height="{dimension}" '
            f'viewBox="0 0 {dimension} {dimension}" role="img">'
            f'<rect width="100%" height="100%" fill="{bg}"/>'
            f'<path fill="{fg}" d="{path}"/></svg>')


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def to_png(matrix: Sequence[Sequence[bool]], *, margin: int = 4, scale: int = 8,
           foreground: str = "#000000", background: str = "#ffffff") -> bytes:
    image = to_pixels(matrix, margin=margin, scale=scale, foreground=foreground, background=background)
    stride = image.width * 4
    raw = b"".join(b"\x00" + image.pixels[i:i + stride] for i in range(0, len(image.pixels), stride))
    # Fixed stored blocks are deterministic across Python/zlib versions.
    blocks = []
    for i in range(0, len(raw), 65535):
        block = raw[i:i + 65535]
        blocks.append(bytes([int(i + len(block) == len(raw))]) + struct.pack("<HH", len(block), len(block) ^ 65535) + block)
    stream = b"\x78\x01" + b"".join(blocks) + struct.pack(">I", zlib.adler32(raw))
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", stream) + _chunk(b"IEND", b"")


def to_svg_data_url(matrix: Sequence[Sequence[bool]], **options: object) -> str:
    svg = to_svg(matrix, **options)  # type: ignore[arg-type]
    if len(svg) * 3 + 31 > DATA_URL_CHARACTER_BUDGET:
        raise InvalidInputError("SVG data URL exceeds rendering budget")
    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="~()*!.'-_")


def to_png_data_url(matrix: Sequence[Sequence[bool]], **options: object) -> str:
    data = to_png(matrix, **options)  # type: ignore[arg-type]
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
