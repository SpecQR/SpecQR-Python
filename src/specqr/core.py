"""Dependency-free QR block coding, module placement, and mask selection.

Ported from SpecQR src/core/{codewords,galois-field,reed-solomon,matrix,mask}.js
at 15ad15e5c770ea0e39072f8f88b2733018f02ffd. Copyright (c) 2026 SpecQR
contributors. Distributed under the MIT license. Matrix rows are indexed [y][x].
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from .errors import DataTooLongError, InvalidInputError
from .tables import (
    alignment_positions,
    block_info,
    data_codeword_count,
    format_bits,
    raw_codeword_count,
    size,
    validate_level,
)

Matrix = tuple[tuple[bool, ...], ...]
_MAX_CODEWORDS = 3706


@dataclass(frozen=True, slots=True)
class CodewordBlock:
    data: bytes
    ecc: bytes


@dataclass(frozen=True, slots=True)
class InterleavedResult:
    codewords: bytes
    blocks: tuple[CodewordBlock, ...]
    data_codewords: int
    error_correction_codewords: int
    total_codewords: int


@dataclass(frozen=True, slots=True)
class MaskPenalty:
    mask_pattern: int
    penalty: int


@dataclass(frozen=True, slots=True)
class MatrixResult:
    matrix: Matrix
    mask_pattern: int
    penalty: int
    mask_penalties: tuple[MaskPenalty, ...]


def _integer(value: int, lower: int, upper: int, label: str) -> None:
    if type(value) is not int or not lower <= value <= upper:
        raise InvalidInputError(f"{label} must be an integer from {lower} to {upper}")


def _bytes(data: bytes, label: str) -> None:
    if not isinstance(data, bytes):
        raise InvalidInputError(f"{label} must be immutable bytes")
    if len(data) > _MAX_CODEWORDS:
        raise InvalidInputError(f"{label} exceeds the maximum QR codeword count")


def pad_data_bits(bits: Sequence[int], version: int, level: str) -> bytes:
    """Pad complete segment bits to the exact data capacity of a symbol.

    Input must be a finite sequence of exact integer 0/1 values, with no implicit
    truth-value conversion. Length is checked before any allocation or traversal.
    """
    capacity = data_codeword_count(version, level)
    if not isinstance(bits, Sequence) or isinstance(bits, (str, bytes, bytearray, memoryview)):
        raise InvalidInputError("Bits must be a finite sequence of integer 0/1 values")
    try:
        bit_length = len(bits)
    except OverflowError as error:
        raise DataTooLongError("Input exceeds the maximum QR data capacity") from error
    if bit_length > capacity * 8:
        raise DataTooLongError(f"Input requires {bit_length} bits, but version {version}-{level} has {capacity * 8} data bits")
    result = bytearray(capacity)
    for index in range(bit_length):
        value = bits[index]
        if type(value) is not int or value not in (0, 1):
            raise InvalidInputError("Bits must contain only integer 0/1 values")
        result[index // 8] |= value << (7 - index % 8)
    terminated_length = bit_length + min(4, capacity * 8 - bit_length)
    padded_byte_length = (terminated_length + 7) // 8
    for index in range(padded_byte_length, capacity):
        result[index] = 0xEC if (index - padded_byte_length) % 2 == 0 else 0x11
    return bytes(result)


def _gf_multiply(left: int, right: int) -> int:
    product = 0
    while right:
        if right & 1:
            product ^= left
        right >>= 1
        left <<= 1
        if left & 0x100:
            left ^= 0x11D
    return product


def gf_multiply(left: int, right: int) -> int:
    """Multiply two bytes in the QR GF(256) field (reduction polynomial 0x11D)."""
    _integer(left, 0, 255, "Left operand")
    _integer(right, 0, 255, "Right operand")
    return _gf_multiply(left, right)


@lru_cache(maxsize=255)
def _divisor(degree: int) -> bytes:
    coefficients = [1] + [0] * degree
    root = 1
    for factor in range(degree):
        for index in range(factor + 1, 0, -1):
            coefficients[index] ^= _gf_multiply(coefficients[index - 1], root)
        root = _gf_multiply(root, 2)
    return bytes(coefficients)


def reed_solomon_divisor(degree: int) -> bytes:
    """Return descending-power coefficients, including the leading monic 1."""
    _integer(degree, 1, 255, "Reed-Solomon degree")
    return _divisor(degree)


def _remainder(data: bytes, divisor: bytes) -> bytes:
    degree = len(divisor) - 1
    result = bytearray(degree)
    for value in data:
        factor = value ^ result[0]
        for index in range(degree - 1):
            result[index] = result[index + 1] ^ _gf_multiply(divisor[index + 1], factor)
        result[-1] = _gf_multiply(divisor[-1], factor)
    return bytes(result)


def reed_solomon_remainder(data: bytes, degree: int) -> bytes:
    """Compute QR Reed-Solomon parity for at most one symbol's codewords."""
    _bytes(data, "Data")
    return _remainder(data, reed_solomon_divisor(degree))


def interleave_codewords(data: bytes, version: int, level: str) -> InterleavedResult:
    info = block_info(version, level)
    _bytes(data, "Data codewords")
    if len(data) != info.data_codewords:
        raise InvalidInputError(f"Expected {info.data_codewords} data codewords; got {len(data)}")
    short_count = info.blocks - info.raw_codewords % info.blocks
    short_data_length = info.raw_codewords // info.blocks - info.ecc_per_block
    divisor = _divisor(info.ecc_per_block)
    blocks = []
    offset = 0
    for index in range(info.blocks):
        length = short_data_length + (index >= short_count)
        block_data = data[offset:offset + length]
        blocks.append(CodewordBlock(block_data, _remainder(block_data, divisor)))
        offset += length
    result = bytearray()
    for column in range(short_data_length + 1):
        for block in blocks:
            if column < len(block.data):
                result.append(block.data[column])
    for column in range(info.ecc_per_block):
        for block in blocks:
            result.append(block.ecc[column])
    if offset != len(data) or len(result) != info.raw_codewords:
        raise RuntimeError("Inconsistent QR block interleaving length")
    return InterleavedResult(bytes(result), tuple(blocks), info.data_codewords,
                             info.raw_codewords - info.data_codewords, info.raw_codewords)


def _mask_condition(mask: int, x: int, y: int) -> bool:
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (y // 2 + x // 3) % 2 == 0
    if mask == 5:
        return x * y % 2 + x * y % 3 == 0
    if mask == 6:
        return (x * y % 2 + x * y % 3) % 2 == 0
    return ((x + y) % 2 + x * y % 3) % 2 == 0


def mask_condition(mask: int, x: int, y: int) -> bool:
    _integer(mask, 0, 7, "Mask pattern")
    _integer(x, 0, 176, "Column")
    _integer(y, 0, 176, "Row")
    return _mask_condition(mask, x, y)


def _line_penalty(line: Sequence[int]) -> int:
    penalty = 0
    run_color = -1
    run_length = 0
    window = 0
    for index, value in enumerate(line):
        if value == run_color:
            run_length += 1
        else:
            if run_length >= 5:
                penalty += run_length - 2
            run_color, run_length = value, 1
        window = ((window << 1) | value) & 0x7FF
        if index >= 10 and window in (0b10111010000, 0b00001011101):
            penalty += 40
    if run_length >= 5:
        penalty += run_length - 2
    return penalty


def _penalty_score(rows: Sequence[Sequence[int]]) -> int:
    side = len(rows)
    score = sum(_line_penalty(row) for row in rows)
    for column in range(side):
        score += _line_penalty([row[column] for row in rows])
    for y in range(side - 1):
        row, below = rows[y], rows[y + 1]
        for x in range(side - 1):
            if row[x] == row[x + 1] == below[x] == below[x + 1]:
                score += 3
    total = side * side
    dark = sum(sum(row) for row in rows)
    return score + abs(dark * 20 - total * 10) // total * 10


def penalty_score(matrix: Sequence[Sequence[bool]]) -> int:
    """Score a square 1–177 module matrix using SpecQR's exact N1–N4 rules."""
    if not isinstance(matrix, Sequence) or not 1 <= len(matrix) <= 177:
        raise InvalidInputError("Matrix must be a square sequence of 1 to 177 rows")
    side = len(matrix)
    rows = []
    for index in range(side):
        row = matrix[index]
        if not isinstance(row, Sequence) or len(row) != side:
            raise InvalidInputError("Matrix rows must have the same length as the matrix")
        converted = bytearray(side)
        for column in range(side):
            value = row[column]
            if type(value) is not bool:
                raise InvalidInputError("Matrix modules must be booleans")
            converted[column] = value
        rows.append(converted)
    return _penalty_score(rows)


class _Grid:
    __slots__ = ("side", "modules", "functions")

    def __init__(self, side: int) -> None:
        self.side = side
        self.modules = [bytearray(side) for _ in range(side)]
        self.functions = [bytearray(side) for _ in range(side)]

    def function(self, x: int, y: int, dark: bool | int) -> None:
        if 0 <= x < self.side and 0 <= y < self.side:
            self.modules[y][x] = dark
            self.functions[y][x] = 1

    def finder(self, left: int, top: int) -> None:
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                in_pattern = 0 <= dx <= 6 and 0 <= dy <= 6
                dark = in_pattern and (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4))
                self.function(left + dx, top + dy, dark)

    def draw_format(self, level: str, mask: int) -> None:
        data = (format_bits(level) << 3) | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ (((remainder >> 9) & 1) * 0x537)
        bits = ((data << 10) | remainder) ^ 0x5412
        for index in range(6):
            self.function(8, index, (bits >> index) & 1)
        self.function(8, 7, (bits >> 6) & 1)
        self.function(8, 8, (bits >> 7) & 1)
        self.function(7, 8, (bits >> 8) & 1)
        for index in range(9, 15):
            self.function(14 - index, 8, (bits >> index) & 1)
        for index in range(8):
            self.function(self.side - 1 - index, 8, (bits >> index) & 1)
        for index in range(8, 15):
            self.function(8, self.side - 15 + index, (bits >> index) & 1)

    def draw_functions(self, version: int, level: str) -> None:
        self.finder(0, 0)
        self.finder(self.side - 7, 0)
        self.finder(0, self.side - 7)
        for index in range(8, self.side - 8):
            self.function(index, 6, index % 2 == 0)
            self.function(6, index, index % 2 == 0)
        positions = alignment_positions(version)
        last = len(positions) - 1
        for yi, y in enumerate(positions):
            for xi, x in enumerate(positions):
                if (xi, yi) in ((0, 0), (last, 0), (0, last)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.function(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(level, 0)
        self.function(8, self.side - 8, True)
        if version >= 7:
            remainder = version
            for _ in range(12):
                remainder = (remainder << 1) ^ (((remainder >> 11) & 1) * 0x1F25)
            bits = (version << 12) | remainder
            for index in range(18):
                a, b = self.side - 11 + index % 3, index // 3
                self.function(a, b, (bits >> index) & 1)
                self.function(b, a, (bits >> index) & 1)

    def draw_codewords(self, codewords: bytes) -> None:
        bit_index = 0
        right = self.side - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vertical in range(self.side):
                y = self.side - 1 - vertical if (right + 1) & 2 == 0 else vertical
                for x in (right, right - 1):
                    if not self.functions[y][x]:
                        if bit_index < len(codewords) * 8:
                            self.modules[y][x] = (codewords[bit_index // 8] >> (7 - bit_index % 8)) & 1
                        bit_index += 1
            right -= 2
        if not 0 <= bit_index - len(codewords) * 8 <= 7:
            raise RuntimeError("Inconsistent QR data-module count")

    def masked(self, level: str, mask: int) -> _Grid:
        candidate = object.__new__(_Grid)
        candidate.side = self.side
        candidate.modules = [row.copy() for row in self.modules]
        # Format reservations are already set; draw_format's writes are idempotent.
        candidate.functions = self.functions
        for y in range(self.side):
            row, reserved = candidate.modules[y], self.functions[y]
            for x in range(self.side):
                if not reserved[x] and _mask_condition(mask, x, y):
                    row[x] ^= 1
        candidate.draw_format(level, mask)
        return candidate


def build_matrix(codewords: bytes, version: int, level: str, mask: int | None = None) -> MatrixResult:
    side = size(version)
    validate_level(level)
    if mask is not None:
        _integer(mask, 0, 7, "Mask pattern")
    _bytes(codewords, "Interleaved codewords")
    expected_length = raw_codeword_count(version)
    if len(codewords) != expected_length:
        raise InvalidInputError(f"Expected {expected_length} interleaved codewords; got {len(codewords)}")
    base = _Grid(side)
    base.draw_functions(version, level)
    base.draw_codewords(codewords)
    best: _Grid | None = None
    best_mask = 0
    best_penalty = 0
    penalties = []
    for candidate_mask in range(8) if mask is None else (mask,):
        candidate = base.masked(level, candidate_mask)
        penalty = _penalty_score(candidate.modules)
        penalties.append(MaskPenalty(candidate_mask, penalty))
        if best is None or penalty < best_penalty:
            best, best_mask, best_penalty = candidate, candidate_mask, penalty
    if best is None:
        raise RuntimeError("No QR mask candidate was evaluated")
    matrix = tuple(tuple(bool(value) for value in row) for row in best.modules)
    return MatrixResult(matrix, best_mask, best_penalty, tuple(penalties))
