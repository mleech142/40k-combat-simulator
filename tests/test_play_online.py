"""
Online play (play/relay_client.py and the routes): two installs play one game through the relay, run here by
relay/dev-server.mjs (the same rules as the Worker, in memory). Skipped when Node isn't installed.
"""

import json
import shutil
import subprocess

import pytest

from combat_sim.models import SavedGame, db
from combat_sim.play import relay_client
from conftest import ROOT
from test_play_api import saved_army
from test_play_game_files import new_game, place, two_pcs  # noqa: F401 (fixture)
from test_play_missions_api import saved_army as missions_army

NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='Node.js not installed')


@pytest.fixture
def relay(monkeypatch):
    """The relay on a free port, for both installs (RELAY_URL)."""
    server = subprocess.Popen([NODE, str(ROOT / 'relay' / 'dev-server.mjs'), '0'], stdout=subprocess.PIPE, text=True)
    try:
        line = server.stdout.readline()
        assert line.startswith('listening on'), line
        url = f'http://127.0.0.1:{line.split()[-1]}'
        monkeypatch.setenv('RELAY_URL', url)
        yield url
    finally:
        server.kill()
        server.wait()


@pytest.fixture
def online(relay, two_pcs):  # noqa: F811
    """A game I (Sam) made, playing Red, put online; and my friend Alex's copy, joined with its invite code."""
    mine, theirs = two_pcs
    mine.post('/api/play/relay', json={'name': 'Sam'})
    theirs.post('/api/play/relay', json={'name': 'Alex'})
    game = mine.post(f'/api/play/games/{new_game(mine, 0)["id"]}/online').get_json()
    assert game['online'] == {'code': game['online']['code'], 'joined': False, 'names': ['Sam', None]} and 'token' not in str(game)
    code = game['online']['code']
    joined = theirs.post('/api/play/games/join', json={'code': f'{code[:5].lower()}-{code[5:]}'})  # As typed
    assert joined.status_code == 200, joined.get_json()
    return mine, theirs, game, joined.get_json()


def sync(client, game):
    return client.post(f'/api/play/games/{game["id"]}/sync').get_json()


def test_joining_takes_the_other_seat(online):
    mine, theirs, game, their_game = online
    assert their_game['seat'] == 1 and their_game['online']['joined'] and their_game['uid'] == game['uid']
    assert their_game['online']['names'] == ['Sam', 'Alex']
    assert sync(mine, game)['online'] == {'code': game['online']['code'], 'joined': True, 'names': ['Sam', 'Alex']}  # Who joined
    assert theirs.post('/api/play/games/join', json={'code': game['online']['code']}).status_code == 409  # Already here
    assert mine.post('/api/play/games/join', json={'code': 'ZZZZZZZZZZ'}).status_code == 400  # No such game


def test_moves_reach_the_other_player(online):
    mine, theirs, game, their_game = online
    game = place(mine, game, 'p1-w', 10, 6).get_json()
    assert game['waiting_on'] == [1]
    their_game = sync(theirs, their_game)
    assert their_game['moved'] == 1 and their_game['state'] == game['state'] and their_game['waiting_on'] == [1]
    assert sync(theirs, their_game)['moved'] == 0
    their_game = place(theirs, their_game, 'p2-g', 10, 38).get_json()
    assert sync(mine, game)['state'] == their_game['state']


def test_a_move_made_on_an_old_board_is_refused_and_the_board_brought_up_to_date(online):
    mine, theirs, game, their_game = online
    place(mine, game, 'p1-w', 10, 6)
    # Their app hasn't fetched my move yet: their action waits until it has
    refused = theirs.post(f'/api/play/games/{their_game["id"]}/actions', json={'type': 'orders', 'player': 1, 'casualties': 'closest'})
    assert refused.status_code == 409 and 'moved' in refused.get_json()['error']
    assert len(refused.get_json()['events']) == 1  # My placement is on their board now
    assert theirs.post(f'/api/play/games/{their_game["id"]}/actions', json={'type': 'orders', 'player': 1, 'casualties': 'closest'}).status_code == 200


def placed(game, unit):
    return [m['position'] for u in game['state']['units'] if u['id'] == unit for m in u['models']]


def test_undo_online_asks_the_other_player_first(online):
    """Testing list item 11: Undo becomes a Correction the other player accepts; the log keeps the action."""
    mine, theirs, game, their_game = online
    place(mine, game, 'p1-w', 10, 6)
    asked = mine.post(f'/api/play/games/{game["id"]}/undo').get_json()
    pending = asked['state']['pending']
    assert pending['player'] == 0 and pending['undo'] == {'event': 0} and 'placing a unit' in pending['note']
    assert all(placed(asked, 'p1-w'))  # Not yet
    assert 'waiting' in mine.post(f'/api/play/games/{game["id"]}/actions', json={'type': 'orders', 'player': 0, 'casualties': 'closest'}).get_json()['error']
    their_game = sync(theirs, their_game)
    assert their_game['state']['pending']['undo'] == {'event': 0}
    answer = theirs.post(f'/api/play/games/{their_game["id"]}/actions', json={'type': 'accept', 'player': 1})
    assert answer.status_code == 200, answer.get_json()
    after = answer.get_json()
    assert not any(placed(after, 'p1-w')) and after['state']['pending'] is None
    assert [e['action']['type'] for e in after['events']] == ['place', 'correction', 'accept'] and after['events'][-1]['undone'] == 0
    assert sync(mine, game)['state'] == after['state']


def test_a_declined_undo_leaves_the_action(online):
    mine, theirs, game, their_game = online
    place(mine, game, 'p1-w', 10, 6)
    mine.post(f'/api/play/games/{game["id"]}/undo')
    their_game = sync(theirs, their_game)
    after = theirs.post(f'/api/play/games/{their_game["id"]}/actions', json={'type': 'decline', 'player': 1}).get_json()
    assert all(placed(after, 'p1-w')) and after['state']['pending'] is None


def test_only_your_own_last_action_and_only_by_undo(online):
    mine, theirs, game, their_game = online
    place(mine, game, 'p1-w', 10, 6)
    their_game = sync(theirs, their_game)
    assert "only they can take it back" in theirs.post(f'/api/play/games/{their_game["id"]}/undo').get_json()['error']
    mine.post(f'/api/play/games/{game["id"]}/undo')
    their_game = sync(theirs, their_game)
    theirs.post(f'/api/play/games/{their_game["id"]}/actions', json={'type': 'decline', 'player': 1})
    sync(mine, game)
    assert 'only they can take it back' in mine.post(f'/api/play/games/{game["id"]}/undo').get_json()['error']  # Their decline is last
    made_up = {'type': 'correction', 'player': 0, 'undo': {'event': 0}}
    assert 'Press Undo' in mine.post(f'/api/play/games/{game["id"]}/actions', json=made_up).get_json()['error']


def test_without_the_relay_an_online_move_is_refused(online, monkeypatch):
    mine, _, game, _ = online
    monkeypatch.setenv('RELAY_URL', 'http://127.0.0.1:9')  # Nothing listens there
    refused = place(mine, game, 'p1-w', 10, 6)
    assert refused.status_code == 503 and "Couldn't reach the relay" in refused.get_json()['error']
    assert mine.get(f'/api/play/games/{game["id"]}').get_json()['events'] == []


def test_online_games_keep_their_seats_and_take_no_files(online):
    mine, _, game, _ = online
    assert mine.put(f'/api/play/games/{game["id"]}/seat', json={'seat': 1}).status_code == 409
    sent = mine.get(f'/api/play/games/{game["id"]}/file').get_json()
    assert mine.post('/api/play/games/import', json={'file': sent}).status_code == 409


def test_a_hot_seat_game_cant_go_online(relay, two_pcs):  # noqa: F811
    mine, _ = two_pcs
    game = new_game(mine, None)
    assert 'Choose which player' in mine.post(f'/api/play/games/{game["id"]}/online').get_json()['error']


def test_a_new_invite_code_shuts_out_whoever_has_the_old_one(online):
    """Someone who saw the code first and joined (here: 'theirs') is cut off; the real friend joins with the new one."""
    mine, theirs, game, their_game = online
    place(mine, game, 'p1-w', 10, 6)
    old = game['online']['code']
    game = mine.post(f'/api/play/games/{game["id"]}/new-code').get_json()
    assert game['online']['code'] != old and not game['online']['joined'] and len(game['events']) == 1
    shut_out = theirs.post(f'/api/play/games/{their_game["id"]}/sync')
    assert shut_out.status_code == 410 and 'new invite code' in shut_out.get_json()['error']
    rejoined = theirs.post('/api/play/games/join', json={'code': game['online']['code']}).get_json()
    assert rejoined['id'] == their_game['id'] and rejoined['seat'] == 1 and len(rejoined['events']) == 1
    assert theirs.post('/api/play/games/join', json={'code': old}).status_code == 400  # The old code is gone


def test_moves_the_other_app_sends_that_could_attack_the_page_are_refused(relay, online):
    """A move sent straight to the relay (not through the app) that would put HTML in the page."""
    mine, theirs, game, their_game = online
    with theirs.application.app_context():
        token = db.session.get(SavedGame, their_game['id']).relay_token
    bad = {'action': {'type': 'orders', 'player': 1}, 'rolls': [], 'models': {}, 'units': {}, 'by': 1,
           'players': [{'cp': 0}, {'cp': '<img src=x onerror=alert(1)>'}]}
    relay_client.append(relay, game['online']['code'], token, 0, [bad])
    refused = mine.post(f'/api/play/games/{game["id"]}/sync')
    assert refused.status_code == 502 and "can't be read here" in refused.get_json()['error']
    assert mine.get(f'/api/play/games/{game["id"]}').get_json()['events'] == []  # Nothing was added


def test_the_relay_address_is_a_setting(relay, two_pcs):  # noqa: F811
    mine, _ = two_pcs
    assert mine.get('/api/play/relay').get_json()['url'] == relay
    assert mine.post('/api/play/relay', json={'url': 'ftp://nope'}).status_code == 400
    assert mine.post('/api/play/relay', json={'url': 'https://relay.example.workers.dev/'}).get_json()['url'] == 'https://relay.example.workers.dev'
    assert mine.post('/api/play/relay', json={'url': ''}).get_json()['url'] == relay  # Back to the default
    assert mine.post('/api/play/relay', json={'name': ' Sam <the> "Bold" '}).get_json()['name'] == 'Sam the Bold'
    assert mine.get('/api/play/relay').get_json()['url'] == relay  # A name alone leaves the address


# --- An open seat: the other player joins with their own army ---------------------------------------------

def open_game(client, army, **options):
    """A game with my army as Red only, put online for the other player to join with theirs."""
    response = client.post('/api/play/games', json={'armies': [army, None], 'seat': 0, 'setup': False, **options})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_a_game_can_wait_for_the_other_players_army(relay, two_pcs):  # noqa: F811
    mine, theirs = two_pcs
    mine.post('/api/play/relay', json={'name': 'Sam'})
    theirs.post('/api/play/relay', json={'name': 'Alex'})
    red = saved_army(mine, 'Red', {'uid': 'w', 'unit': 'Void Warriors'})
    game = open_game(mine, red)
    assert game['waiting_for_army'] and game['state'] is None and game['players'] == ['Red', None]
    assert game['online']['code'] and not game['online']['joined'] and game['name'] == 'Red vs (joining online)'
    assert [g['players'] for g in mine.get('/api/play/games').get_json()] == [['Red', 'joining online']]
    for refused in (mine.post(f'/api/play/games/{game["id"]}/actions', json={'type': 'next_phase'}),
                    mine.get(f'/api/play/games/{game["id"]}/file')):
        assert refused.status_code == 409
    assert sync(mine, game)['waiting_for_army']  # Nobody yet

    # Their app asks for their army first, showing whose game it is
    code = game['online']['code']
    asked = theirs.post('/api/play/games/join', json={'code': code}).get_json()
    assert asked['needs_army'] and asked['opponent'] == {'army': 'Red', 'faction': 'Starfarers', 'disposition': None, 'player': 'Sam'}
    assert theirs.get('/api/play/games').get_json() == []  # Nothing joined yet
    blue = saved_army(theirs, 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    their_game = theirs.post('/api/play/games/join', json={'code': code, 'army': blue}).get_json()
    assert their_game['seat'] == 1 and [p['name'] for p in their_game['state']['players']] == ['Red', 'Blue']
    assert their_game['name'] == 'Red vs Blue' and their_game['online']['names'] == ['Sam', 'Alex']

    # My app picks up the game they completed, and play goes on as in any online game
    game = sync(mine, game)
    assert not game.get('waiting_for_army') and game['state'] == their_game['state'] and game['name'] == 'Red vs Blue'
    game = place(mine, game, 'p1-w', 10, 6).get_json()
    assert sync(theirs, their_game)['state'] == game['state']


def test_choices_that_need_both_armies_are_made_when_they_join(relay, two_pcs):  # noqa: F811
    mine, theirs = two_pcs
    red = missions_army(mine, 'Red', ['d-vanguard'])  # Purge the Foe
    game = open_game(mine, red, battlefield='pairing:B', twist={'key': 'random'})
    assert game['options'] == {'battlefield': "Their pairing's Event Companion layout", 'twist': 'Random'}
    plain = missions_army(theirs, 'Plain', [])  # No force disposition: no pairing to play
    refused = theirs.post('/api/play/games/join', json={'code': game['online']['code'], 'army': plain})
    assert refused.status_code == 400 and 'force disposition' in refused.get_json()['error']
    blue = missions_army(theirs, 'Blue', ['d-bastion'])  # Take and Hold
    their_game = theirs.post('/api/play/games/join', json={'code': game['online']['code'], 'army': blue}).get_json()
    assert their_game['state']['battlefield']['name'] == 'Take and Hold vs Purge the Foe: Layout B'
    assert their_game['twist'] is not None
    assert sync(mine, game)['state'] == their_game['state']


def test_a_joiner_cant_change_the_creators_army(relay, two_pcs):  # noqa: F811
    """The relay only takes the completed game if my army is as I sent it; my app checks again on fetching."""
    mine, _ = two_pcs
    game = open_game(mine, saved_army(mine, 'Red', {'uid': 'w', 'unit': 'Void Warriors'}))
    code = game['online']['code']
    invited = relay_client.invite(relay, code)['game']
    changed = json.loads(json.dumps(invited))
    changed['armies'][0]['name'] = 'Pushover'
    changed['armies'][1] = dict(invited['armies'][0], name='Blue')
    with pytest.raises(relay_client.RelayError) as error:
        relay_client.join(relay, code, 'Mallory', changed)
    assert error.value.status == 400
    assert sync(mine, game)['waiting_for_army']


def test_an_open_seat_must_be_the_other_players(relay, two_pcs):  # noqa: F811
    mine, _ = two_pcs
    red = saved_army(mine, 'Red', {'uid': 'w', 'unit': 'Void Warriors'})
    for body in ({'armies': [red, None], 'seat': 1}, {'armies': [red, None], 'seat': None}, {'armies': [None, None], 'seat': 0}):
        assert mine.post('/api/play/games', json=body).status_code == 400


def test_relay_requests_use_certifi_and_say_when_the_certificate_fails(monkeypatch):
    """Windows' certificate store can hold expired copies that fail valid HTTPS sites; every web request checks
    against certifi's list instead (web.py), and a certificate failure isn't blamed on the internet connection."""
    import ssl
    import urllib.error

    import certifi

    from combat_sim import web
    seen = {}

    def expired(request, timeout, context):
        seen['context'] = context
        raise urllib.error.URLError(ssl.SSLCertVerificationError(1, 'certificate has expired'))
    monkeypatch.setattr(web.urllib.request, 'urlopen', expired)
    with pytest.raises(relay_client.RelayError, match="Couldn't connect to the relay securely: .*expired"):
        relay_client.invite('https://relay.example', 'ABCDEFGHJK')
    assert seen['context'] is web.ssl_context()
    assert seen['context'].cert_store_stats()['x509_ca'] > 0
    assert certifi.where()
