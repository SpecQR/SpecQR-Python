#!/usr/bin/env python3
"""Source-bound TS positives and exact published-Python preservation, no dependencies."""
import argparse
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIX = HERE / 'fixtures'
RESTORED = {1460, 1461, 1465}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('non-finite JSON number')
        return result
    def invalid(value):
        raise ValueError('non-JSON numeric constant')
    return json.loads(data, object_pairs_hook=pairs, parse_float=floating,
                      parse_constant=invalid)


def same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def contract(value):
    if isinstance(value, list):
        return [contract(x) for x in value]
    if isinstance(value, dict):
        if 'code' in value and 'message' in value:
            return {k: contract(value[k]) for k in ('code', 'reason', 'count') if value.get(k) is not None}
        return {k: contract(x) for k, x in value.items() if x is not None and k != 'isVariable'
                and not (k == 'errors' and value.get('ok'))}
    return value


def source():
    excluded = {'.git', '.tools', '.baseline', '__pycache__', 'node_modules', 'artifacts', 'build', 'dist', '.venv'}
    result = {}
    for path in ROOT.rglob('*'):
        if any(part in excluded for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file():
            if path.is_symlink():
                raise ValueError('unexpected source symlink')
            result[path.relative_to(ROOT).as_posix()] = sha(path.read_bytes())
    return result


def process(code, stderr, stdout, count):
    if code != 0 or stderr or not stdout.endswith(b'\n'):
        raise ValueError('exit/stderr/newline contract')
    rows = [strict(line) for line in stdout.splitlines()]
    if len(rows) != count:
        raise ValueError('cardinality contract')
    return rows


def negative_controls():
    checked = []
    def rejects(label, fn):
        try:
            fn()
        except (ValueError, AssertionError):
            checked.append(label)
        else:
            raise AssertionError('control accepted: ' + label)
    for label, data in [('duplicate', b'{"a":1,"a":2}'), ('NaN', b'NaN'), ('Infinity', b'Infinity'), ('overflow', b'1e999')]:
        rejects(label, lambda data=data: strict(data))
    for label, code, stderr, stdout, count in [('exit', 1, b'', b'1\n', 1), ('stderr', 0, b'warning', b'1\n', 1),
          ('missing', 0, b'', b'1\n', 2), ('extra', 0, b'', b'1\n2\n', 1), ('newline', 0, b'', b'1', 1)]:
        rejects(label, lambda c=code, e=stderr, o=stdout, n=count: process(c, e, o, n))
    for label, a, b in [('bool-integer', True, 1), ('integer-float', 1, 1.0), ('text-number', '1', 1),
                       ('container', [], {}), ('extra-field', {'ok': True}, {'ok': True, 'x': 1}),
                       ('payload', 'a%5Eb', 'a^b'), ('count', {'count': 1}, {'count': 2})]:
        if same(a, b):
            raise AssertionError(label)
        checked.append(label)
    return checked


def expectations():
    manifest = strict((FIX / 'manifest.json').read_bytes())
    assert manifest['referenceCommit'] == '16efc6c0a8e397c9df3d051d20fce6c1eebdfad7'
    assert manifest['referenceTree'] == 'ca4c2360a7dc0950c1bfd1f76e44616bcd406023'
    assert manifest['baselineCommit'] == '69bc9d3257f7e86d5b07328437f4da9f95b4c843'
    assert manifest['baselineTree'] == 'c7dabc3da52c4d1304737d58ff5f783558a5b845'
    for name, digest in manifest['sha256'].items():
        assert sha((FIX / name).read_bytes()) == digest, name
    load = lambda name: strict(gzip.decompress((FIX/name).read_bytes()) if name.endswith('.gz') else (FIX/name).read_bytes())
    rows = load('contracts.json.gz'); native = load('published-python1499.json.gz')
    historical = load('gs1-upstream.json')['cases']; current = load('current-ts-gs1-1411.json.gz')['cases']
    shared = load('current-ts-gs1-shared49.json')['cases']; positives = load('approved-restorations80.json')['cases']
    assert len(rows) == len(native['results']) == 1499 and len(historical) == len(current) == 1411
    assert len(shared) == 49 and len(positives) == 80
    assert native['commit'] == manifest['baselineCommit'] and native['tree'] == manifest['baselineTree']
    assert sha((ROOT/'tests/test_gs1.py').read_bytes()) == native['adapterSha256']
    for i, original in enumerate(historical):
        request = {k:v for k,v in original.items() if k != 'expected'}
        assert same(rows[i]['request'], request) and same(current[i]['request'], request)
        assert same(rows[i]['tsExpected'], current[i]['expected'])
    for i, row in enumerate(shared):
        assert same(rows[1411+i]['request'], row['request']) and same(rows[1411+i]['tsExpected'], row['expected'])
    requests = [row['request'] for row in rows]
    assert sha(json.dumps(requests, sort_keys=True, separators=(',', ':')).encode()) == native['requestsSha256']
    differences = {i for i, (row, old) in enumerate(zip(rows, native['results'])) if not same(contract(row['tsExpected']), contract(old))}
    assert differences == RESTORED | {1498}
    assert rows[1498]['request'] == {'op':'linkNormalize','input':'https://faß.de/01/04912345678904'}
    assert native['results'][1498] == 'https://fass.de/01/04912345678904'
    expected = [row['tsExpected'] if i in RESTORED else old for i, (row, old) in enumerate(zip(rows, native['results']))]
    return requests, expected, rows, positives, manifest


def verify(output, python=sys.executable, package_path=None):
    output = Path(output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    before = source(); requests, expected, rows, positives, manifest = expectations()
    payload = b''.join((json.dumps(r, ensure_ascii=True, separators=(',', ':'))+'\n').encode() for r in requests)
    command = [str(python), str(HERE/'adapter.py'), str(package_path or ROOT/'src')]
    result = subprocess.run(command, input=payload, capture_output=True, timeout=300,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONUTF8':'1'})
    output.with_suffix('.stdout.jsonl').write_bytes(result.stdout)
    output.with_suffix('.stderr').write_bytes(result.stderr)
    actual = process(result.returncode, result.stderr, result.stdout, 1499)
    mismatches = [i for i, (want, got) in enumerate(zip(expected, actual)) if not same(want, got)]
    assert not mismatches, mismatches
    for row in positives:
        i = row['caseId']; assert same(row['request'], requests[i]) and same(row['expected'], rows[i]['tsExpected'])
        assert same(contract(actual[i]), contract(row['expected'])), i
    controls = negative_controls(); after = source(); assert before == after, 'source changed during execution'
    report = {'status':'passed','cases':1499,'original1411':1411,'shared49':49,'extra39':39,'positive80':80,
              'restoredOutputIds':sorted(RESTORED),'intentionalNativeIdnaIds':[1498],
              'exactNativePreservationRows':1496,'referenceCommit':manifest['referenceCommit'],
              'baselineCommit':manifest['baselineCommit'],'sourceFiles':before,
              'sourceSha256':sha(json.dumps(before,sort_keys=True,separators=(',',':')).encode()),'sourceStable':True,
              'argv':command,'exitCode':result.returncode,'stderrBytes':len(result.stderr),'responseCount':len(actual),
              'inputSha256':sha(payload),'stdoutSha256':sha(result.stdout),'negativeControls':controls}
    output.write_text(json.dumps(report,indent=2)+'\n'); return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/url-serialization.json')
    parser.add_argument('--python',default=sys.executable)
    parser.add_argument('--package-path',type=Path)
    args=parser.parse_args(); report=verify(args.output,args.python,args.package_path)
    print(json.dumps({k:v for k,v in report.items() if k not in ('sourceFiles','argv')}))


if __name__ == '__main__':
    main()
