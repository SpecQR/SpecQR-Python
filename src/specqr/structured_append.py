"""Bounded, deterministic QR Structured Append generation and reassembly.

The public header uses one-based ``index`` and ``total``.  Parity is the XOR
of the original UTF-8 text or raw binary payload, never Shift JIS codewords.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .errors import DataTooLongError, InvalidGs1Error, InvalidInputError, InvalidModeError
from .tables import character_count_bits, data_codeword_count
from .segments import MAX_MANUAL_SEGMENTS, MAX_PAYLOAD_UNITS

if TYPE_CHECKING:
    from .api import Options, QRResult
    from .segments import Segment


@dataclass(frozen=True)
class SAResult:
    symbols: tuple[QRResult, ...]
    total: int
    parity: int
    input_length: int
    byte_length: int
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True)
class MergeResult:
    data: str | bytes
    total: int
    parity: int
    parts: tuple[Mapping[str, Any], ...]
    diagnostics: Mapping[str, Any]


def _xor(values):
    parity = 0
    for value in values:
        parity ^= value
    return parity


def _text_info(text):
    """Compute canonical metrics without an input-sized encoded copy."""
    if len(text) > MAX_PAYLOAD_UNITS:
        raise DataTooLongError(f"Payload exceeds the {MAX_PAYLOAD_UNITS}-unit resource limit")
    byte_length = parity = 0
    for character in text:
        if 0xD800 <= ord(character) <= 0xDFFF:
            raise InvalidInputError("Text must contain Unicode scalar values; lone surrogates are not supported")
        encoded = character.encode("utf-8")
        byte_length += len(encoded)
        parity ^= _xor(encoded)
    return byte_length, parity


def _binary_length(value):
    if isinstance(value, memoryview):
        try:
            return value.nbytes
        except ValueError as exc:
            raise InvalidInputError("Input memoryview must not be released") from exc
    if isinstance(value, (bytes, bytearray, list, tuple)):
        return len(value)
    raise InvalidInputError("Input must be text, bytes, bytearray, memoryview, or a sequence of byte integers")


def _binary(value):
    if _binary_length(value) > MAX_PAYLOAD_UNITS:
        raise DataTooLongError(f"Payload exceeds the {MAX_PAYLOAD_UNITS}-unit resource limit")
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, (list, tuple)):
        if any(type(byte) is not int or not 0 <= byte <= 255 for byte in value):
            raise InvalidInputError("Every binary input element must be an integer from 0 to 255")
        return bytes(value)
    raise InvalidInputError("Input must be text, bytes, bytearray, memoryview, or a sequence of byte integers")


def calculate_structured_append_parity(
    input: str | bytes | bytearray | memoryview | list[int] | tuple[int, ...],
) -> int:
    """Return the canonical original-message byte XOR (0 for empty input)."""
    if isinstance(input, str):
        return _text_info(input)[1]
    if isinstance(input, (list, tuple)):
        if len(input) > MAX_PAYLOAD_UNITS:
            raise DataTooLongError(f"Payload exceeds the {MAX_PAYLOAD_UNITS}-unit resource limit")
        parity = 0
        for byte in input:
            if type(byte) is not int or not 0 <= byte <= 255:
                raise InvalidInputError("Every binary input element must be an integer from 0 to 255")
            parity ^= byte
        return parity
    return _xor(_binary(input))


def _canonical(segment):
    return _text_info(segment.data) if isinstance(segment.data, str) else (len(segment.data), _xor(segment.data))


def _manual_segments(segments, max_payload_units=None):
    from .segments import normalize_segments

    if not isinstance(segments, (list, tuple)) or not segments:
        raise InvalidInputError("Structured Append requires at least one non-empty data segment in a list or tuple")
    if len(segments) > MAX_MANUAL_SEGMENTS:
        raise DataTooLongError(f"Manual segments exceed the {MAX_MANUAL_SEGMENTS}-segment resource limit")
    unit_limit = min(MAX_PAYLOAD_UNITS, max_payload_units) if max_payload_units is not None else MAX_PAYLOAD_UNITS
    # Every segment contains at least one payload unit.  Reject huge collections
    # before normalization can copy them or instantiate per-segment objects.
    if max_payload_units is not None and len(segments) > max_payload_units:
        raise DataTooLongError("Input segments cannot be split within the selected Structured Append capacity")
    result = []
    units = 0
    for index, value in enumerate(segments):
        raw = value.get("data", value.get("text", value.get("bytes"))) if isinstance(value, Mapping) else getattr(value, "data", None)
        if isinstance(raw, (str, bytes, bytearray, memoryview, list, tuple)):
            units += len(raw) if isinstance(raw, str) else _binary_length(raw)
            if units > unit_limit:
                raise DataTooLongError("Input segments cannot be split within the selected Structured Append capacity")
        segment = normalize_segments([value])[0]
        if segment.mode in ("fnc1", "fnc1-first"):
            raise InvalidGs1Error("Structured Append cannot be combined with manual FNC1 first position segments")
        if segment.mode not in ("numeric", "alphanumeric", "byte", "kanji"):
            raise InvalidModeError(f"Structured Append cannot be combined with manual {segment.mode} control segments")
        if not segment.data:
            raise InvalidInputError(f"segments[{index}] must include non-empty data")
        result.append(segment)
    return tuple(result)


def calculate_structured_append_segments_parity(
    segments: list[Segment | Mapping[str, Any]] | tuple[Segment | Mapping[str, Any], ...],
    **options: Any,
) -> int:
    """XOR original text UTF-8/raw bytes, preserving manual segment order."""
    if "gs1" in options:
        raise InvalidGs1Error("Segment parity cannot be combined with GS1/FNC1 options")
    if options:
        raise InvalidModeError(f"Unsupported segment parity option: {next(iter(options))}")
    return _xor(_canonical(segment)[1] for segment in _manual_segments(segments))


def _diagnostic_options(value, manual):
    if type(value) is bool:
        return "summary", "diagnostics" if value else "output"
    if not manual or not isinstance(value, Mapping):
        raise InvalidInputError("diagnostics must be a boolean" + (" or a diagnostics mapping" if manual else ""))
    unsupported = set(value) - {"split_units", "symbol_results"}
    if unsupported:
        raise InvalidInputError(f"Unsupported Structured Append diagnostics option: {next(iter(unsupported))}")
    detail = value.get("split_units", "summary")
    symbols = value.get("symbol_results", "diagnostics")
    if detail not in ("summary", "full"):
        raise InvalidInputError('diagnostics.split_units must be "summary" or "full"')
    if symbols not in ("output", "diagnostics"):
        raise InvalidInputError('diagnostics.symbol_results must be "output" or "diagnostics"')
    return detail, symbols


def _options(base, options, manual):
    from .api import _options as normalize_options

    opts = dict(options)
    for old, new in (("error_correction", "error_correction_level"), ("mask", "mask_pattern")):
        if old in opts:
            raise InvalidModeError(f"Use {new}; {old} is not supported")
    if "parity" in opts:
        raise InvalidModeError("Structured Append computes canonical parity; parity override is not supported")
    if opts.get("structured_append") is not None and opts.get("structured_append") is not False:
        raise InvalidModeError("Structured Append generation owns its header; structured_append option is not supported")
    if manual:
        for key in ("mode", "encoding", "optimize_segments"):
            if key in opts:
                raise InvalidModeError(f"Manual Structured Append preserves caller modes; {key} is not supported")
    max_symbols = opts.pop("max_symbols", 16)
    if type(max_symbols) is not int or not 2 <= max_symbols <= 16:
        raise InvalidModeError("max_symbols must be an integer from 2 to 16")
    detail, symbol_results = _diagnostic_options(opts.pop("diagnostics", False), manual)
    # Explicit zero is a valid ECI assignment / FNC1 indicator, not false.
    for key, label in (("eci", "ECI"), ("fnc1_second", "FNC1 second position")):
        if key in opts and opts[key] is not None and opts[key] is not False:
            raise InvalidModeError(f"Structured Append cannot be combined with {label}")
    if opts.get("gs1", False):
        raise InvalidGs1Error("Structured Append cannot be combined with gs1")
    if opts.get("boost_error_correction", False):
        raise InvalidModeError("Structured Append does not support boost_error_correction")
    # Python's base API uses None for disabled controls and automatic values.
    for key in ("structured_append", "fnc1_second"):
        if opts.get(key) is False:
            opts[key] = None
    normalized = normalize_options(base, opts)
    if normalized.structured_append is not None:
        raise InvalidModeError("Structured Append generation owns its header")
    if normalized.eci is not None or normalized.fnc1_second is not None:
        raise InvalidModeError("Structured Append cannot be combined with ECI or FNC1 second position")
    if normalized.gs1:
        raise InvalidGs1Error("Structured Append cannot be combined with gs1")
    if normalized.boost_error_correction:
        raise InvalidModeError("Structured Append does not support boost_error_correction")
    if manual and (normalized.mode != "auto" or not normalized.optimize_segments):
        raise InvalidModeError("Manual Structured Append preserves caller segment modes")
    return normalized, max_symbols, detail, symbol_results


def _numeric_bits(length):
    return (length // 3) * 10 + (0, 4, 7)[length % 3]


def _payload_bits(mode, length, byte_length):
    if mode == "numeric":
        return _numeric_bits(length)
    if mode == "alphanumeric":
        return (length // 2) * 11 + (length % 2) * 6
    if mode == "kanji":
        return length * 13
    return byte_length * 8


def _segment_bits(mode, length, byte_length, version):
    count = byte_length if mode == "byte" else length
    width = character_count_bits(version, mode)
    if count >= (1 << width):
        return float("inf")
    return 4 + width + _payload_bits(mode, length, byte_length)


def _capacity_version(options):
    return options.max_version if options.version in (None, "auto") else options.version


def _capacity(options, version):
    return data_codeword_count(version, options.error_correction_level) * 8


def _unit_budget(options, max_symbols):
    # Numeric mode has the smallest per-scalar payload cost.  This upper bound
    # safely rejects impossible inputs before UTF-8 conversion or optimization.
    return max_symbols * ((_capacity(options, _capacity_version(options)) - 20) * 3 // 10)


class _TextIndex:
    """UTF-8 byte offsets with at most one integer per 64 Unicode scalars."""
    def __init__(self, text):
        self.text = text
        self.checkpoints = [0]
        total = 0
        for index, char in enumerate(text, 1):
            point = ord(char)
            total += 1 if point < 0x80 else 2 if point < 0x800 else 3 if point < 0x10000 else 4
            if index % 64 == 0:
                self.checkpoints.append(total)

    def offset(self, index):
        start = (index // 64) * 64
        return self.checkpoints[index // 64] + len(self.text[start:index].encode("utf-8"))

    def byte_length(self, start, length):
        return self.offset(start + length) - self.offset(start)


class _InputSource:
    def __init__(self, value, options, max_symbols):
        from .segments import create_segments

        self.binary = not isinstance(value, str)
        self.length = _binary_length(value) if self.binary else len(value)
        if not self.length:
            raise InvalidInputError("Structured Append requires at least two non-empty symbols")
        if self.length > _unit_budget(options, max_symbols):
            raise DataTooLongError("Input cannot be split within the selected Structured Append capacity")
        if self.binary:
            if options.mode not in ("auto", "byte"):
                raise InvalidModeError("Binary input can only be encoded in byte mode")
            self.value = _binary(value)
            self.byte_length, self.parity = len(self.value), _xor(self.value)
            self.index = None
        else:
            self.value = value
            self.byte_length, self.parity = _text_info(value)
            # Validation is independent of capacity: constructor validation does
            # not serialize count fields or build the entire QR bitstream.
            if options.mode != "auto":
                create_segments(value, mode=options.mode, version=_capacity_version(options), optimize=False)
            self.index = _TextIndex(value)
        self.input_length = self.length
        mode = "byte" if self.binary else options.mode
        version = _capacity_version(options)
        count_width = min(character_count_bits(version, candidate) for candidate in ("numeric", "alphanumeric", "byte", "kanji")) if mode == "auto" else character_count_bits(version, mode)
        required = _numeric_bits(self.length) if mode == "auto" else _payload_bits(mode, self.length, self.byte_length)
        available = max_symbols * max(0, _capacity(options, version) - 24 - count_width)
        if required > available:
            raise DataTooLongError("Input cannot be split within the selected Structured Append capacity")

    def bits(self, start, length, options, version):
        from .segments import create_segments, segments_bit_length, SegmentOptimizationTracker

        capacity = _capacity(options, version)
        if _numeric_bits(length) > capacity - 20:
            return float("inf")
        if self.binary:
            return 20 + _segment_bits("byte", length, length, version)
        byte_length = self.index.byte_length(start, length)
        if options.mode != "auto":
            return 20 + _segment_bits(options.mode, length, byte_length, version)
        if options.optimize_segments:
            tracker = SegmentOptimizationTracker(version)
            bits = 0
            for character in self.value[start:start + length]:
                bits = tracker.append(character)
                if bits + 20 > capacity:
                    break
            return bits + 20
        segments = create_segments(self.value[start:start + length], mode="auto", version=version, optimize=options.optimize_segments)
        return 20 + segments_bit_length(segments, version)

    def largest_prefix(self, start, maximum, options, version):
        if not self.binary and options.mode == "auto" and options.optimize_segments:
            from .segments import SegmentOptimizationTracker

            tracker = SegmentOptimizationTracker(version)
            capacity = _capacity(options, version) - 20
            for index in range(maximum):
                if tracker.append(self.value[start + index]) > capacity:
                    return index
            return maximum
        return _largest_prefix(self, start, maximum, options, version)

    def chunk(self, start, length):
        byte_start = start if self.binary else self.index.offset(start)
        byte_length = length if self.binary else self.index.byte_length(start, length)
        return self.value[start:start + length], {"input_start": start, "input_length": length, "byte_start": byte_start, "byte_length": byte_length}


@dataclass
class _Descriptor:
    segment: Segment
    source_index: int
    split_start: int
    split_count: int
    byte_start: int
    byte_length: int
    text_index: _TextIndex | None

    def range_bytes(self, start, length):
        if self.segment.mode != "byte":
            return self.byte_start, self.byte_length
        if self.text_index is None:
            return self.byte_start + start, length
        return self.byte_start + self.text_index.offset(start), self.text_index.byte_length(start, length)


class _SegmentSource:
    def __init__(self, values, options, max_symbols):
        self.segments = _manual_segments(values, _unit_budget(options, max_symbols))
        self.descriptors = []
        self.length = self.byte_length = self.parity = 0
        self.input_length = len(self.segments)
        total_bits = 0
        version = _capacity_version(options)
        for index, segment in enumerate(self.segments):
            byte_length, parity = _canonical(segment)
            is_text = isinstance(segment.data, str)
            count = len(segment.data)
            split_count = count if segment.mode == "byte" else 1
            self.descriptors.append(_Descriptor(segment, index, self.length, split_count, self.byte_length, byte_length, _TextIndex(segment.data) if segment.mode == "byte" and is_text else None))
            self.length += split_count
            self.byte_length += byte_length
            self.parity ^= parity
            # This is a total-capacity lower bound.  A splittable byte segment
            # may exceed one symbol's character-count field, so no count check.
            total_bits += 4 + character_count_bits(version, segment.mode) + _payload_bits(segment.mode, count, byte_length)
        if total_bits > max_symbols * max(0, _capacity(options, version) - 20):
            raise DataTooLongError("Input segments cannot be split within the selected Structured Append capacity")

    def ranges(self, start, length):
        end = start + length
        for descriptor in self.descriptors:
            finish = descriptor.split_start + descriptor.split_count
            if finish <= start:
                continue
            if descriptor.split_start >= end:
                break
            overlap = max(start, descriptor.split_start)
            yield descriptor, overlap - descriptor.split_start, min(end, finish) - overlap

    def bits(self, start, length, options, version):
        result = 20
        for descriptor, local_start, local_length in self.ranges(start, length):
            _, byte_length = descriptor.range_bytes(local_start, local_length)
            segment = descriptor.segment
            count = local_length if segment.mode == "byte" else len(segment.data)
            result += _segment_bits(segment.mode, count, byte_length, version)
            if result > _capacity(options, version):
                break
        return result

    def chunk(self, start, length):
        from .segments import Segment

        result = []
        first_index = last_index = byte_start = None
        byte_length = 0
        for descriptor, local_start, local_length in self.ranges(start, length):
            offset, size = descriptor.range_bytes(local_start, local_length)
            if first_index is None:
                first_index, byte_start = descriptor.source_index, offset
            last_index = descriptor.source_index + 1
            byte_length += size
            segment = descriptor.segment
            result.append(Segment.byte(segment.data[local_start:local_start + local_length]) if segment.mode == "byte" else segment)
        return tuple(result), {"source_segment_start": first_index, "source_segment_end": last_index, "split_unit_start": start, "split_unit_length": length, "byte_start": byte_start, "byte_length": byte_length}

    def full_detail(self):
        result = []
        for descriptor in self.descriptors:
            segment = descriptor.segment
            for unit in range(descriptor.split_count):
                byte_start, byte_length = descriptor.range_bytes(unit, 1)
                result.append({"source_segment_index": descriptor.source_index, "mode": segment.mode, "unit_start": unit if segment.mode == "byte" else 0, "unit_length": 1 if segment.mode == "byte" else len(segment.data), "byte_start": byte_start, "byte_length": byte_length})
        return result


def _largest_prefix(source, start, maximum, options, version):
    low, high, best = 1, maximum, 0
    capacity = _capacity(options, version)
    while low <= high:
        length = (low + high) // 2
        if source.bits(start, length, options, version) <= capacity:
            best, low = length, length + 1
        else:
            high = length - 1
    return best


def _attempt(source, options, version, max_symbols):
    if source.bits(0, source.length, options, version) <= _capacity(options, version):
        return "single", ()
    ranges = []
    start = 0
    while start < source.length:
        if len(ranges) == max_symbols:
            return "too-long", ()
        maximum = source.length - start - (1 if not ranges else 0)
        length = source.largest_prefix(start, maximum, options, version) if isinstance(source, _InputSource) else _largest_prefix(source, start, maximum, options, version)
        if not length:
            return "too-long", ()
        ranges.append((start, length))
        start += length
    return ("ok", tuple(ranges)) if len(ranges) >= 2 else ("single", ())


def _select(source, options, max_symbols):
    fixed = options.version not in (None, "auto")
    versions = (options.version,) if fixed else range(options.min_version, options.max_version + 1)
    saw_too_long = False
    for version in versions:
        status, ranges = _attempt(source, options, version, max_symbols)
        if status == "ok":
            return version, ranges, "fixed" if fixed else "auto-minimum"
        saw_too_long |= status == "too-long"
    if saw_too_long:
        raise DataTooLongError(f"Input cannot be split into {max_symbols} or fewer Structured Append symbols in the selected version range")
    raise InvalidInputError("Input fits in one symbol in the selected version range; use generate(), generate_segments(), or a low-level structured_append header")


def _generate(source, options, max_symbols, detail, symbol_results, manual):
    from dataclasses import asdict
    from .api import generate, generate_segments, _freeze

    version, ranges, selection = _select(source, options, max_symbols)
    symbols = []
    diagnostics = []
    total = len(ranges)
    for index, (start, length) in enumerate(ranges, 1):
        chunk, offsets = source.chunk(start, length)
        kwargs = asdict(options)
        kwargs.update(version=version, min_version=version, max_version=version, structured_append={"index": index, "total": total, "parity": source.parity})
        result = (generate_segments if manual else generate)(chunk, **kwargs)
        symbols.append(result)
        bits = result.diagnostics["data_bit_length"]
        diagnostics.append({"index": index, "total": total, "parity": source.parity, "sequence_index": index - 1, "sequence_total": total - 1, "sequence_indicator": ((index - 1) << 4) | (total - 1), **offsets, "version": version, "error_correction_level": result.error_correction_level, "data_bit_length": bits, "capacity_bits": _capacity(options, version), "remaining_bits": _capacity(options, version) - bits, "mask_pattern": result.mask_pattern})
    warnings = []
    if total == max_symbols:
        warnings.append({"code": "STRUCTURED_APPEND_MAX_SYMBOLS_NEAR_LIMIT", "severity": "info", "message": "The generated Structured Append set uses the configured maximum number of symbols.", "details": {"total": total, "max_symbols": max_symbols}})
    if symbol_results == "diagnostics":
        warnings.append({"code": "STRUCTURED_APPEND_DECODER_SUPPORT_VARIES", "severity": "info", "message": "Decoder APIs vary in how they expose Structured Append set metadata.", "details": {"total": total}})
    reason = f"Version {version} was requested explicitly." if selection == "fixed" else f"Version {version} is the smallest version in {options.min_version}..{options.max_version} that can split the payload into {total} Structured Append symbols at error correction {options.error_correction_level}."
    summary = {"version": version, "error_correction_level": options.error_correction_level, "version_selection": selection, "version_selection_reason": reason, "total": total, "parity": source.parity, "byte_length": source.byte_length, "input_length": source.input_length, "max_symbols": max_symbols, "split_strategy": "segment-boundary-byte-chunk" if manual else "greedy-largest-fitting", "symbols": diagnostics, "warnings": warnings}
    if manual:
        summary.update(segment_count=len(source.segments), split_unit_count=source.length, split_units_detail=detail)
        if detail == "full":
            summary["split_units"] = source.full_detail()
    return SAResult(tuple(symbols), total, source.parity, source.input_length, source.byte_length, _freeze(summary))


def generate_structured_append(
    input: str | bytes | bytearray | memoryview | list[int] | tuple[int, ...],
    options: Options | None = None,
    **kwargs: Any,
) -> SAResult:
    """Greedily split text/binary into 2..16 equal-version QR symbols."""
    normalized, maximum, detail, symbol_results = _options(options, kwargs, False)
    return _generate(_InputSource(input, normalized, maximum), normalized, maximum, detail, symbol_results, False)


def generate_segments_structured_append(
    segments: list[Segment | Mapping[str, Any]] | tuple[Segment | Mapping[str, Any], ...],
    options: Options | None = None,
    **kwargs: Any,
) -> SAResult:
    """Preserve manual modes; split only byte segments at safe boundaries.

    Standard diagnostics omit ``split_units``.  Request the bounded eager
    detail with ``diagnostics={"split_units": "full"}``.
    """
    normalized, maximum, detail, symbol_results = _options(options, kwargs, True)
    return _generate(_SegmentSource(segments, normalized, maximum), normalized, maximum, detail, symbol_results, True)


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise InvalidInputError(f"{name} must be an integer from {low} to {high}")
    return value


def merge_structured_append_parts(
    parts: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    **options: Any,
) -> MergeResult:
    """Validate a complete decoded set, order it, verify XOR, and concatenate.

    Parts are mappings containing ``index``, ``total``, ``parity``, and
    ``data``.  All data must be text or all data must be bytes-like.  Parity
    detects some accidental corruption; it is not an authenticity check.
    """
    from .api import _freeze

    if options:
        raise InvalidModeError(f"Unsupported merge option: {next(iter(options))}")
    if not isinstance(parts, (list, tuple)) or not parts:
        raise InvalidInputError("Structured Append parts must be a non-empty list or tuple")
    if len(parts) > 16:
        raise InvalidInputError("Structured Append cannot contain more than 16 parts")
    ordered = {}
    total = parity = kind = None
    byte_length = actual_parity = total_units = 0
    for source_index, part in enumerate(parts):
        if not isinstance(part, Mapping):
            raise InvalidInputError(f"parts[{source_index}] must be a mapping")
        index = _integer(part.get("index"), "index", 1, 16)
        part_total = _integer(part.get("total"), "total", 2, 16)
        part_parity = _integer(part.get("parity"), "parity", 0, 255)
        if index > part_total:
            raise InvalidInputError("Structured Append index must not exceed total")
        if total is not None and part_total != total:
            raise InvalidInputError("Structured Append total mismatch")
        if parity is not None and part_parity != parity:
            raise InvalidInputError("Structured Append parity mismatch")
        if index in ordered:
            raise InvalidInputError(f"Structured Append duplicate index {index}")
        data = part.get("data")
        if isinstance(data, (str, bytes, bytearray, memoryview)):
            total_units += len(data) if isinstance(data, str) else _binary_length(data)
            if total_units > MAX_PAYLOAD_UNITS:
                raise DataTooLongError(f"Merged payload exceeds the {MAX_PAYLOAD_UNITS}-unit resource limit")
        if isinstance(data, str):
            part_kind = "string"
            size, checksum = _text_info(data)
        elif isinstance(data, (bytes, bytearray, memoryview)):
            part_kind = "binary"
            data = _binary(data)
            size, checksum = len(data), _xor(data)
        else:
            raise InvalidInputError("Part data must be text, bytes, bytearray, or memoryview")
        if kind is not None and kind != part_kind:
            raise InvalidInputError("Structured Append parts must not mix string and binary data")
        total, parity, kind = part_total, part_parity, part_kind
        byte_length += size
        actual_parity ^= checksum
        ordered[index] = (data, {"index": index, "total": total, "parity": parity, "data_type": kind, "byte_length": size})
    missing = [index for index in range(1, total + 1) if index not in ordered]
    if missing:
        raise InvalidInputError("Structured Append parts are missing indexes: " + ", ".join(map(str, missing)))
    if len(parts) != total:
        raise InvalidInputError("Structured Append part count does not match total")
    if actual_parity != parity:
        raise InvalidInputError(f"Structured Append parity check failed: expected {parity}, got {actual_parity}")
    sorted_parts = [ordered[index] for index in range(1, total + 1)]
    merged = ("" if kind == "string" else b"").join(item[0] for item in sorted_parts)
    diagnostics = {"part_count": total, "total": total, "parity": parity, "data_type": kind, "byte_length": byte_length, "missing": [], "duplicate": [], "parity_check": {"expected": parity, "actual": actual_parity, "matches": True}}
    return MergeResult(merged, total, parity, _freeze([item[1] for item in sorted_parts]), _freeze(diagnostics))
