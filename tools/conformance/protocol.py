"""Test-only process bridge: real Python child processes, strict response counts."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PYTHON = sys.executable
INSTALLED = False
DRIVER = Path(__file__).with_name('python_driver.py')


def configure(python=sys.executable, installed=False):
    global PYTHON, INSTALLED
    PYTHON, INSTALLED = python, installed


def generate(candidate, requests, fault=None):
    command = [PYTHON, '-I', str(DRIVER)]
    if not INSTALLED:
        root = Path(candidate).resolve()
        command += ['--source-root', str(root / 'src' if (root / 'src' / 'specqr').is_dir() else root)]
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('SPECQR_TEST_FAULT', None)
    if fault:
        env['SPECQR_TEST_FAULT'] = fault
    process = subprocess.run(command, input=''.join(json.dumps(r, ensure_ascii=True) + '\n' for r in requests),
                             capture_output=True, text=True, encoding='utf-8', env=env, timeout=900)
    if process.returncode:
        raise RuntimeError(f'Python candidate process failed ({process.returncode}): {process.stderr[-4000:]}')
    results = [json.loads(line) for line in process.stdout.splitlines()]
    if len(results) != len(requests):
        raise RuntimeError(f'Python response count mismatch: {len(results)} != {len(requests)}')
    return results


def matrix_hash(matrix):
    return hashlib.sha256(''.join(matrix).encode('ascii')).hexdigest()
