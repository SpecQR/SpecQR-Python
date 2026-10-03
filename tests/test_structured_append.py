"""Structured Append contracts and pinned SpecQR JavaScript goldens.

The JSON fixture contains complete matrix rows, padded data codewords,
interleaved codewords, and split metadata for 22 sets / 112 symbols.  Running
this test needs only Python's standard library, not Node or a QR decoder.
"""
import dataclasses
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from specqr.api import Options, generate, generate_segments
from specqr.errors import DataTooLongError, InvalidGs1Error, InvalidInputError, InvalidModeError
from specqr.segments import Segment
from specqr.structured_append import (
    calculate_structured_append_parity,
    calculate_structured_append_segments_parity,
    generate_structured_append,
    generate_segments_structured_append,
    merge_structured_append_parts,
)


def snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def plain(value):
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        return {snake(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def options(value):
    result = {snake(key): value for key, value in value.items() if key != "output"}
    for key in ("version", "mask_pattern"):
        if result.get(key) == "auto":
            result[key] = None
    return result


def segments(values):
    return [Segment(value["mode"], bytes(value["bytes"]) if "bytes" in value else value.get("text", value.get("data"))) for value in values]


class StructuredAppendGoldenTests(unittest.TestCase):
    def test_all_pinned_javascript_split_data_and_matrix_goldens(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "structured_append.json").read_text())
        self.assertEqual(fixture["baseline"], "15ad15e5c770ea0e39072f8f88b2733018f02ffd")
        self.assertEqual(len(fixture["cases"]), 22)
        count = 0
        for case in fixture["cases"]:
            request, expected = case["request"], case["expected"]
            with self.subTest(case=request.get("id")):
                kwargs = options(request["options"])
                manual = "segments" in request
                if manual:
                    result = generate_segments_structured_append(segments(request["segments"]), diagnostics={"split_units": "full"}, **kwargs)
                else:
                    data = bytes(request["bytes"]) if "bytes" in request else request["text"]
                    result = generate_structured_append(data, diagnostics=True, **kwargs)
                for key in ("total", "parity", "inputLength", "byteLength"):
                    self.assertEqual(getattr(result, snake(key)), expected[key])
                actual_summary, expected_summary = plain(result.diagnostics), plain(expected["diagnostics"])
                # Narrative wording is Python-specific, structural metadata is not.
                actual_summary.pop("version_selection_reason")
                expected_summary.pop("version_selection_reason")
                self.assertEqual(actual_summary, expected_summary)
                for symbol, expected_symbol in zip(result.symbols, expected["symbols"]):
                    self.assertEqual(["".join("1" if item else "0" for item in row) for row in symbol.matrix], expected_symbol["matrix"])
                    self.assertEqual(symbol.data_codewords.hex(), expected_symbol["data_codewords"])
                    self.assertEqual(symbol.codewords.hex(), expected_symbol["codewords"])
                    count += 1
        self.assertEqual(count, 112)


class StructuredAppendGenerationTests(unittest.TestCase):
    def test_small_fixed_versus_automatic(self):
        # Auto deliberately selects the smallest common version that creates
        # 2..16 parts. A larger fixed version can therefore reject the same data.
        result = generate_structured_append(b"a" * 20, error_correction_level="L", mask_pattern=0)
        self.assertEqual(result.total, 2)
        self.assertEqual(result.diagnostics["version"], 1)
        self.assertEqual(result.diagnostics["version_selection"], "auto-minimum")
        with self.assertRaisesRegex(InvalidInputError, "fits in one"):
            generate_structured_append(b"a" * 20, version=2, error_correction_level="L")
        with self.assertRaisesRegex(InvalidInputError, "fits in one"):
            generate_structured_append("HELLO", version=1)
        # The single-symbol test includes the 20-bit SA header.
        self.assertEqual(generate(b"a" * 16, version=1, error_correction_level="L").version, 1)
        self.assertEqual(generate_structured_append(b"a" * 16, version=1, error_correction_level="L").total, 2)

    def test_shared_options_and_low_level_header_agree(self):
        opts = Options(version=1, error_correction_level="L", mask_pattern=2)
        result = generate_structured_append(b"1234567890abcdefghij", opts)
        for symbol, info in zip(result.symbols, result.diagnostics["symbols"]):
            start, length = info["input_start"], info["input_length"]
            chunk = b"1234567890abcdefghij"[start:start + length]
            header = Segment.structured_append(info["index"], result.total, result.parity)
            expected = generate_segments([header, Segment.byte(chunk)], opts)
            self.assertEqual(symbol.matrix, expected.matrix)
            self.assertEqual(symbol.data_codewords, expected.data_codewords)

    def test_unicode_byte_boundaries_and_canonical_offsets(self):
        source = "é😀漢e\u0301" * 9
        result = generate_structured_append(source, mode="byte", version=1, error_correction_level="L", mask_pattern=0)
        chunks = []
        byte_offset = 0
        for info in result.diagnostics["symbols"]:
            start, length = info["input_start"], info["input_length"]
            chunk = source[start:start + length]
            self.assertEqual(info["byte_start"], byte_offset)
            self.assertEqual(info["byte_length"], len(chunk.encode()))
            byte_offset += len(chunk.encode())
            chunks.append(chunk)
        self.assertEqual("".join(chunks), source)
        self.assertEqual(result.byte_length, len(source.encode()))
        self.assertEqual(result.input_length, len(source))
        self.assertEqual(result.parity, calculate_structured_append_parity(source))

    def test_binary_view_honors_slice_and_owns_data(self):
        source = bytearray(range(64))
        view = memoryview(source)[5:45]
        result = generate_structured_append(view, version=1, error_correction_level="L", mask_pattern=0)
        self.assertEqual(result.byte_length, 40)
        self.assertEqual(result.parity, calculate_structured_append_parity(bytes(range(5, 45))))
        source[:] = bytes(64)
        self.assertEqual(b"".join(s.segments[1].data for s in result.symbols), bytes(range(5, 45)))

    def test_atomic_numeric_alphanumeric_kanji(self):
        for segment in (Segment.numeric("1" * 100), Segment.alphanumeric("A" * 100), Segment.kanji("漢" * 20)):
            with self.subTest(mode=segment.mode):
                with self.assertRaises(DataTooLongError):
                    generate_segments_structured_append([segment], version=1, error_correction_level="L")
        result = generate_segments_structured_append([Segment.numeric("1" * 30), Segment.kanji("漢" * 7)], version=1, error_correction_level="L", mask_pattern=0)
        self.assertEqual(result.total, 2)
        self.assertEqual(result.diagnostics["split_unit_count"], 2)
        self.assertEqual([info["source_segment_start"] for info in result.diagnostics["symbols"]], [0, 1])

    def test_max_symbols_and_warning(self):
        result = generate_structured_append(b"a" * 30, version=1, error_correction_level="L", max_symbols=2)
        self.assertEqual(result.total, 2)
        self.assertEqual(result.diagnostics["warnings"][0]["code"], "STRUCTURED_APPEND_MAX_SYMBOLS_NEAR_LIMIT")
        with self.assertRaises(DataTooLongError):
            generate_structured_append(b"a" * 31, version=1, error_correction_level="L", max_symbols=2)

    def test_maximum_binary_and_numeric_sets(self):
        kwargs = dict(version=40, error_correction_level="L", mask_pattern=0)
        binary = generate_segments_structured_append([Segment.byte(b"a" * 47_216)], **kwargs)
        self.assertEqual(binary.total, 16)
        self.assertEqual(binary.diagnostics["split_unit_count"], 47_216)
        self.assertNotIn("split_units", binary.diagnostics)
        self.assertEqual([part["byte_length"] for part in binary.diagnostics["symbols"]], [2_951] * 16)
        numeric = generate_structured_append("1" * 113_328, mode="numeric", **kwargs)
        self.assertEqual(numeric.total, 16)
        self.assertEqual([part["input_length"] for part in numeric.diagnostics["symbols"]], [7_083] * 16)
        for function, payload, extra in (
            (generate_segments_structured_append, [Segment.byte(b"a" * 47_217)], {}),
            (generate_structured_append, "1" * 113_329, {"mode": "numeric"}),
        ):
            with self.assertRaises(DataTooLongError):
                function(payload, **kwargs, **extra)

    def test_compact_full_diagnostics_are_encoding_independent(self):
        value = [Segment.numeric("12345678901234567890"), Segment.byte("é😀" * 12)]
        kwargs = dict(version=1, error_correction_level="L", mask_pattern=0)
        with patch("specqr.structured_append._SegmentSource.full_detail", side_effect=AssertionError("should not materialize")):
            compact = generate_segments_structured_append(value, **kwargs)
            standard = generate_segments_structured_append(value, diagnostics=True, **kwargs)
        full = generate_segments_structured_append(value, diagnostics={"split_units": "full"}, **kwargs)
        full_output = generate_segments_structured_append(value, diagnostics={"split_units": "full", "symbol_results": "output"}, **kwargs)
        self.assertNotIn("split_units", compact.diagnostics)
        self.assertNotIn("split_units", standard.diagnostics)
        self.assertEqual(compact.diagnostics["split_units_detail"], "summary")
        self.assertEqual(full.diagnostics["split_unit_count"], 25)
        self.assertEqual(len(full.diagnostics["split_units"]), 25)
        self.assertEqual(full.diagnostics["split_units"][0]["unit_length"], 20)
        self.assertEqual(full.diagnostics["split_units"][2]["byte_length"], 4)
        for variant in (standard, full, full_output):
            self.assertEqual([symbol.matrix for symbol in compact.symbols], [symbol.matrix for symbol in variant.symbols])
            self.assertEqual(compact.diagnostics["symbols"], variant.diagnostics["symbols"])
        with self.assertRaises(TypeError):
            full.diagnostics["split_units"][0]["unit_length"] = 99
        with self.assertRaises(dataclasses.FrozenInstanceError):
            compact.total = 99

    def test_conflicting_controls_and_options(self):
        for function, value in ((generate_structured_append, "A" * 31), (generate_segments_structured_append, [Segment.byte(b"a" * 31)])):
            for kwargs in ({"eci": True}, {"eci": 0}, {"fnc1_second": "37"}, {"boost_error_correction": True}, {"structured_append": {"index": 1, "total": 2, "parity": 0}}, {"parity": 0}, {"mask": 0}, {"error_correction": "L"}):
                with self.subTest(function=function.__name__, kwargs=kwargs):
                    with self.assertRaises(InvalidModeError):
                        function(value, **kwargs)
            with self.assertRaises(InvalidGs1Error):
                function(value, gs1=True)
        for control in (Segment.eci(26), Segment.fnc1(), Segment.fnc1_second("A"), Segment.structured_append(1, 2, 0)):
            with self.subTest(mode=control.mode):
                error = InvalidGs1Error if control.mode == "fnc1" else InvalidModeError
                with self.assertRaises(error):
                    generate_segments_structured_append([control, Segment.byte("a" * 30)])
                with self.assertRaises(error):
                    calculate_structured_append_segments_parity([control, Segment.byte("a" * 30)])
        for key in ("mode", "encoding", "optimize_segments"):
            with self.assertRaises(InvalidModeError):
                generate_segments_structured_append([Segment.byte("a" * 30)], **{key: "auto"})

    def test_strict_invalid_values_and_preflight_resource_bound(self):
        for maximum in (True, False, 1, 17, 2.5, "2"):
            with self.assertRaises(InvalidModeError):
                generate_structured_append("A" * 31, max_symbols=maximum)
        for invalid in (None, 10, b"", "", "\ud800", "a\udfffz", [True, 1], [256]):
            with self.subTest(value=repr(invalid)):
                with self.assertRaises(InvalidInputError):
                    generate_structured_append(invalid)
        for value in ([], [Segment.byte("")], [Segment.numeric("")]):
            with self.assertRaises(InvalidInputError):
                generate_segments_structured_append(value)
        for invalid in (None, 0, "full", {"split_units": "all"}, {"symbol_results": "matrix"}, {"typo": True}):
            with self.assertRaises(InvalidInputError):
                generate_segments_structured_append([Segment.byte("a" * 30)], diagnostics=invalid)
        with self.assertRaises(InvalidInputError):
            generate_structured_append("A" * 31, diagnostics={"split_units": "full"})
        with patch("specqr.segments.create_segments", side_effect=AssertionError("huge input reached segment construction")):
            with self.assertRaises(DataTooLongError):
                generate_structured_append("1" * 1_000_000)
        with patch("specqr.segments.normalize_segments", side_effect=AssertionError("huge input normalized")):
            with self.assertRaises(DataTooLongError):
                generate_segments_structured_append([{"mode": "byte", "data": b"a" * 1_000_000}])
        # Infinite generators are never consumed.
        with self.assertRaises(InvalidInputError):
            generate_segments_structured_append(iter([Segment.byte("abc")]))


class StructuredAppendParityAndMergeTests(unittest.TestCase):
    def test_canonical_text_binary_and_kanji_parity(self):
        data = [Segment.numeric("123"), Segment.alphanumeric("AB"), Segment.kanji("漢字"), Segment.byte("é😀"), Segment.byte(b"\x00\xff")]
        canonical = b"123AB" + "漢字é😀".encode() + b"\x00\xff"
        self.assertEqual(calculate_structured_append_segments_parity(data), calculate_structured_append_parity(canonical))
        self.assertEqual(calculate_structured_append_parity(""), 0)
        self.assertEqual(calculate_structured_append_parity([]), 0)
        self.assertEqual(calculate_structured_append_parity([0, 255, 17]), 238)
        with self.assertRaises(InvalidGs1Error):
            calculate_structured_append_segments_parity(data, gs1=False)
        for value in (None, 2, [True], [-1], [256], "\ud800"):
            with self.assertRaises(InvalidInputError):
                calculate_structured_append_parity(value)

    def test_all_helpers_enforce_aggregate_resource_limits(self):
        for value in (b"a" * 1_000_001, "a" * 1_000_001, [0] * 1_000_001):
            with self.assertRaises(DataTooLongError):
                calculate_structured_append_parity(value)
        with self.assertRaises(DataTooLongError):
            calculate_structured_append_segments_parity([{"mode": "byte", "data": b"a"}] * 16_385)
        with self.assertRaises(DataTooLongError):
            calculate_structured_append_segments_parity([{"mode": "byte", "data": b"a" * 500_001}] * 2)
        for data in (b"a" * 500_001, "a" * 500_001):
            with self.assertRaises(DataTooLongError):
                merge_structured_append_parts([{"index": index, "total": 2, "parity": 0, "data": data} for index in (1, 2)])
        released = memoryview(b"abc")
        released.release()
        with self.assertRaises(InvalidInputError):
            calculate_structured_append_parity(released)

    def parts(self, values):
        binary = not isinstance(values[0], str)
        payload = (b"" if binary else "").join(values)
        parity = calculate_structured_append_parity(payload)
        return [{"index": index + 1, "total": len(values), "parity": parity, "data": value} for index, value in enumerate(values)]

    def test_out_of_order_text_binary_merges(self):
        for values in (("é😀", "漢字", "ABC"), (b"\x00\xff", b"ab", b"\x01\x02")):
            parts = self.parts(values)
            result = merge_structured_append_parts(list(reversed(parts)))
            self.assertEqual(result.data, ("" if isinstance(values[0], str) else b"").join(values))
            self.assertEqual([part["index"] for part in result.parts], [1, 2, 3])
            self.assertTrue(result.diagnostics["parity_check"]["matches"])
            self.assertEqual(result.diagnostics["byte_length"], sum(len(value.encode()) if isinstance(value, str) else len(value) for value in values))

    def test_merged_mutable_binary_is_owned(self):
        backing = bytearray(b"xabcdy")
        parts = self.parts([b"ab", b"cd"])
        parts[0]["data"] = memoryview(backing)[1:3]
        result = merge_structured_append_parts(parts)
        backing[:] = bytes(6)
        self.assertEqual(result.data, b"abcd")

    def test_merge_rejects_incomplete_duplicate_inconsistent_corrupt_sets(self):
        base = self.parts(["AB", "CD"])
        bad_sets = [[], [base[0]], [base[0], base[0]], [dict(base[0], index=0), base[1]], [dict(base[0], index=True), base[1]], [dict(base[0], index=3), base[1]], [dict(base[0], total=1), base[1]], [dict(base[0], total=3), base[1]], [dict(base[0], parity=256), base[1]], [dict(base[0], parity=base[0]["parity"] ^ 1), base[1]], [dict(base[0], data="AX"), base[1]], [dict(base[0], data=b"AB"), base[1]], [dict(base[0], data=[65, 66]), base[1]], [dict(base[0], data="\ud800"), base[1]], [None, base[1]], base * 9]
        for parts in bad_sets:
            with self.subTest(parts=repr(parts)):
                with self.assertRaises(InvalidInputError):
                    merge_structured_append_parts(parts)
        with self.assertRaises(InvalidInputError):
            merge_structured_append_parts(iter(base))
        with self.assertRaises(InvalidModeError):
            merge_structured_append_parts(base, ignored=True)


if __name__ == "__main__":
    unittest.main()
