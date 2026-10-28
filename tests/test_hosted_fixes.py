"""
Fixes from the October 2026 architecture review of the self-hosted copy: player IDs never reused, the reference
tables rebuilt in one transaction, one change at a time per game, idle polls without a replay, the setup code,
the login throttle per device, whole-segment path rules, proposals' diff and failures, Data-tab field errors,
server paths kept from players, the shipped compose file, and reverse durability with my own Target rules.
"""

import json
import threading
import time
from pathlib import Path

import pytest

from combat_sim import accounts, data_store, mail, paths
from combat_sim.play import routes as play_routes
from conftest import BSDATA_FIXTURES, welcome

ROOT = Path(__file__).resolve().parent.parent
APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}
HOST = {'name': 'Hosty', 'email': 'h@example.com', 'password': 'throwaway-1'}
ROW = [(10 + 1.5 * i, 6) for i in range(5)]


@pytest.fixture
def hosted(make_client, monkeypatch):
    monkeypatch.setenv(paths.HOSTED_ENV, '1')
    accounts._failed.clear()
    mail._seen.clear()
    mail._sent.clear()
    client, data_file = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)
    return client, data_file


@pytest.fixture
def host(hosted):
    client, _ = hosted
    assert welcome(client, HOST).status_code == 200
    return client


def add_player(host, name):
    link = host.post('/api/players', json={'name': name, 'email': f'{name.lower()}@example.com'}).get_json()['link']
    browser = host.application.test_client()
    assert browser.post('/api/auth/join', json={'token': link.rsplit('/', 1)[1], 'password': 'throwaway-2'}).status_code == 200
    return browser


# --- Players ------------------------------------------------------------------------------

def test_a_removed_players_id_is_never_given_to_the_next(host):
    pat = add_player(host, 'Pat')
    pat_id = pat.get('/api/auth/me').get_json()['id']
    pat.post('/api/scenarios', json={'name': "Pat's plan", 'attacker': {}, 'defender': {}, 'conditions': {}})
    assert host.delete(f'/api/players/{pat_id}').status_code == 200
    assert 'Pat' not in [p['name'] for p in host.get('/api/players').get_json()]
    newcomer = add_player(host, 'Pat')  # the name is free again
    assert newcomer.get('/api/auth/me').get_json()['id'] != pat_id
    assert newcomer.get('/api/scenarios').get_json() == []  # nothing of the old Pat's
    assert pat.get('/api/armies').status_code == 401


# --- The setup code ------------------------------------------------------------------------

def test_creating_the_host_needs_the_setup_code(hosted):
    client, _ = hosted
    code = client.application.config['SETUP_CODE']
    assert code and len(code) == 9
    assert client.post('/api/auth/welcome', json=HOST).status_code == 403
    assert client.post('/api/auth/welcome', json=dict(HOST, setup_code='WRONG-CODE')).status_code == 403
    assert client.post('/api/auth/welcome', json=dict(HOST, setup_code=code.lower())).status_code == 200
    assert client.application.config['SETUP_CODE'] is None  # used once
    assert 'Setup code' not in client.get('/welcome').get_data(as_text=True)  # there's a host now


def test_two_welcome_forms_at_once_make_one_host(hosted):
    client, _ = hosted
    code = client.application.config['SETUP_CODE']
    answers = []

    def send(name):
        answers.append(client.application.test_client().post(
            '/api/auth/welcome', json=dict(HOST, name=name, setup_code=code)).status_code)

    threads = [threading.Thread(target=send, args=(f'Host{i}',)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(answers) == [200, 403, 403, 403]


# --- Logging in ------------------------------------------------------------------------------

def test_wrong_passwords_from_one_device_dont_lock_out_another(host):
    attacker = host.application.test_client()
    for _ in range(accounts.LOGIN_TRIES):
        attacker.post('/api/auth/login', json={'name': 'Hosty', 'password': 'nope-nope'},
                      environ_base={'REMOTE_ADDR': '10.0.0.9'})
    assert attacker.post('/api/auth/login', json={'name': 'Hosty', 'password': HOST['password']},
                         environ_base={'REMOTE_ADDR': '10.0.0.9'}).status_code == 429
    me = host.application.test_client()
    assert me.post('/api/auth/login', json={'name': 'Hosty', 'password': HOST['password']},
                   environ_base={'REMOTE_ADDR': '10.0.0.2'}).status_code == 200
    # Behind this computer's own proxy (Tailscale serve), the forwarded address counts
    proxied = host.application.test_client()
    assert proxied.post('/api/auth/login', json={'name': 'Hosty', 'password': HOST['password']},
                        environ_base={'REMOTE_ADDR': '127.0.0.1'},
                        headers={'X-Forwarded-For': '100.64.0.5'}).status_code == 200


def test_look_alike_paths_arent_open(host):
    stranger = host.application.test_client()
    assert stranger.get('/loginx').status_code == 302  # to the login page, not a 404 left open


# --- Data ------------------------------------------------------------------------------------

def test_players_dont_see_server_paths(host):
    pat = add_player(host, 'Pat')
    theirs = json.dumps(pat.get('/api/data/status').get_json())
    assert 'folder' not in theirs
    assert 'folder' in host.get('/api/data/status').get_json()


def test_a_data_write_with_a_field_missing_is_refused_not_a_crash(host):
    resp = host.post('/api/data/factions', json={})
    assert resp.status_code == 400 and "'name' is missing" in resp.get_json()['error']


def test_a_failed_rebuild_leaves_the_reference_tables_as_they_were(host, monkeypatch):
    factions = len(host.get('/api/data/factions').get_json())
    assert factions

    def broken(data):
        real(dict(data, factions=data['factions'] + data['factions'][:1]))  # a duplicate ID: the insert fails

    real = data_store.rebuild_reference_tables
    import combat_sim.app as app_module
    monkeypatch.setattr(app_module, 'rebuild_reference_tables', broken)
    with pytest.raises(Exception):
        with host.application.app_context():
            app_module.reload_bsdata(host.application)
    assert len(host.get('/api/data/factions').get_json()) == factions  # the drop was rolled back too


def test_the_database_runs_in_wal_mode(host):
    from combat_sim.models import db
    with host.application.app_context():
        assert db.session.execute(db.text('PRAGMA journal_mode')).scalar() == 'wal'


def test_json_is_written_without_leftover_temp_files(tmp_path):
    target = tmp_path / 'out.json'
    threads = [threading.Thread(target=data_store.write_json, args=(target, {'n': i})) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert json.loads(target.read_text(encoding='utf-8'))['n'] in range(8)
    assert [p.name for p in tmp_path.iterdir()] == ['out.json']


# --- Proposals -------------------------------------------------------------------------------

def test_the_hosts_diff_uses_the_data_tabs_field_names(host):
    faction = host.post('/api/data/factions', json={'name': 'Homebrew'}).get_json()['id']
    weapon = host.post('/api/data/weapons', json={'type': 'ranged', 'faction_id': faction, 'name': 'Zapper', 'range': 24,
                                                  'attacks': '2', 'skill': 3, 'strength': 5, 'ap': -1, 'damage': '1'})
    assert weapon.status_code == 200, weapon.get_json()
    weapon_id = weapon.get_json()['id']
    pat = add_player(host, 'Pat')
    body = {'type': 'ranged', 'faction_id': faction, 'name': 'Zapper', 'range': '24', 'attacks': '2', 'skill': 3,
            'strength': 6, 'ap': '-1', 'damage': '1'}
    assert pat.put(f'/api/data/weapons/ranged/{weapon_id}', json=body).status_code == 202
    proposal = host.get('/api/proposals').get_json()[0]
    assert proposal['changes'] == [{'field': 'strength', 'now': 5, 'proposed': 6}]  # range, skill and AP unchanged


def test_a_proposal_missing_a_field_fails_instead_of_crashing(host):
    pat = add_player(host, 'Pat')
    assert pat.post('/api/data/factions', json={'nickname': 'no name'}).status_code == 202
    proposal = host.get('/api/proposals').get_json()[0]
    resp = host.post(f"/api/proposals/{proposal['id']}/approve")
    assert resp.status_code == 400 and resp.get_json()['status'] == 'failed'


# --- Games -----------------------------------------------------------------------------------

def saved_army(browser, name, *units):
    factions = {f['name']: f['id'] for f in browser.get('/api/data/factions').get_json()}
    found = {u['name']: u['id'] for u in browser.get(f'/api/units?faction_id={factions["Starfarers"]}').get_json()}
    body = {'name': name, 'faction_id': factions['Starfarers'], 'points_limit': 2000,
            'units': [dict(unit, unit_id=found[unit.pop('unit')]) for unit in units]}
    return browser.post('/api/armies', json=body).get_json()['id']


@pytest.fixture
def match(host):
    sam = add_player(host, 'Sam')
    red = saved_army(host, 'Red', {'uid': 'w', 'unit': 'Void Warriors'}, {'uid': 'c', 'unit': 'Star Captain'})
    blue = saved_army(sam, 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    sam_id = sam.get('/api/auth/me').get_json()['id']
    game = host.post('/api/play/games', json={'armies': [red, blue], 'setup': False, 'seat': 0, 'opponent': sam_id}).get_json()
    return host, sam, game


def place_body(game, unit, spots):
    models = [m['id'] for u in game['state']['units'] if u['id'] == unit for m in u['models']]
    return {'type': 'place', 'unit': unit, 'models': [{'id': m, 'x': x, 'y': y, 'angle': 0} for m, (x, y) in zip(models, spots)]}


def test_two_actions_at_once_on_one_game_are_both_kept_or_refused(match, monkeypatch):
    host, _, game = match
    real = play_routes.resolve_action

    def slow(*args, **kwargs):  # widen the window between reading the log and saving it
        time.sleep(0.2)
        return real(*args, **kwargs)

    monkeypatch.setattr(play_routes, 'resolve_action', slow)
    cookie = host.get_cookie('combat_sim_session').value
    answers = {}

    def send(unit, spots):
        browser = host.application.test_client()
        browser.set_cookie('combat_sim_session', cookie)
        answers[unit] = browser.post(f'/api/play/games/{game["id"]}/actions', json=place_body(game, unit, spots)).status_code

    threads = [threading.Thread(target=send, args=('p1-w', ROW)),
               threading.Thread(target=send, args=('p1-c', [(30, 6)]))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    events = host.get(f'/api/play/games/{game["id"]}').get_json()['events']
    assert list(answers.values()).count(200) == len(events) >= 1  # nothing a player was told succeeded was lost


def test_an_idle_poll_is_answered_without_the_game(match):
    host, sam, game = match
    quiet = sam.post(f'/api/play/games/{game["id"]}/sync?since=0').get_json()
    assert quiet == {'id': game['id'], 'moved': 0, 'unchanged': True, 'waiting_for_army': False,
                     'online': quiet['online']}
    host.post(f'/api/play/games/{game["id"]}/actions', json=place_body(game, 'p1-w', ROW))
    moved = sam.post(f'/api/play/games/{game["id"]}/sync?since=0').get_json()
    assert moved['moved'] == 1 and moved['state']


# --- Shipping ------------------------------------------------------------------------------

def test_the_shipped_compose_file_is_safe_as_it_stands():
    compose = (ROOT / 'compose.yaml').read_text(encoding='utf-8')
    assert '"127.0.0.1:5000:5000"' in compose  # this computer only
    assert 'COMBAT_SIM_SECURE_COOKIES: "1"' in compose


# --- Analytics -------------------------------------------------------------------------------

GUARD_DATA = {
    'schema_version': 1, 'source': None, 'factions': [{'id': 1, 'name': 'Guard'}],
    'units': [{'id': 1, 'faction_id': 1, 'name': 'Guardsmen', 'toughness': 3, 'save': 5, 'wounds': 1,
               'points_cost': 65, 'default_models': 10, 'keywords': ['Infantry'], 'modifiers': [],
               'ranged_weapons': [{'weapon_id': 1, 'qty': 10}], 'melee_weapons': []}],
    'ranged_weapons': [{'id': 1, 'faction_id': 1, 'name': 'Lasgun', 'attacks': '1', 'ballistic_skill': 4, 'strength': 3,
                        'armor_penetration': 0, 'damage': '1', 'abilities': []}],
    'melee_weapons': [],
    'faction_rules': [{'id': 1, 'faction_id': 1, 'name': 'Hold the Line', 'applies_to': 'Target', 'phase': 'Both',
                       'modifier': 'Toughness Modifier +1'}],
    'stratagems': [], 'keywords': [], 'target_profiles': [],
}


def test_reverse_durability_uses_my_own_target_rules(make_client, monkeypatch):
    monkeypatch.setenv(paths.HOSTED_ENV, '')
    client, _ = make_client(GUARD_DATA)

    def reverse(**extra):
        request = {'attackers': [{'label': 'Guardsmen', 'unit_id': 1,
                                  'weapons': [{'weapon_id': 1, 'weapon_type': 'ranged', 'models': 10}]}],
                   'defenders': [{'unit_id': 1, 'model_count': 10}], 'conditions': {}, 'weapon_filter': 'both'}
        request.update(extra)
        return client.post('/api/matrix/simulate', json=request).get_json()['analysis']['reverse']['rows'][0]['total']

    assert reverse(attacker_rules={'rule_ids': [1]}) < reverse()  # Hold the Line: my Guardsmen are tougher
