"""
Shared test setup.

`make_client(data)` starts the app on its own temporary data JSON and database, so tests
never touch data/app_data.json, data/combat_sim.db, data/bsdata or data/wahapedia. Pass
`bsdata=<folder>` to load BSData catalogues too (tests/fixtures/bsdata holds a small made-up
set) and `wahapedia=<folder>` for a Wahapedia export (tests/fixtures/wahapedia); without them
the app runs on the data JSON alone.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from combat_sim.app import create_app  # noqa: E402

BSDATA_FIXTURES = ROOT / 'tests' / 'fixtures' / 'bsdata'
WAHAPEDIA_FIXTURES = ROOT / 'tests' / 'fixtures' / 'wahapedia'


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Factory: make_client(data_dict, bsdata=None, wahapedia=None) -> (test client, path of the data JSON the app reads and writes)."""
    def _make(data, bsdata=None, wahapedia=None):
        data_file = tmp_path / 'data.json'
        data_file.write_text(json.dumps(data), encoding='utf-8')
        monkeypatch.setenv('DATA_JSON', str(data_file))
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'test.db').replace('\\', '/'))
        monkeypatch.setenv('BSDATA_DIR', str(bsdata or tmp_path / 'no-bsdata'))
        monkeypatch.setenv('WAHAPEDIA_DIR', str(wahapedia or tmp_path / 'no-wahapedia'))
        return create_app().test_client(), data_file
    return _make
