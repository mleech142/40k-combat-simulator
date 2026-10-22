"""
Play mode's games API (/api/play/...): games made from two saved armies, kept as their frozen
armies and event log, actions checked and logged, undo. Built on the made-up BSData factions.
"""

import pytest

from conftest import BSDATA_FIXTURES

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}


@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]


def saved_army(client, name, *units):
    factions = {f['name']: f['id'] for f in client.get('/api/data/factions').get_json()}
    ids = {u['name']: u['id'] for u in client.get(f'/api/units?faction_id={factions["Starfarers"]}').get_json()}
    body = {'name': name, 'faction_id': factions['Starfarers'], 'points_limit': 2000,
            'units': [dict(unit, unit_id=ids[unit.pop('unit')]) for unit in units]}
    response = client.post('/api/armies', json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()['id']


@pytest.fixture
def game(client):
    red = saved_army(client, 'Red', {'uid': 'w', 'unit': 'Void Warriors'}, {'uid': 'c', 'unit': 'Star Captain'})
    blue = saved_army(client, 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    response = client.post('/api/play/games', json={'name': 'First game', 'armies': [red, blue], 'setup': False})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


# --- Games ------------------------------------------------------------------------------

def test_new_game_from_two_saved_armies(client, game):
    assert game['name'] == 'First game' and game['events'] == []
    assert [p['name'] for p in game['state']['players']] == ['Red', 'Blue']
    assert [u['id'] for u in game['state']['units']] == ['p1-w', 'p1-c', 'p2-g']
    assert all(m['position'] is None for u in game['state']['units'] for m in u['models'])  # Not placed yet
    assert game['board'] == {'width': 60, 'depth': 44}
    assert game['zones'][0] == [[0, 0], [60, 0], [60, 12], [0, 12]]  # Dawn of War: Red attacks from the bottom edge
    assert game['ready'] == ['p1-w', 'p1-c']  # Red deploys first
    assert game['objective_oc']['central-1'] == [0, 0] and game['objective_size'] == {'marker_mm': 40, 'range': 3}
    assert game['score'] == [0, 0] and {'key': 'cover', 'name': 'Benefit of cover', 'side': 'Target'} in game['manual_effects']
    assert game['moves'] == {'p1-w': [6], 'p1-c': [6], 'p2-g': [5]}  # Each part's Move, in inches
    assert game['fly'] == []  # Units that can take to the skies
    assert not any(p['fights_first'] for u in game['state']['units'] for p in u['parts'])  # Frozen from the datasheet
    assert game['outlines']['p1-w'] == [{'shape': 'round', 'length': 32 / 25.4, 'width': 32 / 25.4, 'estimated': False}]
    listed = client.get('/api/play/games').get_json()
    assert [(g['id'], g['name'], g['players']) for g in listed] == [(game['id'], 'First game', ['Red', 'Blue'])]
    assert client.get(f'/api/play/games/{game["id"]}').get_json() == game


def test_a_new_game_starts_with_the_mission_sequence(client):
    red = saved_army(client, 'Red', {'uid': 'w', 'unit': 'Void Warriors'})
    blue = saved_army(client, 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    game = client.post('/api/play/games', json={'armies': [red, blue]}).get_json()
    assert game['state']['turn']['phase'] == 'setup' and game['ready'] == []
    url = f'/api/play/games/{game["id"]}/actions'
    client.post(url, json={'type': 'roll_objectives'})
    game = client.post(url, json={'type': 'roll_off'}).get_json()
    winner = game['events'][-1]['roll_off']['winner']
    game = client.post(url, json={'type': 'choose_role', 'role': 'defender'}).get_json()
    assert game['state']['roles'] == {'attacker': 1 - winner, 'defender': winner}
    assert game['state']['turn']['phase'] == 'deployment' and game['state']['turn']['player'] == winner
    assert game['zones'][winner] == [[0, 32], [60, 32], [60, 44], [0, 44]]  # The defender's zone
    assert client.get(f'/api/play/games/{game["id"]}').get_json()['state'] == game['state']  # Replays the same


def test_deleting_a_game(client, game):
    assert client.delete(f'/api/play/games/{game["id"]}').status_code == 200
    assert client.get('/api/play/games').get_json() == []
    assert client.get(f'/api/play/games/{game["id"]}').status_code == 404


def test_a_game_needs_two_saved_armies(client):
    red = saved_army(client, 'Red', {'uid': 'w', 'unit': 'Void Warriors'})
    response = client.post('/api/play/games', json={'name': 'Solo', 'armies': [red]})
    assert response.status_code == 400 and 'two' in response.get_json()['error']
    response = client.post('/api/play/games', json={'name': 'Ghost', 'armies': [red, 99999]})
    assert response.status_code == 400


# --- Placing units -----------------------------------------------------------------------
# Red deploys within 12" of the bottom edge (y 0-12), Blue within 12" of the top (y 32-44).
# Void Warriors are 5 models on 32mm (1.26") bases.

def place(client, game, unit, spots, angle=0):
    models = [m['id'] for u in game['state']['units'] if u['id'] == unit for m in u['models']]
    body = {'type': 'place', 'unit': unit,
            'models': [{'id': m, 'x': x, 'y': y, 'angle': angle} for m, (x, y) in zip(models, spots)]}
    return client.post(f'/api/play/games/{game["id"]}/actions', json=body)


ROW = [(10 + 1.5 * i, 6) for i in range(5)]


def test_placing_a_unit_in_its_deployment_zone(client, game):
    response = place(client, game, 'p1-w', ROW)
    assert response.status_code == 200, response.get_json()
    after = response.get_json()
    assert len(after['events']) == 1 and after['events'][0]['rolls'] == []
    warriors = after['state']['units'][0]
    assert [m['position'] for m in warriors['models']][:2] == [{'x': 10, 'y': 6, 'angle': 0}, {'x': 11.5, 'y': 6, 'angle': 0}]
    assert client.get(f'/api/play/games/{game["id"]}').get_json() == after  # Saved


def test_a_game_says_which_objectives_each_unit_is_in_range_of(client, game):
    assert game['unit_objectives'] == {'p1-w': [], 'p1-c': [], 'p2-g': []}  # Nobody placed yet
    home = next(o for o in game['state']['objectives'] if o['id'] == 'home-attacker')
    response = place(client, game, 'p1-w', [(home['x'] - 3 + 1.5 * i, home['y'] - 2) for i in range(5)])
    assert response.status_code == 200, response.get_json()
    after = response.get_json()
    assert after['unit_objectives'] == {'p1-w': ['home-attacker'], 'p1-c': [], 'p2-g': []}


@pytest.mark.parametrize('spots,message', [
    ([(10 + 1.5 * i, 20) for i in range(5)], 'deployment zone'),  # Middle of the board
    ([(10 + 1.5 * i, 11.8) for i in range(5)], 'deployment zone'),  # Bases stick out past 12"
    ([(0.3 + 1.5 * i, 6) for i in range(5)], 'board'),  # Off the left edge
    ([(10 + 0.5 * i, 6) for i in range(5)], 'overlap'),  # Bases on top of each other
    (ROW[:4], 'every model'),  # One left out
])
def test_placements_the_rules_dont_allow_are_refused(client, game, spots, message):
    response = place(client, game, 'p1-w', spots)
    assert response.status_code == 400 and message in response.get_json()['error']
    assert client.get(f'/api/play/games/{game["id"]}').get_json()['events'] == []


def test_a_unit_cant_be_placed_on_another(client, game):
    assert place(client, game, 'p1-w', ROW).status_code == 200
    assert place(client, game, 'p2-g', [(10, 38), (11.5, 38)]).status_code == 200  # Players take turns
    on_top = place(client, game, 'p1-c', [(10, 6)])
    assert on_top.status_code == 400 and 'overlap' in on_top.get_json()['error']
    assert place(client, game, 'p1-c', [(10, 8)]).status_code == 200
    assert place(client, game, 'p1-w', [(x + 0.5, y) for x, y in ROW]).status_code == 200  # Its own old spots don't count


def test_undo_takes_back_the_last_placement(client, game):
    place(client, game, 'p1-w', ROW)
    response = client.post(f'/api/play/games/{game["id"]}/undo')
    assert response.status_code == 200 and response.get_json()['events'] == []
    assert client.post(f'/api/play/games/{game["id"]}/undo').status_code == 400  # Nothing left to undo


# --- The experimental setting ------------------------------------------------------------

def test_play_link_shows_only_when_the_setting_is_on(client):
    assert 'href="/play"' not in client.get('/armies').get_data(as_text=True)
    assert 'id="play-setting"' in client.get('/setup').get_data(as_text=True)
    response = client.post('/api/play/setting', json={'enabled': True})
    assert response.status_code == 200 and response.get_json() == {'enabled': True}
    assert 'href="/play"' in client.get('/armies').get_data(as_text=True)
    assert 'js/play.js' in client.get('/play').get_data(as_text=True)
    client.post('/api/play/setting', json={'enabled': False})
    assert 'href="/play"' not in client.get('/armies').get_data(as_text=True)


def test_an_advance_roll_stands_but_the_move_after_it_can_be_undone(client, game):
    act = lambda body: client.post(f'/api/play/games/{game["id"]}/actions', json=body)  # noqa: E731
    place(client, game, 'p1-w', ROW)
    place(client, game, 'p2-g', [(10, 38), (11.5, 38)])
    place(client, game, 'p1-c', [(30, 6)])
    for body in ({'type': 'end_deployment'}, {'type': 'next_phase'}):
        assert act(body).status_code == 200, act(body).get_json()
    rolled = act({'type': 'advance', 'unit': 'p1-w'}).get_json()
    roll = rolled['state']['units'][0]['activity']['advance_roll']
    models = [m['id'] for m in rolled['state']['units'][0]['models']]
    moved = act({'type': 'move', 'unit': 'p1-w', 'mode': 'advance',
                 'models': [{'id': m, 'x': x, 'y': y + 1} for m, (x, y) in zip(models, ROW)]})
    assert moved.status_code == 200, moved.get_json()
    undone = client.post(f'/api/play/games/{game["id"]}/undo').get_json()
    activity = undone['state']['units'][0]['activity']
    assert activity['advance_roll'] == roll and 'moved' not in activity  # The move is gone, the roll stays
    assert client.post(f'/api/play/games/{game["id"]}/undo').status_code == 400


def test_shooting_through_the_api_logs_each_attack_with_its_dice(client, game):
    act = lambda body: client.post(f'/api/play/games/{game["id"]}/actions', json=body)  # noqa: E731
    place(client, game, 'p1-w', ROW)
    place(client, game, 'p2-g', [(10, 38), (11.5, 38)])  # Crew guns (36") reach the Void Warriors (~30.7")
    place(client, game, 'p1-c', [(30, 6)])
    assert act({'type': 'end_deployment'}).status_code == 200
    for _ in range(9):  # Red's phases (the Fight phase has three steps), then Blue's Command and Movement
        assert act({'type': 'next_phase'}).status_code == 200
    options = client.get(f'/api/play/games/{game["id"]}/shooting/p2-g').get_json()
    assert options['reason'] is None
    assert options['weapons'][0]['targets']['p1-w'] == {'ok': True, 'models': 2, 'reason': None}
    assert client.get(f'/api/play/games/{game["id"]}/shooting/nobody').status_code == 404
    shot = act({'type': 'shoot', 'unit': 'p2-g', 'target': 'p1-w'})
    assert shot.status_code == 200, shot.get_json()
    event = shot.get_json()['events'][-1]
    attack = next(a for a in event['attacks'] if a['name'] == 'Crew gun')
    assert (attack['target'], attack['models'], attack['attacks']) == ('p1-w', 2, 4)  # 2 crew, 2 shots each
    first, last = attack['rolls']
    assert event['rolls'][first]['purpose'] == 'hit' and last > first
    again = act({'type': 'shoot', 'unit': 'p2-g', 'target': 'p1-w'})
    assert again.status_code == 400 and 'already shot' in again.get_json()['error']
    assert client.post(f'/api/play/games/{game["id"]}/undo').status_code == 400  # Dice were rolled
