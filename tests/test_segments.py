"""Bit-level segment contracts, deterministic optimization, and invalid inputs."""

from array import array
from dataclasses import FrozenInstanceError
import itertools
import random
import unittest
from unittest.mock import patch

from specqr.errors import (
    DataTooLongError, InvalidEciError, InvalidGs1Error,
    InvalidInputError, InvalidModeError, InvalidVersionError,
)
from specqr.segments import (
    Segment, SegmentOptimizationTracker, can_encode_kanji,
    create_segments, kanji_value, normalize_segments, segments_bit_length,
)


def bit_string(segment, version=1):
    return "".join(map(str, segment.bits(version)))


class SegmentBitsTests(unittest.TestCase):
    def test_known_numeric_vector(self):
        segment = Segment.numeric("01234567")
        self.assertEqual(bit_string(segment), "00010000001000000000110001010110011000011")
        self.assertEqual(segment.bit_length(1), 41)
        self.assertEqual(segment.count, 8)
        self.assertEqual(segment.character_count, 8)
        self.assertEqual(segment.logical_bytes, b"01234567")

    def test_known_alphanumeric_vector(self):
        segment = Segment.alphanumeric("HELLO")
        self.assertEqual(bit_string(segment), "00100000001010110000101101111000110011000")
        self.assertEqual(segment.bit_length(1), 41)

    def test_utf8_byte_count_uses_encoded_bytes(self):
        segment = Segment.byte("é😀")
        self.assertEqual(segment.count, 6)
        self.assertEqual(segment.character_count, 2)
        self.assertEqual(segment.byte_count, 6)
        self.assertEqual(segment.logical_bytes, b"\xc3\xa9\xf0\x9f\x98\x80")
        self.assertEqual(bit_string(segment), "010000000110110000111010100111110000100111111001100010000000")

    def test_kanji_vector_and_canonical_logical_bytes(self):
        segment = Segment.kanji("漢字")
        self.assertEqual(bit_string(segment), "10000000001000111001111110101000011010")
        self.assertEqual(segment.count, 2)
        self.assertEqual(segment.character_count, 2)
        self.assertEqual(segment.byte_count, 4)
        self.assertEqual(segment.logical_bytes, "漢字".encode("utf-8"))
        self.assertEqual((kanji_value("漢"), kanji_value("字")), (1855, 2586))

    def test_cp932_decoder_semantics_and_excluded_ranges(self):
        for character in "～∥－￠￡￢①㍉漢あ":
            self.assertTrue(can_encode_kanji(character), character)
        for character in ("〜", "‖", "−", "¢", "£", "¬", "A", "", "漢字", "😀", "ｱ", "\ue000"):
            self.assertFalse(can_encode_kanji(character), character)

    def test_empty_data_segments(self):
        for factory, expected in ((Segment.numeric, 14), (Segment.alphanumeric, 13), (Segment.byte, 12), (Segment.kanji, 12)):
            segment = factory("")
            self.assertEqual(segment.bit_length(1), expected)
            self.assertEqual(len(segment.bits(1)), expected)
            self.assertEqual(segment.logical_bytes, b"")

    def test_version_count_width_transitions(self):
        expected = {
            "numeric": (18, 20, 20, 22),
            "alphanumeric": (19, 21, 21, 23),
            "byte": (20, 28, 28, 28),
            "kanji": (25, 27, 27, 29),
        }
        for mode, text in (("numeric", "1"), ("alphanumeric", "A"), ("byte", "x"), ("kanji", "漢")):
            segment = Segment(mode, text)
            self.assertEqual(tuple(segment.bit_length(version) for version in (9, 10, 26, 27)), expected[mode])
            self.assertEqual(tuple(len(segment.bits(version)) for version in (9, 10, 26, 27)), expected[mode])

    def test_oversized_count_can_be_estimated_but_not_encoded(self):
        segment = Segment.byte(b"x" * 256)
        self.assertEqual(segment.bit_length(1), 2060)
        with self.assertRaises(DataTooLongError):
            segment.bits(1)
        self.assertEqual(len(segment.bits(10)), 2068)

    def test_bits_are_bounded_before_expansion(self):
        segment = Segment.byte(b"x" * 100000)
        self.assertEqual(segment.bit_length(40), 800020)
        with self.assertRaises(DataTooLongError):
            segment.bits(40)

    def test_payload_resource_caps_before_encoding_or_copy(self):
        for value in ("a" * 1000001, b"a" * 1000001, bytearray(1000001), memoryview(bytes(1000001))):
            with self.assertRaises(DataTooLongError):
                Segment.byte(value)
        self.assertEqual(Segment.byte(b"a" * 150000).byte_count, 150000)

    def test_immutable_and_hashable_binary_snapshot(self):
        original = bytearray(b"abc")
        segment = Segment.byte(original)
        original[0] = 0
        self.assertEqual(segment.data, b"abc")
        self.assertEqual(segment.logical_bytes, b"abc")
        self.assertEqual(segment.character_count, 0)
        self.assertIsInstance(hash(segment), int)
        with self.assertRaises(FrozenInstanceError):
            segment.data = b"different"

    def test_memoryview_offsets_strides_and_multibyte_elements(self):
        self.assertEqual(Segment.byte(memoryview(b"!abc?")[1:4]).data, b"abc")
        self.assertEqual(Segment.byte(memoryview(b"abcdef")[::2]).data, b"ace")
        values = array("H", [1, 257, 65535])
        self.assertEqual(Segment.byte(memoryview(values)).data, values.tobytes())
        closed = memoryview(b"abc")
        closed.release()
        with self.assertRaises(InvalidInputError):
            Segment.byte(closed)

    def test_scalar_and_mode_validation(self):
        invalid_modes = [None, 1, True, "auto", "unknown", []]
        for mode in invalid_modes:
            with self.assertRaises(InvalidModeError):
                Segment(mode, "123")
        for text in ("١٢٣", "１２３", "12a", "12\n"):
            with self.assertRaises(InvalidModeError):
                Segment.numeric(text)
        for text in ("lowercase", "é", "A\n"):
            with self.assertRaises(InvalidModeError):
                Segment.alphanumeric(text)
        for value in (None, 123, [1, 2], (1, 2), True):
            with self.assertRaises(InvalidInputError):
                Segment.byte(value)
        with self.assertRaises(InvalidModeError):
            Segment.kanji("😀")

    def test_surrogates_are_rejected_in_all_text_modes(self):
        for text in ("\ud800", "a\udfff", "\ud83d\ude00"):
            for factory in (Segment.byte, Segment.numeric, Segment.alphanumeric, Segment.kanji):
                with self.assertRaises(InvalidInputError):
                    factory(text)
            with self.assertRaises(InvalidInputError):
                create_segments(text)

    def test_inapplicable_fields_and_invalid_versions(self):
        with self.assertRaises(InvalidModeError):
            Segment("numeric", "123", assignment_number=26)
        with self.assertRaises(InvalidGs1Error):
            Segment("fnc1", "123")
        for version in (0, 41, True, 1.0, "1", None):
            with self.assertRaises(InvalidVersionError):
                Segment.fnc1().bits(version)


class ControlSegmentTests(unittest.TestCase):
    def test_eci_vectors_and_boundary_widths(self):
        for value, expected in ((26, "011100011010"), (300, "01111000000100101100"), (20000, "0111110000000100111000100000")):
            self.assertEqual(bit_string(Segment.eci(value)), expected)
        for value, length in ((0, 12), (127, 12), (128, 20), (16383, 20), (16384, 28), (999999, 28)):
            segment = Segment.eci(value)
            self.assertEqual(segment.bit_length(1), length)
            self.assertEqual(len(segment.bits(40)), length)
            self.assertEqual((segment.count, segment.byte_count, segment.character_count), (0, 0, 0))
            self.assertEqual(segment.logical_bytes, b"")
        for value in (-1, 1000000, 1.0, "26", True, None):
            with self.assertRaises(InvalidEciError):
                Segment.eci(value)

    def test_fnc1_vectors(self):
        self.assertEqual(bit_string(Segment.fnc1()), "0101")
        self.assertEqual(bit_string(Segment.fnc1_second("A")), "100110100101")
        for value, codeword in (("00", 0), ("37", 37), ("99", 99), ("A", 165), ("Z", 190), ("a", 197), ("z", 222)):
            segment = Segment.fnc1_second(value)
            self.assertEqual(segment.application_indicator_codeword, codeword)
            self.assertEqual(int(bit_string(segment)[4:], 2), codeword)
        for value in ("", "7", "123", "1A", "AB", "é", "١٢", 37, True, None):
            with self.assertRaises(InvalidModeError):
                Segment.fnc1_second(value)

    def test_structured_append_vector_and_one_based_contract(self):
        self.assertEqual(bit_string(Segment.structured_append(2, 5, 0xA7)), "00110001010010100111")
        self.assertEqual(bit_string(Segment.structured_append(16, 16, 255)), "00111111111111111111")
        for args in ((0, 2, 0), (3, 2, 0), (1, 1, 0), (1, 17, 0), (1, 2, 256), (1, 2, -1), (True, 2, 0), (1, 2.0, 0)):
            with self.assertRaises(InvalidModeError):
                Segment.structured_append(*args)

    def test_control_position_duplicates_and_combinations(self):
        controls = (Segment.fnc1(), Segment.fnc1_second("A"), Segment.structured_append(1, 2, 0))
        for control in controls:
            expected = InvalidGs1Error if control.mode == "fnc1" else InvalidModeError
            with self.assertRaises(expected):
                normalize_segments([Segment.byte("a"), control])
            with self.assertRaises(expected):
                normalize_segments([control, control])
        for left, right in itertools.combinations((*controls, Segment.eci(26)), 2):
            with self.assertRaises((InvalidGs1Error, InvalidModeError)):
                normalize_segments([left, right])

    def test_multiple_eci_allowed_and_do_not_transcode(self):
        segments = (Segment.eci(3), Segment.byte(b"\xe9"), Segment.eci(26), Segment.byte("é"))
        self.assertEqual(normalize_segments(segments), segments)
        self.assertEqual(segments[1].logical_bytes, b"\xe9")
        self.assertEqual(segments[3].logical_bytes, b"\xc3\xa9")
        self.assertEqual(create_segments("é", mode="byte", eci=3)[1].logical_bytes, b"\xc3\xa9")

    def test_low_level_fnc1_percent_is_preserved_verbatim(self):
        segments = normalize_segments([Segment.fnc1(), Segment.alphanumeric("A%B%%C")])
        self.assertEqual(segments[1].data, "A%B%%C")
        self.assertEqual(segments[1].logical_bytes, b"A%B%%C")


class ManualSegmentTests(unittest.TestCase):
    def test_manual_modes_and_boundaries_preserved(self):
        segments = normalize_segments([
            {"mode": "alphanumeric", "data": "ORDER-"},
            {"mode": "numeric", "text": "1234567890"},
            {"mode": "byte", "bytes": bytearray(b"hello")},
            Segment.byte("next"),
        ])
        self.assertEqual(tuple(segment.mode for segment in segments), ("alphanumeric", "numeric", "byte", "byte"))
        self.assertEqual(segments[2].data, b"hello")
        self.assertIsInstance(segments, tuple)

    def test_mapping_control_fields(self):
        self.assertEqual(normalize_segments([{"mode": "eci", "assignment_number": 26}]), (Segment.eci(26),))
        self.assertEqual(normalize_segments([{"mode": "structured-append", "index": 1, "total": 2, "parity": 255}]), (Segment.structured_append(1, 2, 255),))

    def test_malformed_manual_inputs(self):
        for sequence in (None, "123", b"abc", {"mode": "byte"}, iter([])):
            with self.assertRaises(InvalidInputError):
                normalize_segments(sequence)
        for item in (None, 1, [], {"mode": "byte", "data": "a", "text": "b"}, {"mode": "byte", "data": "a", "unexpected": 1}):
            with self.assertRaises(InvalidInputError):
                normalize_segments([item])
        with self.assertRaises(InvalidModeError):
            normalize_segments([{"data": "abc"}])
        self.assertEqual(normalize_segments([]), ())

    def test_manual_resource_budgets(self):
        with self.assertRaises(DataTooLongError):
            normalize_segments([Segment.byte(b"")] * 16385)
        with self.assertRaises(DataTooLongError):
            normalize_segments([{"mode": "byte", "data": "a" * 500001}] * 2)
        with self.assertRaises(DataTooLongError):
            normalize_segments([Segment.byte("a" * 500001)] * 2)
        with self.assertRaises(InvalidGs1Error):
            normalize_segments([{"mode": "fnc1", "data": None}])


class SegmentOptimizationTests(unittest.TestCase):
    def test_mode_selection(self):
        for text, mode in (("1234567890", "numeric"), ("HELLO WORLD", "alphanumeric"), ("こんにちは", "kanji"), ("https://example.com", "byte"), ("", "byte")):
            for optimize in (False, True):
                segments = create_segments(text, optimize=optimize)
                self.assertEqual(tuple(segment.mode for segment in segments), (mode,))
                self.assertEqual(segments[0].data, text)

    def test_mixed_numeric_byte_optimization(self):
        text = "abc123456789012345678901234567890def"
        segments = create_segments(text)
        self.assertEqual(tuple((segment.mode, segment.data) for segment in segments), (
            ("byte", "abc"), ("numeric", "123456789012345678901234567890"), ("byte", "def"),
        ))
        self.assertLess(segments_bit_length(segments, 1), segments_bit_length(create_segments(text, optimize=False), 1))

    def test_eci_disables_automatic_kanji_but_not_explicit_kanji(self):
        for optimize in (False, True):
            segments = create_segments("こんにちは", eci=True, optimize=optimize)
            self.assertEqual(segments, (Segment.eci(26), Segment.byte("こんにちは")))
        self.assertEqual(create_segments("漢字", mode="kanji", eci=26), (Segment.eci(26), Segment.kanji("漢字")))
        self.assertEqual(create_segments(b"a", eci=0)[0], Segment.eci(0))

    def test_exact_fit_mixed_eci_example(self):
        segments = create_segments("a111111111bb", eci=26)
        self.assertEqual(tuple(segment.mode for segment in segments), ("eci", "byte", "numeric", "byte"))
        self.assertEqual(tuple(segment.bit_length(1) for segment in segments), (12, 20, 44, 28))
        self.assertEqual(segments_bit_length(segments, 1), 104)

    def test_binary_input_and_input_option_validation(self):
        self.assertEqual(create_segments(b"\x00\xff"), (Segment.byte(b"\x00\xff"),))
        with self.assertRaises(InvalidModeError):
            create_segments(b"123", mode="numeric")
        with self.assertRaises(InvalidInputError):
            create_segments("abc", optimize=1)
        for value in (None, 123, [0, 255], iter([0])):
            with self.assertRaises(InvalidInputError):
                create_segments(value)

    def test_oversized_auto_dp_rejected_before_state_allocation(self):
        with patch("specqr.segments._advance", side_effect=AssertionError("DP must not run")):
            with self.assertRaises(DataTooLongError):
                create_segments("1" * 7090)
            self.assertEqual(create_segments("1" * 100000, optimize=False)[0].mode, "numeric")
            self.assertEqual(create_segments("1" * 100000, mode="numeric")[0].count, 100000)

    def test_tracker_matches_optimized_prefixes(self):
        text = "AB12x漢字1234567890123456789😀HELLO"
        for version in (1, 10, 27):
            for allow_kanji in (False, True):
                tracker = SegmentOptimizationTracker(version, allow_kanji)
                for length, character in enumerate(text, 1):
                    expected = create_segments(text[:length], version=version, eci=None if allow_kanji else 26)
                    self.assertEqual(tracker.append(character), segments_bit_length(expected, version) - (0 if allow_kanji else 12))
        tracker = SegmentOptimizationTracker(1)
        for bad in ("", "ab", "\ud800", 1):
            with self.assertRaises(InvalidInputError):
                tracker.append(bad)

    def test_randomized_cost_against_independent_partition_optimizer(self):
        randomizer = random.Random(9172)
        for version in (1, 10, 27):
            for _ in range(70):
                text = "".join(randomizer.choice("012ABxy漢😀") for _ in range(randomizer.randint(1, 13)))
                # Independent O(n²) exhaustive partition optimization. No state
                # modulo recurrence or optimizer implementation is reused.
                best = [0] + [10**9] * len(text)
                for end in range(1, len(text) + 1):
                    for start in range(end):
                        for mode in ("numeric", "alphanumeric", "kanji", "byte"):
                            try:
                                segment = Segment(mode, text[start:end])
                            except InvalidModeError:
                                continue
                            best[end] = min(best[end], best[start] + segment.bit_length(version))
                actual = create_segments(text, version=version)
                self.assertEqual(segments_bit_length(actual, version), best[-1], (version, text))
                self.assertEqual("".join(segment.data for segment in actual), text)
                self.assertTrue(all(type(bit) is int and bit in (0, 1) for segment in actual for bit in segment.bits(version)))


if __name__ == "__main__":
    unittest.main()
