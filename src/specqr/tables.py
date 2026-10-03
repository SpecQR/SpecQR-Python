"""QR Code Model 2 tables and bounded capacity calculations.

Ported from SpecQR src/core/tables.js at 15ad15e5c770ea0e39072f8f88b2733018f02ffd.
Copyright (c) 2026 SpecQR contributors. Distributed under the MIT license.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InvalidInputError, InvalidModeError, InvalidVersionError


ERROR_CORRECTION_LEVEL_ORDER = ("L", "M", "Q", "H")
_FORMAT_BITS = (1, 0, 3, 2)

ECC_CODEWORDS_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)

NUM_ERROR_CORRECTION_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)


@dataclass(frozen=True, slots=True)
class BlockInfo:
    blocks: int
    ecc_per_block: int
    raw_codewords: int
    data_codewords: int


def validate_version(version: int) -> None:
    if type(version) is not int or not 1 <= version <= 40:
        raise InvalidVersionError("QR version must be an integer from 1 to 40")


def validate_level(level: str) -> None:
    if not isinstance(level, str) or level not in ERROR_CORRECTION_LEVEL_ORDER:
        raise InvalidInputError("Error correction level must be one of L, M, Q, H")


def _level_index(level: str) -> int:
    validate_level(level)
    return ERROR_CORRECTION_LEVEL_ORDER.index(level)


def format_bits(level: str) -> int:
    return _FORMAT_BITS[_level_index(level)]


def size(version: int) -> int:
    validate_version(version)
    return version * 4 + 17


def raw_codeword_count(version: int) -> int:
    validate_version(version)
    result = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        result -= (25 * count - 10) * count - 55
        if version >= 7:
            result -= 36
    return result // 8


def data_codeword_count(version: int, level: str) -> int:
    return block_info(version, level).data_codewords


def block_info(version: int, level: str) -> BlockInfo:
    validate_version(version)
    ordinal = _level_index(level)
    blocks = NUM_ERROR_CORRECTION_BLOCKS[ordinal][version]
    ecc = ECC_CODEWORDS_PER_BLOCK[ordinal][version]
    raw = raw_codeword_count(version)
    return BlockInfo(blocks, ecc, raw, raw - blocks * ecc)


def alignment_positions(version: int) -> tuple[int, ...]:
    validate_version(version)
    if version == 1:
        return ()
    count = version // 7 + 2
    denominator = count * 2 - 2
    step = 26 if version == 32 else ((version * 4 + 4 + denominator - 1) // denominator) * 2
    positions = [6]
    for index in range(count - 1):
        positions.insert(1, size(version) - 7 - index * step)
    return tuple(positions)


def character_count_bits(version: int, mode: str) -> int:
    validate_version(version)
    group = 0 if version <= 9 else 1 if version <= 26 else 2
    if mode == "numeric":
        return (10, 12, 14)[group]
    if mode == "alphanumeric":
        return (9, 11, 13)[group]
    if mode == "byte":
        return (8, 16, 16)[group]
    if mode == "kanji":
        return (8, 10, 12)[group]
    raise InvalidModeError("Mode must be numeric, alphanumeric, byte, or kanji")
