"""
Online play (play/relay_client.py and the routes): two installs play one game through the relay, run here by
relay/dev-server.mjs (the same rules as the Worker, in memory). Skipped when Node isn't installed.
"""

import shutil
import subprocess

import pytest

from combat_sim.models import SavedGame, db
from combat_sim.play import relay_client
from conftest import ROOT
from test_play_game_files import new_game, place, two_pcs  # noqa: F401 (fixture)

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


def test_undo_takes_the_move_back_on_the_relay_too(online):
    mine, theirs, game, their_game = online
    place(mine, game, 'p1-w', 10, 6)
    assert mine.post(f'/api/play/games/{game["id"]}/undo').status_code == 200
    assert sync(theirs, their_game)['events'] == []


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
