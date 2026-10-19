"""
Data management API: every confirmed edit is written straight to the data JSON, and the
app rebuilds identical data from that file on the next start.
"""

import json

import pytest

DATA = {
    'schema_version': 1,
    'source': {'workbook': 'test.xlsx', 'generated': '2026-01-01T00:00:00+00:00'},
    'factions': [{'id': 1, 'name': 'Death Guard'}],
    'units': [
        {'id': 1, 'faction_id': 1, 'name': 'Plague Marines', 'toughness': 5, 'save': 3, 'wounds': 2,
         'points_cost': 90, 'default_models': 5, 'points_by_size': [{'models': 5, 'points': 90}],
         'keywords': ['Infantry'], 'modifiers': [], 'can_lead': [], 'abilities': 'Disgustingly Resilient',
         'ranged_weapons': [{'weapon_id': 1, 'qty': 5}], 'melee_weapons': [{'weapon_id': 1, 'qty': 5}]},
    ],
    'ranged_weapons': [{'id': 1, 'faction_id': 1, 'name': 'Boltgun', 'attacks': '2', 'ballistic_skill': 3,
                        'strength': 4, 'armor_penetration': 1, 'damage': '1', 'range_inches': None,
                        'abilities': ['Lethal Hits']}],
    'melee_weapons': [{'id': 1, 'faction_id': 1, 'name': 'Plague Knife', 'attacks': '3', 'weapon_skill': 3,
                       'strength': 4, 'armor_penetration': 1, 'damage': '1', 'abilities': ['Lethal Hits']}],
    'faction_rules': [{'id': 1, 'faction_id': 1, 'name': 'Contagion', 'applies_to': 'Target', 'phase': 'Both',
                       'modifier': 'Toughness Modifier -1'}],
    'stratagems': [{'id': 1, 'faction_id': None, 'name': 'Smokescreen', 'cp': '1', 'applies_to': 'Target',
                    'phase': 'Shooting', 'modifier': 'Stealth', 'notes': None}],
    'keywords': [{'id': 1, 'name': 'Lethal Hits', 'category': 'Weapon', 'applies_to': 'Both', 'format': 'Lethal Hits',
                  'summary': 'Critical hits auto-wound.', 'sim_step': 'Hit', 'modelled': True, 'rule_ref': '24.23'}],
    'target_profiles': [],
}


@pytest.fixture
def setup(make_client):
    client, data_file = make_client(DATA)
    saved = lambda: json.loads(data_file.read_text(encoding='utf-8'))
    return client, saved


def unit_payload(**overrides):
    payload = {'faction_id': 1, 'name': 'Blightlord Terminators', 'toughness': 6, 'save': 2, 'wounds': 3,
               'invulnerable_save': 4, 'move': '4', 'oc': 1, 'leadership': 6,
               'points_by_size': [{'models': 5, 'points': 185}, {'models': 10, 'points': 370}],
               'keywords': 'Infantry, Terminator', 'modifiers': ['Feel No Pain 5+'], 'can_lead': [],
               'abilities': 'Deep Strike', 'ranged_weapons': [{'weapon_id': 1, 'qty': 5}], 'melee_weapons': []}
    payload.update(overrides)
    return payload


def test_pages_render(setup):
    client, _ = setup
    for page in ('/', '/matchups', '/scenarios', '/data'):
        assert client.get(page).status_code == 200, page


def test_startup_loads_the_data_file(setup):
    client, _ = setup
    unit = client.get('/api/units/1').get_json()
    assert unit['abilities'] == 'Disgustingly Resilient'
    assert [(w['name'], w['qty']) for w in unit['ranged_weapons']] == [('Boltgun', 5)]
    assert client.get('/api/data/keywords').get_json()[0]['name'] == 'Lethal Hits'
    assert client.get('/api/units?search=plague').get_json()[0]['name'] == 'Plague Marines'


def test_faction_crud_writes_through(setup):
    client, saved = setup
    created = client.post('/api/data/factions', json={'name': 'Orks'}).get_json()
    assert {'id': created['id'], 'name': 'Orks'} in saved()['factions']
    assert client.post('/api/data/factions', json={'name': 'Orks'}).status_code == 400  # duplicate name

    client.put(f"/api/data/factions/{created['id']}", json={'name': 'Orkz'})
    assert any(f['name'] == 'Orkz' for f in saved()['factions'])
    counts = {f['name']: f['unit_count'] for f in client.get('/api/data/factions').get_json()}
    assert counts == {'Death Guard': 1, 'Orkz': 0}


def test_unit_create_edit_delete(setup):
    client, saved = setup
    created = client.post('/api/data/units', json=unit_payload()).get_json()
    record = next(u for u in saved()['units'] if u['id'] == created['id'])
    assert record['keywords'] == ['Infantry', 'Terminator']  # comma text becomes a list
    assert record['points_cost'] == 185 and record['default_models'] == 5  # first size row
    assert record['ranged_weapons'] == [{'weapon_id': 1, 'qty': 5}]

    client.put(f"/api/data/units/{created['id']}", json=unit_payload(name='Blightlords', ranged_weapons=[]))
    record = next(u for u in saved()['units'] if u['id'] == created['id'])
    assert record['name'] == 'Blightlords' and record['ranged_weapons'] == []

    listed = {u['name']: u for u in client.get('/api/data/units').get_json()}
    assert listed['Plague Marines']['ranged_weapon_count'] == 1

    client.delete(f"/api/data/units/{created['id']}")
    assert all(u['id'] != created['id'] for u in saved()['units'])


def test_loadout_only_takes_the_units_own_faction_weapons(setup):
    client, saved = setup
    orks = client.post('/api/data/factions', json={'name': 'Orks'}).get_json()
    shoota = client.post('/api/data/weapons', json={'type': 'ranged', 'faction_id': orks['id'], 'name': 'Shoota',
                                                    'attacks': '2', 'skill': '5', 'strength': 4, 'ap': 0,
                                                    'damage': '1'}).get_json()
    unit = client.post('/api/data/units', json=unit_payload(
        ranged_weapons=[{'weapon_id': 1, 'qty': 5}, {'weapon_id': shoota['id'], 'qty': 5}])).get_json()
    record = next(u for u in saved()['units'] if u['id'] == unit['id'])
    assert record['ranged_weapons'] == [{'weapon_id': 1, 'qty': 5}]


def test_weapon_crud_and_moving_faction(setup):
    client, saved = setup
    created = client.post('/api/data/weapons', json={'type': 'ranged', 'faction_id': 1, 'name': 'Plague Spewer',
                                                     'range': '12', 'attacks': 'D6', 'skill': '-', 'strength': 5,
                                                     'ap': -1, 'damage': '1',
                                                     'abilities': 'Torrent, Anti-Infantry 2+'}).get_json()
    weapon = next(w for w in saved()['ranged_weapons'] if w['id'] == created['id'])
    assert weapon['ballistic_skill'] is None and weapon['range_inches'] == 12 and weapon['armor_penetration'] == 1
    assert weapon['abilities'] == ['Torrent', 'Anti-Infantry 2+']
    assert client.get(f"/api/data/weapons/ranged/{created['id']}").get_json()['faction'] == 'Death Guard'

    # Moving the Boltgun to another faction drops it from Death Guard loadouts
    orks = client.post('/api/data/factions', json={'name': 'Orks'}).get_json()
    client.put('/api/data/weapons/ranged/1', json={'faction_id': orks['id'], 'name': 'Boltgun', 'attacks': '2',
                                                   'skill': '3', 'strength': 4, 'ap': 1, 'damage': '1'})
    assert next(u for u in saved()['units'] if u['id'] == 1)['ranged_weapons'] == []

    client.delete('/api/data/weapons/melee/1')
    assert saved()['melee_weapons'] == []
    assert next(u for u in saved()['units'] if u['id'] == 1)['melee_weapons'] == []

    listed = client.get('/api/data/weapons?type=ranged&faction_id=1').get_json()
    assert [w['name'] for w in listed] == ['Plague Spewer']


def test_rules_and_stratagems_crud(setup):
    client, saved = setup
    rule = client.post('/api/data/rules', json={'faction_id': 1, 'name': 'Nurgle\'s Gift', 'applies_to': 'Attacker',
                                                'phase': 'Fighting', 'modifier': 'Lethal Hits'}).get_json()
    client.put(f"/api/data/rules/{rule['id']}", json={'name': 'Nurgles Gift', 'applies_to': 'Attacker',
                                                      'phase': 'Both', 'modifier': ''})
    stored = next(r for r in saved()['faction_rules'] if r['id'] == rule['id'])
    assert stored['name'] == 'Nurgles Gift' and stored['modifier'] is None
    assert [r['name'] for r in client.get('/api/data/rules?faction_id=1').get_json()] == ['Contagion', 'Nurgles Gift']

    strat = client.post('/api/data/stratagems', json={'faction_id': 1, 'name': 'Cloud of Flies', 'cp': 1,
                                                      'applies_to': 'Target', 'phase': 'Shooting',
                                                      'modifier': 'Stealth', 'notes': 'n'}).get_json()
    client.put(f"/api/data/stratagems/{strat['id']}", json={'name': 'Cloud of Flies', 'cp': '2'})
    assert next(s for s in saved()['stratagems'] if s['id'] == strat['id'])['cp'] == '2'
    assert [s['name'] for s in client.get('/api/data/stratagems?core=1').get_json()] == ['Smokescreen']
    assert [s['name'] for s in client.get('/api/data/stratagems?faction_id=1').get_json()] == ['Cloud of Flies']

    client.delete(f"/api/data/rules/{rule['id']}")
    client.delete(f"/api/data/stratagems/{strat['id']}")
    assert len(saved()['faction_rules']) == 1 and len(saved()['stratagems']) == 1


def test_deleting_a_faction_cascades(setup):
    client, saved = setup
    client.delete('/api/data/factions/1')
    data = saved()
    assert data['factions'] == [] and data['units'] == [] and data['ranged_weapons'] == []
    assert data['faction_rules'] == []
    assert [s['name'] for s in data['stratagems']] == ['Smokescreen']  # core stratagems stay


def test_reload_round_trips(setup, make_client):
    client, saved = setup
    client.post('/api/data/units', json=unit_payload())
    before = client.get('/api/data/export').get_json()
    reloaded, _ = make_client(saved())
    assert reloaded.get('/api/data/export').get_json() == before


def test_import_replaces_data_and_checks_version(setup):
    client, saved = setup
    replacement = dict(DATA, factions=[{'id': 5, 'name': 'Orks'}], units=[], ranged_weapons=[], melee_weapons=[],
                       faction_rules=[])
    assert client.post('/api/data/import', json=replacement).status_code == 200
    assert [f['name'] for f in saved()['factions']] == ['Orks']

    assert client.post('/api/data/import', json={'schema_version': 99}).status_code == 400
    assert client.post('/api/data/import', json={'units': []}).status_code == 400
    assert [f['name'] for f in saved()['factions']] == ['Orks']  # unchanged by rejected imports


# --- Rules the pages get from the server (not duplicated in JavaScript) -----------------

def test_unit_api_sends_default_ticks_and_points_table(setup):
    client, _ = setup
    client.post('/api/data/weapons', json={'type': 'ranged', 'faction_id': 1, 'name': 'Plasma Gun - Standard',
                                           'attacks': '1', 'skill': '3', 'strength': 7, 'ap': 2, 'damage': '1'})
    client.post('/api/data/weapons', json={'type': 'ranged', 'faction_id': 1, 'name': 'Plasma Gun - Supercharged',
                                           'attacks': '1', 'skill': '3', 'strength': 8, 'ap': 3, 'damage': '2'})
    weapons = {w['name']: w['id'] for w in client.get('/api/data/weapons?type=ranged').get_json()}
    unit = client.post('/api/data/units', json=unit_payload(
        ranged_weapons=[{'weapon_id': weapons[n], 'qty': 1} for n in ('Boltgun', 'Plasma Gun - Standard', 'Plasma Gun - Supercharged')],
        points_by_size=[{'models': 5, 'points': 185}, {'models': 10, 'points': 370}])).get_json()

    data = client.get(f"/api/units/{unit['id']}").get_json()
    assert {w['name']: w['default_use'] for w in data['ranged_weapons']} == {
        'Boltgun': True, 'Plasma Gun - Standard': True, 'Plasma Gun - Supercharged': False}
    table = data['points_by_model_count']
    assert table[0] is None and len(table) == 100
    assert (table[5], table[10], table[7]) == (185, 370, 259)  # 185 + 2 x 37


def test_keyword_check_endpoint(setup):
    client, _ = setup
    check = lambda text, mode='': client.post('/api/keywords/check', json={'text': text, 'mode': mode}).get_json()
    assert check('Lethal Hits, Anti-Infantry 2+') == {'unknown': ['Anti-Infantry 2+'], 'capitals': []}  # only Lethal Hits known here
    assert check('lethal hits') == {'unknown': [], 'capitals': [['lethal hits', 'Lethal Hits']]}
    assert check('Infantry, Lethal Hitz 1', mode='unit') == {'unknown': ['Lethal Hitz 1'], 'capitals': []}
