"""
Runs the JavaScript unit tests (tests/js, Node's built-in test runner) as part of pytest,
so `python -m pytest tests` checks both languages. Skipped when Node isn't installed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which('node')


@pytest.mark.skipif(NODE is None, reason='Node.js not installed')
def test_javascript_unit_tests():
    result = subprocess.run([NODE, '--test', 'tests/js/*.test.js'], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
