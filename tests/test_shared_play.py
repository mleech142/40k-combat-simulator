"""
Players' own lists and games on a self-hosted copy (hosted-version-scope.md section 8 c): armies everyone can
read but only their owner change, private scenarios, comparisons and battlefields, games between two players of
the copy (each plays their own seat, here, with no relay) and "your move" emails.
"""

import copy as copying

import pytest

from combat_sim import accounts, mail, paths
from combat_sim.play.battlefields import BUILTIN
from conftest import BSDATA_FIXTURES, welcome

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}
ROW = [(10 + 1.5 * i, 6) for i in range(5)]


@pytest.fixture
def copy(make_client, monkeypatch):
    """A self-hosted copy with the host (Hosty) and two players (Pat, Sam), each logged in their own browser."""
    monkeypatch.setenv(paths.HOSTED_ENV, '1')
    monkeypatch.delenv('SMTP_HOST', raising=False)
    accounts._failed.clear()
    mail._seen.clear()
    mail._sent.clear()
    host, _ = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)
    assert welcome(host, {'name': 'Hosty', 'email': 'h@example.com', 'password': 'throwaway-1'}).status_code == 200
    browsers = {'host': host}
    for name in ('Pat', 'Sam'):
        link = host.post('/api/players', json={'name': name, 'email': f'{name.lower()}@example.com'}).get_json()['link']
        browser = host.application.test_client()
        assert browser.post('/api/auth/join', json={'token': link.rsplit('/', 1)[1], 'password': 'throwaway-2'}).status_code == 200
        browsers[name.lower()] = browser
    return browsers


def ids(browser):
    return {u['name']: u['id'] for u in browser.get('/api/play/opponents').get_json()}


def saved_army(browser, name, *units):
    factions = {f['name']: f['id'] for f in browser.get('/api/data/factions').get_json()}
    found = {u['name']: u['id'] for u in browser.get(f'/api/units?faction_id={factions["Starfarers"]}').get_json()}
    body = {'name': name, 'faction_id': factions['Starfarers'], 'points_limit': 2000,
            'units': [dict(unit, unit_id=found[unit.pop('unit')]) for unit in units]}
    response = browser.post('/api/armies', json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()['id']


@pytest.fixture
def match(copy):
    """A game Pat started against Sam: Pat plays Red (player 1), Sam plays Blue."""
    red = saved_army(copy['pat'], 'Red', {'uid': 'w', 'unit': 'Void Warriors'}, {'uid': 'c', 'unit': 'Star Captain'})
    blue = saved_army(copy['sam'], 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    response = copy['pat'].post('/api/play/games', json={'name': 'Grudge', 'armies': [red, blue], 'setup': False,
                                                          'seat': 0, 'opponent': ids(copy['pat'])['Sam']})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def place(browser, game, unit, spots):
    models = [m['id'] for u in game['state']['units'] if u['id'] == unit for m in u['models']]
    body = {'type': 'place', 'unit': unit, 'models': [{'id': m, 'x': x, 'y': y, 'angle': 0} for m, (x, y) in zip(models, spots)]}
    return browser.post(f'/api/play/games/{game["id"]}/actions', json=body)


# --- Armies, scenarios, comparisons, battlefields --------------------------------------

def test_everyone_sees_each_others_armies_but_only_changes_their_own(copy):
    army = saved_army(copy['pat'], "Pat's list", {'uid': 'w', 'unit': 'Void Warriors'})
    listed = {a['id']: a for a in copy['sam'].get('/api/armies').get_json()}
    assert listed[army]['mine'] is False and listed[army]['owner'] == 'Pat'
    assert copy['pat'].get('/api/armies').get_json()[0]['mine'] is True
    seen = copy['sam'].get(f'/api/armies/{army}').get_json()
    assert seen['mine'] is False and seen['owner'] == 'Pat' and seen['units']
    assert copy['sam'].get(f'/api/armies/{army}/match').status_code == 200
    assert copy['sam'].put(f'/api/armies/{army}', json=dict(seen, name='Mine now')).status_code == 403
    assert copy['sam'].delete(f'/api/armies/{army}').status_code == 403
    # A copy is Sam's own
    mine = copy['sam'].post(f'/api/armies/{army}/copy').get_json()
    assert mine['mine'] is True and mine['owner'] == 'Sam'
    assert copy['sam'].delete(f"/api/armies/{mine['id']}").status_code == 200


def test_scenarios_and_comparisons_are_private(copy):
    scenario = copy['pat'].post('/api/scenarios', json={'name': 'Mine', 'attacker': {}, 'defender': {}, 'conditions': {}}).get_json()['id']
    assert [s['name'] for s in copy['pat'].get('/api/scenarios').get_json()] == ['Mine']
    assert copy['sam'].get('/api/scenarios').get_json() == []
    assert copy['sam'].delete(f'/api/scenarios/{scenario}').status_code == 404
    assert copy['sam'].put(f'/api/scenarios/{scenario}', json={'name': 'Taken'}).status_code == 404
    comparison = copy['pat'].post('/api/comparisons', json={'name': 'Matrix', 'attackers': [], 'defenders': [], 'conditions': {}}).get_json()['id']
    assert copy['sam'].get('/api/comparisons').get_json() == []
    assert copy['sam'].get(f'/api/comparisons/{comparison}').status_code == 404
    assert copy['pat'].get(f'/api/comparisons/{comparison}').status_code == 200


def test_battlefields_are_private(copy):
    saved = copy['pat'].post('/api/play/battlefields', json=dict(copying.deepcopy(BUILTIN['dawn-of-war']), name='My table'))
    assert saved.status_code == 200, saved.get_json()
    key = saved.get_json()['key']
    assert key in [b['key'] for b in copy['pat'].get('/api/play/battlefields').get_json()]
    assert key not in [b['key'] for b in copy['sam'].get('/api/play/battlefields').get_json()]
    assert copy['sam'].get(f'/api/play/battlefields/{key}').status_code == 404
    assert copy['sam'].delete(f'/api/play/battlefields/{key}').status_code == 404


# --- Games between two players of the copy ----------------------------------------------

def test_a_game_against_another_player_gives_each_their_seat(copy, match):
    assert match['seat'] == 0 and match['online'] == {'code': None, 'joined': True, 'local': True, 'names': ['Pat', 'Sam']}
    theirs = copy['sam'].get(f'/api/play/games/{match["id"]}').get_json()
    assert theirs['seat'] == 1
    assert [(g['name'], g['seat'], g['online']) for g in copy['sam'].get('/api/play/games').get_json()] == [('Grudge', 1, True)]
    # Nobody else's
    assert copy['host'].get('/api/play/games').get_json() == []
    assert copy['host'].get(f'/api/play/games/{match["id"]}').status_code == 404
    assert place(copy['host'], match, 'p1-w', ROW).status_code == 404


def test_each_player_moves_only_their_own_side(copy, match):
    assert place(copy['sam'], match, 'p1-w', ROW).status_code == 403  # Pat's unit
    response = place(copy['pat'], match, 'p1-w', ROW)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['waiting_on'] == [1]
    # Sam's page asks how many moves it hasn't seen
    synced = copy['sam'].post(f'/api/play/games/{match["id"]}/sync?since=0').get_json()
    assert synced['moved'] == 1 and synced['seat'] == 1


def test_undo_asks_the_other_player(copy, match):
    place(copy['pat'], match, 'p1-w', ROW)
    answer = copy['pat'].post(f'/api/play/games/{match["id"]}/undo').get_json()
    last = answer['events'][-1]['action']
    assert last['type'] == 'correction' and 'undo' in last  # Sam accepts or declines it
    assert len(answer['events']) == 2


def test_armies_must_be_their_players(copy):
    red = saved_army(copy['pat'], 'Red', {'uid': 'w', 'unit': 'Void Warriors'})
    blue = saved_army(copy['sam'], 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    sam = ids(copy['pat'])['Sam']
    swapped = copy['pat'].post('/api/play/games', json={'armies': [blue, red], 'setup': False, 'seat': 0, 'opponent': sam})
    assert swapped.status_code == 400 and "isn't yours" in swapped.get_json()['error']
    hot_seat = copy['pat'].post('/api/play/games', json={'armies': [red, blue], 'setup': False})
    assert hot_seat.status_code == 400  # Blue is Sam's: Pat copies it first
    no_side = copy['pat'].post('/api/play/games', json={'armies': [red, blue], 'setup': False, 'opponent': sam})
    assert no_side.status_code == 400


def test_a_game_here_needs_no_relay_and_only_its_starter_deletes_it(copy, match):
    assert copy['pat'].post(f'/api/play/games/{match["id"]}/online').status_code == 400
    assert copy['pat'].put(f'/api/play/games/{match["id"]}/seat', json={'seat': None}).status_code == 409
    assert copy['sam'].delete(f'/api/play/games/{match["id"]}').status_code == 403
    assert copy['pat'].delete(f'/api/play/games/{match["id"]}').status_code == 200


# --- "Your move" emails ------------------------------------------------------------------

@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setenv('SMTP_HOST', 'mail.example')
    monkeypatch.setattr(mail, 'send', sent.append)
    monkeypatch.setattr(mail.threading, 'Thread', lambda target, args, daemon: type('T', (), {'start': lambda self: target(*args)})())
    return sent


def test_the_other_player_is_emailed_when_its_their_move(copy, match, outbox):
    place(copy['pat'], match, 'p1-w', ROW)
    assert len(outbox) == 1
    assert outbox[0]['To'] == 'sam@example.com' and outbox[0]['Subject'] == 'Your move: Grudge'
    assert f'/play?game={match["id"]}' in outbox[0].get_content() and 'Pat has moved' in outbox[0].get_content()


def test_no_email_while_they_watch_or_when_turned_off(copy, match, outbox):
    copy['sam'].post(f'/api/play/games/{match["id"]}/sync?since=0')  # Sam has the game open
    place(copy['pat'], match, 'p1-w', ROW)
    assert outbox == []
    mail._seen.clear()
    copy['sam'].post('/api/auth/account', json={'notify': False})
    assert copy['sam'].get('/api/auth/me').get_json()['notify'] is False
    copy['pat'].post(f'/api/play/games/{match["id"]}/undo')
    assert outbox == []


def test_no_mail_server_no_emails(copy, match, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, 'send', sent.append)
    place(copy['pat'], match, 'p1-w', ROW)
    assert sent == []  # SMTP_HOST isn't set
