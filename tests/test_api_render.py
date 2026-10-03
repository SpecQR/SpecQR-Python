from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zlib

import specqr
from specqr import (generate, generate_segments, estimate, analyze_segments, get_capacity,
                    Segment, Options, DataTooLongError, InvalidInputError, InvalidModeError)
from specqr.render import to_pixels, to_png, to_svg, parse_color


class APITests(unittest.TestCase):
    def test_immutable_result_and_snapshot(self):
        data = bytearray(b'hello')
        result = generate(data)
        data[0] = 0
        self.assertEqual(result.segments[0].data, b'hello')
        with self.assertRaises(FrozenInstanceError):
            result.version = 40
        with self.assertRaises(TypeError):
            result.matrix[0][0] = False
        with self.assertRaises(TypeError):
            result.diagnostics['colors']['ratio'] = 0

    def test_planning_does_not_build_matrix_or_codewords(self):
        with patch('specqr.api.build_matrix', side_effect=AssertionError), patch('specqr.api.interleave_codewords', side_effect=AssertionError):
            self.assertTrue(estimate('planning').ok)
            self.assertTrue(analyze_segments([Segment.numeric('123')]).ok)
        self.assertFalse(estimate('x' * 100, version=1).ok)
        plan = estimate('x' * 10000, max_version=1)
        self.assertIsNone(plan.version)
        self.assertEqual(plan.capacity_version, 1)
        self.assertLess(plan.remaining_bits, 0)
        self.assertNotIn('CAPACITY_NEAR_LIMIT', [w['code'] for w in plan.diagnostics['warnings']])
        with self.assertRaises(DataTooLongError):
            generate('x' * 100, version=1)

    def test_capacity_exact_boundaries(self):
        for version in (1, 9, 10, 26, 27, 40):
            for level in 'LMQH':
                for mode, unit in [('numeric', '0'), ('alphanumeric', 'A'), ('byte', 'a'), ('kanji', '漢')]:
                    cap = get_capacity(version, level, mode=mode)
                    self.assertTrue(estimate(unit * cap.maximum, version=version, error_correction_level=level, mode=mode).ok)
                    self.assertFalse(estimate(unit * (cap.maximum + 1), version=version, error_correction_level=level, mode=mode).ok)

    def test_ecc_boost_and_options(self):
        q = generate('1', error_correction_level='L', boost_error_correction=True)
        self.assertEqual(q.error_correction_level, 'H')
        self.assertEqual(q.version, 1)
        self.assertEqual(generate('abc', Options(mask_pattern=2)).mask_pattern, 2)
        for name, value in [('version', True), ('mask_pattern', False), ('scale', True), ('margin', -1),
                            ('eci', 1000000), ('print_dpi', float('nan')), ('print_dpi', 10**1000), ('print_dpi', 5e-324), ('optimize_segments', 1),
                            ('min_version', 41), ('foreground', 1), ('unknown', 4)]:
            with self.subTest(name=name), self.assertRaises(specqr.SpecQRError):
                generate('abc', **{name: value})

    def test_fnc1_percent_payload_safety(self):
        text = '10A%B'
        q = generate(text, gs1=True)
        self.assertEqual(q.segments[0].mode, 'fnc1')
        self.assertEqual(q.segments[1].mode, 'byte')
        self.assertEqual(q.segments[1].data, text)
        with self.assertRaises(InvalidModeError):
            generate(text, gs1=True, mode='alphanumeric')
        q2 = generate('A%B', fnc1_second='01')
        self.assertEqual(q2.segments[1].mode, 'byte')
        manual = generate_segments([Segment.fnc1(), Segment.alphanumeric('10A%%B')])
        self.assertEqual(manual.segments[1].data, '10A%%B')

    def test_released_memoryview(self):
        zero = memoryview(b'A').cast('B', shape=[])
        self.assertEqual(generate(zero).codewords, generate(b'A').codewords)
        self.assertEqual(estimate(zero).data_bit_length, estimate(b'A').data_bit_length)
        value = memoryview(b'abc')
        value.release()
        with self.assertRaises(InvalidInputError):
            estimate(value)

    def test_manual_gs1_controls_do_not_reparse_payload(self):
        result = generate_segments([Segment.byte('HELLO')], gs1=True)
        self.assertEqual(result.segments[0].mode, 'fnc1')
        self.assertIsNone(result.diagnostics['gs1_validation']['element_count'])
        self.assertTrue(analyze_segments([Segment.byte('HELLO')], gs1=True).ok)
        high = generate('10HELLO', gs1=True)
        self.assertEqual(high.diagnostics['gs1_validation']['element_count'], 1)
        self.assertEqual(high.diagnostics['gs1_validation']['ais'], ('10',))

    def test_control_conflicts(self):
        for opts in [dict(gs1=True, eci=26), dict(fnc1_second='AB'),
                     dict(eci=1, structured_append=dict(index=1, total=2, parity=0))]:
            with self.assertRaises(specqr.SpecQRError):
                generate('10ABC', **opts)
        with self.assertRaises(specqr.SpecQRError):
            generate_segments([Segment.fnc1(), Segment.byte('abc')], gs1=True)

    def test_concurrent_determinism(self):
        inputs = ['HELLO', '1234567890', '漢字🙂', 'A' * 250, bytes(range(200))] * 4
        expected = [generate(x).codewords for x in inputs]
        with ThreadPoolExecutor(max_workers=8) as pool:
            actual = list(pool.map(lambda x: generate(x).codewords, inputs))
        self.assertEqual(expected, actual)

    def test_deterministic_fuzz(self):
        rng = random.Random(60291)
        for _ in range(80):
            value = ''.join(rng.choice('012ABxy漢字🙂%') for _ in range(rng.randrange(0, 90)))
            p = estimate(value)
            q = generate(value)
            self.assertTrue(p.ok)
            self.assertEqual(p.version, q.version)
            self.assertEqual(p.data_bit_length, q.diagnostics['data_bit_length'])
            self.assertEqual(q.matrix, generate(value, version=q.version, mask_pattern=q.mask_pattern).matrix)


class RenderTests(unittest.TestCase):
    matrix = ((True, False), (False, True))

    def test_png_crc_pixels_and_determinism(self):
        png = to_png(self.matrix, scale=3, margin=1, foreground='#1234', background='#abcdef80')
        self.assertEqual(png, to_png(self.matrix, scale=3, margin=1, foreground='#1234', background='#abcdef80'))
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        offset, image = 8, b''
        while offset < len(png):
            count = struct.unpack('>I', png[offset:offset+4])[0]
            name, data = png[offset+4:offset+8], png[offset+8:offset+8+count]
            crc = struct.unpack('>I', png[offset+8+count:offset+12+count])[0]
            self.assertEqual(crc, zlib.crc32(name+data))
            if name == b'IHDR':
                self.assertEqual(struct.unpack('>IIBBBBB', data), (12,12,8,6,0,0,0))
            if name == b'IDAT':
                image += data
            offset += count + 12
        raw = zlib.decompress(image)
        rgba = to_pixels(self.matrix, scale=3, margin=1, foreground='#1234', background='#abcdef80')
        self.assertEqual(len(raw), 12*(12*4+1))
        self.assertEqual(b''.join(raw[i+1:i+49] for i in range(0,len(raw),49)), rgba.pixels)
        self.assertEqual(rgba.pixels[:4], bytes.fromhex('abcdef80'))
        self.assertEqual(rgba.pixels[(3*12+3)*4:(3*12+3)*4+4], bytes.fromhex('11223344'))

    def test_svg_escaped_and_geometry(self):
        svg = to_svg(self.matrix, margin=2, scale=3, foreground='red"/><script>alert(1)</script>')
        root = ET.fromstring(svg)
        self.assertEqual(root.attrib['width'], '18')
        self.assertEqual(len(root), 2)
        self.assertEqual(root[1].attrib['fill'], 'red"/><script>alert(1)</script>')

    def test_colors_and_budgets(self):
        self.assertEqual(parse_color('#ABC'), (170,187,204,255))
        self.assertEqual(parse_color('transparent'), (0,0,0,0))
        for opts in [dict(scale=1000000), dict(margin=10**20), dict(scale=True), dict(margin=-1)]:
            with self.assertRaises(InvalidInputError):
                to_png(self.matrix, **opts)
        for matrix in [[], [[1]], [[True, False]], 'bad', [[True] * 178] * 178]:
            with self.assertRaises(InvalidInputError):
                to_png(matrix)
        with self.assertRaises(specqr.InvalidColorError):
            to_png(self.matrix, foreground='red')
        self.assertTrue(to_svg(self.matrix, foreground='red'))
        for invalid in ('red\x00', '\ud800'):
            with self.assertRaises(specqr.InvalidColorError):
                to_svg(self.matrix, foreground=invalid)

    def test_data_urls_and_render_errors(self):
        q = generate('test')
        self.assertTrue(q.to_png_data_url().startswith('data:image/png;base64,iVBOR'))
        self.assertTrue(q.to_svg_data_url().startswith('data:image/svg+xml;charset=utf-8,%3Csvg'))
        with self.assertRaises(specqr.InvalidOutputError):
            q.render('pdf')

    def test_warnings(self):
        q = generate('test', margin=1, foreground='#777', background='#888', print_dpi=1200, scale=1)
        codes = {w['code'] for w in q.diagnostics['warnings']}
        self.assertTrue({'SCAN_RISK','QUIET_ZONE_TOO_SMALL','COLOR_CONTRAST_LOW','PRINT_MODULE_TOO_SMALL'} <= codes)


if __name__ == '__main__':
    unittest.main()
