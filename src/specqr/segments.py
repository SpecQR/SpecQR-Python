"""Immutable QR segments and deterministic, standard-library-only segmentation.

Text is encoded strictly: surrogate code points are rejected, never replaced.
An ECI designator labels subsequent byte data; it does not transcode strings.
Text byte segments always use UTF-8. Supply bytes for another ECI encoding.

Low-level FNC1 alphanumeric data is already escaped QR data: a single ``%``
denotes a group separator and ``%%`` denotes a literal percent to FNC1 readers.
This module deliberately preserves manual data rather than rewriting it.

Resource limits are deterministic: at most 1,000,000 text scalars or binary
bytes per payload, 16,384 manual segments, and 1,000,000 aggregate input units
per manual sequence. These limits also apply to parity/planning input; ordinary
QR capacity is much lower. Automatic single-symbol optimization is limited to
7,089 scalars and bit materialization to 23,648 bits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, TypeAlias

from .errors import (
    DataTooLongError,
    InvalidEciError,
    InvalidGs1Error,
    InvalidInputError,
    InvalidModeError,
)
from .tables import character_count_bits, validate_version

Mode: TypeAlias = Literal[
    "numeric", "alphanumeric", "byte", "kanji", "eci", "fnc1",
    "fnc1-second", "structured-append",
]
BinaryInput: TypeAlias = bytes | bytearray | memoryview

ALPHANUMERIC_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
_ALPHANUMERIC = {character: index for index, character in enumerate(ALPHANUMERIC_CHARSET)}
_DATA_MODES = ("numeric", "alphanumeric", "kanji", "byte")
_CONTROL_MODES = ("eci", "fnc1", "fnc1-second", "structured-append")
_INDICATORS = {
    "numeric": 0b0001, "alphanumeric": 0b0010, "byte": 0b0100,
    "kanji": 0b1000, "eci": 0b0111, "fnc1": 0b0101,
    "fnc1-second": 0b1001, "structured-append": 0b0011,
}
MAX_SINGLE_SYMBOL_CHARACTERS = 7089
MAX_SINGLE_SYMBOL_DATA_BITS = 23648
MAX_PAYLOAD_UNITS = 1_000_000
MAX_MANUAL_SEGMENTS = 16_384


def _validate_version(version: int) -> int:
    validate_version(version)
    return version


def _strict_utf8(text: str) -> bytes:
    try:
        return text.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise InvalidInputError("QR text must not contain surrogate code points") from error


def _payload_units(data: object) -> int:
    try:
        size = data.nbytes if isinstance(data, memoryview) else len(data) if isinstance(data, (str, bytes, bytearray)) else 0
    except ValueError as error:
        raise InvalidInputError("QR input requires accessible binary data") from error
    if size > MAX_PAYLOAD_UNITS:
        raise DataTooLongError(f"Payload exceeds the {MAX_PAYLOAD_UNITS}-unit resource limit")
    return size


@lru_cache(maxsize=1)
def _kanji_map() -> dict[str, int]:
    """Reverse the same bounded Shift_JIS ranges as SpecQR's WHATWG decoder.

    CP932 decoding, unlike Python's strict shift_jis codec, reproduces the
    WHATWG mappings in these ranges. Keeping the first decoded mapping also
    reproduces SpecQR's deterministic duplicate-code-point policy.
    """
    result: dict[str, int] = {}
    for lead in (*range(0x81, 0xA0), *range(0xE0, 0xEC)):
        for trail in (*range(0x40, 0x7F), *range(0x80, 0xFD)):
            try:
                character = bytes((lead, trail)).decode("cp932", "strict")
            except UnicodeDecodeError:
                continue
            if len(character) == 1 and character != "\ufffd":
                result.setdefault(character, (lead << 8) | trail)
    return result


def can_encode_kanji(character: str) -> bool:
    """Whether one Unicode scalar has a supported QR Kanji mapping."""
    return (
        isinstance(character, str) and len(character) == 1
        and ord(character) >= 0x80 and character in _kanji_map()
    )


def kanji_value(character: str) -> int:
    """Return the thirteen-bit QR Kanji value for one supported scalar."""
    code = _kanji_map().get(character)
    if code is None:
        raise InvalidModeError(f"kanji mode cannot encode character: {character!r}")
    adjusted = code - (0x8140 if code <= 0x9FFC else 0xC140)
    return (adjusted >> 8) * 0xC0 + (adjusted & 0xFF)


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidModeError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Segment:
    """A validated data or control segment with immutable payload ownership.

    ``count`` is the count written to QR data headers. ``character_count`` is
    the source text's scalar count (zero for binary and controls).
    ``byte_count`` counts two bytes per Kanji scalar for QR diagnostics;
    ``logical_bytes`` instead uses canonical UTF-8 for textual data in every
    mode, including Kanji, as required by SpecQR Structured Append parity.
    """

    mode: Mode
    data: str | BinaryInput | None = None
    assignment_number: int | None = None
    application_indicator: str | None = None
    index: int | None = None
    total: int | None = None
    parity: int | None = None
    _logical: bytes = field(init=False, repr=False, compare=False, default=b"")

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in _INDICATORS:
            raise InvalidModeError(f"Unsupported segment mode: {self.mode!r}")
        values = {
            "data": self.data, "assignment_number": self.assignment_number,
            "application_indicator": self.application_indicator, "index": self.index,
            "total": self.total, "parity": self.parity,
        }
        allowed = (
            {"data"} if self.mode in _DATA_MODES else
            {"assignment_number"} if self.mode == "eci" else
            {"application_indicator"} if self.mode == "fnc1-second" else
            {"index", "total", "parity"} if self.mode == "structured-append" else set()
        )
        forbidden = [name for name, value in values.items() if value is not None and name not in allowed]
        if forbidden:
            error = InvalidGs1Error if self.mode == "fnc1" else InvalidModeError
            raise error(f"{self.mode} segment does not accept {', '.join(forbidden)}")

        if self.mode == "eci":
            if type(self.assignment_number) is not int or not 0 <= self.assignment_number <= 999999:
                raise InvalidEciError("ECI assignment number must be an integer from 0 to 999999")
        elif self.mode == "fnc1-second":
            value = self.application_indicator
            if not isinstance(value, str) or not (
                len(value) == 2 and all("0" <= character <= "9" for character in value)
                or len(value) == 1 and ("A" <= value <= "Z" or "a" <= value <= "z")
            ):
                raise InvalidModeError("FNC1 second application_indicator must be two ASCII digits or one Latin letter")
        elif self.mode == "structured-append":
            index = _integer(self.index, "Structured Append index", 1, 16)
            total = _integer(self.total, "Structured Append total", 2, 16)
            _integer(self.parity, "Structured Append parity", 0, 255)
            if index > total:
                raise InvalidModeError("Structured Append index must not exceed total")
        elif self.mode in _DATA_MODES:
            _payload_units(self.data)
            if self.mode == "byte" and isinstance(self.data, (bytes, bytearray, memoryview)):
                try:
                    payload = bytes(self.data)
                except (TypeError, ValueError) as error:
                    raise InvalidInputError("byte segment requires accessible binary data") from error
                object.__setattr__(self, "data", payload)
                object.__setattr__(self, "_logical", payload)
                return
            if not isinstance(self.data, str):
                raise InvalidInputError(f"{self.mode} segment requires text" + (" or bytes-like data" if self.mode == "byte" else ""))
            encoded = _strict_utf8(self.data)
            if self.mode == "numeric" and not all("0" <= character <= "9" for character in self.data):
                raise InvalidModeError("numeric mode can only encode decimal digits 0-9")
            if self.mode == "alphanumeric" and not all(character in _ALPHANUMERIC for character in self.data):
                raise InvalidModeError(f"alphanumeric mode can only encode: {ALPHANUMERIC_CHARSET}")
            if self.mode == "kanji":
                for character in self.data:
                    if not can_encode_kanji(character):
                        raise InvalidModeError(f"kanji mode cannot encode character: {character!r}")
            object.__setattr__(self, "_logical", encoded)

    @classmethod
    def numeric(cls, data: str) -> Segment:
        return cls("numeric", data)

    @classmethod
    def alphanumeric(cls, data: str) -> Segment:
        return cls("alphanumeric", data)

    @classmethod
    def byte(cls, data: str | BinaryInput) -> Segment:
        return cls("byte", data)

    @classmethod
    def kanji(cls, data: str) -> Segment:
        return cls("kanji", data)

    @classmethod
    def eci(cls, assignment_number: int) -> Segment:
        return cls("eci", assignment_number=assignment_number)

    @classmethod
    def fnc1(cls) -> Segment:
        return cls("fnc1")

    @classmethod
    def fnc1_second(cls, application_indicator: str) -> Segment:
        return cls("fnc1-second", application_indicator=application_indicator)

    @classmethod
    def structured_append(cls, index: int, total: int, parity: int) -> Segment:
        return cls("structured-append", index=index, total=total, parity=parity)

    @property
    def is_control(self) -> bool:
        return self.mode in _CONTROL_MODES

    @property
    def text(self) -> str | None:
        return self.data if isinstance(self.data, str) else None

    @property
    def logical_bytes(self) -> bytes:
        return self._logical

    @property
    def count(self) -> int:
        if self.is_control:
            return 0
        return len(self._logical) if self.mode == "byte" else len(self.data)

    @property
    def character_count(self) -> int:
        return len(self.data) if isinstance(self.data, str) else 0

    @property
    def byte_count(self) -> int:
        return self.count * 2 if self.mode == "kanji" else len(self._logical)

    @property
    def application_indicator_codeword(self) -> int | None:
        if self.mode != "fnc1-second":
            return None
        value = self.application_indicator
        return int(value) if len(value) == 2 else ord(value) + 100

    def bit_length(self, version: int) -> int:
        """Full unpadded length, including oversized inputs for planning.

        This arithmetic estimate can exceed a count field or symbol capacity;
        ``bits`` performs the count and bounded-allocation checks.
        """
        _validate_version(version)
        if self.mode == "eci":
            return 12 if self.assignment_number < 128 else 20 if self.assignment_number < 16384 else 28
        if self.mode in ("fnc1", "fnc1-second", "structured-append"):
            return {"fnc1": 4, "fnc1-second": 12, "structured-append": 20}[self.mode]
        width = character_count_bits(version, self.mode)
        count = self.count
        if self.mode == "numeric":
            payload = count // 3 * 10 + (0, 4, 7)[count % 3]
        elif self.mode == "alphanumeric":
            payload = count // 2 * 11 + count % 2 * 6
        else:
            payload = count * (13 if self.mode == "kanji" else 8)
        return 4 + width + payload

    def bits(self, version: int) -> tuple[int, ...]:
        """Return complete header/payload bits, without terminator or padding."""
        length = self.bit_length(version)
        if not self.is_control and self.count >= 1 << character_count_bits(version, self.mode):
            raise DataTooLongError(f"Input has {self.count} {self.mode} units, too many for version {version}")
        if length > MAX_SINGLE_SYMBOL_DATA_BITS:
            raise DataTooLongError("Segment exceeds the maximum single-symbol bit capacity")
        result: list[int] = []

        def append(value: int, width: int) -> None:
            result.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))

        append(_INDICATORS[self.mode], 4)
        if self.mode == "eci":
            value = self.assignment_number
            if value < 128:
                append(value, 8)
            elif value < 16384:
                append(0b10, 2)
                append(value, 14)
            else:
                append(0b110, 3)
                append(value, 21)
        elif self.mode == "fnc1-second":
            append(self.application_indicator_codeword, 8)
        elif self.mode == "structured-append":
            append(self.index - 1, 4)
            append(self.total - 1, 4)
            append(self.parity, 8)
        elif not self.is_control:
            append(self.count, character_count_bits(version, self.mode))
            if self.mode == "byte":
                for byte in self._logical:
                    append(byte, 8)
            elif self.mode == "numeric":
                for start in range(0, len(self.data), 3):
                    chunk = self.data[start:start + 3]
                    append(int(chunk), (0, 4, 7, 10)[len(chunk)])
            elif self.mode == "alphanumeric":
                for start in range(0, len(self.data) - 1, 2):
                    append(_ALPHANUMERIC[self.data[start]] * 45 + _ALPHANUMERIC[self.data[start + 1]], 11)
                if len(self.data) % 2:
                    append(_ALPHANUMERIC[self.data[-1]], 6)
            else:
                for character in self.data:
                    append(kanji_value(character), 13)
        assert len(result) == length
        return tuple(result)


def normalize_segments(segments: Sequence[Segment | Mapping[str, object]]) -> tuple[Segment, ...]:
    """Validate manual boundaries and controls, accepting typed segments or mappings.

    Mappings use the constructor's snake_case keys. ``text`` and ``bytes`` are
    accepted as unambiguous aliases for ``data``; no other keys are ignored.
    Generators and unbounded iterables are intentionally unsupported.
    """
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes, bytearray, memoryview)):
        raise InvalidInputError("manual segments must be a finite sequence")
    if len(segments) > MAX_MANUAL_SEGMENTS:
        raise DataTooLongError(f"Manual segments exceed the {MAX_MANUAL_SEGMENTS}-segment resource limit")
    result: list[Segment] = []
    units = 0
    allowed = {"mode", "data", "assignment_number", "application_indicator", "index", "total", "parity", "text", "bytes"}
    for index, item in enumerate(segments):
        if isinstance(item, Segment):
            units += _payload_units(item.data)
            if units > MAX_PAYLOAD_UNITS:
                raise DataTooLongError(f"Manual payload exceeds the {MAX_PAYLOAD_UNITS}-unit resource limit")
            result.append(item)
            continue
        if not isinstance(item, Mapping):
            raise InvalidInputError(f"segments[{index}] must be a Segment or mapping")
        unknown = set(item) - allowed
        if unknown:
            raise InvalidInputError(f"segments[{index}] has unsupported fields")
        values = dict(item)
        payload_names = [name for name in ("data", "text", "bytes") if name in values]
        if len(payload_names) > 1:
            raise InvalidInputError(f"segments[{index}] has ambiguous payload fields")
        if payload_names and payload_names[0] != "data":
            values["data"] = values.pop(payload_names[0])
        if "mode" not in values:
            raise InvalidModeError(f"segments[{index}] requires mode")
        if values["mode"] in _CONTROL_MODES and payload_names:
            error = InvalidGs1Error if values["mode"] == "fnc1" else InvalidModeError
            raise error(f"segments[{index}] control segment must not include a payload field")
        units += _payload_units(values.get("data"))
        if units > MAX_PAYLOAD_UNITS:
            raise DataTooLongError(f"Manual payload exceeds the {MAX_PAYLOAD_UNITS}-unit resource limit")
        result.append(Segment(**values))
    return validate_control_segments(tuple(result))


def validate_control_segments(segments: tuple[Segment, ...]) -> tuple[Segment, ...]:
    """Enforce the deliberately bounded SpecQR control-combination contract."""
    found: dict[str, list[int]] = {mode: [] for mode in _CONTROL_MODES}
    for index, segment in enumerate(segments):
        if segment.is_control:
            found[segment.mode].append(index)
    for mode in ("fnc1", "fnc1-second", "structured-append"):
        positions = found[mode]
        error = InvalidGs1Error if mode == "fnc1" else InvalidModeError
        if len(positions) > 1:
            raise error(f"manual segments can include at most one {mode} segment")
        if positions and positions[0] != 0:
            raise error(f"manual {mode} segment must be the first segment")
    present = [mode for mode in _CONTROL_MODES if found[mode]]
    if len(present) > 1:
        error = InvalidGs1Error if found["fnc1"] else InvalidModeError
        raise error("FNC1, FNC1 second, Structured Append, and ECI cannot be combined in this implementation")
    return segments


def segments_bit_length(segments: Sequence[Segment], version: int) -> int:
    """Sum full unpadded lengths, including oversized inputs for planning."""
    _validate_version(version)
    return sum(segment.bit_length(version) for segment in segments)


@dataclass(frozen=True, slots=True)
class _State:
    cost: int
    segment_count: int
    mode: str | None
    mod: int
    previous: tuple[str | None, int] | None


def _advance(states: dict[tuple[str | None, int], _State], character: str,
             version: int, allow_kanji: bool) -> dict[tuple[str | None, int], _State]:
    modes = [mode for mode in _DATA_MODES if (
        mode == "byte"
        or mode == "numeric" and "0" <= character <= "9"
        or mode == "alphanumeric" and character in _ALPHANUMERIC
        or mode == "kanji" and allow_kanji and can_encode_kanji(character)
    )]
    byte_cost = len(character.encode("utf-8")) * 8
    next_states: dict[tuple[str | None, int], _State] = {}
    for key, state in states.items():
        for mode in modes:
            same = mode == state.mode
            mod = state.mod if same else 0
            cost = (
                (4 if mod == 0 else 3) if mode == "numeric" else
                (6 if mod == 0 else 5) if mode == "alphanumeric" else
                13 if mode == "kanji" else byte_cost
            )
            next_mod = (mod + 1) % (3 if mode == "numeric" else 2) if mode in ("numeric", "alphanumeric") else 0
            next_key = (mode, next_mod)
            candidate = _State(
                state.cost + cost + (0 if same else 4 + character_count_bits(version, mode)),
                state.segment_count + (0 if same else 1), mode, next_mod, key,
            )
            current = next_states.get(next_key)
            if current is None or (candidate.cost, candidate.segment_count) < (current.cost, current.segment_count):
                # Assignment retains the original key position, just like JS Map.set.
                next_states[next_key] = candidate
    return next_states


class SegmentOptimizationTracker:
    """Constant-memory prefix bit costs for Structured Append planning.

    The tracker does not impose a single-symbol input cap, because callers may
    use it to determine split points in larger inputs. ``append`` accepts one
    well-formed Unicode scalar at a time and returns the minimum bit cost.
    """

    def __init__(self, version: int, allow_kanji: bool = True) -> None:
        self.version = _validate_version(version)
        if type(allow_kanji) is not bool:
            raise InvalidInputError("allow_kanji must be a boolean")
        self.allow_kanji = allow_kanji
        self._states = {(None, 0): _State(0, 0, None, 0, None)}

    def append(self, character: str) -> int:
        if not isinstance(character, str) or len(character) != 1:
            raise InvalidInputError("append requires exactly one Unicode scalar")
        _strict_utf8(character)
        self._states = _advance(self._states, character, self.version, self.allow_kanji)
        return min(state.cost for state in self._states.values())


def _optimize(text: str, version: int, allow_kanji: bool) -> tuple[Segment, ...]:
    if len(text) > MAX_SINGLE_SYMBOL_CHARACTERS:
        raise DataTooLongError("Text exceeds maximum single-symbol character capacity")
    if not text:
        return (Segment.byte(""),)
    layers = [{(None, 0): _State(0, 0, None, 0, None)}]
    for character in text:
        layers.append(_advance(layers[-1], character, version, allow_kanji))
    key = min(layers[-1], key=lambda key: (layers[-1][key].cost, layers[-1][key].segment_count))
    assignments = [""] * len(text)
    for index in range(len(text), 0, -1):
        state = layers[index][key]
        assignments[index - 1] = state.mode
        key = state.previous
    segments: list[Segment] = []
    start = 0
    for index in range(1, len(text) + 1):
        if index == len(text) or assignments[index] != assignments[start]:
            segments.append(Segment(assignments[start], text[start:index]))
            start = index
    return tuple(segments)


def create_segments(input: str | BinaryInput, mode: str = "auto", version: int = 1,
                    optimize: bool = True, eci: int | bool | None = None) -> tuple[Segment, ...]:
    """Build one symbol's deterministic data segments and optional leading ECI.

    ``eci=True`` means UTF-8 ECI assignment 26. Other ECI assignments label raw
    bytes without transcoding. Automatic ECI segmentation excludes Kanji, while
    an explicitly requested Kanji segment remains available.
    """
    _validate_version(version)
    if type(optimize) is not bool:
        raise InvalidInputError("optimize must be a boolean")
    if not isinstance(mode, str) or mode not in ("auto", *_DATA_MODES):
        raise InvalidModeError(f"Unsupported input mode: {mode!r}")
    assignment = None if eci is None or eci is False else 26 if eci is True else eci
    prefix = () if assignment is None else (Segment.eci(assignment),)
    if isinstance(input, (bytes, bytearray, memoryview)):
        if mode not in ("auto", "byte"):
            raise InvalidModeError("Binary input can only be encoded in byte mode")
        return prefix + (Segment.byte(input),)
    if not isinstance(input, str):
        raise InvalidInputError("QR input must be a string or bytes-like object")
    if mode == "auto" and optimize and len(input) > MAX_SINGLE_SYMBOL_CHARACTERS:
        raise DataTooLongError("Text exceeds maximum single-symbol character capacity")
    _payload_units(input)
    _strict_utf8(input)
    if mode != "auto":
        segments = (Segment(mode, input),)
    elif optimize:
        segments = _optimize(input, version, assignment is None)
    else:
        selected = "byte"
        if input and all("0" <= character <= "9" for character in input):
            selected = "numeric"
        elif input and all(character in _ALPHANUMERIC for character in input):
            selected = "alphanumeric"
        elif input and assignment is None and all(can_encode_kanji(character) for character in input):
            selected = "kanji"
        segments = (Segment(selected, input),)
    return prefix + segments
