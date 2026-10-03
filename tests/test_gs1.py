"""Pinned SpecQR GS1 differential fixtures and Python-specific safety tests."""
from dataclasses import fields, is_dataclass, FrozenInstanceError
import itertools
import json
from pathlib import Path
import random
import unittest

from specqr import gs1 as g
from specqr.errors import InvalidGs1Error

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gs1-upstream.json"
UPSTREAM_COMMIT = "15ad15e5c770ea0e39072f8f88b2733018f02ffd"


def _camel(name):
    first, *rest = name.split("_")
    return first + "".join(part.title() for part in rest)


def _serialize(value):
    if is_dataclass(value):
        result = {_camel(f.name): _serialize(getattr(value, f.name)) for f in fields(value) if getattr(value, f.name) is not None}
        if isinstance(value, (g.GS1ValidationResult, g.GS1DigitalLinkValidationResult)):
            if value.ok:
                result.pop("errors", None)
            else:
                result.pop("elements", None)
                result.pop("hasSeparators", None)
                result.pop("result", None)
        return result
    if isinstance(value, (tuple, list)):
        return [_serialize(item) for item in value]
    return value


def _contract(value):
    """Compare public payload/metadata and diagnostic categories, not wording.

    The fixture retains full upstream diagnostics for inspection. Python uses
    code-point offsets and idiomatic names, so English text is not an API gate.
    """
    if isinstance(value, dict):
        if "code" in value and ("message" in value):
            # Category and reason determine cross-language behavior. Counts
            # remain significant on warnings.
            return {k: _contract(v) for k, v in value.items() if k in ("code", "reason", "count")}
        return {k: _contract(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_contract(v) for v in value]
    return value


def _evaluate(fixture):
    op = fixture["op"]
    input = fixture.get("input", "")
    elements = fixture.get("elements", [])
    mapping = {"baseUrl": "base_url", "primaryAi": "primary_ai", "pathAis": "path_ais",
               "unknownQuery": "unknown_query", "collectAllErrors": "collect_all_errors",
               "allowUnsupportedAi": "allow_unsupported_ai"}
    options = {mapping.get(key, key): value for key, value in fixture.get("options", {}).items()}
    funcs = {
        "dictionary": lambda: g.get_supported_gs1_ais(), "info": lambda: g.get_gs1_ai_info(input),
        "checkDigit": lambda: g.calculate_gs1_check_digit(input),
        "validateCheckDigit": lambda: g.validate_gs1_check_digit(input),
        "gtinDigit": lambda: g.calculate_gtin_check_digit(input),
        "gtinAppend": lambda: g.append_gtin_check_digit(input),
        "gtinValidate": lambda: g.validate_gtin_check_digit(input),
        "ssccDigit": lambda: g.calculate_sscc_check_digit(input),
        "ssccAppend": lambda: g.append_sscc_check_digit(input),
        "ssccValidate": lambda: g.validate_sscc_check_digit(input),
        "human": lambda: g.parse_gs1_human_readable(input),
        "raw": lambda: g.parse_gs1_element_string(input),
        "create": lambda: g.create_gs1_element_string(elements),
        "validateElements": lambda: g.validate_gs1_elements(elements, **options),
        "validateRaw": lambda: g.validate_gs1_element_string(input, **options),
        "linkCreate": lambda: g.create_gs1_digital_link(elements, base_url=options.pop("base_url", ""), **options),
        "linkParse": lambda: g.parse_gs1_digital_link(input, **options),
        "linkValidate": lambda: g.validate_gs1_digital_link(input, **options),
        "linkNormalize": lambda: g.normalize_gs1_digital_link(input, **options),
    }
    try:
        return _serialize(funcs[op]())
    except InvalidGs1Error as error:
        return {"throws": {"code": error.code, "message": str(error)}}


class GS1FixtureTests(unittest.TestCase):
    def test_pinned_upstream_fixtures(self):
        data = json.loads(FIXTURE_PATH.read_text())
        self.assertEqual(data["upstreamCommit"], UPSTREAM_COMMIT)
        self.assertEqual(len(data["cases"]), 1411)
        deltas = json.loads((FIXTURE_PATH.parent / "gs1-deltas.json").read_text())
        overrides = {entry["case_id"]: entry["python_expected"] for entry in deltas["differences"]}
        self.assertEqual(set(overrides), {1155, 1157, 1374, 1376, 1377, 1378})
        self.assertEqual(deltas["unchanged_outcome_count"], 1405)
        self.assertEqual(deltas["accepted_to_rejected_count"], 5)
        self.assertEqual(deltas["diagnostic_only_count"], 1)
        for case_id, fixture in enumerate(data["cases"]):
            with self.subTest(case_id=case_id, operation=fixture["op"]):
                actual = _evaluate(fixture)
                expected = overrides.get(case_id, fixture["expected"])
                self.assertEqual(_contract(actual), _contract(expected))

    def test_ascii_url_oracle_cases(self):
        data = json.loads((FIXTURE_PATH.parent / "gs1-url-ascii-upstream.json").read_text())
        self.assertEqual(data["upstreamCommit"], UPSTREAM_COMMIT)
        self.assertEqual(len(data["cases"]), 512)
        for index, case in enumerate(data["cases"]):
            with self.subTest(case_id=index):
                if case["expected"] is None:
                    with self.assertRaises(InvalidGs1Error):
                        g.normalize_gs1_digital_link(case["input"])
                else:
                    self.assertEqual(g.normalize_gs1_digital_link(case["input"]), case["expected"])


class GS1SafetyTests(unittest.TestCase):
    primary = g.GS1Element("01", "04912345678904")
    uri = "https://example.com/01/04912345678904"

    def test_catalog_immutable_and_bounded(self):
        self.assertEqual(len(g.get_supported_gs1_ais()), 50)
        for ai in ("90", "3106", "3206", "250", None, 1):
            self.assertIsNone(g.get_gs1_ai_info(ai))
        with self.assertRaises(FrozenInstanceError):
            g.get_gs1_ai_info("01").label = "changed"
        self.assertTrue(g.validate_gs1_elements([{"ai": "17", "value": "999999"}, {"ai": "414", "value": "1234567890123"}]).ok)

    def test_raw_separator_and_percent_preservation(self):
        values = (self.primary, g.GS1Element("10", "100%"), g.GS1Element("17", "251231"))
        raw = g.create_gs1_element_string(values)
        self.assertEqual(raw, "010491234567890410100%\x1d17251231")
        self.assertEqual(g.parse_gs1_element_string(raw).elements, values)
        self.assertFalse(g.validate_gs1_element_string("10ABC17251231").ok)
        # The inherited ambiguity heuristic intentionally does not validate the
        # supposed suffix's numeric value.
        self.assertFalse(g.validate_gs1_element_string("10ABC17XXXXXX").ok)
        self.assertFalse(g.validate_gs1_element_string("2000\x1d").ok)
        self.assertFalse(g.validate_gs1_element_string("10LOT\x1d").ok)

    def test_dot_payload_never_disappears(self):
        for dot in (".", ".."):
            values = (self.primary, g.GS1Element("10", dot))
            with self.assertRaises(InvalidGs1Error):
                g.create_gs1_digital_link(values, base_url="https://example.com")
            uri = g.create_gs1_digital_link(values, base_url="https://example.com", path_ais=())
            self.assertEqual(g.parse_gs1_digital_link(uri).elements, values)
            self.assertEqual(g.normalize_gs1_digital_link(uri), uri)
        for encoded in (".", "..", "%2e", "%2E%2e", ".%2E", "%2e."):
            uri = self.uri + "/10/" + encoded
            self.assertFalse(g.validate_gs1_digital_link(uri).ok)
            with self.assertRaises(InvalidGs1Error):
                g.parse_gs1_digital_link(uri)
        for value in ("%2e", "%2E%2e", "%", "100%", "%2f", "a/b", "a?b", "a#b"):
            elements = (self.primary, g.GS1Element("10", value))
            uri = g.create_gs1_digital_link(elements, base_url="https://example.com")
            self.assertEqual(g.parse_gs1_digital_link(uri).elements, elements)
        self.assertEqual(g.normalize_gs1_digital_link("https://example.com/a/%2e%2E/01/04912345678904"), self.uri)

    def test_query_preservation_and_utf8_policy(self):
        uri = self.uri + "?x=hello%00world&x=%F0%28%8C%28&bare&=empty&10=LOT+ONE"
        parsed = g.parse_gs1_digital_link(uri)
        self.assertEqual(parsed.unknown_query, (g.GS1UnknownQuery("x", "hello\x00world"), g.GS1UnknownQuery("x", "\ufffd(\ufffd("), g.GS1UnknownQuery("bare", ""), g.GS1UnknownQuery("", "empty")))
        self.assertEqual(parsed.query_elements, (g.GS1Element("10", "LOT ONE"),))
        self.assertTrue(g.validate_gs1_digital_link(uri).ok)
        malformed = self.uri + "?x=%zz"
        self.assertEqual(g.parse_gs1_digital_link(malformed).unknown_query[0].value, "%zz")
        self.assertFalse(g.validate_gs1_digital_link(malformed).ok)
        for encoded in ("%C0%AF", "%ED%A0%80", "%F4%90%80%80", "%E2%82", "%zz"):
            with self.assertRaises(InvalidGs1Error):
                g.parse_gs1_digital_link(self.uri + "/10/" + encoded)

    def test_url_accepted_forms(self):
        cases = {
            "https:/EXAMPLE.COM:443": "https://example.com",
            "https:////example.com": "https://example.com",
            "https:example.com": "https://example.com",
            "https://127.1": "https://127.0.0.1",
            "https://0177.1": "https://127.0.0.1",
            "https://0x7f.1": "https://127.0.0.1",
            "https://2130706433": "https://127.0.0.1",
            "https://１２７.１": "https://127.0.0.1",
            "https://[::ffff:192.168.1.1]": "https://[::ffff:c0a8:101]",
            "https://例え.テスト": "https://xn--r8jz45g.xn--zckzah",
            "https://user:p@ss@example.com": "https://user:p%40ss@example.com",
            "https://example.com:": "https://example.com",
            "https://exa_mple.com.": "https://exa_mple.com.",
        }
        for base, expected in cases.items():
            with self.subTest(base=base):
                self.assertEqual(g.create_gs1_digital_link((self.primary,), base_url=base), expected + "/01/04912345678904")
        self.assertEqual(g.create_gs1_digital_link((self.primary,), base_url="https://example.com?#"), self.uri + "#")
        self.assertEqual(g.normalize_gs1_digital_link(self.uri + "?#"), self.uri)
        self.assertEqual(g.normalize_gs1_digital_link("https:\\example.com\\01\\04912345678904?x=a\\b"), self.uri + "?x=a%5Cb")

    def test_precise_idna2003_boundary(self):
        for host, expected in (("faß.de", "fass.de"), ("ς.gr", "xn--4xa.gr"),
                               ("a\u200cb.com", "ab.com"), ("a\u200db.com", "ab.com")):
            uri = g.create_gs1_digital_link((self.primary,), base_url="https://" + host)
            self.assertEqual(uri, "https://" + expected + "/01/04912345678904")
        # ASCII names are not subjected to IDNA2003's DNS length restrictions.
        for host in ("a..b", "a" * 64 + ".com", "xn--fa-hia.de"):
            self.assertEqual(g.create_gs1_digital_link((self.primary,), base_url="https://" + host), "https://" + host + "/01/04912345678904")


    def test_query_unicode_scalar_conversion(self):
        for value, expected in (("\ud800", "%EF%BF%BD"), ("\udc00", "%EF%BF%BD"),
                                ("\ud83d\ude00", "%F0%9F%98%80"), ("😀", "%F0%9F%98%80")):
            self.assertEqual(g.normalize_gs1_digital_link(self.uri + "?x=" + value), self.uri + "?x=" + expected)

    def test_concurrent_calls(self):
        from concurrent.futures import ThreadPoolExecutor
        def check(index):
            gtin = g.append_gtin_check_digit(f"{index:013d}")
            elements = (g.GS1Element("01", gtin), g.GS1Element("10", "100%"))
            uri = g.create_gs1_digital_link(elements, base_url="https://example.com")
            return g.parse_gs1_digital_link(uri).elements == elements and g.normalize_gs1_digital_link(uri) == uri
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertTrue(all(executor.map(check, range(200))))

    def test_invalid_inputs_nonthrowing(self):
        for bad in (None, 3, "", [], {}, b"abc"):
            self.assertFalse(g.validate_gs1_element_string(bad).ok)
            self.assertFalse(g.validate_gs1_digital_link(bad).ok)
        for bad in (None, "abc", [], [None], [{"ai": None, "value": "x"}], [{"ai": "10", "value": None}]):
            self.assertFalse(g.validate_gs1_elements(bad).ok)
        for options in ({"context": "bogus"}, {"allow_unsupported_ai": True}, {"collect_all_errors": 1}):
            self.assertFalse(g.validate_gs1_elements([self.primary], **options).ok)
        self.assertFalse(g.validate_gs1_digital_link(self.uri, unknown_query="bogus").ok)

    def test_resource_limits_and_iterators(self):
        huge = "x" * (g.GS1_MAX_INPUT_CHARACTERS + 1)
        for function in (g.parse_gs1_element_string, g.parse_gs1_human_readable, g.calculate_gs1_check_digit, g.parse_gs1_digital_link):
            with self.assertRaises(InvalidGs1Error):
                function(huge)
        self.assertFalse(g.validate_gs1_elements(itertools.repeat(g.GS1Element("20", "00"))).ok)
        self.assertFalse(g.validate_gs1_element_string("2000" * (g.GS1_MAX_ELEMENTS + 1)).ok)
        self.assertFalse(g.validate_gs1_digital_link(self.uri + "?" + "&".join(["x=1"] * (g.GS1_MAX_ELEMENTS + 1))).ok)
        self.assertFalse(g.validate_gs1_digital_link("https://e/" + "/".join(["a"] * (g.GS1_MAX_ELEMENTS + 1))).ok)
        def broken():
            raise RuntimeError("caller iterator failure")
            yield
        with self.assertRaisesRegex(RuntimeError, "caller iterator failure"):
            g.validate_gs1_elements(broken())

    def test_aggregate_validation_work_budget(self):
        import time
        # A repeated reference previously caused the same invalid 100k value to
        # undergo a full printable-ASCII scan 1,000 times before returning.
        repeated = {"ai": "10", "value": "A" * 100_000}
        start = time.monotonic()
        checked = g.validate_gs1_elements([repeated] * 1_000)
        self.assertFalse(checked.ok)
        self.assertEqual(checked.errors[0].code, "GS1_INVALID_INPUT")
        self.assertIn("aggregate text", checked.errors[0].message)
        self.assertLess(time.monotonic() - start, 2.0)
        # Typed elements and generators receive the same aggregate accounting.
        typed = g.GS1Element("10", repeated["value"])
        self.assertFalse(g.validate_gs1_elements(itertools.repeat(typed, 1_000)).ok)
        with self.assertRaisesRegex(InvalidGs1Error, "aggregate text"):
            g.create_gs1_element_string([typed] * 1_000)
        with self.assertRaisesRegex(InvalidGs1Error, "aggregate text"):
            g.create_gs1_digital_link([self.primary] + [typed] * 1_000, base_url="https://example.com")
        # path_ais contains strings, not element objects, but is bounded too.
        with self.assertRaisesRegex(InvalidGs1Error, "aggregate text"):
            g.create_gs1_digital_link([self.primary], base_url="https://example.com", path_ais=["A" * 100_000] * 11)
        # For ordinary inputs the existing validation order is unchanged.
        ordinary = g.validate_gs1_elements([{"ai": "10", "value": "A" * 21}, {"ai": "17", "value": "abcdef"}])
        self.assertEqual([issue.code for issue in ordinary.errors], ["GS1_INVALID_LENGTH", "GS1_INVALID_CHARSET"])

    def test_deterministic_properties(self):
        randomizer = random.Random(60115)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ%-_/+=!@#$^&*.? "
        for _ in range(250):
            gtin = g.append_gtin_check_digit("".join(str(randomizer.randrange(10)) for _ in range(13)))
            self.assertTrue(g.validate_gtin_check_digit(gtin))
            lot = "L" + "".join(randomizer.choice(alphabet) for _ in range(randomizer.randrange(1, 18)))
            values = (g.GS1Element("01", gtin), g.GS1Element("10", lot), g.GS1Element("17", "251231"))
            raw = g.create_gs1_element_string(iter(values))
            self.assertEqual(g.parse_gs1_element_string(raw).elements, values)
            uri = g.create_gs1_digital_link(values, base_url="https://example.com/items")
            self.assertEqual(g.parse_gs1_digital_link(uri).elements, values)
            self.assertEqual(g.normalize_gs1_digital_link(uri), uri)


if __name__ == "__main__":
    unittest.main()
