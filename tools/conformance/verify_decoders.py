#!/usr/bin/env python3
"""Independent development-only ZXing-C++ and ZXing Java decoding.

Checks complete real PNG pixels before decoding. Java strict PNG detection uses
scale 3, without PURE_BARCODE or matrix fallback. The known default-scale8
case and an independent same-pixel control are reported separately, including
both rejections if they occur. No failed detection is counted as success.
"""
# SPDX-License-Identifier: MIT
# Adapted from SpecQR-CPP tools/verify-decode.py at e91cd8.
import argparse
import base64
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import string
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib
from protocol import generate, matrix_hash, configure, DRIVER
from verify_conformance import snapshot

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
_FAILURE_OUTPUT = None
_FAILURE_CONTEXT = {}
JAR_VERSION = '3.5.4'
JAR_SHA256 = '71de5d89341b5fcf5dd89da7f44e84d825d0e084cdf3ec77c9abe26b0f0ceb13'
JAR_URL = f'https://repo.maven.apache.org/maven2/com/google/zxing/core/{JAR_VERSION}/core-{JAR_VERSION}.jar'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def b64(data):
    return base64.b64encode(bytes(data)).decode('ascii')


def verify_png(encoded, matrix, scale):
    png = bytes.fromhex(encoded)
    require(png[:8] == b'\x89PNG\r\n\x1a\n', 'PNG signature mismatch')
    position, compressed, width, height = 8, bytearray(), 0, 0
    while position < len(png):
        length = struct.unpack('>I', png[position:position + 4])[0]
        kind, data = png[position + 4:position + 8], png[position + 8:position + 8 + length]
        require(zlib.crc32(kind + data) == struct.unpack('>I', png[position + 8 + length:position + 12 + length])[0], 'PNG CRC mismatch')
        if kind == b'IHDR':
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', data)
            require((depth, color, compression, filtering, interlace) == (8, 6, 0, 0, 0), 'PNG RGBA format mismatch')
        if kind == b'IDAT':
            compressed.extend(data)
        position += length + 12
    dimension = (len(matrix) + 8) * scale
    require(width == height == dimension, 'PNG dimensions mismatch')
    raw = zlib.decompress(compressed)
    require(len(raw) == dimension * (dimension * 4 + 1), 'PNG scanline length mismatch')
    white, black = b'\xff\xff\xff\xff' * scale, b'\x00\x00\x00\xff' * scale
    blank = white * (len(matrix) + 8)
    rows = [blank] * 4 + [white * 4 + b''.join(black if v == '1' else white for v in row) + white * 4 for row in matrix] + [blank] * 4
    luminance = bytearray()
    for y in range(dimension):
        row = raw[y * (dimension * 4 + 1):(y + 1) * (dimension * 4 + 1)]
        require(row[0] == 0 and row[1:] == rows[y // scale], f'PNG pixel/quiet-zone mismatch at row {y}')
        luminance.extend(row[1::4])
    return png, luminance, dimension


def independent_png(matrix, scale):
    dimension = (len(matrix) + 8) * scale
    raw = bytearray()
    for y in range(dimension):
        raw.append(0)
        for x in range(dimension):
            row, column = y // scale - 4, x // scale - 4
            raw.append(0 if 0 <= row < len(matrix) and 0 <= column < len(matrix) and matrix[row][column] == '1' else 255)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', dimension, dimension, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def verify_control_pixels(png, expected_luminance, dimension):
    position, compressed = 8, bytearray()
    while position < len(png):
        length = struct.unpack('>I', png[position:position + 4])[0]
        kind, data = png[position + 4:position + 8], png[position + 8:position + 8 + length]
        require(zlib.crc32(kind + data) == struct.unpack('>I', png[position + 8 + length:position + 12 + length])[0], 'Independent control PNG CRC mismatch')
        if kind == b'IHDR':
            require(struct.unpack('>IIBBBBB', data) == (dimension, dimension, 8, 0, 0, 0, 0), 'Independent control PNG format mismatch')
        if kind == b'IDAT':
            compressed.extend(data)
        position += length + 12
    raw = zlib.decompress(compressed)
    require(len(raw) == dimension * (dimension + 1), 'Independent control scanline length differs')
    for y in range(dimension):
        row = raw[y * (dimension + 1):(y + 1) * (dimension + 1)]
        require(row[0] == 0 and row[1:] == expected_luminance[y * dimension:(y + 1) * dimension], 'Independent control pixels differ from Python PNG')

def cases(include_second):
    result = []
    def add(name, request, expected):
        result.append((name, request, expected))
    for level in 'LMQH':
        for mask in range(8):
            options = {'version': 4, 'errorCorrectionLevel': level, 'maskPattern': mask}
            for mode, text in [('numeric', '012345678901234567890123456789'), ('alphanumeric', 'SPECQR / 12345 %'), ('kanji', '漢字東京大阪日本語')]:
                add(f'{mode}-{level}-{mask}', {'text': text, 'options': {**options, 'mode': mode}}, {'text': text, 'bytes': text.encode('shift_jis' if mode == 'kanji' else 'utf-8')})
            text = 'e\u0301🙂漢字 café'
            add(f'utf8-{level}-{mask}', {'text': text, 'options': {**options, 'mode': 'byte', 'eci': 26}}, {'text': text, 'bytes': text.encode(), 'identifier': ']Q2'})
            data = bytes([0, 255, 128, 127, 13, 10, 29, 0, 254, 65])
            add(f'binary-{level}-{mask}', {'bytes': list(data), 'options': options}, {'bytes': data})
            add(f'fnc1-alpha-{level}-{mask}', {'segments': [{'mode': 'fnc1'}, {'mode': 'alphanumeric', 'text': '10LOT%21SER%%IAL'}], 'options': options}, {'text': '10LOT\x1d21SER%IAL', 'bytes': b'10LOT\x1d21SER%IAL', 'identifier': ']Q3'})
            add(f'fnc1-high-{level}-{mask}', {'text': '10LOT%\x1d21SER%%IAL', 'options': {**options, 'gs1': True}}, {'text': '10LOT%\x1d21SER%%IAL', 'bytes': b'10LOT%\x1d21SER%%IAL', 'identifier': ']Q3'})
    for assignment, data, text in [(3, [99,97,102,233], 'café'), (20,[138,191,142,154],'漢字'), (170,[65,83,67,73,73],'ASCII')]:
        add(f'eci-{assignment}', {'segments':[{'mode':'eci','assignmentNumber':assignment},{'mode':'byte','bytes':data}]}, {'text':text,'bytes':bytes(data),'identifier':']Q2'})
    for total in range(2, 17):
        for index in range(1, total + 1):
            data, checksum = bytes([0, index, total, 255, 128]), (total * 17 + index * 31) & 255
            add(f'sa-{index}-{total}', {'bytes': list(data), 'options': {'version': 2, 'maskPattern': index % 8, 'structuredAppend': {'index': index, 'total': total, 'parity': checksum}}}, {'bytes': data, 'sequence': (index - 1) * 16 + total - 1, 'parity': checksum})
    if include_second:
        indicators = [f'{n:02}' for n in range(100)] + list(string.ascii_uppercase + string.ascii_lowercase)
        for i, indicator in enumerate(indicators):
            for route in ['manual', 'options']:
                request = {'options': {'version': 3, 'errorCorrectionLevel': 'LMQH'[i % 4], 'maskPattern': i % 8}}
                if route == 'manual':
                    request['segments'] = [{'mode': 'fnc1-second', 'applicationIndicator': indicator}, {'mode': 'alphanumeric', 'text': 'ABC%123%%XYZ'}]
                    data = b'ABC\x1d123%XYZ'
                else:
                    request.update(text='ABC%123%%XYZ'); request['options']['fnc1Second'] = indicator
                    data = b'ABC%123%%XYZ'
                add(f'second-{indicator}-{route}', request, {'bytes': indicator.encode() + data, 'identifier': ']Q5'})
    # Detection and real PNG coverage across every Model 2 version. Fixed
    # deterministic payload size grows independently of the encoder's estimates.
    for version in range(1, 41):
        data = bytes((i * 149 + version * 43) & 255 for i in range(min(5 + version * 5, 200)))
        add(f'version-{version}', {'bytes': list(data), 'options': {'version': version, 'errorCorrectionLevel': 'LMQH'[(version - 1) % 4], 'maskPattern': (version - 1) % 8}}, {'bytes': data})
    return result


def main():
    global _FAILURE_OUTPUT, _FAILURE_CONTEXT
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate', type=Path, default=ROOT)
    p.add_argument('--python', default=sys.executable)
    p.add_argument('--installed', action='store_true')
    p.add_argument('--decoder', choices=['cpp', 'java'], required=True)
    p.add_argument('--dependency-dir', type=Path, required=True)
    p.add_argument('--java', default='java')
    p.add_argument('--node', default='node')
    p.add_argument('--no-install', action='store_true')
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    configure(args.python, args.installed)
    args.output = args.output or ROOT / 'artifacts' / f'zxing-{args.decoder}.json'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _FAILURE_OUTPUT = args.output
    _FAILURE_CONTEXT = {'status': 'running', 'decoder': args.decoder, 'candidate': str(args.candidate), 'scopeCompleted': False}
    work = args.output.parent / f'zxing-{args.decoder}-fixtures'
    work.mkdir(parents=True, exist_ok=True)
    args.dependency_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    source_hashes = snapshot(args.candidate, 'all')
    python_identity = generate(args.candidate, [{'command': 'identity', 'nonce': 'decoder-' + str(time.time_ns())}])[0]
    require('error' not in python_identity, 'Python candidate import failed')
    adapter_sha = hashlib.sha256(DRIVER.read_bytes()).hexdigest()
    decoder_identity = {}
    if args.decoder == 'cpp':
        requirements = TOOLS / 'zxing-cpp-requirements.txt'
        requirement_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()
        receipt = args.dependency_dir / '.verified-requirements-sha256'
        if not receipt.exists() or receipt.read_text().strip() != requirement_hash:
            require(not args.no_install, 'Hash-verified ZXing-C++ wheels not present; no tests ran')
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--target', str(args.dependency_dir), '--upgrade', '--only-binary=:all:', '--no-deps', '--require-hashes', '-r', str(requirements)], check=True)
            receipt.write_text(requirement_hash + '\n')
        sys.path.insert(0, str(args.dependency_dir.resolve()))
        zxing = importlib.import_module('zxingcpp')
        require(importlib.metadata.version('zxing-cpp') == '3.1.1', 'Unexpected ZXing-C++ version')
        decoder_identity = {'decoder': 'ZXing-C++ 3.1.1', 'requirementsSha256': requirement_hash,
                            'extensionSha256': hashlib.sha256(Path(zxing.__file__).read_bytes()).hexdigest()}
    else:
        jar = args.dependency_dir / f'core-{JAR_VERSION}.jar'
        if not jar.exists():
            require(not args.no_install, 'Pinned ZXing Java JAR not present; no tests ran')
            with urllib.request.urlopen(JAR_URL, timeout=60) as response:
                data = response.read(4 * 1024 * 1024 + 1)
            require(hashlib.sha256(data).hexdigest() == JAR_SHA256, 'Downloaded Java JAR hash mismatch')
            jar.write_bytes(data)
        require(hashlib.sha256(jar.read_bytes()).hexdigest() == JAR_SHA256, 'Java JAR hash mismatch')
        version = subprocess.check_output([args.java, '--version'], text=True).splitlines()[0]
        decoder_identity = {'decoder': 'ZXing Java 3.5.4', 'jarSha256': JAR_SHA256, 'java': version}
    scale = 8 if args.decoder == 'cpp' else 3
    tests, inputs, expectations = cases(args.decoder == 'cpp'), [], []
    counts = {'matrixDecodes': 0, 'pngDecodes': 0, 'verifiedRgbaPixels': 0, 'structuredAppendHeaders': 0, 'fnc1SecondSymbols': 0, 'damagedSymbolsCorrected': 0, 'eciTextModeChecks': 0}
    failures = []
    _FAILURE_CONTEXT.update(counts=counts, failures=failures, pythonIdentity=python_identity, sourceSha256=source_hashes)
    negative_controls = []
    decoded_versions = {'matrix': set(), 'png': set()}
    for offset in range(0, len(tests), 12):
        batch = tests[offset:offset + 12]
        output = generate(args.candidate, [{**r, 'pngScale': scale} for _, r, _ in batch])
        for (name, request, expected), symbol in zip(batch, output):
            require('error' not in symbol, f'Generation failed {name}: {symbol}')
            png, luminance, dimension = verify_png(symbol['png'], symbol['matrix'], scale)
            counts['verifiedRgbaPixels'] += dimension * dimension
            counts['structuredAppendHeaders'] += int(name.startswith('sa-'))
            counts['fnc1SecondSymbols'] += int(name.startswith('second-'))
            if args.decoder == 'cpp':
                width = (len(symbol['matrix']) + 8) * 3
                pixels = bytearray()
                for y in range(width):
                    for x in range(width):
                        my, mx = y // 3 - 4, x // 3 - 4
                        pixels.append(0 if 0 <= my < len(symbol['matrix']) and 0 <= mx < len(symbol['matrix']) and symbol['matrix'][my][mx] == '1' else 255)
                for route, data, size in [('matrix', pixels, width), ('png', luminance, dimension)]:
                    decoded = zxing.read_barcode(memoryview(data).cast('B', shape=(size, size)), text_mode=zxing.TextMode.Plain)
                    # ZXing-C++ exposes the base identifier through this property even
                    # when ECI exists. ECI mode exposes the effective ]Q2 identifier
                    # in text and transcodes payload to UTF-8. Check both APIs.
                    identifier = expected.get('identifier', ']Q1')
                    property_identifier = ']Q1' if identifier == ']Q2' else identifier
                    ok = decoded is not None and decoded.valid and decoded.bytes == expected['bytes'] and decoded.symbology_identifier == property_identifier and decoded.ec_level == symbol['ecc'] and decoded.extra['Version'] == str(symbol['version']) and decoded.extra['DataMask'] == symbol['mask']
                    if identifier == ']Q2':
                        eci_decoded = zxing.read_barcode(memoryview(data).cast('B', shape=(size, size)), text_mode=zxing.TextMode.ECI)
                        eci_ok = eci_decoded is not None and eci_decoded.valid and eci_decoded.text == ']Q2\\000026' + expected['text']
                        ok = ok and eci_ok
                        counts['eciTextModeChecks'] += int(eci_ok)
                    if ok:
                        counts[route + 'Decodes'] += 1
                        decoded_versions[route].add(symbol['version'])
                    else:
                        failures.append({'case': name, 'route': route, 'decoded': None if decoded is None else {'bytes': decoded.bytes.hex(), 'identifier': decoded.symbology_identifier, 'ecc': decoded.ec_level}, 'expectedHex': expected['bytes'].hex()})
            else:
                expected = {**expected, 'data': symbol['data'], 'ecc': symbol['ecc'], 'version': symbol['version']}
                for route, value in [('matrix', ','.join(symbol['matrix'])), ('png', str((work / (name + '.png')).resolve()))]:
                    if route == 'png':
                        Path(value).write_bytes(png)
                    inputs.append(f'{route}\t{value}')
                    expectations.append((name, route, expected))
    # Read metadata and reconstruct complete high-level Structured Append sets.
    groups = [
        ('numeric', {'text': '0123456789' * 13, 'options': {'version': 1, 'mode': 'numeric'}}, ('0123456789' * 13).encode()),
        ('alphanumeric', {'text': 'SPECQR / 12345 ' * 10, 'options': {'version': 2, 'mode': 'alphanumeric'}}, ('SPECQR / 12345 ' * 10).encode()),
        ('unicode', {'text': 'e\u0301🙂漢字' * 12, 'options': {'version': 2, 'mode': 'byte'}}, ('e\u0301🙂漢字' * 12).encode()),
        ('all-bytes', {'bytes': list(range(256)), 'options': {'version': 2}}, bytes(range(256))),
        ('sixteen', {'bytes': list(range(240)), 'options': {'version': 1, 'errorCorrectionLevel': 'L'}}, bytes(range(240)))
    ]
    group_checks = []
    counts.update(highLevelSets=0, highLevelSymbols=0, independentReconstructions=0)
    for name, request, source in groups:
        result = generate(args.candidate, [{**request, 'command': 'structured-append', 'pngScale': scale}])[0]
        require('error' not in result, f'Structured Append generation failed: {name}: {result}')
        checksum = 0
        for byte in source:
            checksum ^= byte
        require(result['parity'] == checksum and 2 <= result['total'] <= 16 and len(result['symbols']) == result['total'], 'SA source parity/count differs')
        if name == 'sixteen':
            require(result['total'] == 16, 'Maximum-count SA fixture did not generate 16 members')
        indices, parts = [], []
        for i, symbol in enumerate(result['symbols']):
            png, luminance, dimension = verify_png(symbol['png'], symbol['matrix'], scale)
            counts['verifiedRgbaPixels'] += dimension * dimension
            counts['highLevelSymbols'] += 1
            if args.decoder == 'cpp':
                decoded = zxing.read_barcode(memoryview(luminance).cast('B', shape=(dimension, dimension)), text_mode=zxing.TextMode.Plain)
                require(decoded is not None and decoded.valid, f'SA PNG detection failed {name}-{i}')
                parts.append(decoded.bytes)
                counts['pngDecodes'] += 1
            else:
                expected = {'data': symbol['data'], 'ecc': symbol['ecc'], 'sequence': i * 16 + result['total'] - 1,
                            'parity': checksum, 'identifier': ']Q2' if request['options'].get('eci') == 26 else ']Q1'}
                indices.append(len(expectations))
                for route, value in [('matrix', ','.join(symbol['matrix'])), ('png', str((work / f'high-{name}-{i}.png').resolve()))]:
                    if route == 'png':
                        Path(value).write_bytes(png)
                    inputs.append(f'{route}\t{value}')
                    expectations.append((f'high-{name}-{i}', route, expected))
        if args.decoder == 'cpp':
            require(b''.join(parts) == source, f'SA reconstruction failed {name}')
            counts['independentReconstructions'] += 1
        else:
            group_checks.append((name, indices, source, 'bytes' in request))
        counts['highLevelSets'] += 1
    # Three distinct data codewords in version 1, independently corrected.
    if args.decoder == 'java':
        requests = [{'text': 'ECC', 'includeMatrix': True, 'options': {'version': 1, 'mode': 'byte', 'errorCorrectionLevel': level, 'maskPattern': mask}} for level in 'LMQH' for mask in range(8)]
        for i, symbol in enumerate(generate(args.candidate, requests)):
            damaged = [list(row) for row in symbol['matrix']]
            for y in [20, 16, 12]:
                damaged[y][20] = '0' if damaged[y][20] == '1' else '1'
            inputs.append('matrix\t' + ','.join(''.join(row) for row in damaged))
            expectations.append((f'damage-{i}', 'damage', {'text': 'ECC', 'data': symbol['data'], 'ecc': symbol['ecc'], 'errorsCorrected': 3}))
    # Genuine undecodable matrix/PNG controls; never counted as successful decodes.
    blank_matrix = ['0' * 21] * 21
    if args.decoder == 'java':
        blank_path = (work / 'blank-negative-control.png').resolve()
        blank_path.write_bytes(independent_png(blank_matrix, 3))
        inputs.extend(['matrix\t' + ','.join(blank_matrix), 'png\t' + str(blank_path)])
        expectations.extend([('blank-matrix', 'negative', {'error': True}),
                             ('blank-png', 'negative', {'error': True})])
    else:
        for route, blank_scale in [('matrix', 3), ('png', 8)]:
            dimension = 29 * blank_scale
            pixels = bytearray([255]) * (dimension * dimension)
            if route == 'png':
                verify_control_pixels(independent_png(blank_matrix, blank_scale), pixels, dimension)
            decoded = zxing.read_barcode(memoryview(pixels).cast('B', shape=(dimension, dimension)), text_mode=zxing.TextMode.Plain)
            rejected = decoded is None or not decoded.valid
            negative_controls.append({'case': 'blank-' + route, 'rejected': rejected})
            if not rejected:
                failures.append({'case': 'blank-' + route, 'error': 'Undecodable control was accepted'})
    # Keep the exact default-scale8 detector regression and independent control.
    request = {'text': 'SPECQR / 12345 %', 'options': {'version': 4, 'errorCorrectionLevel': 'L', 'maskPattern': 0, 'mode': 'alphanumeric'}, 'pngScale': 8}
    diagnostic = generate(args.candidate, [request])[0]
    png, luminance, dimension = verify_png(diagnostic['png'], diagnostic['matrix'], 8)
    control = independent_png(diagnostic['matrix'], 8)
    verify_control_pixels(control, luminance, dimension)
    diagnostic_report = {'case': 'alphanumeric-L-0', 'scale': 8, 'quietZone': 4, 'allPixelsMatch': True, 'independentControlPixelsIdentical': True,
                         'pythonPngSha256': hashlib.sha256(png).hexdigest(), 'controlPngSha256': hashlib.sha256(control).hexdigest(),
                         'matrixSha256': matrix_hash(diagnostic['matrix']), 'countedAsStrictSuccess': False}
    if args.decoder == 'java':
        for name, data in [('python-default-scale8', png), ('independent-default-scale8', control)]:
            path = (work / (name + '.png')).resolve(); path.write_bytes(data); inputs.append(f'png\t{path}')
        input_path = work / 'inputs.tsv'; input_path.write_text('\n'.join(inputs) + '\n')
        output = subprocess.check_output([args.java, '-Djava.awt.headless=true', '--class-path', str(jar.resolve()), str(TOOLS / 'zxing-java' / 'DecodeSymbols.java'), str(input_path.resolve())], text=True)
        (work / 'decoded.jsonl').write_text(output)
        records = [json.loads(line) for line in output.splitlines()]
        require(len(records) == len(inputs), 'Java response count mismatch')
        for record, (name, route, expected) in zip(records, expectations):
            if expected.get('error'):
                rejected = 'error' in record
                negative_controls.append({'case': name, 'rejected': rejected, 'actual': record})
                if not rejected:
                    failures.append({'case': name, 'route': route, 'error': 'Undecodable control was accepted'})
                continue
            ok = 'error' not in record
            if ok:
                ok = record['rawBytesBase64'] == b64(bytes.fromhex(expected['data'])) and record['ecc'] == expected['ecc'] and record['symbologyIdentifier'] == expected.get('identifier', ']Q1') and record['sequence'] == expected.get('sequence') and record['parity'] == expected.get('parity')
                if 'text' in expected:
                    ok = ok and base64.b64decode(record['textBase64']).decode() == expected['text']
                elif 'bytes' in expected:
                    ok = ok and b''.join(base64.b64decode(v) for v in record['byteSegments']) == expected['bytes']
            if 'errorsCorrected' in expected:
                ok = ok and record.get('errorsCorrected') == expected['errorsCorrected']
            if ok:
                counts['damagedSymbolsCorrected' if route == 'damage' else route + 'Decodes'] += 1
                if route in decoded_versions and 'version' in expected:
                    decoded_versions[route].add(expected['version'])
            else:
                failures.append({'case': name, 'route': route, 'actual': record})
        for name, indices, source, binary in group_checks:
            for route in [0, 1]:
                parts = sorted((records[i + route] for i in reversed(indices)), key=lambda r: r.get('sequence', -1))
                reconstructed = (b''.join(base64.b64decode(v) for part in parts for v in part.get('byteSegments', [])) if binary
                                 else b''.join(base64.b64decode(part.get('textBase64', '')) for part in parts))
                if reconstructed == source:
                    counts['independentReconstructions'] += 1
                else:
                    failures.append({'case': 'high-' + name, 'route': ['matrix', 'png'][route], 'error': 'independent reconstruction mismatch'})
        outcomes = [{k: v for k, v in r.items() if k != 'ordinal'} for r in records[-2:]]
        diagnostic_report.update(outcomes=outcomes, matchingOutcomes=outcomes[0] == outcomes[1],
                                 detectionFailures=sum('error' in r for r in outcomes), detectionSuccesses=sum('error' not in r for r in outcomes))
        require(outcomes[0] == outcomes[1], 'Default-scale PNG differs from same-pixel independent control')
        for outcome in outcomes:
            if 'error' not in outcome:
                require(base64.b64decode(outcome['textBase64']).decode() == request['text'] and outcome['rawBytesBase64'] == b64(bytes.fromhex(diagnostic['data'])) and outcome['ecc'] == 'L' and outcome['symbologyIdentifier'] == ']Q1', 'Default-scale diagnostic decoded incorrect payload/metadata')
    else:
        decoded = zxing.read_barcode(memoryview(luminance).cast('B', shape=(dimension, dimension)), text_mode=zxing.TextMode.Plain)
        ok = decoded is not None and decoded.valid and decoded.bytes == request['text'].encode()
        diagnostic_report.update(exactPayloadDecoded=ok, detectionFailures=int(not ok), detectionSuccesses=int(ok))
        require(ok, 'ZXing-C++ default-scale8 PNG diagnostic failed')
    eci_controls = []
    if args.decoder == 'cpp':
        controls = subprocess.check_output([args.node, str(TOOLS / 'eci-control.mjs')], text=True)
        for control in map(json.loads, controls.splitlines()):
            request = {'includeMatrix': True, 'segments': [{'mode': 'eci', 'assignmentNumber': control['assignment']}, {'mode': 'byte', 'bytes': control['bytes']}],
                       'options': {'version': 4, 'errorCorrectionLevel': 'M', 'maskPattern': 0}}
            symbol = generate(args.candidate, [request])[0]
            require(symbol.get('matrix') == control['matrix'], 'Python ECI control differs from independent Nayuki')
            width = (len(control['matrix']) + 8) * 3
            pixels = bytearray()
            for y in range(width):
                for x in range(width):
                    my, mx = y // 3 - 4, x // 3 - 4
                    pixels.append(0 if 0 <= my < len(control['matrix']) and 0 <= mx < len(control['matrix']) and control['matrix'][my][mx] == '1' else 255)
            image = memoryview(pixels).cast('B', shape=(width, width))
            plain = zxing.read_barcode(image, text_mode=zxing.TextMode.Plain)
            eci = zxing.read_barcode(image, text_mode=zxing.TextMode.ECI)
            require(plain is not None and eci is not None and plain.valid and eci.valid, 'Independent ECI control detection failed')
            require(plain.bytes == bytes(control['bytes']) and plain.symbology_identifier == ']Q1', 'Independent ECI base API differs')
            require(eci.symbology_identifier == ']Q1' and eci.text == ']Q2\\000026' + control['text'], 'Independent ECI text API differs')
            eci_controls.append({'source': 'nayuki-qr-code-generator@1.8.0', 'assignment': control['assignment'],
                                 'matrixSha256': matrix_hash(control['matrix']), 'pythonMatrixIdentical': True,
                                 'decodedBytesHex': plain.bytes.hex(), 'plainIdentifier': plain.symbology_identifier,
                                 'eciIdentifierProperty': eci.symbology_identifier, 'eciText': eci.text})
        (args.output.parent / 'zxing-cpp-eci-controls.json').write_text(json.dumps({'status': 'passed', 'controls': eci_controls}, indent=2, ensure_ascii=False) + '\n')
    for route, versions in decoded_versions.items():
        missing = sorted(set(range(1, 41)) - versions)
        if missing:
            failures.append({'route': route, 'missingDecodedVersions': missing})
    if len(negative_controls) != 2 or not all(control['rejected'] for control in negative_controls):
        failures.append({'error': 'Decoder negative-control coverage or rejection mismatch'})
    end_identity = generate(args.candidate, [{'command': 'identity', 'nonce': python_identity['nonce']}])[0]
    if end_identity.get('packageFilesSha256') != python_identity.get('packageFilesSha256'):
        failures.append({'error': 'Imported Python package files changed during decoder run'})
    if snapshot(args.candidate, 'all') != source_hashes:
        failures.append({'error': 'Candidate sources changed during decoder run'})
    if hashlib.sha256(DRIVER.read_bytes()).hexdigest() != adapter_sha:
        failures.append({'error': 'Adapter changed during decoder run'})
    report = {'status': 'failed' if failures else 'passed', **decoder_identity, 'adapterSha256': adapter_sha, 'sourceSha256': source_hashes, 'installedConsumer': args.installed, 'pythonIdentity': python_identity,
              'counts': counts, 'decodedVersions': {k: sorted(v) for k, v in decoded_versions.items()}, 'independentNayukiEciControls': eci_controls, 'strictSymbols': len(tests), 'pngScale': scale, 'pureBarcodeHint': False,
              'defaultScaleDiagnostic': diagnostic_report, 'negativeControls': negative_controls, 'eciApiObservation': ('ZXing-C++ 3.1.1 symbology_identifier is base ]Q1 for ECI; TextMode.ECI independently exposes ]Q2 and transcodes to ECI26 UTF-8. Both are asserted.' if args.decoder == 'cpp' else 'Java returns ]Q2 for ECI; asserted directly.'), 'failures': failures, 'elapsedSeconds': round(time.perf_counter() - started, 3),
              'limitations': ['Synthetic PNG detection is not camera/print certification.', 'Java excludes FNC1-second; ZXing-C++ tests all 152 indicators.']}
    report['scopeCompleted'] = True
    _FAILURE_CONTEXT = report
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    require(not failures, f'{len(failures)} strict independent decoder checks failed')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if _FAILURE_OUTPUT is not None:
            _FAILURE_CONTEXT.update(status='failed', terminalError=f'{type(error).__name__}: {error}')
            _FAILURE_OUTPUT.write_text(json.dumps(_FAILURE_CONTEXT, indent=2) + '\n')
        raise
