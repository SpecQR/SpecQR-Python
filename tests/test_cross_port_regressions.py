"""Behavioral regressions; preserve the Python port's documented safe policies."""
import math
import unittest
import specqr as q


class CrossPortRegressionTests(unittest.TestCase):
    def test_fnc1_payloads_and_forced_modes(self):
        for text in ('10ABC%DEF', '10ABC%%DEF', '10%ABC', '10ABC%',
                     '10ABC%\x1d21DEF%', '10' + 'A' * 19 + '%'):
            for optimize in (False, True):
                with self.subTest(text=text, optimize=optimize):
                    opts = dict(gs1=True, optimize_segments=optimize, mask_pattern=3)
                    actual, plan = q.generate(text, **opts), q.estimate(text, **opts)
                    self.assertTrue(plan.ok)
                    self.assertEqual(plan.version, actual.version)
                    self.assertEqual(actual.segments[1].mode, 'byte')
                    self.assertEqual(actual.segments[1].data, text)
                    self.assertEqual(actual.codewords, q.generate(text, mode='byte', **opts).codewords)
                    for operation in (q.generate, q.estimate):
                        with self.assertRaises(q.InvalidModeError):
                            operation(text, mode='alphanumeric', **opts)
        for indicator in ('37', 'A'):
            for text in ('%', '%%', 'ABC%DEF%%', 'AA%\x1dBB%%', '漢字%'):
                actual = q.generate(text, fnc1_second=indicator)
                self.assertEqual(actual.segments[1].mode, 'byte')
                self.assertEqual(actual.segments[1].data, text)
                with self.assertRaises(q.InvalidModeError):
                    q.generate(text, fnc1_second=indicator, mode='alphanumeric')
        for text in ('ABC%DEF', 'ABC%%DEF'):
            segments = [q.Segment.fnc1(), q.Segment.alphanumeric(text)]
            self.assertEqual(q.generate_segments(segments).segments[1].data, text)
            self.assertTrue(q.analyze_segments(segments).ok)
        self.assertEqual(q.generate('ABC%DEF').segments[0].mode, 'alphanumeric')

    def test_fnc1_capacity_and_resource_bounds(self):
        opts = dict(gs1=True, version=1, error_correction_level='L')
        self.assertTrue(q.estimate('10' + 'A' * 14 + '%', **opts).ok)
        over = '10' + 'A' * 15 + '%'
        self.assertFalse(q.estimate(over, **opts).ok)
        with self.assertRaises(q.DataTooLongError):
            q.generate(over, **opts)
        self.assertGreater(q.generate(over, gs1=True, error_correction_level='L').version, 1)
        for version in (9, 10, 26, 27):
            plan = q.estimate('A%', fnc1_second='A', version=version)
            self.assertEqual(plan.data_bit_length, 40 if version < 10 else 48)
        self.assertFalse(q.estimate('%' * 100_000, fnc1_second='37').ok)
        with self.assertRaises(q.DataTooLongError):
            q.generate('%' * 100_000, fnc1_second='37')
        with self.assertRaises(q.DataTooLongError):
            q.estimate('%' * 1_000_001, fnc1_second='37')

    def test_dot_paths_fail_without_losing_payload(self):
        root = 'https://example.com/01/04912345678904'
        primary = q.GS1Element('01', '04912345678904')
        for dot in ('.', '..', '%2e', '%2E%2e', '.%2E', '%2e.'):
            raw = root + '/10/' + dot
            for operation in (q.parse_gs1_digital_link, q.normalize_gs1_digital_link):
                with self.subTest(raw=raw, operation=operation.__name__), self.assertRaises(q.InvalidGs1Error):
                    operation(raw)
            self.assertFalse(q.validate_gs1_digital_link(raw).ok)
        for dot in ('.', '..'):
            elements = [primary, q.GS1Element('10', dot)]
            with self.assertRaises(q.InvalidGs1Error):
                q.create_gs1_digital_link(elements, base_url='https://example.com')
            self.assertEqual(q.create_gs1_digital_link(elements, base_url='https://example.com', path_ais=()), root + '?10=' + dot)
        erased = 'https://example.com/01/./../01/04912345678904'
        for operation in (q.parse_gs1_digital_link, q.normalize_gs1_digital_link):
            with self.assertRaises(q.InvalidGs1Error):
                operation(erased)
        self.assertFalse(q.validate_gs1_digital_link(erased).ok)
        query = root + '?10=..&21=.&utm=a&utm=b'
        self.assertEqual(q.normalize_gs1_digital_link(query), query)
        self.assertEqual(q.parse_gs1_digital_link(query).elements,
                         (primary, q.GS1Element('10', '..'), q.GS1Element('21', '.')))
        self.assertEqual(q.create_gs1_digital_link([primary, q.GS1Element('10', '%2e')], base_url='https://example.com'), root + '/10/%252e')
        self.assertEqual(q.create_gs1_digital_link([primary], base_url='https://example.com/a/../b'), 'https://example.com/b/01/04912345678904')

    def test_conservative_finite_print_geometry(self):
        # Python always exposes diagnostics and validates options at version 40.
        # Fixed version 1 deliberately retains the existing conservative policy.
        for dpi in (5e-324, 1e-305, 1e-304, 0, -1, math.nan, math.inf):
            for version in (1, 40):
                with self.subTest(dpi=dpi, version=version), self.assertRaises(q.InvalidInputError):
                    q.Options(version=version, print_dpi=dpi)
        for version in (1, 40):
            geometry = q.generate('1', version=version, print_dpi=1e-303).diagnostics['print']
            self.assertTrue(math.isfinite(geometry['module_size_mm']))
            self.assertTrue(math.isfinite(geometry['symbol_size_mm']))
        actual = q.generate('1', version=1, mask_pattern=0, print_dpi=300)
        self.assertAlmostEqual(actual.diagnostics['print']['module_size_mm'], 8 / 300 * 25.4)
        self.assertEqual(actual.codewords, q.generate('1', version=1, mask_pattern=0).codewords)
        self.assertEqual([s.codewords for s in q.generate_structured_append('1' * 100, version=1, print_dpi=300).symbols],
                         [s.codewords for s in q.generate_structured_append('1' * 100, version=1).symbols])

    def test_invalid_ecc_is_typed_on_all_routes(self):
        for level in ('constructor', 'toString', 'valueOf', '__proto__', 'hasOwnProperty', '', 'm', None):
            options = dict(error_correction_level=level)
            for call in (lambda: q.generate('A', **options), lambda: q.estimate('A', **options),
                         lambda: q.generate_segments([q.Segment.byte('A')], **options),
                         lambda: q.analyze_segments([q.Segment.byte('A')], **options),
                         lambda: q.generate_structured_append('A', **options),
                         lambda: q.generate_segments_structured_append([q.Segment.byte('A')], **options),
                         lambda: q.get_capacity(1, level)):
                with self.subTest(level=level), self.assertRaises(q.InvalidInputError):
                    call()
        for level in 'LMQH':
            self.assertEqual(q.generate('A', error_correction_level=level).error_correction_level, level)


if __name__ == '__main__':
    unittest.main()
