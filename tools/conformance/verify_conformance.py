#!/usr/bin/env python3
"""Live Python/JS/Nayuki conformance. References are never updated on failure.

The default run includes all public and internal stages. A selected single stage
is explicitly a scoped result. Decoders are separate strict tools, not implied by
this report. Dependencies are isolated in this test-only directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from protocol import configure, generate

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
BASELINE_COMMIT = '15ad15e5c770ea0e39072f8f88b2733018f02ffd'
KNOWN_ERROR_CODES = {
    'DataTooLongError': 'DATA_TOO_LONG', 'InvalidInputError': 'INVALID_INPUT',
    'InvalidVersionError': 'INVALID_VERSION', 'InvalidModeError': 'INVALID_MODE',
    'InvalidColorError': 'INVALID_COLOR', 'InvalidEciError': 'INVALID_ECI',
    'InvalidGs1Error': 'INVALID_GS1', 'InvalidOutputError': 'INVALID_OUTPUT',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_snapshot():
    files = set()
    for pattern in ('*.py', '*.mjs', '*.json', '*.txt', 'zxing-java/*.java'):
        files.update(TOOLS.glob(pattern))
    return {str(p.relative_to(TOOLS)): digest(p) for p in sorted(files)}


def snapshot(candidate, scope):
    source = candidate / 'src' if (candidate / 'src').is_dir() else candidate / 'specqr'
    paths = sorted(source.rglob('*.py'))
    if scope == 'internal':
        paths = [p for p in paths if p.name in ('core.py', 'tables.py', 'errors.py')]
    paths += [p for p in [candidate / 'pyproject.toml'] if p.exists()]
    return {str(p.relative_to(candidate)): digest(p) for p in paths}


def compare(record, actual):
    if 'error' in actual:
        raise AssertionError(f'Candidate error: {actual}')
    mismatches = {key: {'expected': value, 'actual': actual.get(key)}
                  for key, value in record['expected'].items() if actual.get(key) != value}
    if mismatches:
        raise AssertionError(json.dumps(mismatches, ensure_ascii=True)[:1200])


def assert_typed_rejection(actual):
    expected_code = KNOWN_ERROR_CODES.get(actual.get('error'))
    if actual.get('isSpecQRError') is not True or expected_code is None or actual.get('code') != expected_code:
        raise AssertionError(f'Expected a known SpecQRError subclass and matching stable code; got {actual}')


def negative_controls(candidate, record):
    # All six tests start another real Python interpreter and reach the candidate
    # before the test-only bridge corrupts the result or exits. No in-memory fake
    # of the candidate can satisfy these controls.
    compare(record, generate(candidate, [record['request']])[0])
    controls = []
    for fault in ['matrixHash', 'data', 'codewords', 'exit', 'drop', 'error']:
        try:
            actual = generate(candidate, [record['request']], fault=fault)[0]
            compare(record, actual)
        except (AssertionError, RuntimeError) as error:
            controls.append({'fault': fault, 'detected': True, 'detail': str(error)[:180]})
        else:
            raise AssertionError(f'Negative control was silently accepted: {fault}')
    return controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=ROOT)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--node', default='node')
    parser.add_argument('--installed', action='store_true', help='Use only the selected Python installation; never insert source paths')
    parser.add_argument('--suite', choices=['all', 'public', 'internal'], default='all')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'conformance.json')
    args = parser.parse_args()
    configure(args.python, args.installed)
    candidate, baseline = args.candidate.resolve(), args.baseline.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report = {'status': 'running', 'requestedScope': args.suite, 'completeConformanceScope': args.suite == 'all',
              'candidate': str(candidate), 'installedConsumer': args.installed,
              'baselineRepository': 'https://github.com/SpecQR/SpecQR', 'baselineCommit': BASELINE_COMMIT,
              'independentOracle': 'nayuki-qr-code-generator@1.8.0',
              'counts': {'publicMatrices': 0, 'nayukiMatrices': 0, 'rawMatrices': 0, 'capacityCases': 0,
                         'boundaryEstimates': 0, 'gfProducts': 0, 'reedSolomonDegrees': 0,
                         'malformedInputs': 0, 'structuredAppendSets': 0, 'structuredAppendMatrices': 0},
              'stages': {}, 'limitations': ['Finite deterministic corpus, not ISO/GS1 certification.',
                  'Decoder, rendering, packaging and clean-consumer checks have separate reports.'],
              'intentionalDifferences': ['High-level FNC1 literal percent compares with explicitly byte-encoded JS output.']}
    before = snapshot(candidate, args.suite)
    report['sourceSha256'] = before
    report['testToolSha256'] = tool_snapshot()
    try:
        revision = subprocess.check_output(['git', '-C', str(baseline), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != BASELINE_COMMIT:
            raise RuntimeError(f'Expected pinned JS baseline {BASELINE_COMMIT}, got {revision}')
        subprocess.run(['git', '-C', str(baseline), 'diff', '--quiet', 'HEAD', '--', 'src', 'package.json'], check=True)
        nonce = secrets.token_hex(24)
        identity = generate(candidate, [{'command': 'identity', 'nonce': nonce}])[0]
        if identity.get('nonce') != nonce or identity.get('pid') == os.getpid():
            raise RuntimeError(f'Python subprocess identity check failed: {identity}')
        if not args.installed and not Path(identity['module']).is_relative_to(candidate):
            raise RuntimeError(f'Imported package is outside requested candidate: {identity}')
        report['pythonIdentity'] = identity
        # Smoke remains fixed-condition so all corruption classes are observable.
        smoke = subprocess.check_output([args.node, str(TOOLS / 'oracle.mjs'), str(baseline), '--suite=internal-smoke' if args.suite == 'internal' else '--suite=decode-smoke'], text=True)
        smoke_record = json.loads(smoke)
        report['negativeControls'] = negative_controls(candidate, smoke_record)
        report['stages']['negativeControls'] = 'passed'
        concurrency_cases = []
        public_ordinal = 0
        for suite in (['public', 'internal'] if args.suite == 'all' else [args.suite]):
            print(f'Generating {suite} pinned-reference cases...', flush=True)
            reference = args.output.parent / f'{suite}-reference.jsonl'
            with reference.open('w', encoding='utf-8') as stream:
                subprocess.run([args.node, str(TOOLS / 'oracle.mjs'), str(baseline), '--suite=' + suite], stdout=stream, check=True)
            report['stages'][suite] = 'running'
            first_failure = None
            rows_seen = 0
            with reference.open(encoding='utf-8') as stream:
                while True:
                    records = []
                    for _ in range(48):
                        line = stream.readline()
                        if not line:
                            break
                        records.append(json.loads(line))
                    if not records:
                        break
                    outputs = generate(candidate, [r['request'] for r in records])
                    for record, actual in zip(records, outputs):
                        rows_seen += 1
                        try:
                            compare(record, actual)
                        except AssertionError:
                            first_failure = {'request': record['request'], 'expected': record['expected'], 'actual': actual}
                            failure = args.output.parent / 'first-conformance-failure.json'
                            failure.write_text(json.dumps(first_failure, indent=2, ensure_ascii=True) + '\n')
                            raise
                        command = record['request'].get('command')
                        if command is None:
                            if public_ordinal % 43 == 0 and len(concurrency_cases) < 64:
                                concurrency_cases.append(record)
                            public_ordinal += 1
                        counter = {'raw': 'rawMatrices', 'capacity': 'capacityCases', 'estimate': 'boundaryEstimates',
                                   'structured-append': 'structuredAppendSets', 'rs': 'reedSolomonDegrees'}.get(command, 'publicMatrices')
                        if command == 'gf':
                            report['counts']['gfProducts'] += 65536
                        else:
                            report['counts'][counter] += 1
                        report['counts']['nayukiMatrices'] += int(record.get('independent', False))
                        if command == 'structured-append':
                            report['counts']['structuredAppendMatrices'] += len(actual['symbols'])
            expected_rows = 5450 if suite == 'public' else 4576
            if rows_seen != expected_rows:
                raise AssertionError(f'{suite} reference row count changed or was truncated: {rows_seen} != {expected_rows}')
            expected_counts = ({'publicMatrices': 2868, 'nayukiMatrices': 2240, 'capacityCases': 640,
                                'boundaryEstimates': 1920, 'structuredAppendSets': 22, 'structuredAppendMatrices': 112}
                               if suite == 'public' else {'rawMatrices': 4320, 'gfProducts': 65536, 'reedSolomonDegrees': 255})
            for key, count in expected_counts.items():
                if report['counts'][key] != count:
                    raise AssertionError(f'{suite} coverage count mismatch: {key}={report["counts"][key]} != {count}')
            report['stages'][suite] = 'passed'
            report['stages'][suite + 'ReferenceSha256'] = digest(reference)
            print(f'PASS {suite}: {json.dumps(report["counts"])}', flush=True)
        if args.suite in ('all', 'public'):
            parallel = concurrency_cases * 4
            response = generate(candidate, [{'command': 'concurrency', 'requests': [r['request'] for r in parallel]}])[0]
            if 'results' not in response or len(response['results']) != len(parallel):
                raise AssertionError(f'Concurrent Python execution failed: {response}')
            for record, actual in zip(parallel, response['results']):
                compare(record, actual)
            report['counts']['concurrentMatrices'] = len(parallel)
            report['stages']['concurrency'] = 'passed'
            malformed = [{'text': 'bad%🙂', 'options': opts} for opts in [
                {'version': 0}, {'version': 41}, {'version': True}, {'maskPattern': 8}, {'maskPattern': -2},
                {'maskPattern': True}, {'eci': -2}, {'eci': 1000000}, {'fnc1Second': '?'}, {'fnc1Second': 'AA'},
                {'mode': 'numeric'}, {'mode': 'alphanumeric'}, {'mode': 'kanji'},
                {'gs1': True, 'mode': 'alphanumeric'}, {'errorCorrectionLevel': 'invalid'},
            ]]
            malformed += [{'text': text} for text in ['\ud800', '\udfff', 'A\ud800Z']]
            for request, actual in zip(malformed, generate(candidate, malformed)):
                assert_typed_rejection(actual)
                report['counts']['malformedInputs'] += 1
            report['malformedNegativeControls'] = []
            for fault in ('leak-TypeError', 'leak-OverflowError', 'leak-AssertionError'):
                actual = generate(candidate, [malformed[0]], fault=fault)[0]
                try:
                    assert_typed_rejection(actual)
                except AssertionError:
                    report['malformedNegativeControls'].append({'fault': fault, 'detected': True})
                else:
                    raise AssertionError(f'Builtin error leak silently counted as typed rejection: {fault}')
            report['stages']['malformed'] = 'passed'
        if before != snapshot(candidate, args.suite):
            raise RuntimeError('Candidate sources changed while tests ran; rerun against immutable sources')
        end_identity = generate(candidate, [{'command': 'identity', 'nonce': nonce}])[0]
        if end_identity.get('packageFilesSha256') != identity.get('packageFilesSha256'):
            raise RuntimeError('Imported Python package files changed during conformance run')
        current_tools = tool_snapshot()
        if current_tools != report['testToolSha256']:
            raise RuntimeError('Test tools changed while tests ran; rerun against immutable tools')
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        report['elapsedSeconds'] = round(time.perf_counter() - started, 3)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k not in ('sourceSha256', 'testToolSha256')}, indent=2))


if __name__ == '__main__':
    main()
