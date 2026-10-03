"""Bounded, dependency-free GS1 element strings and Digital Link helpers.

This is the 50-AI SpecQR catalog, not a complete GS1 validator. URL handling is
an intentionally bounded adapter; Unicode host names use Python's IDNA2003.
No function in this module performs network access.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import ipaddress
import json
import re
from urllib.parse import parse_qsl, quote, unquote_to_bytes

from .errors import InvalidGs1Error

GS1_FNC1_SEPARATOR = "\x1d"
GS1_MAX_INPUT_CHARACTERS = 1_000_000
GS1_MAX_ELEMENTS = 16_384


@dataclass(frozen=True, slots=True)
class GS1Element:
    ai: str
    value: str


@dataclass(frozen=True, slots=True)
class GS1AiLength:
    type: str
    exact: int | None = None
    min: int | None = None
    max: int | None = None

    @property
    def is_variable(self) -> bool:
        return self.type == "variable"


@dataclass(frozen=True, slots=True)
class GS1AiInfo:
    ai: str
    label: str
    length: GS1AiLength
    value_kind: str
    check_digit_rule: str
    digital_link_role: str
    separator: str
    digital_link_path_for_primary: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class GS1ElementStringParseResult:
    elements: tuple[GS1Element, ...]
    has_separators: bool


@dataclass(frozen=True, slots=True)
class GS1ValidationIssue:
    code: str
    message: str
    reason: str | None = None
    ai: str | None = None
    value: str | None = None
    key: str | None = None
    offset: int | None = None
    element_index: int | None = None
    expected: str | bool | None = None
    count: int | None = None


@dataclass(frozen=True, slots=True)
class GS1ValidationResult:
    ok: bool
    elements: tuple[GS1Element, ...] | None = None
    has_separators: bool | None = None
    errors: tuple[GS1ValidationIssue, ...] = ()
    warnings: tuple[GS1ValidationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class GS1UnknownQuery:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class GS1DigitalLinkParseResult:
    elements: tuple[GS1Element, ...]
    primary: GS1Element
    path_elements: tuple[GS1Element, ...]
    query_elements: tuple[GS1Element, ...]
    unknown_query: tuple[GS1UnknownQuery, ...]


@dataclass(frozen=True, slots=True)
class GS1DigitalLinkValidationResult:
    ok: bool
    result: GS1DigitalLinkParseResult | None = None
    errors: tuple[GS1ValidationIssue, ...] = ()
    warnings: tuple[GS1ValidationIssue, ...] = ()


GS1ElementLike = GS1Element | Mapping[str, object]


def _fail(message: str) -> None:
    raise InvalidGs1Error(message)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        _fail(f"{label} must be a string")
    if len(value) > GS1_MAX_INPUT_CHARACTERS:
        _fail(f"{label} must contain at most {GS1_MAX_INPUT_CHARACTERS} characters")
    return value


def _bounded(values: Iterable[object], label: str = "GS1 elements") -> tuple:
    # Never materialize an unbounded iterable. Exceptions from a caller's iterator
    # deliberately propagate; only input-validation failures are translated.
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        _fail(f"{label} must be an iterable of elements")
    try:
        iterator = iter(values)
    except TypeError:
        _fail(f"{label} must be an iterable of elements")
    result = []
    text_work = 0
    for value in iterator:
        if len(result) >= GS1_MAX_ELEMENTS:
            _fail(f"{label} must contain at most {GS1_MAX_ELEMENTS} elements")
        # Bound total validation work before any per-element character scans.
        # Repeated references count repeatedly: they are validated repeatedly.
        # This also bounds string entries in the path_ais iterable.
        if isinstance(value, GS1Element):
            text_fields = (value.ai, value.value)
        elif isinstance(value, Mapping):
            text_fields = (value.get("ai"), value.get("value"))
        else:
            text_fields = (value,)
        text_work += sum(len(field) for field in text_fields if isinstance(field, str))
        if text_work > GS1_MAX_INPUT_CHARACTERS:
            _fail(f"{label} aggregate text exceeds the character work budget ({GS1_MAX_INPUT_CHARACTERS})")
        result.append(value)
    return tuple(result)


def _digits(value: str) -> bool:
    return bool(value) and all("0" <= c <= "9" for c in value)


def _is_ai(value: str) -> bool:
    return 2 <= len(value) <= 4 and _digits(value)


def _catalog() -> tuple[GS1AiInfo, ...]:
    result = []
    def add(ai, label, size, variable=False, kind="numeric", check="none", role="data-attribute"):
        length = GS1AiLength("variable", min=1, max=size) if variable else GS1AiLength("fixed", exact=size)
        result.append(GS1AiInfo(ai, label, length, kind, check, role,
                              "required-when-followed" if variable else "none",
                              ("01",) if role == "key-qualifier" else None))
    add("00", "Serial shipping container code", 18, check="sscc", role="primary-key")
    add("01", "Global trade item number", 14, check="gtin", role="primary-key")
    add("02", "Contained trade item GTIN", 14, check="gtin")
    add("10", "Batch or lot number", 20, True, "text", role="key-qualifier")
    for ai, label in (("11", "Production date"), ("12", "Due date"), ("13", "Packaging date"),
                      ("15", "Best before date"), ("16", "Sell by date"), ("17", "Expiration date")):
        add(ai, label, 6)
    add("20", "Internal product variant", 2)
    add("21", "Serial number", 20, True, "text", role="key-qualifier")
    add("22", "Consumer product variant", 20, True, "text", role="key-qualifier")
    add("30", "Variable count", 8, True)
    add("37", "Count of contained trade items", 8, True)
    for ai, label in (("240", "Additional product identification"), ("241", "Customer part number"),
                      ("400", "Customer purchase order number")):
        add(ai, label, 30, True, "text")
    for ai, label in (("410", "Ship to global location number"), ("411", "Bill to global location number"),
                      ("412", "Purchased from global location number"), ("413", "Ship for global location number"),
                      ("414", "Identification of a physical location"), ("415", "Global location number of the invoicing party")):
        add(ai, label, 13, role="primary-key" if ai == "414" else "data-attribute")
    add("420", "Ship to postal code", 20, True, "text")
    for ai, label in (("422", "Country of origin"), ("424", "Country of processing"),
                      ("425", "Country of disassembly"), ("426", "Country covering full process chain")):
        add(ai, label, 3)
    for start, label in ((3100, "Net weight in kilograms"), (3200, "Net weight in pounds")):
        for n in range(start, start + 6):
            add(str(n), label, 6)
    for n in range(91, 100):
        add(str(n), "Company internal information", 90, True, "text")
    return tuple(result)


_CATALOG = _catalog()
_AI_INFO = {entry.ai: entry for entry in _CATALOG}
_PRIMARY = ("00", "01", "414")


def get_supported_gs1_ais() -> tuple[GS1AiInfo, ...]:
    """Return immutable metadata for the 50 supported, concrete AIs."""
    return _CATALOG


def get_gs1_ai_info(ai: str) -> GS1AiInfo | None:
    return _AI_INFO.get(ai) if isinstance(ai, str) else None


def _numeric(value: object, label: str) -> str:
    if not isinstance(value, str):
        _fail(f"{label} must be a string to preserve leading zeroes")
    _text(value, label)
    if not _digits(value):
        _fail(f"{label} must contain digits only")
    return value


def calculate_gs1_check_digit(digits: str) -> str:
    body = _numeric(digits, "GS1 check digit input")
    total = 0
    weight = 3
    for char in reversed(body):
        total = (total + (ord(char) - 48) * weight) % 10
        weight = 4 - weight
    return str((-total) % 10)


def validate_gs1_check_digit(digits_with_check_digit: str) -> bool:
    value = _numeric(digits_with_check_digit, "GS1 check digit value")
    if len(value) < 2:
        _fail("GS1 check digit value must include body digits and one check digit")
    return calculate_gs1_check_digit(value[:-1]) == value[-1]


def calculate_gtin_check_digit(gtin_without_check_digit: str) -> str:
    body = _numeric(gtin_without_check_digit, "GTIN body")
    if len(body) not in (7, 11, 12, 13):
        _fail("GTIN body must be 7, 11, 12, or 13 digits")
    return calculate_gs1_check_digit(body)


def append_gtin_check_digit(gtin_without_check_digit: str) -> str:
    return gtin_without_check_digit + calculate_gtin_check_digit(gtin_without_check_digit)


def validate_gtin_check_digit(gtin: str) -> bool:
    value = _numeric(gtin, "GTIN")
    if len(value) not in (8, 12, 13, 14):
        _fail("GTIN must be 8, 12, 13, or 14 digits")
    return validate_gs1_check_digit(value)


def calculate_sscc_check_digit(sscc_without_check_digit: str) -> str:
    body = _numeric(sscc_without_check_digit, "SSCC body")
    if len(body) != 17:
        _fail("SSCC body must be exactly 17 digits")
    return calculate_gs1_check_digit(body)


def append_sscc_check_digit(sscc_without_check_digit: str) -> str:
    return sscc_without_check_digit + calculate_sscc_check_digit(sscc_without_check_digit)


def validate_sscc_check_digit(sscc: str) -> bool:
    value = _numeric(sscc, "SSCC")
    if len(value) != 18:
        _fail("SSCC must be exactly 18 digits")
    return validate_gs1_check_digit(value)


def _element(element: GS1ElementLike, index: int) -> GS1Element:
    if isinstance(element, GS1Element):
        ai, value = element.ai, element.value
    elif isinstance(element, Mapping):
        ai, value = element.get("ai"), element.get("value")
    else:
        _fail(f"GS1 element {index} must be an object")
    if not isinstance(ai, str):
        _fail(f"GS1 element {index} AI must be a string")
    if not isinstance(value, str):
        _fail(f"GS1 element {index} value must be a string to preserve leading zeroes")
    _text(ai, "GS1 AI")
    _text(value, "GS1 value")
    if not _is_ai(ai):
        _fail(f"GS1 element {index} has invalid AI {json.dumps(ai[:100], ensure_ascii=False)}; expected 2 to 4 digits")
    info = get_gs1_ai_info(ai)
    if info is None:
        _fail(f"Unsupported GS1 AI {ai}. Add explicit support before using it.")
    prefix = f"GS1 AI {ai} value "
    if not value:
        _fail(prefix + "must not be empty")
    if GS1_FNC1_SEPARATOR in value:
        _fail(prefix + "must not contain the FNC1 separator")
    if "(" in value or ")" in value:
        _fail(prefix + "must be raw data without human-readable parentheses")
    if any(not " " <= c <= "~" for c in value):
        _fail(prefix + "must use printable ASCII characters")
    if info.value_kind == "numeric" and not _digits(value):
        _fail(prefix + "must contain digits only")
    if info.length.is_variable:
        if len(value) > info.length.max:
            _fail(prefix + f"must be at most {info.length.max} characters")
    elif len(value) != info.length.exact:
        _fail(prefix + f"must be exactly {info.length.exact} characters")
    if info.check_digit_rule == "gtin" and not validate_gtin_check_digit(value):
        _fail(prefix + "has an invalid GTIN check digit")
    if info.check_digit_rule == "sscc" and not validate_sscc_check_digit(value):
        _fail(prefix + "has an invalid SSCC check digit")
    return GS1Element(ai, value)


def parse_gs1_human_readable(input: str) -> tuple[GS1Element, ...]:
    _text(input, "GS1 human-readable input")
    if not input:
        _fail("GS1 human-readable input must not be empty")
    result = []
    position = 0
    while position < len(input):
        if len(result) >= GS1_MAX_ELEMENTS:
            _fail("GS1 elements exceed element limit")
        if input[position] != "(":
            _fail(f"GS1 human-readable input must contain an AI in parentheses at offset {position}")
        close = input.find(")", position + 1)
        if close < 0:
            _fail(f"GS1 AI starting at offset {position} is missing a closing parenthesis")
        end = input.find("(", close + 1)
        if end < 0:
            end = len(input)
        result.append(_element(GS1Element(input[position + 1:close], input[close + 1:end]), len(result)))
        position = end
    return tuple(result)


def create_gs1_element_string(elements: Iterable[GS1ElementLike]) -> str:
    values = _bounded(elements)
    if not values:
        _fail("GS1 elements must not be empty")
    parts = []
    size = 0
    for index, raw in enumerate(values):
        e = _element(raw, index)
        part = e.ai + e.value
        if _AI_INFO[e.ai].length.is_variable and index < len(values) - 1:
            part += GS1_FNC1_SEPARATOR
        size += len(part)
        if size > GS1_MAX_INPUT_CHARACTERS:
            _fail("GS1 element string output exceeds character limit")
        parts.append(part)
    return "".join(parts)


def _read_ai(input: str, offset: int) -> GS1AiInfo | None:
    for length in (4, 3, 2):
        info = _AI_INFO.get(input[offset:offset + length])
        if info is not None and len(info.ai) == length:
            return info
    return None


def parse_gs1_element_string(input: str) -> GS1ElementStringParseResult:
    _text(input, "GS1 element string input")
    if not input:
        _fail("GS1 element string input must not be empty")
    if "(" in input or ")" in input:
        _fail("GS1 element string input must be raw data without human-readable parentheses; use parse_gs1_human_readable() and create_gs1_element_string() first")
    result = []
    position = 0
    while position < len(input):
        if len(result) >= GS1_MAX_ELEMENTS:
            _fail("GS1 elements exceed element limit")
        if input[position] == GS1_FNC1_SEPARATOR:
            _fail(f"GS1 element string has an unexpected FNC1 separator at offset {position}")
        info = _read_ai(input, position)
        if info is None:
            _fail(f"Unsupported GS1 AI at offset {position}")
        start = position + len(info.ai)
        end = input.find(GS1_FNC1_SEPARATOR, start) if info.length.is_variable else min(len(input), start + info.length.exact)
        if end < 0:
            end = len(input)
        if info.length.is_variable and end == len(input):
            # A fixed element is at most 2+18 characters. Scan only this suffix,
            # preserving the upstream heuristic without quadratic input work.
            for offset in range(max(start + 1, end - 22), end):
                suffix = _read_ai(input, offset)
                if suffix and not suffix.length.is_variable and offset + len(suffix.ai) + suffix.length.exact == end:
                    _fail(f"GS1 variable-length element at offset {start} is missing an FNC1 separator before offset {offset}")
        result.append(_element(GS1Element(info.ai, input[start:end]), len(result)))
        position = end
        if position < len(input) and input[position] == GS1_FNC1_SEPARATOR and info.length.is_variable:
            position += 1
            if position == len(input):
                _fail("GS1 element string must not end with an FNC1 separator")
    return GS1ElementStringParseResult(tuple(result), GS1_FNC1_SEPARATOR in input)


def _issue(error: InvalidGs1Error, *, element=None, element_index=None, input=None, digital_link=False) -> GS1ValidationIssue:
    message = str(error)
    code, reason, expected = "GS1_INVALID_INPUT", "invalid-input", None
    rules = [
        (r"Unsupported GS1 AI", "GS1_UNSUPPORTED_AI", "unsupported-ai", "supported GS1 AI"),
        (r"exactly \d+ characters|at most \d+ characters", "GS1_INVALID_LENGTH", "invalid-length", None),
        (r"digits only|printable ASCII", "GS1_INVALID_CHARSET", "invalid-charset", "digits only" if "digits only" in message else "printable ASCII"),
        (r"missing an FNC1 separator", "GS1_MISSING_SEPARATOR", "missing-separator", "FNC1 separator before the next GS1 element"),
        (r"unexpected FNC1 separator|must not end with an FNC1 separator|must not contain the FNC1 separator", "GS1_UNEXPECTED_SEPARATOR", "unexpected-separator", "separator only after a non-final variable-length GS1 element"),
        (r"invalid GTIN check digit|invalid SSCC check digit", "GS1_INVALID_CHECK_DIGIT", "invalid-check-digit", "valid SSCC check digit" if "SSCC" in message else "valid GTIN check digit"),
        (r"cannot be placed in the Digital Link path|path values must not be dot segments", "GS1_INVALID_DIGITAL_LINK_PLACEMENT", "invalid-digital-link-placement", None),
        (r"duplicate AI [0-9]{2,4}", "GS1_DUPLICATE_AI", "duplicate-ai", "unique GS1 AI within the Digital Link URI"),
    ]
    if digital_link:
        rules = [
            (r"absolute http or https URL|must use http or https", "GS1_DIGITAL_LINK_INVALID_URI", "invalid-uri", "absolute http or https URL"),
            (r"must not include a fragment", "GS1_DIGITAL_LINK_FRAGMENT_NOT_ALLOWED", "fragment-not-allowed", "URI without fragment"),
            (r"valid percent-encoding", "GS1_INVALID_PERCENT_ENCODING", "invalid-percent-encoding", "percent escapes must use two hexadecimal digits"),
            (r"query parameter .* is not a GS1 AI", "GS1_DIGITAL_LINK_UNKNOWN_QUERY", "unknown-query", 'GS1 AI query parameter or unknown_query="preserve"'),
            (r"primary_ai must be one|unknown_query must be", "GS1_INVALID_INPUT", "invalid-options", None),
            (r"path must include primary AI|path must contain AI/value pairs|path segment [0-9]+ must be a GS1 AI|path must not contain empty segments", "GS1_INVALID_INPUT", "malformed-path", "Digital Link path containing primary AI and AI/value pairs"),
        ] + rules
    for pattern, code_value, reason_value, expected_value in rules:
        match = re.search(pattern, message)
        if match:
            code, reason, expected = code_value, reason_value, expected_value
            if code == "GS1_INVALID_LENGTH":
                expected = match.group()
            break
    def find(pattern):
        match = re.search(pattern, message)
        return match.group(1) if match else None
    ai = find(r"(?:GS1 AI |duplicate AI )([0-9]{2,4})")
    offset_text = find(r"offset ([0-9]+)")
    offset = int(offset_text) if offset_text is not None else None
    if ai is None and isinstance(input, str) and offset is not None:
        match = re.match(r"[0-9]{2,4}", input[offset:])
        if match:
            ai = match.group()
        else:
            for size in (2, 3, 4):
                if offset >= size and input[offset - size:offset] in _AI_INFO:
                    ai = input[offset - size:offset]
                    break
    explicit_index = find(r"GS1 element ([0-9]+)")
    if explicit_index is not None:
        element_index = int(explicit_index)
    value = element.value if isinstance(element, GS1Element) else element.get("value") if isinstance(element, Mapping) else None
    if not isinstance(value, str) or len(value) > 90:
        value = None
    key = None
    if code == "GS1_DIGITAL_LINK_UNKNOWN_QUERY":
        match = re.search(r'query parameter ("(?:[^"\\]|\\.)*")', message)
        if match:
            key = json.loads(match.group(1))
    return GS1ValidationIssue(code, message, reason, ai, value, key, offset, element_index, expected)


def _validation_options(context: str, collect_all_errors: bool, allow_unsupported_ai: bool) -> GS1ValidationIssue | None:
    if context not in ("element-string", "digital-link"):
        return GS1ValidationIssue("GS1_INVALID_INPUT", 'GS1 validation context must be "element-string" or "digital-link"', "invalid-options", expected="element-string or digital-link")
    if allow_unsupported_ai is not False:
        return GS1ValidationIssue("GS1_INVALID_INPUT", "GS1 validation allow_unsupported_ai must be false", "invalid-options", expected=False)
    if not isinstance(collect_all_errors, bool):
        return GS1ValidationIssue("GS1_INVALID_INPUT", "GS1 validation collect_all_errors must be a bool", "invalid-options")
    return None


def validate_gs1_elements(elements: Iterable[GS1ElementLike], *, context: str = "element-string", collect_all_errors: bool = True, allow_unsupported_ai: bool = False) -> GS1ValidationResult:
    option_error = _validation_options(context, collect_all_errors, allow_unsupported_ai)
    if option_error:
        return GS1ValidationResult(False, errors=(option_error,))
    try:
        values = _bounded(elements)
        if not values:
            _fail("GS1 elements must not be empty")
    except InvalidGs1Error as error:
        return GS1ValidationResult(False, errors=(_issue(error),))
    normalized, errors = [], []
    for index, element in enumerate(values):
        try:
            normalized.append(_element(element, index))
        except InvalidGs1Error as error:
            errors.append(_issue(error, element=element, element_index=index))
            if not collect_all_errors:
                break
    if errors:
        return GS1ValidationResult(False, errors=tuple(errors))
    if context == "digital-link" and not any(e.ai in _PRIMARY for e in normalized):
        return GS1ValidationResult(False, errors=(GS1ValidationIssue("GS1_INVALID_DIGITAL_LINK_PLACEMENT", "GS1 Digital Link elements must include a primary AI 00, 01, or 414", "invalid-digital-link-placement", expected="primary AI 00, 01, or 414"),))
    return GS1ValidationResult(True, tuple(normalized))


def validate_gs1_element_string(input: str, *, context: str = "element-string", collect_all_errors: bool = True, allow_unsupported_ai: bool = False) -> GS1ValidationResult:
    option_error = _validation_options(context, collect_all_errors, allow_unsupported_ai)
    if option_error:
        return GS1ValidationResult(False, errors=(option_error,))
    try:
        parsed = parse_gs1_element_string(input)
        return GS1ValidationResult(True, parsed.elements, parsed.has_separators)
    except InvalidGs1Error as error:
        return GS1ValidationResult(False, errors=(_issue(error, input=input),))


_INVALID_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")


def _decode_path(value: str, label: str) -> str:
    try:
        if _INVALID_PERCENT.search(value):
            raise ValueError("bad percent escape")
        return unquote_to_bytes(value).decode("utf-8", "strict")
    except (ValueError, UnicodeError):
        _fail(f"GS1 Digital Link path {label} must be valid percent-encoding")


def _usv(value: str) -> str:
    """Match URL's USVString conversion for explicit UTF-16 surrogate input."""
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return value


def _encode_url_part(value: str, *, query: bool = False) -> str:
    forbidden = '\"#\'<> ' if query else '\"#<>?`{} '
    # quote would normalize '~' and other sets. Keep the WHATWG subset explicit.
    return "".join(f"%{byte:02X}" if byte <= 32 or byte > 126 or chr(byte) in forbidden else chr(byte)
                   for byte in _usv(value).encode("utf-8"))


def _form_encode(value: str) -> str:
    # URLSearchParams encodes ~ and leaves *; urllib.quote_plus differs on ~.
    return "".join(chr(byte) if 65 <= byte <= 90 or 97 <= byte <= 122 or 48 <= byte <= 57 or chr(byte) in "*-._"
                   else "+" if byte == 32 else f"%{byte:02X}" for byte in _usv(value).encode("utf-8"))


def _path_encode(value: str) -> str:
    return quote(value, safe="~!*'()-._", encoding="utf-8", errors="strict")


def _invalid_uri() -> None:
    _fail("GS1 Digital Link URI must be an absolute http or https URL")


def _host(raw: str) -> str:
    if not raw:
        _invalid_uri()
    if raw.startswith("["):
        if not raw.endswith("]") or "%" in raw:
            _invalid_uri()
        try:
            address = int(ipaddress.IPv6Address(raw[1:-1]))
            groups = [(address >> shift) & 0xffff for shift in range(112, -1, -16)]
            best_start, best_length = 0, 0
            index = 0
            while index < 8:
                if groups[index]:
                    index += 1
                    continue
                end = index
                while end < 8 and not groups[end]:
                    end += 1
                if end - index > best_length:
                    best_start, best_length = index, end - index
                index = end
            rendered = [format(group, "x") for group in groups]
            if best_length > 1:
                text = ":".join(rendered[:best_start]) + "::" + ":".join(rendered[best_start + best_length:])
            else:
                text = ":".join(rendered)
            return "[" + text + "]"
        except ValueError:
            _invalid_uri()
    try:
        if _INVALID_PERCENT.search(raw):
            _invalid_uri()
        value = unquote_to_bytes(raw).decode("utf-8", "strict")
        if not value or any(ord(c) <= 32 or ord(c) == 127 or c in "#/:<>?@[\\]^|%" for c in value):
            _invalid_uri()
        # ASCII DNS syntax is deliberately not tightened beyond URL rules.
        # IDNA applies only to non-ASCII labels, with the stdlib's 2003 mapping.
        value = value.translate({0x3002: ".", 0xff0e: ".", 0xff61: "."})
        value = ".".join(label.encode("idna").decode("ascii") if not label.isascii() else label
                         for label in value.split(".")).lower()
    except UnicodeError:
        _invalid_uri()
    if not value or any(ord(c) <= 32 or ord(c) == 127 or c in "#/:<>?@[\\]^|%" for c in value):
        _invalid_uri()
    pieces = value.split(".")
    if pieces[-1] == "":
        pieces.pop()
    last = pieces[-1] if pieces else ""
    is_hex = last.startswith("0x") and all(c in "0123456789abcdef" for c in last[2:])
    if not (_digits(last) or is_hex):
        return value
    if len(pieces) > 4:
        _invalid_uri()
    numbers = []
    for piece in pieces:
        if not piece:
            _invalid_uri()
        radix, body = (16, piece[2:]) if piece.startswith("0x") else (8, piece[1:]) if len(piece) > 1 and piece[0] == "0" else (10, piece)
        number = 0
        for char in body:
            digit = "0123456789abcdef".find(char)
            if digit < 0 or digit >= radix:
                _invalid_uri()
            number = number * radix + digit
            if number > 0xffffffff:
                _invalid_uri()
        numbers.append(number)
    if any(n > 255 for n in numbers[:-1]) or numbers[-1] >= 1 << (8 * (5 - len(numbers))):
        _invalid_uri()
    address = numbers[-1] + sum(n << (8 * (3 - i)) for i, n in enumerate(numbers[:-1]))
    return ".".join(str((address >> shift) & 255) for shift in (24, 16, 8, 0))


def _credentials(value: str) -> str:
    result = []
    colon_seen = False
    for byte in _usv(value).encode("utf-8"):
        char = chr(byte)
        if char == ":" and not colon_seen:
            result.append(":")
            colon_seen = True
        elif byte <= 32 or byte > 126 or char in '\"#<>?`{}/:;=@[\\]^|':
            result.append(f"%{byte:02X}")
        else:
            result.append(char)
    return "".join(result).removesuffix(":")


def _normalize_path(path: str, *, base_url: bool) -> str:
    if path.count("/") > GS1_MAX_ELEMENTS:
        _fail("GS1 Digital Link path component count exceeds limit")
    parts = path.split("/")
    result = []
    primary_seen = False
    for index, part in enumerate(parts):
        if not base_url and part in _PRIMARY:
            primary_seen = True
        dot = re.sub("%2e", ".", part, flags=re.IGNORECASE)
        if dot in (".", ".."):
            if primary_seen:
                _fail("GS1 Digital Link path values must not be dot segments; place these values in the query")
            if dot == ".." and len(result) > 1:
                result.pop()
            if index == len(parts) - 1:
                result.append("")
        else:
            result.append(part)
    value = "/".join(result)
    return _encode_url_part(value if value.startswith("/") else "/" + value)


@dataclass(slots=True)
class _Url:
    scheme: str
    authority: str
    path: str
    query: str | None
    fragment: str | None

    def serialize(self) -> str:
        result = self.scheme + "://" + self.authority + self.path
        if self.query is not None:
            result += "?" + self.query
        if self.fragment is not None:
            result += "#" + self.fragment
        return _text(result, "GS1 Digital Link output")


def _url(input: str, *, base_url: bool = False) -> _Url:
    _text(input, "GS1 Digital Link URI")
    clean = input.strip("".join(chr(i) for i in range(33))).replace("\t", "").replace("\r", "").replace("\n", "")
    match = re.match(r"([a-zA-Z][a-zA-Z0-9+.-]*):", clean)
    if not match:
        _invalid_uri()
    scheme = match.group(1).lower()
    rest = clean[match.end():]
    fragment = None
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
    query = None
    if "?" in rest:
        rest, query = rest.split("?", 1)
        query = _encode_url_part(query, query=True)
    if scheme not in ("http", "https", "ftp", "ws", "wss"):
        # Syntactically valid non-HTTP schemes are rejected by the GS1 caller.
        return _Url(scheme, "", rest, query, fragment)
    rest = rest.replace("\\", "/").lstrip("/")
    authority, slash, path = rest.partition("/")
    path = "/" + path if slash else "/"
    if not authority:
        _invalid_uri()
    credentials = ""
    if "@" in authority:
        userinfo, authority = authority.rsplit("@", 1)
        credentials = _credentials(userinfo)
        if credentials:
            credentials += "@"
    raw_port = ""
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0:
            _invalid_uri()
        host, suffix = authority[:close + 1], authority[close + 1:]
        if suffix:
            if not suffix.startswith(":"):
                _invalid_uri()
            raw_port = suffix[1:]
    else:
        host, colon, raw_port = authority.partition(":")
    port = ""
    if raw_port:
        if not _digits(raw_port):
            _invalid_uri()
        # Limit parsing by value, not length; leading zeroes remain accepted.
        number = 0
        for char in raw_port:
            number = number * 10 + ord(char) - 48
            if number > 65535:
                _invalid_uri()
        if (scheme, number) not in (("http", 80), ("https", 443), ("ws", 80), ("wss", 443), ("ftp", 21)):
            port = ":" + str(number)
    return _Url(scheme, credentials + _host(host) + port, _normalize_path(path, base_url=base_url), query, fragment)


def _check_uri(url: _Url, *, base_url: bool = False) -> None:
    if url.scheme not in ("http", "https"):
        _fail("GS1 Digital Link URI must use http or https")
    if base_url:
        if url.query or url.fragment:
            _fail("GS1 Digital Link base_url must not include query or fragment components")
    elif url.fragment:
        _fail("GS1 Digital Link URI must not include a fragment")


def _primary(ai: str) -> str:
    if ai not in _PRIMARY:
        _fail("GS1 Digital Link primary_ai must be one of 00, 01, or 414")
    return ai


def _policy(policy: str) -> None:
    if policy not in ("preserve", "reject"):
        _fail('GS1 Digital Link unknown_query must be "preserve" or "reject"')


def _eligible(ai: str, primary: str) -> bool:
    return primary == "01" and ai in ("10", "21", "22")


def _placement(ai: str, primary: str) -> None:
    if ai not in _AI_INFO:
        _fail(f"Unsupported GS1 AI {ai}. Add explicit support before using it.")
    if not _eligible(ai, primary):
        _fail(f"GS1 AI {ai} cannot be placed in the Digital Link path after primary AI {primary}")


def _unique(ai: str, seen: set[str]) -> None:
    if ai in seen:
        _fail(f"GS1 Digital Link input must not contain duplicate AI {ai}")
    seen.add(ai)


def create_gs1_digital_link(elements: Iterable[GS1ElementLike] | GS1ElementStringParseResult, *, base_url: str, primary_ai: str = "01", path_ais: Iterable[str] | None = None) -> str:
    if isinstance(elements, GS1ElementStringParseResult):
        elements = elements.elements
    values = _bounded(elements)
    if base_url is None or base_url == "":
        _fail("GS1 Digital Link base_url is required")
    url = _url(base_url, base_url=True)
    _check_uri(url, base_url=True)
    _primary(primary_ai)
    paths = None
    if path_ais is not None:
        paths = set()
        for ai in _bounded(path_ais, "GS1 Digital Link path_ais"):
            if not isinstance(ai, str) or not _is_ai(ai):
                _fail("GS1 Digital Link path_ais entries must be 2 to 4 digit AI strings")
            if ai != primary_ai:
                _placement(ai, primary_ai)
                paths.add(ai)
    if not values:
        _fail("GS1 Digital Link input elements must not be empty")
    normalized, seen = [], set()
    for index, value in enumerate(values):
        element = _element(value, index)
        _unique(element.ai, seen)
        normalized.append(element)
    primary = next((e for e in normalized if e.ai == primary_ai), None)
    if primary is None:
        _fail(f"GS1 Digital Link input must include primary AI {primary_ai}")
    path_elements, query_elements = [primary], []
    for e in normalized:
        if e.ai == primary_ai:
            continue
        if e.ai in paths if paths is not None else _eligible(e.ai, primary_ai):
            path_elements.append(e)
        else:
            query_elements.append(e)
    for e in path_elements:
        if e.value in (".", ".."):
            _fail("GS1 Digital Link path values must not be dot segments; use path_ais=() to place these values in the query")
    url.path = url.path.rstrip("/") + "/" + "/".join(_path_encode(value) for e in path_elements for value in (e.ai, e.value))
    query_elements.sort(key=lambda e: (e.ai, e.value))
    url.query = "&".join(_form_encode(e.ai) + "=" + _form_encode(e.value) for e in query_elements) or None
    return url.serialize()


def _segments(path: str) -> list[str]:
    value = path.strip("/")
    if not value:
        _fail("GS1 Digital Link path must include primary AI 00, 01, or 414")
    parts = value.split("/")
    if "" in parts:
        _fail("GS1 Digital Link path must not contain empty segments")
    return parts


def _first_ai(parts: list[str], primary_ai: str | None) -> int:
    for index, part in enumerate(parts):
        if part == primary_ai if primary_ai is not None else part in _PRIMARY:
            return index
    _fail("GS1 Digital Link path must include primary AI 00, 01, or 414")


def _parse_link(url: _Url, primary_ai: str | None, unknown_query: str) -> GS1DigitalLinkParseResult:
    _check_uri(url)
    if primary_ai is not None:
        _primary(primary_ai)
    _policy(unknown_query)
    parts = _segments(url.path)
    start = _first_ai(parts, primary_ai)
    if (len(parts) - start) % 2:
        _fail("GS1 Digital Link path must contain AI/value pairs")
    path, query, unknown, seen = [], [], [], set()
    for index in range(start, len(parts), 2):
        ai = parts[index]
        if not _is_ai(ai):
            _fail(f"GS1 Digital Link path segment {index + 1} must be a GS1 AI")
        value = _decode_path(parts[index + 1], f"value for AI {ai}")
        element = _element(GS1Element(ai, value), len(path))
        if path:
            _placement(ai, path[0].ai)
        _unique(ai, seen)
        path.append(element)
    raw_query = url.query or ""
    if raw_query.count("&") + bool(raw_query) > GS1_MAX_ELEMENTS:
        _fail("GS1 Digital Link query component count exceeds limit")
    # WHATWG URLSearchParams forgiving form decoding. keep_blank_values is
    # essential, as are list order and repeated non-GS1 keys.
    for key, value in parse_qsl(raw_query, keep_blank_values=True, encoding="utf-8", errors="replace", max_num_fields=GS1_MAX_ELEMENTS):
        if _is_ai(key):
            element = _element(GS1Element(key, value), len(path) + len(query))
            _unique(key, seen)
            query.append(element)
        elif unknown_query == "preserve":
            unknown.append(GS1UnknownQuery(key, value))
        else:
            _fail(f"GS1 Digital Link query parameter {json.dumps(key, ensure_ascii=False)} is not a GS1 AI")
    return GS1DigitalLinkParseResult(tuple(path + query), path[0], tuple(path), tuple(query), tuple(unknown))


def parse_gs1_digital_link(uri: str, *, primary_ai: str | None = None, unknown_query: str = "preserve") -> GS1DigitalLinkParseResult:
    return _parse_link(_url(uri), primary_ai, unknown_query)


def validate_gs1_digital_link(uri: str, *, primary_ai: str | None = None, unknown_query: str = "preserve", normalize: bool = False) -> GS1DigitalLinkValidationResult:
    if normalize is not False:
        return GS1DigitalLinkValidationResult(False, errors=(GS1ValidationIssue("GS1_INVALID_INPUT", "GS1 Digital Link validation normalize is not implemented yet", "unsupported-option", expected=False),))
    try:
        url = _url(uri)
        if _INVALID_PERCENT.search(url.path) or _INVALID_PERCENT.search(url.query or ""):
            _fail("GS1 Digital Link URI must use valid percent-encoding")
        parsed = _parse_link(url, primary_ai, unknown_query)
        warnings = []
        if url.scheme == "http":
            warnings.append(GS1ValidationIssue("GS1_DIGITAL_LINK_HTTP", "GS1 Digital Link URI uses http. Use https when transport security is required.", "http-uri"))
        if parsed.unknown_query:
            warnings.append(GS1ValidationIssue("GS1_DIGITAL_LINK_UNKNOWN_QUERY_PRESERVED", "GS1 Digital Link URI contains non-GS1 query parameters preserved in unknown_query.", "unknown-query-preserved", count=len(parsed.unknown_query)))
        return GS1DigitalLinkValidationResult(True, parsed, warnings=tuple(warnings))
    except InvalidGs1Error as error:
        return GS1DigitalLinkValidationResult(False, errors=(_issue(error, digital_link=True),))


def normalize_gs1_digital_link(uri: str, *, primary_ai: str | None = None, unknown_query: str = "preserve", mode: str = "specqr-deterministic") -> str:
    if mode != "specqr-deterministic":
        _fail('GS1 Digital Link normalization mode must be "specqr-deterministic"')
    url = _url(uri)
    _check_uri(url)
    if _INVALID_PERCENT.search(url.path) or _INVALID_PERCENT.search(url.query or ""):
        _fail("GS1 Digital Link URI must use valid percent-encoding")
    parsed = _parse_link(url, primary_ai, unknown_query)
    parts = _segments(url.path)
    start = _first_ai(parts, primary_ai)
    stem = _Url(url.scheme, url.authority, "/" + "/".join(parts[:start]), None, None).serialize()
    # Dot-valued query qualifiers must stay in query; moving them to a URL path
    # would silently change data in downstream browser URL normalization.
    path_ais = tuple(e.ai for e in parsed.elements if _eligible(e.ai, parsed.primary.ai) and e.value not in (".", ".."))
    normalized = create_gs1_digital_link(parsed.elements, base_url=stem, primary_ai=parsed.primary.ai, path_ais=path_ais)
    if parsed.unknown_query:
        separator = "&" if "?" in normalized else "?"
        normalized += separator + "&".join(_form_encode(p.key) + "=" + _form_encode(p.value) for p in parsed.unknown_query)
    return _text(normalized, "GS1 Digital Link output")


__all__ = [name for name in globals() if name.startswith("GS1") or name.startswith(("get_supported_gs1_", "get_gs1_", "calculate_gs1_", "calculate_gtin_", "calculate_sscc_", "append_gtin_", "append_sscc_", "validate_gs1_", "validate_gtin_", "validate_sscc_", "parse_gs1_", "create_gs1_", "normalize_gs1_"))]
