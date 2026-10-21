"""
Play mode's battlefields API (/api/play/battlefields): the built-in deployment maps, battlefields saved
with terrain, checks on what's saved, and games played on them.
"""

import copy

import pytest

from conftest import BSDATA_FIXTURES
from test_play_api import APP_DATA, saved_army

from combat_sim.play.battlefields import BUILTIN


@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]


def ruin(id_='t1', x=27, y=20, category='dense', footprint='medium'):
    return {'id': id_, 'footprint': footprint, 'x': x, 'y': y, 'angle': 0, 'category': category, 'height': 4}


def with_terrain(*pieces, name='My table'):
    battlefield = copy.deepcopy(BUILTIN['dawn-of-war'])
    battlefield.update(name=name, terrain=list(pieces))
    return {k: v for k, v in battlefield.items() if k not in ('id', 'source')}


def test_the_built_in_maps_are_listed_and_read_only(client):
    listed = client.get('/api/play/battlefields').get_json()
    assert [b['key'] for b in listed][:6] == [f'builtin:{key}' for key in BUILTIN]
    assert listed[0] == {'key': 'builtin:dawn-of-war', 'name': 'Dawn of War', 'source': 'builtin', 'terrain': 0}
    dawn = client.get('/api/play/battlefields/builtin:dawn-of-war').get_json()
    assert dawn['zones']['attacker'] == [[0, 0], [60, 0], [60, 12], [0, 12]]
    assert client.put('/api/play/battlefields/builtin:dawn-of-war', json=with_terrain()).status_code == 403


def test_saving_editing_and_deleting_a_battlefield(client):
    saved = client.post('/api/play/battlefields', json=with_terrain(ruin())).get_json()
    assert saved['name'] == 'My table' and saved['source'] == 'custom' and saved['key'].startswith('saved:')
    central = next(o for o in saved['objectives'] if o['kind'] == 'central')
    assert central['terrain'] == 't1'  # Terrain set over an objective makes it a terrain objective
    assert [b['name'] for b in client.get('/api/play/battlefields').get_json()][-1] == 'My table'
    edited = client.put(f'/api/play/battlefields/{saved["key"]}', json=with_terrain(name='Renamed')).get_json()
    assert edited['name'] == 'Renamed' and edited['terrain'] == []
    assert next(o for o in edited['objectives'] if o['kind'] == 'central')['terrain'] is None
    assert client.delete(f'/api/play/battlefields/{saved["key"]}').status_code == 200
    assert client.get(f'/api/play/battlefields/{saved["key"]}').status_code == 404


@pytest.mark.parametrize('change,message', [
    (lambda b: b['terrain'].append(ruin(footprint='huge')), 'footprint'),
    (lambda b: b['terrain'].append(ruin(category='lava')), 'category'),
    (lambda b: b['zones']['attacker'].append([70, 5]), 'board'),
    (lambda b: b['objectives'].append({'id': 'x', 'kind': 'secret', 'role': None, 'x': 1, 'y': 1}), 'kind'),
    (lambda b: b.update(name=''), 'name'),
])
def test_battlefields_that_dont_make_sense_are_refused(client, change, message):
    battlefield = with_terrain()
    change(battlefield)
    response = client.post('/api/play/battlefields', json=battlefield)
    assert response.status_code == 400 and message in response.get_json()['error']


def test_a_game_is_played_on_the_chosen_battlefield_and_keeps_its_own_copy(client):
    saved = client.post('/api/play/battlefields', json=with_terrain(ruin())).get_json()
    red = saved_army(client, 'Red', {'uid': 'w', 'unit': 'Void Warriors'})
    blue = saved_army(client, 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    game = client.post('/api/play/games', json={'armies': [red, blue], 'battlefield': saved['key']}).get_json()
    assert game['state']['battlefield']['name'] == 'My table' and len(game['terrain']) == 1
    assert next(o for o in game['state']['objectives'] if o['kind'] == 'central')['terrain'] == 't1'
    client.put(f'/api/play/battlefields/{saved["key"]}', json=with_terrain(name='Changed later'))
    again = client.get(f'/api/play/games/{game["id"]}').get_json()
    assert again['state']['battlefield']['name'] == 'My table'  # The game's copy doesn't change
    tipping = client.post('/api/play/games', json={'armies': [red, blue], 'battlefield': 'builtin:tipping-point'}).get_json()
    assert tipping['state']['battlefield']['name'] == 'Tipping Point'
    assert client.post('/api/play/games', json={'armies': [red, blue], 'battlefield': 'saved:999'}).status_code == 400
