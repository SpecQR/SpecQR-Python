#!/usr/bin/env python3
"""Strict independent jsQR detection of supported modes and actual Python PNGs.

jsQR cannot validate FNC1 or Structured Append headers. Those are mandatory in
the separate ZXing suites; this scope reports no successes for those features.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from protocol import configure, generate, DRIVER
from verify_decoders import verify_png
from verify_conformance import snapshot

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]


def fixtures():
    tests = []
    for level in 'LMQH':
        for mask in range(8):
            opts = {'version': 4, 'errorCorrectionLevel': level, 'maskPattern': mask}
            for mode, text in [('numeric', '01234567890123456789'), ('alphanumeric', 'SPECQR / 12345 %'),
                               ('byte', 'ASCII byte mode'), ('kanji', '漢字東京大阪日本語')]:
                tests.append((f'{mode}-{level}-{mask}', {'text': text, 'options': {**opts, 'mode': mode}}, {'text': text}))
            text = 'e\u0301🙂漢字 café'
            tests.append((f'eci-{level}-{mask}', {'text': text, 'options': {**opts, 'mode': 'byte', 'eci': 26}},
                          {'text': text, 'bytes': list(text.encode()), 'eci': 26}))
            data = [0, 255, 128, 127, 13, 10, 29, 0, 254, 65]
            tests.append((f'binary-{level}-{mask}', {'bytes': data, 'options': opts}, {'bytes': data}))
    for version in range(1, 41):
        data = [(i * 149 + version * 43) & 255 for i in range(min(5 + version * 5, 200))]
        tests.append((f'version-{version}', {'bytes': data, 'options': {'version': version,
                      'errorCorrectionLevel': 'LMQH'[(version - 1) % 4], 'maskPattern': (version - 1) % 8}}, {'bytes': data}))
    return tests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=ROOT)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--node', default='node')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'jsqr.json')
    args = parser.parse_args()
    configure(args.python, args.installed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    before = snapshot(args.candidate, 'all')
    identity = generate(args.candidate, [{'command': 'identity', 'nonce': 'jsqr-' + str(time.time_ns())}])[0]
    if 'error' in identity:
        raise RuntimeError(f'Python package identity failed: {identity}')
    started = time.perf_counter()
    tests, inputs, expected = fixtures(), [], []
    counts = {'matrixDecodes': 0, 'pngDecodes': 0, 'eciHeaders': 0, 'verifiedRgbaPixels': 0}
    failures, versions = [], {'matrix': set(), 'png': set()}
    report = {'status': 'running', 'decoder': 'jsQR 1.4.0', 'counts': counts,
              'sourceSha256': before, 'pythonIdentity': identity, 'installedConsumer': args.installed, 'driverSha256': hashlib.sha256(DRIVER.read_bytes()).hexdigest(),
              'limitations': ['jsQR does not support FNC1 or Structured Append; mandatory ZXing suites verify them.',
                              'Synthetic detection is not camera or printed-media certification.']}
    try:
        for offset in range(0, len(tests), 12):
            batch = tests[offset:offset + 12]
            outputs = generate(args.candidate, [{**request, 'pngScale': 4} for _, request, _ in batch])
            for (name, request, checks), symbol in zip(batch, outputs):
                if 'error' in symbol:
                    raise AssertionError(f'Candidate failed to generate {name}: {symbol}')
                _, luminance, dimension = verify_png(symbol['png'], symbol['matrix'], 4)
                counts['verifiedRgbaPixels'] += dimension * dimension
                inputs += [{'matrix': symbol['matrix'], 'scale': 4},
                           {'luminanceHex': luminance.hex(), 'width': dimension}]
                expected += [(name, 'matrix', {**checks, 'version': symbol['version']}),
                             (name, 'png', {**checks, 'version': symbol['version']})]
        # Decoder negative control is a real all-white image, never fabricated JSON.
        inputs.append({'matrix': ['0' * 21] * 21, 'scale': 4})
        output = subprocess.check_output([args.node, str(TOOLS / 'decode_jsqr.mjs')],
                                         input=''.join(json.dumps(r) + '\n' for r in inputs), text=True)
        results = [json.loads(line) for line in output.splitlines()]
        if len(results) != len(inputs):
            raise AssertionError('jsQR response count mismatch')
        for result, (name, route, checks) in zip(results, expected):
            ok = 'error' not in result and all(result.get(k) == value for k, value in checks.items() if k != 'eci')
            if 'eci' in checks:
                eci_ok = any(chunk.get('type') == 'eci' and chunk.get('assignmentNumber') == checks['eci']
                             for chunk in result.get('chunks', []))
                ok = ok and eci_ok
                counts['eciHeaders'] += int(eci_ok)
            if ok:
                counts[route + 'Decodes'] += 1
                versions[route].add(checks['version'])
            else:
                failures.append({'case': name, 'route': route, 'actual': result, 'expected': checks})
        report['blankImageNegativeControl'] = results[-1].get('error') == 'NoSymbol'
        if not report['blankImageNegativeControl']:
            failures.append({'case': 'all-white-image', 'actual': results[-1]})
        for route, seen in versions.items():
            if seen != set(range(1, 41)):
                failures.append({'route': route, 'missingVersions': sorted(set(range(1, 41)) - seen)})
        if snapshot(args.candidate, 'all') != before:
            raise RuntimeError('Candidate sources changed during decoder run')
        end_identity = generate(args.candidate, [{'command': 'identity', 'nonce': identity['nonce']}])[0]
        if end_identity.get('packageFilesSha256') != identity.get('packageFilesSha256'):
            raise RuntimeError('Imported package files changed during jsQR run')
        report.update(status='failed' if failures else 'passed', failures=failures,
                      decodedVersions={k: sorted(v) for k, v in versions.items()}, strictSymbols=len(tests))
        if failures:
            raise AssertionError(f'{len(failures)} independent jsQR checks failed')
    except Exception as error:
        end_identity = generate(args.candidate, [{'command': 'identity', 'nonce': identity['nonce']}])[0]
        if end_identity.get('packageFilesSha256') != identity.get('packageFilesSha256'):
            raise RuntimeError('Imported package files changed during jsQR run')
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        report['elapsedSeconds'] = round(time.perf_counter() - started, 3)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k != 'sourceSha256'}, indent=2))


if __name__ == '__main__':
    main()
