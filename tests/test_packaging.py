"""
Running as the packaged exe: where data lives, first-run seeding, and the port fallback in run.py.
"Frozen" is simulated by setting sys.frozen / sys._MEIPASS the way PyInstaller does.
"""

import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

from combat_sim import paths

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A fake PyInstaller bundle holding a shipped data JSON, and an empty per-user folder."""
    bundle = tmp_path / 'bundle'
    (bundle / 'data').mkdir(parents=True)
    (bundle / 'data' / paths.DATA_FILE).write_text('{"shipped": true}', encoding='utf-8')
    user = tmp_path / 'user'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle), raising=False)
    monkeypatch.setattr(paths, 'user_data_dir', lambda: user)
    return bundle, user


def test_from_source_uses_the_repository_data_folder():
    assert not paths.is_frozen()
    assert paths.data_dir() == ROOT / 'data'


def test_frozen_seeds_the_user_folder_on_first_run(frozen):
    bundle, user = frozen
    assert paths.prepare_data_dir() == user
    assert json.loads((user / paths.DATA_FILE).read_text(encoding='utf-8')) == {'shipped': True}


def test_frozen_never_overwrites_the_users_data(frozen):
    bundle, user = frozen
    user.mkdir()
    (user / paths.DATA_FILE).write_text('{"edited": true}', encoding='utf-8')
    paths.prepare_data_dir()
    assert json.loads((user / paths.DATA_FILE).read_text(encoding='utf-8')) == {'edited': True}


def test_version_1_data_and_database_are_archived_once(frozen):
    bundle, user = frozen
    user.mkdir()
    (user / paths.V1_DATA_FILE).write_text('{"v1": true}', encoding='utf-8')
    (user / paths.DATABASE_FILE).write_bytes(b'old scenarios')
    paths.prepare_data_dir()
    archive = user / paths.V1_ARCHIVE_FOLDER
    assert json.loads((archive / paths.V1_DATA_FILE).read_text(encoding='utf-8')) == {'v1': True}
    assert (archive / paths.DATABASE_FILE).read_bytes() == b'old scenarios'
    assert not (user / paths.V1_DATA_FILE).exists() and not (user / paths.DATABASE_FILE).exists()
    assert json.loads((user / paths.DATA_FILE).read_text(encoding='utf-8')) == {'shipped': True}

    (user / paths.DATABASE_FILE).write_bytes(b'new scenarios')  # Later starts leave the new database alone
    paths.prepare_data_dir()
    assert (user / paths.DATABASE_FILE).read_bytes() == b'new scenarios'


EMPTY_DATA = {'schema_version': 1, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [],
              'melee_weapons': [], 'faction_rules': [], 'stratagems': [], 'keywords': [], 'target_profiles': []}


def test_app_defaults_to_the_prepared_data_folder(frozen, monkeypatch):
    from combat_sim import __version__
    from combat_sim.app import create_app
    bundle, user = frozen
    (bundle / 'data' / paths.DATA_FILE).write_text(json.dumps(EMPTY_DATA), encoding='utf-8')
    monkeypatch.delenv('DATA_JSON', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)

    app = create_app()
    assert app.config['DATA_JSON'] == str(user / paths.DATA_FILE)
    assert app.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite:///' + (user / paths.DATABASE_FILE).as_posix()
    assert f'v{__version__}' in app.test_client().get('/', follow_redirects=True).get_data(as_text=True)


@pytest.fixture(scope='module')
def run_module():
    spec = importlib.util.spec_from_file_location('run', ROOT / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pick_port_prefers_the_requested_port(run_module):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        free = sock.getsockname()[1]
    assert run_module.pick_port(preferred=free) == free


def test_pick_port_falls_back_when_busy(run_module):
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0))
        busy.listen()
        taken = busy.getsockname()[1]
        port = run_module.pick_port(preferred=taken)
    assert port != taken and port > 0
