"""
Shared test setup.

`make_client(data)` starts the app on its own temporary data JSON and database, so tests
never touch data/40k_data.json or data/combat_sim.db.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from combat_sim.app import create_app  # noqa: E402


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Factory: make_client(data_dict) -> (test client, path of the data JSON the app reads and writes)."""
    def _make(data):
        data_file = tmp_path / 'data.json'
        data_file.write_text(json.dumps(data), encoding='utf-8')
        monkeypatch.setenv('DATA_JSON', str(data_file))
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'test.db').replace('\\', '/'))
        return create_app().test_client(), data_file
    return _make
