"""
The Setup page: choosing the BSData folder, "Download latest" (GitHub faked), re-importing,
skipping setup, the settings file and the data version in the footer.
"""

import io
import json
import shutil
import urllib.error
import zipfile

import pytest

from combat_sim import bsdata_download, folder_dialog
from combat_sim.app import create_app
from combat_sim.settings import load_settings, update_settings
from conftest import BSDATA_FIXTURES

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [],
            'melee_weapons': [], 'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [],
            'target_profiles': []}
SHA = 'abcdef1234567890abcdef1234567890abcdef12'


def zip_of(files):
    """A GitHub-style zipball: {path inside the archive: bytes}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def fixture_zip(extra=None):
    files = {f'BSData-wh40k-11e-abcdef1/{p.name}': p.read_bytes() for p in BSDATA_FIXTURES.glob('*.json')}
    files.update(extra or {})
    return zip_of(files)


@pytest.fixture
def setup(make_client, tmp_path):
    """App with no BSData yet; returns (client, data folder)."""
    client, data_file = make_client(APP_DATA)
    return client, data_file.parent


@pytest.fixture
def fake_github(monkeypatch):
    calls = {'zip': fixture_zip()}
    monkeypatch.setattr(bsdata_download, 'latest_commit', lambda: {'sha': SHA, 'date': '2026-10-19T01:14:55Z'})
    monkeypatch.setattr(bsdata_download, 'download_zip', lambda sha: calls['zip'])
    return calls


def status(client):
    return client.get('/api/data/status').get_json()


# --- Settings -----------------------------------------------------------------------------

def test_settings_file_round_trip(tmp_path):
    path = tmp_path / 'settings.json'
    assert load_settings(path) == {}
    update_settings(path, bsdata_dir='C:/x', setup_skipped=True)
    assert load_settings(path) == {'bsdata_dir': 'C:/x', 'setup_skipped': True}
    update_settings(path, bsdata_dir=None)
    assert load_settings(path) == {'setup_skipped': True}
    path.write_text('not json', encoding='utf-8')
    assert load_settings(path) == {}


# --- First run --------------------------------------------------------------------------------

def test_simulator_sends_you_to_setup_until_bsdata_loads(setup):
    client, _ = setup
    assert client.get('/').headers['Location'].endswith('/setup')
    assert 'Download latest' in client.get('/setup').get_data(as_text=True)


def test_skipping_setup(setup):
    client, folder = setup
    assert client.post('/api/bsdata/skip-setup').status_code == 200
    assert client.get('/').status_code == 200
    assert load_settings(folder / 'settings.json')['setup_skipped'] is True


def test_footer_credits_bsdata_and_offers_setup(setup):
    client, _ = setup
    page = client.get('/data').get_data(as_text=True)
    assert 'not endorsed by Games Workshop' in page
    assert 'Set up</a>' in page


# --- Choosing a folder ---------------------------------------------------------------------------

def test_use_a_folder(setup):
    client, folder = setup
    result = client.post('/api/bsdata/folder', json={'path': str(BSDATA_FIXTURES)}).get_json()
    assert result['loaded'] and result['factions'] == 3
    assert result['label'] == 'game system revision 7'  # A folder of your own: its revision, not a commit
    assert load_settings(folder / 'settings.json')['bsdata_dir'] == str(BSDATA_FIXTURES.resolve())
    assert client.get('/').status_code == 200
    assert 'game system revision 7' in client.get('/data').get_data(as_text=True)

    restarted = create_app().test_client()  # The choice is remembered
    assert status(restarted)['folder'] == str(BSDATA_FIXTURES.resolve())


def test_folder_must_exist_and_hold_catalogues(setup, tmp_path):
    client, _ = setup
    response = client.post('/api/bsdata/folder', json={'path': str(tmp_path / 'nope')})
    assert response.status_code == 400 and 'is not a folder' in response.get_json()['error']
    empty = tmp_path / 'empty'
    empty.mkdir()
    response = client.post('/api/bsdata/folder', json={'path': str(empty)})
    assert response.status_code == 400 and 'holds no .json files' in response.get_json()['error']


def test_unreadable_folder_keeps_the_one_that_worked(setup, tmp_path):
    client, folder = setup
    client.post('/api/bsdata/folder', json={'path': str(BSDATA_FIXTURES)})
    broken = tmp_path / 'broken'
    broken.mkdir()
    (broken / 'Broken.json').write_text('{oops', encoding='utf-8')
    response = client.post('/api/bsdata/folder', json={'path': str(broken)})
    assert response.status_code == 400 and 'Broken.json' in response.get_json()['error']
    assert status(client)['loaded'] and status(client)['folder'] == str(BSDATA_FIXTURES.resolve())
    assert load_settings(folder / 'settings.json')['bsdata_dir'] == str(BSDATA_FIXTURES.resolve())


def test_back_to_the_default_folder(setup):
    client, folder = setup
    client.post('/api/bsdata/folder', json={'path': str(BSDATA_FIXTURES)})
    result = client.post('/api/bsdata/folder', json={'path': None}).get_json()
    assert result['loaded'] is False
    assert 'bsdata_dir' not in load_settings(folder / 'settings.json')


def test_browse_returns_the_chosen_folder(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(folder_dialog, 'choose_folder', lambda initial=None: 'D:/BSData')
    assert client.post('/api/bsdata/browse').get_json() == {'path': 'D:/BSData'}
    monkeypatch.setattr(folder_dialog, 'choose_folder', lambda initial=None: None)
    assert client.post('/api/bsdata/browse').get_json() == {'path': None}


# --- Download latest ---------------------------------------------------------------------------------

def test_download_latest(setup, fake_github):
    client, folder = setup
    result = client.post('/api/bsdata/download').get_json()
    assert result['loaded'] and result['factions'] == 3
    assert result['folder'] == str(folder / 'bsdata')
    assert result['label'] == '@ abcdef1, 2026-10-19'
    settings = load_settings(folder / 'settings.json')
    assert settings['download']['sha'] == SHA and settings['download']['files'] == 6
    assert '@ abcdef1, 2026-10-19' in client.get('/data').get_data(as_text=True)


def test_failed_download_keeps_the_current_data(setup, monkeypatch):
    client, folder = setup
    client.post('/api/bsdata/folder', json={'path': str(BSDATA_FIXTURES)})

    def offline():
        raise bsdata_download.DownloadError('Could not reach GitHub: offline')
    monkeypatch.setattr(bsdata_download, 'latest_commit', offline)
    response = client.post('/api/bsdata/download')
    assert response.status_code == 502 and 'offline' in response.get_json()['error']
    assert status(client)['loaded'] and status(client)['folder'] == str(BSDATA_FIXTURES.resolve())


def test_download_replaces_the_previous_download(setup, fake_github):
    client, folder = setup
    client.post('/api/bsdata/download')
    (folder / 'bsdata' / 'Stale.json').write_text('{}', encoding='utf-8')
    fake_github['zip'] = fixture_zip()
    assert client.post('/api/bsdata/download').status_code == 200
    assert not (folder / 'bsdata' / 'Stale.json').exists()
    assert not (folder / 'bsdata.old').exists()


# --- Installing a zip safely ------------------------------------------------------------------------

def test_install_zip_keeps_only_top_level_json(tmp_path):
    target = tmp_path / 'bsdata'
    count = bsdata_download.install_zip(zip_of({
        'repo-sha/A.json': b'{"a": 1}',
        'repo-sha/README.md': b'readme',
        'repo-sha/.github/workflow.json': b'{}',
        'repo-sha/../../evil.json': b'{}',
        'top.json': b'{}',
    }), target)
    assert count == 1
    assert sorted(p.name for p in target.iterdir()) == ['A.json']
    assert not (tmp_path / 'evil.json').exists()


def test_install_zip_failures_leave_the_folder_alone(tmp_path):
    target = tmp_path / 'bsdata'
    target.mkdir()
    (target / 'Old.json').write_text('{}', encoding='utf-8')
    with pytest.raises(bsdata_download.DownloadError, match='no catalogue'):
        bsdata_download.install_zip(zip_of({'repo-sha/README.md': b'x'}), target)
    with pytest.raises(bsdata_download.DownloadError, match='not a zip'):
        bsdata_download.install_zip(b'not a zip', target)
    assert [p.name for p in target.iterdir()] == ['Old.json']
    assert [p.name for p in tmp_path.iterdir()] == ['bsdata']  # No staging folders left behind


def test_latest_commit_reads_githubs_answer(monkeypatch):
    answer = json.dumps({'sha': SHA, 'commit': {'committer': {'date': '2026-10-19T01:14:55Z'}}}).encode()
    monkeypatch.setattr(bsdata_download, '_get', lambda url: answer)
    assert bsdata_download.latest_commit() == {'sha': SHA, 'date': '2026-10-19T01:14:55Z'}
    monkeypatch.setattr(bsdata_download, '_get', lambda url: b'{"message": "rate limited"}')
    with pytest.raises(bsdata_download.DownloadError, match='unexpected answer'):
        bsdata_download.latest_commit()


def test_network_errors_become_download_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError('no route')
    monkeypatch.setattr(bsdata_download.urllib.request, 'urlopen', fail)
    with pytest.raises(bsdata_download.DownloadError, match='Could not reach GitHub'):
        bsdata_download.download_zip(SHA)


# --- Re-import -------------------------------------------------------------------------------------

def test_reimport_picks_up_changed_files(setup, tmp_path):
    client, _ = setup
    folder = tmp_path / 'mine'
    shutil.copytree(BSDATA_FIXTURES, folder)
    client.post('/api/bsdata/folder', json={'path': str(folder)})
    path = folder / 'Library - Neutral Forces.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    content['catalogue']['revision'] = 42
    path.write_text(json.dumps(content), encoding='utf-8')
    result = client.post('/api/bsdata/reimport').get_json()
    assert result['from_cache'] is False
    assert result['source']['catalogues']['Library - Neutral Forces'] == 42
