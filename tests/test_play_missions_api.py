"""
Play mode's missions through the API: the matchup the new-game form shows (dispositions, primaries and
the pairing's Event Companion layouts), games on a pairing's layout, and the Mission panel's data.
"""

import pytest

from conftest import BSDATA_FIXTURES
from test_play_api import APP_DATA


@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]


def saved_army(client, name, detachments, disposition=None):
    factions = {f['name']: f['id'] for f in client.get('/api/data/factions').get_json()}
    ids = {u['name']: u['id'] for u in client.get(f'/api/units?faction_id={factions["Starfarers"]}').get_json()}
    body = {'name': name, 'faction_id': factions['Starfarers'], 'points_limit': 2000, 'detachment_keys': detachments,
            'disposition': disposition, 'units': [{'uid': 'w', 'unit_id': ids['Void Warriors']}]}
    response = client.post('/api/armies', json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()['id']


def test_the_matchup_names_both_primaries_and_the_pairings_layouts(client):
    red = saved_army(client, 'Red', ['d-vanguard'])  # Purge the Foe
    blue = saved_army(client, 'Blue', ['d-bastion'])  # Take and Hold
    matchup = client.get(f'/api/play/matchup?armies={red}&armies={blue}').get_json()
    assert matchup['dispositions'] == ['Purge the Foe', 'Take and Hold']
    assert matchup['primaries'] == ['Unstoppable Force', 'Immovable Object']
    assert [b['name'] for b in matchup['layouts']] == [f'Take and Hold vs Purge the Foe: Layout {x}' for x in 'ABC']
    plain = saved_army(client, 'Plain', [])
    assert client.get(f'/api/play/matchup?armies={red}&armies={plain}').get_json() == {
        'dispositions': ['Purge the Foe', None], 'primaries': None, 'layouts': []}


def test_a_game_on_the_pairings_layout_with_its_missions(client):
    red, blue = saved_army(client, 'Red', ['d-vanguard']), saved_army(client, 'Blue', ['d-bastion'])
    game = client.post('/api/play/games', json={'armies': [red, blue], 'battlefield': 'pairing:B'}).get_json()
    assert game['state']['battlefield']['name'] == 'Take and Hold vs Purge the Foe: Layout B'
    mission = game['mission']
    assert [p['name'] for p in mission['primaries']] == ['Unstoppable Force', 'Immovable Object']
    first = mission['primaries'][0]['lines'][0]
    assert (first['text'], first['timing'], first['needs']) == ('Destroy an enemy unit this turn', 'End of your turn', None)
    assert mission['caps'] == {'round': 15, 'game': 45} and game['score'] == [0, 0]
    assert set(mission['now'][0]) == {line['id'] for line in mission['primaries'][0]['lines'] if not line['needs']}
    randomly = client.post('/api/play/games', json={'armies': [red, blue], 'battlefield': 'pairing:random'}).get_json()
    assert randomly['state']['battlefield']['name'].startswith('Take and Hold vs Purge the Foe: Layout ')


def test_games_without_dispositions_have_no_mission(client):
    red, plain = saved_army(client, 'Red', ['d-vanguard']), saved_army(client, 'Plain', [])
    game = client.post('/api/play/games', json={'armies': [red, plain]}).get_json()
    assert game['mission'] is None
    assert client.post('/api/play/games', json={'armies': [red, plain], 'battlefield': 'pairing:A'}).status_code == 400
