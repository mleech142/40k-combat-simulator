"""
Named players on a self-hosted copy (accounts.py): the host, join links, logging in, host-only routes, and writes
from other sites. Outside hosted mode there is no login (tests elsewhere run that way).
"""

from datetime import datetime, timedelta

import pytest

from conftest import welcome
from combat_sim import accounts, paths
from combat_sim.models import User, db

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}
HOST = {'name': 'Hosty', 'email': 'host@example.com', 'password': 'throwaway-1'}


@pytest.fixture
def hosted(make_client, monkeypatch):
    """A fresh self-hosted copy: (client, data file). No players yet."""
    monkeypatch.setenv(paths.HOSTED_ENV, '1')
    accounts._failed.clear()
    return make_client(APP_DATA)


@pytest.fixture
def host(hosted):
    client, _ = hosted
    assert welcome(client, HOST).status_code == 200
    return client


def add_player(host_client, name='Pat'):
    resp = host_client.post('/api/players', json={'name': name, 'email': f'{name.lower()}@example.com'})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def join(client, link, password='another-pw'):
    return client.post('/api/auth/join', json={'token': link.rsplit('/', 1)[1], 'password': password})


def player_client(host_client, name='Pat'):
    """A second browser logged in as a new player."""
    link = add_player(host_client, name)['link']
    other = host_client.application.test_client()
    assert join(other, link).status_code == 200
    return other


def test_a_new_copy_asks_for_its_host_first(hosted):
    client, _ = hosted
    resp = client.get('/')
    assert resp.status_code == 302 and resp.headers['Location'].endswith('/welcome')
    assert client.get('/api/factions').status_code == 401
    page = client.get('/welcome').get_data(as_text=True)
    assert 'throwaway password' in page


def test_the_host_is_created_once(hosted):
    client, _ = hosted
    assert welcome(client, dict(HOST, password='short')).status_code == 400
    assert welcome(client, dict(HOST, email='nope')).status_code == 400
    resp = welcome(client, HOST)
    assert resp.get_json()['is_host'] is True
    assert client.get('/api/auth/me').get_json()['name'] == 'Hosty'
    # Nobody can make themselves host afterwards
    stranger = client.application.test_client()
    assert welcome(stranger, dict(HOST, name='Other')).status_code == 403
    assert stranger.get('/welcome').status_code == 302


def test_passwords_are_stored_hashed(host):
    with host.application.app_context():
        user = User.query.one()
        assert HOST['password'] not in user.password_hash


def test_logging_in_and_out(host):
    browser = host.application.test_client()
    assert browser.get('/armies').status_code == 302
    assert browser.post('/api/auth/login', json={'name': 'hosty', 'password': 'wrong-password'}).status_code == 401
    assert browser.post('/api/auth/login', json={'name': 'hosty', 'password': HOST['password']}).status_code == 200
    assert browser.get('/armies').status_code == 200
    browser.post('/api/auth/logout')
    assert browser.get('/api/armies').status_code == 401


def test_wrong_passwords_wait_after_a_few_tries(host):
    browser = host.application.test_client()
    for _ in range(accounts.LOGIN_TRIES):
        assert browser.post('/api/auth/login', json={'name': 'Hosty', 'password': 'bad-password'}).status_code == 401
    assert browser.post('/api/auth/login', json={'name': 'Hosty', 'password': HOST['password']}).status_code == 429


def test_a_player_joins_with_a_link_and_chooses_their_password(host):
    player = add_player(host)
    assert player['joined'] is False and player['link'].startswith('/join/')
    browser = host.application.test_client()
    page = browser.get(player['link']).get_data(as_text=True)
    assert 'Welcome, Pat' in page and 'throwaway password' in page
    assert join(browser, player['link'], 'short').status_code == 400
    assert join(browser, player['link']).status_code == 200
    me = browser.get('/api/auth/me').get_json()
    assert me['name'] == 'Pat' and me['is_host'] is False and me['joined'] is True
    # The link works once
    assert join(host.application.test_client(), player['link']).status_code == 403
    assert 'Link not valid' in host.application.test_client().get(player['link']).get_data(as_text=True)


def test_join_links_expire(host):
    player = add_player(host)
    with host.application.app_context():
        user = User.query.filter_by(name_key='pat').one()
        user.join_expires = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert join(host.application.test_client(), player['link']).status_code == 403


def test_names_are_unique_ignoring_case(host):
    add_player(host, 'Pat')
    assert host.post('/api/players', json={'name': 'PAT', 'email': 'x@example.com'}).status_code == 400
    assert host.post('/api/players', json={'name': '<b>', 'email': 'x@example.com'}).status_code == 400


def test_a_new_link_resets_a_forgotten_password_and_logs_out_old_logins(host):
    player = player_client(host)
    pat = next(p for p in host.get('/api/players').get_json() if p['name'] == 'Pat')
    link = host.post(f"/api/players/{pat['id']}/link").get_json()['link']
    # Their old password works until the new link is used
    assert player.get('/api/armies').status_code == 200
    assert join(host.application.test_client(), link, 'brand-new-pw').status_code == 200
    assert player.get('/api/armies').status_code == 401


def test_changing_your_password_needs_the_current_one(host):
    assert host.post('/api/auth/account', json={'password': 'wrong-pw', 'new_password': 'newer-pw-1'}).status_code == 403
    assert host.post('/api/auth/account', json={'password': HOST['password'], 'new_password': 'newer-pw-1'}).status_code == 200
    assert host.get('/api/auth/me').status_code == 200  # still logged in here
    assert host.post('/api/auth/account', json={'email': 'new@example.com'}).get_json()['email'] == 'new@example.com'
    browser = host.application.test_client()
    assert browser.post('/api/auth/login', json={'name': 'Hosty', 'password': 'newer-pw-1'}).status_code == 200


def test_players_cant_do_the_hosts_jobs(host):
    player = player_client(host)
    assert player.get('/setup').status_code == 403
    assert player.get('/players').status_code == 403
    assert player.get('/api/players').status_code == 403
    assert player.post('/api/players', json={'name': 'Sneaky', 'email': 's@example.com'}).status_code == 403
    assert player.post('/api/bsdata/reimport').status_code == 403
    assert player.post('/api/wahapedia/download').status_code == 403
    assert player.post('/api/play/relay', json={'name': 'x'}).status_code == 403
    assert player.post('/api/data/import', json=APP_DATA).status_code == 403
    assert player.post('/api/data/factions', json={'name': 'Mine'}).status_code == 202  # a proposal for the host
    # Reading the data and using the app is fine
    assert player.get('/api/data/status').status_code == 200
    assert player.get('/data').status_code == 200
    page = player.get('/armies').get_data(as_text=True)
    assert 'href="/setup"' not in page and 'href="/players"' not in page and '>' + ' Pat' in page


def test_the_host_cant_be_removed_but_players_can(host):
    player = player_client(host)
    everyone = {p['name']: p['id'] for p in host.get('/api/players').get_json()}
    assert host.delete(f"/api/players/{everyone['Hosty']}").status_code == 403
    assert host.delete(f"/api/players/{everyone['Pat']}").status_code == 200
    assert player.get('/api/armies').status_code == 401


def test_writes_from_other_sites_are_refused(host):
    other_site = {'Origin': 'https://evil.example', 'Content-Type': 'application/json'}
    assert host.post('/api/armies', data='{}', headers=other_site).status_code == 403
    assert host.post('/api/auth/logout', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    same_site = {'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'}
    assert host.post('/api/auth/logout', headers=same_site).status_code == 200


def test_logins_survive_a_restart(hosted, make_client):
    client, data_file = hosted
    key = (data_file.parent / accounts.SECRET_KEY_FILE)
    welcome(client, HOST)
    assert key.exists()
    assert client.application.config['SECRET_KEY'] == key.read_text(encoding='utf-8').strip()


def test_no_login_on_your_own_computer(make_client, monkeypatch):
    monkeypatch.setenv(paths.HOSTED_ENV, '')
    client, _ = make_client(APP_DATA)
    assert client.get('/api/armies').status_code == 200
    assert client.get('/welcome').status_code == 404
    assert client.post('/api/data/factions', json={'name': 'Mine'}).status_code != 403


def test_each_player_chooses_whether_play_is_in_their_menu(host):
    player = player_client(host)
    assert 'href="/play"' not in player.get('/armies').get_data(as_text=True)
    assert player.post('/api/play/setting', json={'enabled': True}).status_code == 200
    assert 'href="/play"' in player.get('/armies').get_data(as_text=True)
    assert 'href="/play"' not in host.get('/armies').get_data(as_text=True)  # The host's menu is the host's
    assert 'id="play-setting"' in player.get('/account').get_data(as_text=True)
