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
    # The files listed here, not a glob: Node only expands globs itself from version 21
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'tests' / 'js').glob('*.test.js'))
    assert files, 'No JavaScript tests found in tests/js'
    result = subprocess.run([NODE, '--test', *files], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]


@pytest.mark.skipif(NODE is None, reason='Node.js not installed')
def test_relay_unit_tests():
    """The Play relay's rules (relay/src/relay.js), which run on Cloudflare (docs/relay.md)."""
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'relay' / 'test').glob('*.test.mjs'))
    assert files, 'No relay tests found in relay/test'
    result = subprocess.run([NODE, '--test', *files], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
