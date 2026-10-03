#!/usr/bin/env python3
"""Build, audit and install a wheel in a clean, dependency-free consumer."""
from __future__ import annotations
import ast
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    for path in (ROOT / 'src' / 'specqr').rglob('*.py'):
        tree = ast.parse(path.read_text('utf-8'), filename=path.name)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split('.')[0]]
            for name in names:
                assert name in sys.stdlib_module_names or name == 'specqr', (path.name, name)
    spec = importlib.util.spec_from_file_location('build_backend', ROOT / 'build_backend.py')
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    with tempfile.TemporaryDirectory(prefix='specqr-consumer-') as temporary:
        work = Path(temporary)
        wheel = work / backend.build_wheel(work)
        wheel_again = work / 'repeat'
        wheel_again.mkdir()
        second = wheel_again / backend.build_wheel(wheel_again)
        assert wheel.read_bytes() == second.read_bytes(), 'wheel build must be deterministic'
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            assert 'specqr/py.typed' in names
            assert all(name.startswith(('specqr/', 'specqr_python-')) for name in names)
            metadata = archive.read(next(n for n in names if n.endswith('/METADATA'))).decode()
            assert 'Requires-Dist:' not in metadata
            assert 'Requires-Python: >=3.11' in metadata
            record = next(n for n in names if n.endswith('/RECORD'))
            for name, digest, count in csv.reader(io.StringIO(archive.read(record).decode())):
                if name == record:
                    continue
                data = archive.read(name)
                expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
                assert digest == 'sha256=' + expected and len(data) == int(count)
        environment = work / 'env'
        venv.EnvBuilder(with_pip=True, clear=False).create(environment)
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        command = environment / ('Scripts/specqr.exe' if os.name == 'nt' else 'bin/specqr')
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env['PYTHONNOUSERSITE'] = '1'
        subprocess.run([str(python), '-I', '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)], cwd=work, env=env, check=True, capture_output=True)
        consumer = work / 'consumer.py'
        consumer.write_text('''import importlib.metadata as m, json, pathlib, sys
import specqr
from specqr import generate, Segment, estimate, create_gs1_element_string
assert pathlib.Path(specqr.__file__).is_relative_to(pathlib.Path(sys.prefix))
assert not m.requires('specqr-python')
q = generate('独立した consumer 123', eci=26)
assert q.version >= 1 and q.to_png().startswith(b'\\x89PNG')
assert '<svg ' in q.to_svg() and estimate('ABC').ok
assert specqr.generate_segments([Segment.numeric('123')]).version == 1
assert create_gs1_element_string([{'ai':'10','value':'LOT%7'}]) == '10LOT%7'
assert specqr.__version__ == m.version('specqr-python')
print(json.dumps({'installed':True,'dependencies':m.requires('specqr-python') or [],'version':specqr.__version__}))
''', encoding='utf-8')
        result = subprocess.run([str(python), '-I', str(consumer)], cwd=work, env=env, check=True, capture_output=True, text=True)
        for executable in ([str(command)], [str(python), '-I', '-m', 'specqr']):
            png = subprocess.run([*executable, 'consumer QR', '--format', 'png'], cwd=work, env=env, check=True, capture_output=True).stdout
            assert png.startswith(b'\x89PNG\r\n\x1a\n')
            matrix = subprocess.run([*executable, 'HELLO', '--format', 'matrix'], cwd=work, env=env, check=True, capture_output=True).stdout
            assert len(json.loads(matrix)) == 21
            invalid = subprocess.run([*executable, 'x', '--version', '0'], cwd=work, env=env, capture_output=True)
            assert invalid.returncode == 2
        print(result.stdout.strip())
        print('PASS: stdlib import audit, zero dependency metadata, deterministic wheel, RECORD, isolated installed consumer and both CLIs')


if __name__ == '__main__':
    main()
