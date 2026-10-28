"""
Self-hosting (docs/self-hosting.md): the data folder from COMBAT_SIM_DATA, hosted mode's refusals, run.py's
hosted options and what goes into the Docker image.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from conftest import welcome
from combat_sim import paths

ROOT = Path(__file__).resolve().parent.parent
APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}
HOST = {'name': 'Hosty', 'email': 'host@example.com', 'password': 'throwaway-1'}


@pytest.fixture
def hosted_client(make_client, monkeypatch):
    monkeypatch.setenv(paths.HOSTED_ENV, '1')
    client, _ = make_client(APP_DATA)
    assert welcome(client, HOST).status_code == 200  # logged in as the host
    return client


@pytest.fixture
def run_module(monkeypatch):
    monkeypatch.setenv(paths.HOSTED_ENV, '')  # run.main() may set it; restored afterwards
    spec = importlib.util.spec_from_file_location('run', ROOT / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_data_folder_can_be_named(tmp_path, monkeypatch):
    folder = tmp_path / 'hosted-data'
    monkeypatch.setenv(paths.DATA_ENV, str(folder))
    assert paths.data_dir() == folder
    assert paths.prepare_data_dir() == folder
    # Seeded with the shipped app data JSON, as the packaged app's folder is
    assert (folder / paths.DATA_FILE).read_bytes() == (ROOT / 'data' / paths.DATA_FILE).read_bytes()


@pytest.mark.parametrize('value, hosted', [('1', True), ('true', True), ('ON', True),
                                           ('', False), ('0', False), ('no', False)])
def test_hosted_mode_is_read_from_the_environment(monkeypatch, value, hosted):
    monkeypatch.setenv(paths.HOSTED_ENV, value)
    assert paths.is_hosted() is hosted


def test_hosted_mode_refuses_the_folder_dialog_and_folders_by_path(hosted_client, tmp_path):
    assert hosted_client.post('/api/bsdata/browse').status_code == 403
    resp = hosted_client.post('/api/bsdata/folder', json={'path': str(tmp_path)})
    assert resp.status_code == 403
    assert 'hosted mode' in resp.get_json()['error']
    # Going back to the default (downloaded) folder is still allowed
    assert hosted_client.post('/api/bsdata/folder', json={'path': None}).status_code != 403
    assert hosted_client.get('/api/data/status').get_json()['hosted'] is True


def test_hosted_mode_refuses_changing_the_relay(hosted_client):
    assert hosted_client.post('/api/play/relay', json={'url': 'https://elsewhere.example'}).status_code == 403
    resp = hosted_client.post('/api/play/relay', json={'name': 'Sam'})
    assert resp.status_code == 200
    assert resp.get_json()['name'] == 'Hosty'  # on a self-hosted copy, the name you play under is your account's


def test_the_setup_page_hides_what_hosted_mode_refuses(hosted_client, make_client, monkeypatch):
    page = hosted_client.get('/setup').get_data(as_text=True)
    assert 'id="folder-input"' not in page
    monkeypatch.setenv(paths.HOSTED_ENV, '')
    page = make_client(APP_DATA)[0].get('/setup').get_data(as_text=True)
    assert 'id="folder-input"' in page


def test_on_your_own_computer_nothing_is_refused(make_client, tmp_path):
    client, _ = make_client(APP_DATA)
    assert client.get('/api/data/status').get_json()['hosted'] is False
    # Refused only because it isn't a folder of catalogues, not by hosted mode
    assert client.post('/api/bsdata/folder', json={'path': str(tmp_path / 'missing')}).status_code == 400
    assert client.post('/api/play/relay', json={'url': 'https://elsewhere.example'}).status_code == 200


def test_hosted_mode_refuses_the_debugger(run_module):
    with pytest.raises(SystemExit):
        run_module.main(['--hosted', '--debug'])


def test_hosted_mode_listens_everywhere_on_the_exact_port(run_module, monkeypatch):
    served = {}

    class FakeApp:
        config = {'DATA_JSON': 'x', 'BSDATA_DIR': 'y'}

    monkeypatch.setattr(run_module, 'create_app', lambda: FakeApp)
    monkeypatch.setattr(run_module, 'pick_port', lambda *a, **k: pytest.fail('hosted mode must not pick a port'))
    monkeypatch.setattr(run_module.webbrowser, 'open', lambda url: pytest.fail('hosted mode opens no browser'))
    monkeypatch.setitem(sys.modules, 'waitress', type(sys)('waitress'))
    sys.modules['waitress'].serve = lambda app, host, port, threads: served.update(host=host, port=port)
    assert run_module.main(['--hosted', '--port', '5123']) == 0
    assert served == {'host': '0.0.0.0', 'port': 5123}


def test_the_docker_image_holds_no_game_data():
    """Only the app and the shipped app data JSON go in: never BSData or Wahapedia files, or a database."""
    ignore = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    rules = [line for line in ignore if line and not line.startswith('#')]
    assert rules[0] == '*'  # everything is left out but what's named
    allowed = {line[1:] for line in ignore if line.startswith('!')}
    assert allowed == {'run.py', 'requirements.txt', 'src/', 'data/app_data.json'}
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'COMBAT_SIM_HOSTED=1' in dockerfile and 'COMBAT_SIM_DATA=/data' in dockerfile
    assert 'USER app' in dockerfile
