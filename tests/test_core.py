"""Core regression and boundary tests; no optional packages or Node required."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from specqr.core import (
    build_matrix,
    gf_multiply,
    interleave_codewords,
    mask_condition,
    pad_data_bits,
    penalty_score,
    reed_solomon_divisor,
    reed_solomon_remainder,
)
from specqr.errors import DataTooLongError, InvalidInputError, InvalidModeError, InvalidVersionError
from specqr.tables import (
    ECC_CODEWORDS_PER_BLOCK,
    NUM_ERROR_CORRECTION_BLOCKS,
    alignment_positions,
    block_info,
    character_count_bits,
    data_codeword_count,
    raw_codeword_count,
    size,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "specqr-js-golden.json").read_text(encoding="utf-8"))


def rows(matrix):
    return ["".join("1" if value else "0" for value in row) for row in matrix]


class TableTests(unittest.TestCase):
    def test_common_capacities_and_alignment(self):
        for table in (ECC_CODEWORDS_PER_BLOCK, NUM_ERROR_CORRECTION_BLOCKS):
            self.assertEqual(len(table), 4)
            self.assertTrue(all(len(row) == 41 for row in table))
        self.assertEqual((size(1), size(40)), (21, 177))
        self.assertEqual((raw_codeword_count(1), raw_codeword_count(40)), (26, 3706))
        self.assertEqual(tuple(data_codeword_count(1, level) for level in "LMQH"), (19, 16, 13, 9))
        self.assertEqual(data_codeword_count(40, "L"), 2956)
        self.assertEqual(alignment_positions(1), ())
        self.assertEqual(alignment_positions(2), (6, 18))
        self.assertEqual(alignment_positions(7), (6, 22, 38))
        self.assertEqual(alignment_positions(32), (6, 34, 60, 86, 112, 138))
        self.assertEqual(alignment_positions(40), (6, 30, 58, 86, 114, 142, 170))

    def test_count_width_boundaries(self):
        expected = {"numeric": (10, 12, 14), "alphanumeric": (9, 11, 13),
                    "byte": (8, 16, 16), "kanji": (8, 10, 12)}
        for mode, widths in expected.items():
            self.assertEqual(tuple(character_count_bits(version, mode) for version in (9, 10, 26, 27)),
                             (widths[0], widths[1], widths[1], widths[2]))

    def test_rejects_invalid_types_and_values(self):
        for version in (True, False, 0, 41, -1, 1.0, "1", None, 10**100):
            for function in (size, raw_codeword_count, alignment_positions):
                with self.subTest(function=function.__name__, value=version), self.assertRaises(InvalidVersionError):
                    function(version)
        for level in (True, 0, "l", "X", "", [], {}, None):
            with self.subTest(level=level), self.assertRaises(InvalidInputError):
                data_codeword_count(1, level)
        with self.assertRaises(InvalidModeError):
            character_count_bits(1, "unknown")


class CodingTests(unittest.TestCase):
    def test_padding_empty_partial_full(self):
        self.assertEqual(pad_data_bits((), 1, "H"), bytes([0, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11]))
        self.assertEqual(pad_data_bits((1, 0, 1), 1, "H")[:3], bytes([0xA0, 0xEC, 0x11]))
        self.assertEqual(pad_data_bits((1,) * 72, 1, "H"), b"\xff" * 9)
        self.assertEqual(pad_data_bits((1,) * 71, 1, "H"), b"\xff" * 8 + b"\xfe")
        self.assertEqual(pad_data_bits((1,) * 68, 1, "H"), b"\xff" * 8 + b"\xf0")
        self.assertEqual(pad_data_bits((1,) * 64, 1, "H"), b"\xff" * 8 + b"\x00")
        with self.assertRaises(DataTooLongError):
            pad_data_bits((0,) * 73, 1, "H")

    def test_padding_golden_data_bits(self):
        for fixture in FIXTURES:
            expected = fixture["expected"]
            diagnostics = expected["diagnostics"]
            data = bytes(expected["dataCodewords"])
            bits = tuple((data[index // 8] >> (7 - index % 8)) & 1
                         for index in range(diagnostics["dataBitLength"]))
            with self.subTest(case=fixture["id"]):
                self.assertEqual(pad_data_bits(bits, diagnostics["version"], diagnostics["errorCorrectionLevel"]), data)

    def test_padding_rejects_unbounded_and_nonbinary_inputs(self):
        for bits in ((True,), (False,), (2,), (-1,), (0.0,), "01", b"\x00", iter([0, 1]), None):
            with self.subTest(bits=bits), self.assertRaises(InvalidInputError):
                pad_data_bits(bits, 1, "L")
        # A huge finite sequence is rejected by length without materializing it.
        with self.assertRaises(DataTooLongError):
            pad_data_bits(range(10**12), 40, "L")
        with self.assertRaises(DataTooLongError):
            pad_data_bits(range(10**100), 40, "L")

    def test_field_multiplication_exhaustively(self):
        # Independent polynomial convolution followed by long division.
        for left in range(256):
            for right in range(256):
                product = 0
                for bit in range(8):
                    if right & (1 << bit):
                        product ^= left << bit
                for bit in range(14, 7, -1):
                    if product & (1 << bit):
                        product ^= 0x11D << (bit - 8)
                self.assertEqual(gf_multiply(left, right), product)
        self.assertEqual(gf_multiply(0x53, 0xCA), 0x8F)

    def test_reed_solomon(self):
        self.assertEqual(reed_solomon_divisor(7), bytes([1, 127, 122, 154, 164, 11, 68, 117]))
        for degree in (1, 7, 10, 18, 30, 255):
            data = bytes(range(40))
            parity = reed_solomon_remainder(data, degree)
            self.assertEqual(len(parity), degree)
            self.assertEqual(reed_solomon_remainder(data + parity, degree), bytes(degree))
            self.assertEqual(reed_solomon_remainder(b"", degree), bytes(degree))

    def test_coding_rejects_invalid_inputs(self):
        for degree in (True, False, 0, -1, 256, 1.0, "7", None):
            with self.subTest(degree=degree), self.assertRaises(InvalidInputError):
                reed_solomon_divisor(degree)
        for value in (True, False, -1, 256, 1.0, "1", None):
            with self.subTest(value=value), self.assertRaises(InvalidInputError):
                gf_multiply(value, 1)
            with self.assertRaises(InvalidInputError):
                gf_multiply(1, value)
        for data in ([0], bytearray(19), "", None, bytes(3707)):
            with self.subTest(data=type(data)), self.assertRaises(InvalidInputError):
                reed_solomon_remainder(data, 7)
        for length in (0, 18, 20):
            with self.assertRaises(InvalidInputError):
                interleave_codewords(bytes(length), 1, "L")

    def test_all_versions_and_ecc_block_layouts(self):
        for version in range(1, 41):
            for level in "LMQH":
                with self.subTest(version=version, level=level):
                    info = block_info(version, level)
                    data = bytes(index % 256 for index in range(info.data_codewords))
                    result = interleave_codewords(data, version, level)
                    self.assertEqual(len(result.codewords), info.raw_codewords)
                    self.assertEqual(result.total_codewords, info.raw_codewords)
                    self.assertEqual(result.data_codewords, info.data_codewords)
                    self.assertEqual(result.error_correction_codewords, info.blocks * info.ecc_per_block)
                    self.assertEqual(len(result.blocks), info.blocks)
                    self.assertEqual(b"".join(block.data for block in result.blocks), data)
                    lengths = [len(block.data) for block in result.blocks]
                    self.assertEqual(lengths, sorted(lengths))
                    self.assertLessEqual(max(lengths) - min(lengths), 1)
                    for block in result.blocks:
                        self.assertEqual(len(block.ecc), info.ecc_per_block)
                        self.assertEqual(reed_solomon_remainder(block.data + block.ecc, info.ecc_per_block), bytes(info.ecc_per_block))


class MatrixTests(unittest.TestCase):
    def test_exact_golden_codewords_matrices_and_metadata(self):
        for fixture in FIXTURES:
            with self.subTest(case=fixture["id"]):
                expected = fixture["expected"]
                diagnostics = expected["diagnostics"]
                version, level, mask = (diagnostics[key] for key in ("version", "errorCorrectionLevel", "maskPattern"))
                coded = interleave_codewords(bytes(expected["dataCodewords"]), version, level)
                self.assertEqual(list(coded.codewords), expected["interleavedCodewords"])
                built = build_matrix(coded.codewords, version, level, mask)
                actual_rows = rows(built.matrix)
                self.assertEqual(actual_rows, expected["matrixRows"])
                self.assertEqual(hashlib.sha256("\n".join(actual_rows).encode()).hexdigest(), expected["matrixSha256"])
                self.assertEqual(sum(sum(row) for row in built.matrix), expected["darkModules"])
                self.assertEqual(built.mask_pattern, mask)
                self.assertEqual(built.penalty, diagnostics["maskPenalty"])
                self.assertEqual(penalty_score(built.matrix), built.penalty)
                self.assertEqual([(item.mask_pattern, item.penalty) for item in built.mask_penalties], [(mask, built.penalty)])

    def test_all_versions_and_ecc_matrix_invariants(self):
        for version in range(1, 41):
            for level in "LMQH":
                with self.subTest(version=version, level=level):
                    codewords = interleave_codewords(bytes(data_codeword_count(version, level)), version, level).codewords
                    built = build_matrix(codewords, version, level, version % 8)
                    self.assertEqual(len(built.matrix), size(version))
                    self.assertTrue(all(len(row) == size(version) for row in built.matrix))
                    self.assertTrue(built.matrix[-8][8])
                    self.assertTrue(all(type(value) is bool for row in built.matrix for value in row))

    def test_auto_mask_uses_matching_lowest_penalty(self):
        rng = random.Random(71617)
        for version, level in ((1, "M"), (7, "H"), (32, "L"), (40, "Q")):
            codewords = bytes(rng.randrange(256) for _ in range(raw_codeword_count(version)))
            built = build_matrix(codewords, version, level)
            penalties = [(item.penalty, item.mask_pattern) for item in built.mask_penalties]
            self.assertEqual(len(penalties), 8)
            self.assertEqual((built.penalty, built.mask_pattern), min(penalties))
            fixed = build_matrix(codewords, version, level, built.mask_pattern)
            self.assertEqual(built.matrix, fixed.matrix)
            for item in built.mask_penalties:
                self.assertEqual(build_matrix(codewords, version, level, item.mask_pattern).penalty, item.penalty)
        with patch("specqr.core._penalty_score", return_value=42):
            self.assertEqual(build_matrix(bytes(26), 1, "L").mask_pattern, 0)

    def test_mask_formulas_and_penalty_rules(self):
        formulas = (
            lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (y // 2 + x // 3) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )
        for mask, formula in enumerate(formulas):
            for y in range(21):
                for x in range(21):
                    self.assertEqual(mask_condition(mask, x, y), formula(x, y))
        fixtures = [(["11111", "01010", "10101", "01010", "10101"], 23),
                    (["11010", "11010", "00101", "01010", "10101"], 3),
                    (["10111010000"] + ["01010101010" if row % 2 else "10101010101" for row in range(1, 11)], 40),
                    (["1111111111"] * 10, 503)]
        for fixture_rows, expected in fixtures:
            matrix = tuple(tuple(value == "1" for value in row) for row in fixture_rows)
            self.assertEqual(penalty_score(matrix), expected)
            self.assertEqual(penalty_score(tuple(zip(*matrix))), expected)

    def test_immutability_and_boundaries(self):
        built = build_matrix(bytes(26), 1, "L", 0)
        with self.assertRaises(FrozenInstanceError):
            built.penalty = 0
        with self.assertRaises(TypeError):
            built.matrix[0][0] = False
        for mask in (True, False, -1, 8, 1.0, "auto", [], {}):
            with self.subTest(mask=mask), self.assertRaises(InvalidInputError):
                build_matrix(bytes(26), 1, "L", mask)
        for data in (bytes(25), bytes(27), [0] * 26, bytearray(26), None):
            with self.assertRaises(InvalidInputError):
                build_matrix(data, 1, "L", 0)
        for matrix in ((), ((True, False),), ((1,),), ((0.0,),), "1", ((),), [[True]] * 178):
            with self.subTest(matrix=type(matrix)), self.assertRaises(InvalidInputError):
                penalty_score(matrix)
        for coordinate in (True, -1, 177, 1.0):
            with self.assertRaises(InvalidInputError):
                mask_condition(0, coordinate, 0)


if __name__ == "__main__":
    unittest.main()
