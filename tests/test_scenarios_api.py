"""
Saved scenarios (Simulator) and saved comparisons (Matchups). These live only in the
database, not the data JSON.
"""

import pytest

DATA = {'schema_version': 1, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [],
        'melee_weapons': [], 'faction_rules': [], 'stratagems': [], 'keywords': [], 'target_profiles': []}

SCENARIO = {
    'name': 'Bolters vs Boyz',
    'attacker': {'unit_name': 'Intercessors', 'weapon_name': 'Bolt rifle x5',
                 'weapons': [{'name': 'Bolt rifle', 'type': 'ranged', 'attacks': '2', 'skill': 3, 'strength': 4,
                              'ap': 1, 'damage': '1', 'abilities': [], 'models': 5}]},
    'defender': {'unit_name': 'Boyz', 'toughness': 5, 'save': 5, 'wounds': 1, 'model_count': 10,
                 'extra_modifiers': 'Feel No Pain 6+'},
    'conditions': {'half_range': True},
}


@pytest.fixture
def client(make_client):
    return make_client(DATA)[0]


def test_scenarios_list_includes_their_setup(client):
    """Regression: a second GET /api/scenarios route used to shadow this one and drop the setup."""
    created = client.post('/api/scenarios', json=SCENARIO).get_json()
    listed = client.get('/api/scenarios').get_json()
    assert [s['id'] for s in listed] == [created['id']]
    assert listed[0]['attacker_config']['unit_name'] == 'Intercessors'
    assert listed[0]['defender_config']['extra_modifiers'] == 'Feel No Pain 6+'
    assert listed[0]['battle_conditions'] == {'half_range': True}


def test_saved_scenario_reruns(client):
    """The Scenarios page re-runs a scenario by posting its saved configs to /api/simulate."""
    client.post('/api/scenarios', json=SCENARIO)
    scenario = client.get('/api/scenarios').get_json()[0]
    result = client.post('/api/simulate', json={'attacker': scenario['attacker_config'],
                                                'defender': scenario['defender_config'],
                                                'conditions': scenario['battle_conditions'],
                                                'method': 'analytical'}).get_json()
    assert result['weapons'][0]['name'] == 'Bolt rifle'
    assert result['summary']['average_damage'] > 0


def test_scenario_update_and_delete(client):
    created = client.post('/api/scenarios', json=SCENARIO).get_json()
    client.put(f"/api/scenarios/{created['id']}", json={'name': 'Renamed', 'results': {'summary': {}}})
    assert client.get('/api/scenarios').get_json()[0]['name'] == 'Renamed'
    client.delete(f"/api/scenarios/{created['id']}")
    assert client.get('/api/scenarios').get_json() == []


def test_comparisons_save_list_load_delete(client):
    comparison = {'name': 'Army vs Orks', 'attackers': [{'unit_id': 1, 'weapons': []}],
                  'defenders': [{'unit_id': 2, 'model_count': 10, 'extra_modifiers': 'Stealth'}],
                  'conditions': {'cover': True, 'rules_ticked': {'attacker': ['rule:1'], 'defender': []}},
                  'results': None}
    created = client.post('/api/comparisons', json=comparison).get_json()

    listed = client.get('/api/comparisons').get_json()
    assert listed[0]['attacker_count'] == 1 and listed[0]['defender_count'] == 1

    loaded = client.get(f"/api/comparisons/{created['id']}").get_json()
    assert loaded['defenders'][0]['extra_modifiers'] == 'Stealth'
    assert loaded['battle_conditions']['rules_ticked'] == {'attacker': ['rule:1'], 'defender': []}

    client.delete(f"/api/comparisons/{created['id']}")
    assert client.get('/api/comparisons').get_json() == []
