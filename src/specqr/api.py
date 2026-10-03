"""Typed public encoding, planning and diagnostics API."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any
import math

from .errors import (DataTooLongError, InvalidEciError, InvalidGs1Error,
                     InvalidInputError, InvalidModeError, InvalidOutputError,
                     InvalidVersionError)
from .segments import Segment, create_segments, normalize_segments, segments_bit_length
from .tables import character_count_bits, data_codeword_count, raw_codeword_count, size
from .core import pad_data_bits, interleave_codewords, build_matrix
from . import render


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(v) for v in value)
    return value


def _integer(value: object, name: str, low: int, high: int, error: type[Exception] = InvalidInputError) -> int:
    if type(value) is not int or not low <= value <= high:
        raise error(f"{name} must be an integer from {low} to {high}")
    return value


@dataclass(frozen=True, slots=True)
class Options:
    error_correction_level: str = "M"
    version: int | None = None
    min_version: int = 1
    max_version: int = 40
    mask_pattern: int | None = None
    mode: str = "auto"
    optimize_segments: bool = True
    boost_error_correction: bool = False
    eci: int | bool | None = None
    gs1: bool = False
    fnc1_second: str | None = None
    structured_append: Segment | Mapping[str, int] | None = None
    margin: int = 4
    scale: int = 8
    foreground: str = "#000000"
    background: str = "#ffffff"
    print_dpi: float | None = None

    def __post_init__(self) -> None:
        if self.error_correction_level not in ("L", "M", "Q", "H"):
            raise InvalidInputError("error_correction_level must be L, M, Q, or H")
        for name in ("min_version", "max_version"):
            _integer(getattr(self, name), name, 1, 40, InvalidVersionError)
        if self.min_version > self.max_version:
            raise InvalidVersionError("min_version must not exceed max_version")
        if self.version is not None:
            _integer(self.version, "version", 1, 40, InvalidVersionError)
        if self.mask_pattern is not None:
            _integer(self.mask_pattern, "mask_pattern", 0, 7)
        if self.mode not in ("auto", "numeric", "alphanumeric", "byte", "kanji"):
            raise InvalidModeError("mode must be auto, numeric, alphanumeric, byte, or kanji")
        for name in ("optimize_segments", "boost_error_correction", "gs1"):
            if type(getattr(self, name)) is not bool:
                raise InvalidInputError(f"{name} must be bool")
        if self.eci is True:
            object.__setattr__(self, "eci", 26)
        elif self.eci is False:
            object.__setattr__(self, "eci", None)
        elif self.eci is not None:
            _integer(self.eci, "eci", 0, 999999, InvalidEciError)
        if self.fnc1_second is not None:
            Segment.fnc1_second(self.fnc1_second)
        sa = self.structured_append
        if sa is not None:
            if isinstance(sa, Mapping):
                if set(sa) != {"index", "total", "parity"}:
                    raise InvalidModeError("structured_append requires exactly index, total, parity")
                sa = Segment.structured_append(sa["index"], sa["total"], sa["parity"])
            if not isinstance(sa, Segment) or sa.mode != "structured-append":
                raise InvalidModeError("structured_append must be a Structured Append segment or mapping")
            object.__setattr__(self, "structured_append", sa)
        if sum((self.gs1, self.fnc1_second is not None, self.eci is not None, sa is not None)) > 1:
            raise InvalidModeError("GS1, FNC1 second, ECI, and Structured Append options cannot be combined")
        _integer(self.margin, "margin", 0, render.MAX_GEOMETRY_INTEGER)
        _integer(self.scale, "scale", 1, render.MAX_GEOMETRY_INTEGER)
        for name in ("foreground", "background"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > render.SVG_CHARACTER_BUDGET // 12:
                raise InvalidInputError(f"{name} must be a bounded color string")
        if self.print_dpi is not None:
            try:
                valid = (type(self.print_dpi) in (int, float) and math.isfinite(self.print_dpi)
                         and self.print_dpi > 0
                         and math.isfinite((177 + self.margin * 2) * (self.scale / self.print_dpi * 25.4)))
            except (OverflowError, ZeroDivisionError):
                valid = False
            if not valid:
                raise InvalidInputError("print_dpi must be positive and produce finite print geometry")


def _options(options: Options | None, kwargs: dict[str, Any]) -> Options:
    if options is None:
        try:
            return Options(**kwargs)
        except TypeError as exc:
            raise InvalidInputError(str(exc)) from exc
    if not isinstance(options, Options):
        raise InvalidInputError("options must be Options or None")
    try:
        return replace(options, **kwargs) if kwargs else options
    except TypeError as exc:
        raise InvalidInputError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class Capacity:
    version: int
    error_correction_level: str
    size: int
    data_codewords: int
    total_codewords: int
    capacity_bits: int
    mode: str | None
    character_count_bits: int | None
    mode_indicator_bits: int | None
    control_bits: int
    payload_bits: int | None
    max_characters: int | None
    max_bytes: int | None

    @property
    def maximum(self) -> int | None:
        return self.max_bytes if self.mode == "byte" else self.max_characters


def get_capacity(version: int, error_correction_level: str = "M", *, mode: str | None = None,
                 control_bits: int = 0) -> Capacity:
    _integer(version, "version", 1, 40, InvalidVersionError)
    if error_correction_level not in ("L", "M", "Q", "H"):
        raise InvalidInputError("error_correction_level must be L, M, Q, or H")
    _integer(control_bits, "control_bits", 0, 2**53 - 1)
    data = data_codeword_count(version, error_correction_level)
    width = payload = maximum = None
    if mode is not None:
        if mode not in ("numeric", "alphanumeric", "byte", "kanji"):
            raise InvalidModeError("capacity mode must be numeric, alphanumeric, byte, or kanji")
        width = character_count_bits(version, mode)
        payload = max(0, data * 8 - control_bits - 4 - width)
        if mode == "numeric":
            maximum = (payload // 10) * 3 + (2 if payload % 10 >= 7 else 1 if payload % 10 >= 4 else 0)
        elif mode == "alphanumeric":
            maximum = (payload // 11) * 2 + int(payload % 11 >= 6)
        else:
            maximum = payload // (8 if mode == "byte" else 13)
        maximum = min(maximum, (1 << width) - 1)
    return Capacity(version, error_correction_level, size(version), data, raw_codeword_count(version),
                    data * 8, mode, width, None if mode is None else 4, control_bits, payload,
                    None if mode == "byte" else maximum, maximum if mode == "byte" else None)


@dataclass(frozen=True, slots=True)
class Plan:
    ok: bool
    version: int | None
    capacity_version: int
    error_correction_level: str
    requested_error_correction_level: str
    boosted_error_correction: bool
    data_bit_length: int
    capacity_bits: int
    remaining_bits: int
    segments: tuple[Segment, ...]
    diagnostics: Mapping[str, Any]

    @property
    def selected_version(self) -> int | None:
        return self.version

    @property
    def overflow_bits(self) -> int:
        return max(0, -self.remaining_bits)

    @property
    def capacity_utilization(self) -> float:
        return self.data_bit_length / self.capacity_bits

    @property
    def warnings(self) -> tuple[Mapping[str, Any], ...]:
        return self.diagnostics["warnings"]


@dataclass(frozen=True, slots=True)
class QRResult:
    matrix: render.Matrix
    version: int
    mask_pattern: int
    error_correction_level: str
    data_codewords: bytes
    codewords: bytes
    segments: tuple[Segment, ...]
    diagnostics: Mapping[str, Any]
    options: Options

    @property
    def error_correction_codewords(self) -> bytes:
        """Interleaved ECC bytes, excluding the interleaved data prefix."""
        return self.codewords[len(self.data_codewords):]

    def _render_options(self, overrides: dict[str, Any]) -> dict[str, Any]:
        values = {k: getattr(self.options, k) for k in ("margin", "scale", "foreground", "background")}
        values.update(overrides)
        return values

    def to_svg(self, **options: Any) -> str:
        return render.to_svg(self.matrix, **self._render_options(options))

    def to_png(self, **options: Any) -> bytes:
        return render.to_png(self.matrix, **self._render_options(options))

    def to_pixels(self, **options: Any) -> render.Pixels:
        return render.to_pixels(self.matrix, **self._render_options(options))

    def to_svg_data_url(self, **options: Any) -> str:
        return render.to_svg_data_url(self.matrix, **self._render_options(options))

    def to_png_data_url(self, **options: Any) -> str:
        return render.to_png_data_url(self.matrix, **self._render_options(options))

    def render(self, output: str = "svg", **options: Any) -> Any:
        if output == "matrix":
            if options:
                raise InvalidInputError("matrix output does not use render options")
            return self.matrix
        function = {"svg": self.to_svg, "png": self.to_png, "pixels": self.to_pixels,
                    "svg-data-url": self.to_svg_data_url, "png-data-url": self.to_png_data_url}.get(output)
        if function is None:
            raise InvalidOutputError("output must be matrix, svg, png, pixels, svg-data-url, or png-data-url")
        return function(**options)


def _with_controls(segments: tuple[Segment, ...], opts: Options) -> tuple[Segment, ...]:
    prefix: tuple[Segment, ...] = ()
    if opts.eci is not None:
        prefix = (Segment.eci(opts.eci),)
    elif opts.gs1:
        prefix = (Segment.fnc1(),)
    elif opts.fnc1_second is not None:
        prefix = (Segment.fnc1_second(opts.fnc1_second),)
    elif opts.structured_append is not None:
        prefix = (opts.structured_append,)  # type: ignore[assignment]
    return normalize_segments(prefix + segments)


def _diagnostics(segments: tuple[Segment, ...], version: int, level: str, bits: int,
                 opts: Options, *, planning: bool, ok: bool) -> dict[str, Any]:
    capacity = data_codeword_count(version, level) * 8
    controls = tuple(s for s in segments if s.mode not in ("numeric", "alphanumeric", "byte", "kanji"))
    data_segments = tuple(s for s in segments if s.mode in ("numeric", "alphanumeric", "byte", "kanji"))
    modes = {s.mode for s in data_segments}
    mode = next(iter(modes)) if len(modes) == 1 else "mixed" if modes else "byte"
    warnings: list[dict[str, Any]] = []
    def warn(code: str, severity: str, message: str, **details: Any) -> None:
        warnings.append({"code": code, "severity": severity, "message": message, "details": details})
    if opts.margin < 4:
        warn("QUIET_ZONE_TOO_SMALL", "warning", "QR readers expect at least four quiet-zone modules.", margin=opts.margin)
    fg, bg = render.parse_color(opts.foreground, strict=False), render.parse_color(opts.background, strict=False)
    ratio = None if fg is None or bg is None else render.contrast_ratio(fg, bg)
    if ratio is None:
        warn("COLOR_CONTRAST_UNKNOWN", "info", "These SVG colors cannot be checked for contrast.")
    elif ratio < 4.5:
        warn("COLOR_CONTRAST_LOW", "warning", "Color contrast is below the recommended minimum.", ratio=ratio)
    elif ratio < 7:
        warn("COLOR_CONTRAST_MODERATE", "info", "Stronger color contrast is recommended.", ratio=ratio)
    if fg and bg and (fg[3] < 255 or bg[3] < 255):
        warn("COLOR_ALPHA_USED", "warning", "Transparent colors can reduce scan reliability.")
    if 0 <= capacity - bits < capacity * .05:
        warn("CAPACITY_NEAR_LIMIT", "info", "The selected version is close to full capacity.")
    mm = None if opts.print_dpi is None else opts.scale / opts.print_dpi * 25.4
    if mm is not None and mm < .25:
        warn("PRINT_MODULE_TOO_SMALL", "warning", "Print modules are smaller than 0.25 mm.", module_size_mm=mm)
    if any(w["severity"] == "warning" for w in warnings):
        warn("SCAN_RISK", "warning", "One or more settings may reduce scan reliability.",
             blocking_warnings=[w["code"] for w in warnings if w["severity"] == "warning"])
    sa = next((s for s in controls if s.mode == "structured-append"), None)
    second = next((s for s in controls if s.mode == "fnc1-second"), None)
    eci = next((s.assignment_number for s in controls if s.mode == "eci"), None)
    fnc1 = "first-position" if any(s.mode == "fnc1" for s in controls) else "second-position" if second else None
    selection = "fixed" if opts.version is not None else "auto-minimum" if ok else "auto-range"
    gs1_validation = {"enabled": fnc1 == "first-position", "element_count": None if fnc1 == "first-position" else 0,
                      "ais": (), "has_separators": False}
    return {"phase": "planning" if planning else "generation", "render_planned": False,
            "mask_evaluated": not planning, "codewords_built": not planning,
            "version": version if ok or opts.version is not None else None,
            "size": size(version) if ok or opts.version is not None else None,
            "error_correction_level": level, "requested_error_correction_level": opts.error_correction_level,
            "boosted_error_correction": level != opts.error_correction_level, "version_selection": selection,
            "version_selection_reason": (f"Version {version} was requested explicitly." if opts.version is not None
                else f"Version {version} is the smallest version in {opts.min_version}..{opts.max_version} that fits."
                if ok else f"No version in {opts.min_version}..{opts.max_version} fits; capacity is for version {version}."),
            "mode": mode, "control_segments": [{"mode": s.mode, "bit_length": s.bit_length(version)} for s in controls],
            "eci_assignment_number": eci, "fnc1": fnc1, "gs1": fnc1 == "first-position", "gs1_validation": gs1_validation,
            "fnc1_second": {"enabled": second is not None, "application_indicator": second.application_indicator if second else None,
                "application_indicator_codeword": (int(second.application_indicator) if len(second.application_indicator) == 2
                    else ord(second.application_indicator) + 100) if second else None},
            "structured_append": {"enabled": sa is not None, "index": sa.index if sa else None,
                "total": sa.total if sa else None, "parity": sa.parity if sa else None,
                "sequence_index": sa.index - 1 if sa else None, "sequence_total": sa.total - 1 if sa else None,
                "sequence_indicator": ((sa.index - 1) << 4) | (sa.total - 1) if sa else None},
            "segments": [{"mode": s.mode, "character_count": s.character_count, "byte_count": s.byte_count,
                          "bit_length": s.bit_length(version)} for s in segments],
            "data_bit_length": bits, "capacity_bits": capacity, "remaining_bits": capacity - bits,
            "capacity_utilization": bits / capacity, "input_bytes": sum(len(s.logical_bytes) for s in segments),
            "quiet_zone": {"modules": opts.margin, "recommended_modules": 4, "is_sufficient": opts.margin >= 4},
            "colors": {"ratio": ratio, "is_inspectable": ratio is not None,
                "foreground_alpha": fg[3] if fg else None, "background_alpha": bg[3] if bg else None,
                "is_strong": ratio is not None and ratio >= 7,
                "is_sufficient": ratio is not None and ratio >= 4.5 and fg[3] == bg[3] == 255},
            "print": {"dpi": opts.print_dpi, "module_pixels": opts.scale, "module_size_mm": mm,
                "symbol_size_mm": None if mm is None else (size(version) + opts.margin * 2) * mm,
                "recommended_minimum_module_size_mm": .25, "is_module_size_sufficient": None if mm is None else mm >= .25},
            "warnings": warnings}


def _select(factory: Any, opts: Options) -> Plan:
    versions = (opts.version,) if opts.version is not None else range(opts.min_version, opts.max_version + 1)
    for version in versions:
        segments = factory(version)
        bits = segments_bit_length(segments, version)
        level = opts.error_correction_level
        ok = bits <= data_codeword_count(version, level) * 8
        if ok:
            if opts.boost_error_correction:
                for stronger in "LMQH"["LMQH".index(level) + 1:]:
                    if bits <= data_codeword_count(version, stronger) * 8:
                        level = stronger
            break
    capacity = data_codeword_count(version, level) * 8
    diagnostics = _diagnostics(segments, version, level, bits, opts, planning=True, ok=ok)
    if getattr(factory, "gs1_validation", None) is not None:
        diagnostics["gs1_validation"] = factory.gs1_validation
    return Plan(ok, version if ok or opts.version is not None else None, version, level,
                opts.error_correction_level, level != opts.error_correction_level,
                bits, capacity, capacity - bits, segments, _freeze(diagnostics))


def _input_factory(value: str | bytes | bytearray | memoryview, opts: Options) -> Any:
    mode = opts.mode
    gs1_validation = None
    if isinstance(value, memoryview):
        try:
            value.nbytes
        except ValueError as exc:
            raise InvalidInputError("input memoryview has been released") from exc
    if opts.gs1:
        if not isinstance(value, str):
            raise InvalidGs1Error("high-level GS1 input must be a text element string")
        from .gs1 import parse_gs1_element_string
        parsed = parse_gs1_element_string(value)
        gs1_validation = {"enabled": True, "element_count": len(parsed.elements),
                          "ais": tuple(e.ai for e in parsed.elements), "has_separators": "\x1d" in value}
    if (opts.gs1 or opts.fnc1_second is not None) and isinstance(value, str) and "%" in value:
        if mode == "alphanumeric":
            raise InvalidModeError("literal % with high-level FNC1 requires byte mode; use manual escaped segments for low-level encoding")
        if mode == "auto":
            mode = "byte"
    cache: dict[int, tuple[Segment, ...]] = {}
    def factory(version: int) -> tuple[Segment, ...]:
        group = 0 if version <= 9 else 1 if version <= 26 else 2
        if group not in cache:
            optimize = opts.optimize_segments
            if isinstance(value, str) and len(value) > 7089:
                optimize = False
            # ECI belongs in control prefix; non-None disables automatic Kanji.
            data = create_segments(value, mode=mode, version=version, optimize=optimize, eci=opts.eci)
            # create_segments may already provide the option ECI; normalize once.
            if opts.eci is not None and data and data[0].mode == "eci":
                data = data[1:]
            cache[group] = _with_controls(data, opts)
        return cache[group]
    setattr(factory, "gs1_validation", gs1_validation)
    return factory


def estimate(value: str | bytes | bytearray | memoryview, options: Options | None = None, **kwargs: Any) -> Plan:
    opts = _options(options, kwargs)
    return _select(_input_factory(value, opts), opts)


def analyze_segments(segments: Sequence[Segment | Mapping[str, Any]], options: Options | None = None, **kwargs: Any) -> Plan:
    opts = _options(options, kwargs)
    data = _with_controls(normalize_segments(segments), opts)
    return _select(lambda version: data, opts)


def _build(plan: Plan, opts: Options) -> QRResult:
    if not plan.ok:
        raise DataTooLongError(f"Input requires {plan.data_bit_length} bits; version {plan.capacity_version}-{plan.error_correction_level} holds {plan.capacity_bits}")
    version = plan.capacity_version
    bits = tuple(bit for segment in plan.segments for bit in segment.bits(version))
    data = pad_data_bits(bits, version, plan.error_correction_level)
    interleaved = interleave_codewords(data, version, plan.error_correction_level)
    built = build_matrix(interleaved.codewords, version, plan.error_correction_level, opts.mask_pattern)
    diagnostics = _diagnostics(plan.segments, version, plan.error_correction_level, plan.data_bit_length,
                               opts, planning=False, ok=True)
    diagnostics["gs1_validation"] = plan.diagnostics["gs1_validation"]
    diagnostics.update({"mask_pattern": built.mask_pattern, "mask_penalty": built.penalty,
                        "mask_penalties": [{"mask_pattern": p.mask_pattern, "penalty": p.penalty} for p in built.mask_penalties],
                        "mask_selection_reason": (f"Mask pattern {built.mask_pattern} was requested explicitly."
                            if opts.mask_pattern is not None else f"Mask pattern {built.mask_pattern} had the lowest penalty ({built.penalty})."),
                        "data_codewords": len(data), "error_correction_codewords": len(interleaved.codewords) - len(data),
                        "total_codewords": len(interleaved.codewords)})
    return QRResult(built.matrix, version, built.mask_pattern, plan.error_correction_level,
                    data, interleaved.codewords, plan.segments, _freeze(diagnostics), opts)


def generate(value: str | bytes | bytearray | memoryview, options: Options | None = None, **kwargs: Any) -> QRResult:
    opts = _options(options, kwargs)
    return _build(_select(_input_factory(value, opts), opts), opts)


def generate_segments(segments: Sequence[Segment | Mapping[str, Any]], options: Options | None = None, **kwargs: Any) -> QRResult:
    opts = _options(options, kwargs)
    data = _with_controls(normalize_segments(segments), opts)
    return _build(_select(lambda version: data, opts), opts)
