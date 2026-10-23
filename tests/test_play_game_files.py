"""
Play mode between two PCs without a server (docs/play-mode-design.md, Connection): each app holds one
seat, and the game goes back and forth as a game file. Two installs, each on its own database.
"""

import json

import pytest

from combat_sim import __version__
from combat_sim.app import create_app
from combat_sim.play import game_file
from conftest import BSDATA_FIXTURES, WAHAPEDIA_FIXTURES
from test_play_api import APP_DATA, saved_army


@pytest.fixture
def two_pcs(tmp_path, monkeypatch):
    """Two installs of the app, each with its own data and database: (mine, theirs)."""
    clients = []
    for name in ('mine', 'theirs'):
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'data.json').write_text(json.dumps(APP_DATA), encoding='utf-8')
        monkeypatch.setenv('DATA_JSON', str(folder / 'data.json'))
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(folder / 'test.db').replace('\\', '/'))
        monkeypatch.setenv('BSDATA_DIR', str(BSDATA_FIXTURES))
        monkeypatch.setenv('WAHAPEDIA_DIR', str(WAHAPEDIA_FIXTURES))
        clients.append(create_app().test_client())
    return clients


def new_game(client, seat):
    red = saved_army(client, 'Red', {'uid': 'w', 'unit': 'Void Warriors'})
    blue = saved_army(client, 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    response = client.post('/api/play/games', json={'name': 'Grudge match', 'armies': [red, blue], 'setup': False, 'seat': seat})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def place(client, game, unit, x, y):
    models = [m['id'] for u in game['state']['units'] if u['id'] == unit for m in u['models']]
    spots = [{'id': m, 'x': x + 1.5 * i, 'y': y} for i, m in enumerate(models)]
    return client.post(f'/api/play/games/{game["id"]}/actions', json={'type': 'place', 'unit': unit, 'models': spots})


def game_file_of(client, game):
    response = client.get(f'/api/play/games/{game["id"]}/file')
    assert response.status_code == 200
    assert 'attachment' in response.headers['Content-Disposition'] and 'Grudge match' in response.headers['Content-Disposition']
    return response.get_json()


def test_a_game_says_which_seat_this_app_holds_and_who_it_waits_for(two_pcs):
    mine, _ = two_pcs
    game = new_game(mine, 0)
    assert game['seat'] == 0 and game['waiting_on'] == [0] and len(game['uid']) == 32
    assert game['app_version'] == __version__ and not game['needs_update']
    hot_seat = new_game(mine, None)
    assert hot_seat['seat'] is None and hot_seat['uid'] != game['uid']
    assert [g['seat'] for g in mine.get('/api/play/games').get_json()] == [None, 0]


def test_a_seat_only_plays_its_own_side(two_pcs):
    mine, _ = two_pcs
    game = new_game(mine, 1)  # Blue; Red deploys first
    refused = place(mine, game, 'p1-w', 10, 6)
    assert refused.status_code == 403 and "Red's to do" in refused.get_json()['error']
    hot_seat = new_game(mine, None)
    assert place(mine, hot_seat, 'p1-w', 10, 6).status_code == 200


def test_a_game_goes_back_and_forth_as_a_file(two_pcs):
    mine, theirs = two_pcs
    game = new_game(mine, 0)
    game = place(mine, game, 'p1-w', 10, 6).get_json()
    assert game['events'][-1]['by'] == 0 and game['waiting_on'] == [1]
    sent = game_file_of(mine, game)
    assert sent['format'] == game_file.FORMAT and sent['sent_by'] == 0 and sent['uid'] == game['uid']

    # Their app has never seen the game: it's added, and they hold the other seat
    imported = theirs.post('/api/play/games/import', json={'file': sent})
    assert imported.status_code == 200, imported.get_json()
    their_game = imported.get_json()
    assert their_game['seat'] == 1 and their_game['state'] == game['state'] and their_game['imported'] == 'new'
    their_game = place(theirs, their_game, 'p2-g', 10, 38).get_json()
    assert their_game['waiting_on'] == [1]  # The last to place ends deployment
    their_game = theirs.post(f'/api/play/games/{their_game["id"]}/actions', json={'type': 'end_deployment'}).get_json()
    assert their_game['waiting_on'] == [0]  # Red's first turn

    # Back to mine: the file carries on from my copy, so its new events are added
    back = mine.post('/api/play/games/import', json={'file': game_file_of(theirs, their_game)}).get_json()
    assert back['id'] == game['id'] and back['seat'] == 0 and back['imported'] == 'newer'
    assert back['state'] == their_game['state'] and back['waiting_on'] == [0]
    assert len(mine.get('/api/play/games').get_json()) == 1

    # The same file again, or an older one, changes nothing
    assert mine.post('/api/play/games/import', json={'file': game_file_of(theirs, their_game)}).get_json()['imported'] == 'same'
    assert mine.post('/api/play/games/import', json={'file': sent}).get_json()['imported'] == 'older'


def test_copies_that_went_different_ways_need_a_choice(two_pcs):
    mine, theirs = two_pcs
    game = new_game(mine, None)  # Hot-seat: the file says nothing about seats, so they choose one
    sent = game_file_of(mine, game)
    assert sent['sent_by'] is None
    assert 'seat' in theirs.post('/api/play/games/import', json={'file': sent}).get_json()['error']
    their_game = theirs.post('/api/play/games/import', json={'file': sent, 'seat': 0}).get_json()
    place(theirs, their_game, 'p1-w', 10, 6)
    place(mine, game, 'p1-w', 20, 6)  # Both moved the same unit to different places
    conflict = mine.post('/api/play/games/import', json={'file': game_file_of(theirs, their_game)})
    assert conflict.status_code == 409 and conflict.get_json()['conflict']
    replaced = mine.post('/api/play/games/import', json={'file': game_file_of(theirs, their_game), 'replace': True}).get_json()
    assert replaced['imported'] == 'replaced' and replaced['events'] == theirs.get(f'/api/play/games/{their_game["id"]}').get_json()['events']


@pytest.mark.parametrize('broken, message', [
    ({'format': 'something else'}, 'not a game file'),
    ({'format_version': 99}, 'newer version'),
    ({'events': 'nope'}, 'damaged'),
    ({'armies': [{'name': 'Red'}]}, 'damaged'),
])
def test_files_that_are_not_games_are_refused(two_pcs, broken, message):
    mine, theirs = two_pcs
    sent = dict(game_file_of(mine, new_game(mine, 0)), **broken)
    response = theirs.post('/api/play/games/import', json={'file': sent})
    assert response.status_code == 400 and message in response.get_json()['error']
    assert theirs.get('/api/play/games').get_json() == []


def test_a_game_played_on_a_newer_version_asks_to_update(two_pcs):
    mine, theirs = two_pcs
    game = new_game(mine, 0)
    sent = dict(game_file_of(mine, game), app_version='3.0.0')
    their_game = theirs.post('/api/play/games/import', json={'file': sent}).get_json()
    assert their_game['needs_update'] and their_game['app_version'] == '3.0.0'
    refused = place(theirs, their_game, 'p2-g', 10, 38)
    assert refused.status_code == 409 and '3.0.0' in refused.get_json()['error']


@pytest.mark.parametrize('version', ['99.0.0', '2.2', 'latest', None])
def test_a_version_that_cant_be_real_is_refused(two_pcs, version):
    """Else a made-up version would stop the other player until they 'update' to it."""
    mine, theirs = two_pcs
    sent = dict(game_file_of(mine, new_game(mine, 0)), app_version=version)
    response = theirs.post('/api/play/games/import', json={'file': sent})
    assert response.status_code == 400 and 'version' in response.get_json()['error']


# --- What another app sends can't become HTML or break the game (docs/relay.md, risks) -----------------------

def hostile(sent, change):
    """A copy of a game file, changed by `change(file)`."""
    sent = json.loads(json.dumps(sent))
    change(sent)
    return sent


def set_unit(key, value):
    def change(sent):
        sent['armies'][0]['units'][0][key] = value
    return change


def set_event(key, value):
    def change(sent):
        sent['events'][0][key] = value
    return change


def model_change(model, change):
    def apply(sent):
        sent['events'][0]['models'] = {model: change}
    return apply


@pytest.mark.parametrize('change, message', [
    (set_unit('name', 'Squad<img src=x onerror=alert(1)>'), 'may not contain'),
    (set_unit('uid', 'w" onmouseover="alert(1)'), 'unit ID'),
    (lambda f: f['armies'][1].update(name='</h4><script>x</script>'), 'may not contain'),
    (lambda f: f['armies'][0]['stratagems'].append({'key': 'x" onclick="y', 'kind': 'stratagem', 'name': 'Bad', 'cp': 1}), 'stratagem key'),
    (lambda f: f['armies'][0]['units'][0]['parts'][0].update(models='3" onclick="y'), 'model count'),
    (lambda f: f.update(battlefield={'objectives': [{'id': 'a" x="', 'x': 1, 'y': 1}]}), 'objective or terrain ID'),
    (set_event('action', {'type': 'self_destruct'}), 'unknown action'),
    (set_event('by', 'Blue'), 'nobody'),
    (model_change('p9-nope-0-1', {'alive': False}), "doesn't have"),
    (model_change('p1-w-0-1', {'wounds_left': '1" onclick="y'}), 'wounds'),
    (model_change('p1-w-0-1', {'position': {'x': 'far', 'y': 1}}), 'number'),
    (model_change('p1-w-0-1', {'colour': 'red'}), "can't make"),
    (set_event('rolls', [{'purpose': 'hit', 'need': 3, 'dice': [{'face': '6" onclick="y', 'success': True, 'critical': True}]}]), 'die'),
    (set_event('turn', {'phase': '<b>', 'player': 0, 'round': 1}), 'may not contain'),
    (set_event('turn', {'phase': 'tea', 'player': 0, 'round': 1}), 'turn'),
    (set_event('objectives', {'centre': 7}), 'held by nobody'),
])
def test_a_game_file_that_could_attack_the_page_or_break_the_game_is_refused(two_pcs, change, message):
    mine, theirs = two_pcs
    sent = game_file_of(mine, place(mine, new_game(mine, 0), 'p1-w', 10, 6).get_json())
    response = theirs.post('/api/play/games/import', json={'file': hostile(sent, change)})
    assert response.status_code == 400 and message in response.get_json()['error'], response.get_json()
    assert theirs.get('/api/play/games').get_json() == []


def test_game_files_carry_no_rule_text_and_each_pc_reads_its_own(two_pcs):
    mine, theirs = two_pcs
    game = new_game(mine, 0)
    entry = next(e for e in game['state']['players'][0]['stratagems'] if e['name'] == 'Smokescreen')
    assert 'text' not in entry and entry['ref'] == {'wahapedia_id': entry['ref']['wahapedia_id']}
    assert game['texts'][entry['key']].startswith('WHEN:')  # From this PC's Wahapedia data
    sent = game_file_of(mine, game)  # Events that use one carry its key alone (test_play_loop)
    assert 'WHEN:' not in json.dumps(sent) and 'EFFECT' not in json.dumps(sent)
    their_game = theirs.post('/api/play/games/import', json={'file': sent}).get_json()
    assert their_game['texts'][entry['key']].startswith('WHEN:')  # Their own copy of the text


def test_rule_text_frozen_by_older_games_is_left_out_of_the_file(two_pcs):
    mine, _ = two_pcs
    game = new_game(mine, None)
    from combat_sim.models import SavedGame, db  # An older game: its entries kept their text
    with mine.application.app_context():
        saved = db.session.get(SavedGame, game['id'])
        armies = json.loads(json.dumps(saved.armies))
        for entry in armies[0]['stratagems']:
            entry['text'] = 'WHEN: long ago'
        saved.armies = armies
        db.session.commit()
    assert mine.get(f'/api/play/games/{game["id"]}').get_json()['texts']  # Still shown here
    assert 'long ago' not in json.dumps(game_file_of(mine, game))


def test_a_seat_can_only_undo_its_own_last_action(two_pcs):
    mine, theirs = two_pcs
    game = place(mine, new_game(mine, 0), 'p1-w', 10, 6).get_json()
    their_game = theirs.post('/api/play/games/import', json={'file': game_file_of(mine, game)}).get_json()
    refused = theirs.post(f'/api/play/games/{their_game["id"]}/undo')
    assert refused.status_code == 403 and 'Red' in refused.get_json()['error']
    assert mine.post(f'/api/play/games/{game["id"]}/undo').status_code == 200


def test_the_seat_can_be_changed(two_pcs):
    mine, _ = two_pcs
    game = new_game(mine, 0)
    assert mine.put(f'/api/play/games/{game["id"]}/seat', json={'seat': None}).get_json()['seat'] is None
    assert mine.put(f'/api/play/games/{game["id"]}/seat', json={'seat': 1}).get_json()['seat'] == 1
    assert mine.put(f'/api/play/games/{game["id"]}/seat', json={'seat': 2}).status_code == 400


def test_versions_compare_by_number():
    assert game_file.newer('2.10.0', '2.9.1') and not game_file.newer('2.2.2', '2.2.2') and not game_file.newer('1.0', '2.0')
