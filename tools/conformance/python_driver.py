#!/usr/bin/env python3
"""Test-only JSON-lines adapter. Every response comes from the real Python API.

This module contains no reference expected values and imports no third-party
packages. It can target a source checkout or a clean installed distribution.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def matrix_rows(matrix):
    if not isinstance(matrix, tuple) or any(not isinstance(row, tuple) or len(row) != len(matrix)
                                           or any(type(cell) is not bool for cell in row) for row in matrix):
        raise AssertionError('Public matrix must be an immutable square tuple of boolean tuples')
    return [''.join('1' if cell else '0' for cell in row) for row in matrix]


def matrix_hash(rows):
    return hashlib.sha256(''.join(rows).encode('ascii')).hexdigest()


OPTION_NAMES = {
    'errorCorrectionLevel': 'error_correction_level', 'maskPattern': 'mask_pattern',
    'optimizeSegments': 'optimize_segments', 'boostErrorCorrection': 'boost_error_correction',
    'fnc1Second': 'fnc1_second', 'structuredAppend': 'structured_append',
    'minVersion': 'min_version', 'maxVersion': 'max_version', 'maxSymbols': 'max_symbols',
}


def options(value):
    # Python always returns QRResult; the JS-only matrix/diagnostics presentation
    # switches do not affect the symbol being compared. Do not emulate rendering.
    if 'output' in value and value['output'] != 'matrix':
        raise ValueError('The differential bridge only supports JS matrix output')
    return {OPTION_NAMES.get(key, key): item for key, item in value.items()
            if key not in ('output', 'diagnostics')}


def segments(values):
    result = []
    for value in values:
        item = {OPTION_NAMES.get(key, {'assignmentNumber': 'assignment_number',
                'applicationIndicator': 'application_indicator'}.get(key, key)): data
                for key, data in value.items() if key not in ('bytes', 'text')}
        if 'bytes' in value:
            item['data'] = bytes(value['bytes'])
        elif 'text' in value:
            item['data'] = value['text']
        result.append(item)
    return result


def payload(request):
    if 'rawText' in request:
        return bytes(request['rawText']).decode('utf-8', errors='strict')
    return bytes(request['bytes']) if 'bytes' in request else request.get('text', '')


def serialize_symbol(result, request):
    rows = matrix_rows(result.matrix)
    output = {'version': result.version, 'ecc': result.error_correction_level,
              'mask': result.mask_pattern, 'matrixHash': matrix_hash(rows),
              'data': bytes(result.data_codewords).hex(), 'codewords': bytes(result.codewords).hex()}
    if request.get('includeMatrix') or 'pngScale' in request:
        output['matrix'] = rows
    if 'pngScale' in request:
        output['png'] = result.to_png(scale=request['pngScale'], margin=4).hex()
    return output


def dispatch(request, api):
    from specqr import core, tables
    command = request.get('command', 'generate')
    if command == 'concurrency':
        from concurrent.futures import ThreadPoolExecutor
        tasks = request['requests']
        if len(tasks) > 512 or any(r.get('command') == 'concurrency' for r in tasks):
            raise ValueError('Invalid bounded concurrency test request')
        with ThreadPoolExecutor(max_workers=8) as executor:
            return {'results': list(executor.map(lambda r: dispatch(r, api), tasks))}
    if command == 'identity':
        package_root = Path(next(iter(api.__path__))).resolve()
        return {'python': sys.version, 'executable': sys.executable, 'module': str(Path(api.__file__).resolve()) if api.__file__ else str(Path(next(iter(api.__path__))).resolve()),
                'pid': os.getpid(), 'nonce': request['nonce'], 'packageVersion': getattr(api, '__version__', None),
                'packageFilesSha256': {str(p.relative_to(package_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in sorted(package_root.rglob('*.py'))}}
    if command == 'raw':
        version, level, seed, mask = (request[k] for k in ('version', 'ecc', 'seed', 'mask'))
        ordinal = 'LMQH'.index(level)
        data = bytes(0 if seed == 0 else 255 if seed == 1 else
                     ((i * 149 + version * 43 + ordinal * 89 + seed * 67) ^ (i >> (seed + 1))) & 255
                     for i in range(tables.data_codeword_count(version, level)))
        interleaved = core.interleave_codewords(data, version, level)
        result = core.build_matrix(interleaved.codewords, version, level, None if mask < 0 else mask)
        return {'data': data.hex(), 'codewords': bytes(interleaved.codewords).hex(),
                'matrixHash': matrix_hash(matrix_rows(result.matrix)), 'mask': result.mask_pattern,
                'penalty': result.penalty, 'penalties': [p.penalty for p in result.mask_penalties]}
    if command == 'gf':
        return {'bytes': bytes(core.gf_multiply(a, b) for a in range(256) for b in range(256)).hex()}
    if command == 'rs':
        degree = request['degree']
        data = bytes((i * 61 + degree) & 255 for i in range(300))
        return {'generator': core.reed_solomon_divisor(degree).hex(),
                'remainder': core.reed_solomon_remainder(data, degree).hex()}
    opts = options(request.get('options', {}))
    if command == 'capacity':
        result = api.get_capacity(**opts)
        return {'maximum': result.maximum, 'dataCodewords': result.data_codewords,
                'capacityBits': result.capacity_bits, 'countBits': result.character_count_bits}
    if command == 'estimate':
        result = api.estimate(payload(request), **opts)
        return {'fits': result.ok, 'version': result.version, 'requiredBits': result.data_bit_length,
                'capacityBits': result.capacity_bits}
    if command == 'structured-append':
        result = (api.generate_segments_structured_append(segments(request['segments']), **opts)
                  if 'segments' in request else api.generate_structured_append(payload(request), **opts))
        symbols = [serialize_symbol(symbol, request) for symbol in result.symbols]
        return {'total': result.total, 'parity': result.parity, 'inputLength': result.input_length,
                'byteLength': result.byte_length, 'symbols': symbols,
                'matrixHashes': [symbol['matrixHash'] for symbol in symbols],
                'versions': [symbol['version'] for symbol in symbols], 'masks': [symbol['mask'] for symbol in symbols]}
    result = (api.generate_segments(segments(request['segments']), **opts)
              if 'segments' in request else api.generate(payload(request), **opts))
    return serialize_symbol(result, request)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    args = parser.parse_args()
    if args.source_root:
        sys.path.insert(0, str(args.source_root.resolve()))
    import specqr
    for line in sys.stdin:
        request = json.loads(line)
        try:
            result = dispatch(request, specqr)
        except Exception as error:
            result = {'error': type(error).__name__, 'message': str(error),
                      'isSpecQRError': isinstance(error, specqr.SpecQRError),
                      'code': getattr(error, 'code', None)}
        fault = os.environ.get('SPECQR_TEST_FAULT') if request.get('command') != 'identity' else None
        if fault == 'exit':
            raise SystemExit(73)
        if fault == 'drop':
            continue
        if fault == 'error':
            result = {'error': 'InjectedFailure', 'message': 'test-only negative control'}
        if fault in ('leak-TypeError', 'leak-OverflowError', 'leak-AssertionError'):
            error_type = {'leak-TypeError': TypeError, 'leak-OverflowError': OverflowError,
                          'leak-AssertionError': AssertionError}[fault]
            try:
                raise error_type('test-only builtin exception leak control')
            except Exception as error:
                result = {'error': type(error).__name__, 'message': str(error),
                          'isSpecQRError': isinstance(error, specqr.SpecQRError),
                          'code': getattr(error, 'code', None)}
        if fault in ('matrixHash', 'data', 'codewords') and fault in result:
            value = result[fault]
            index = len(value) - 1 if fault == 'codewords' else 0
            result[fault] = value[:index] + ('1' if value[index] != '1' else '0') + value[index + 1:]
        print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
