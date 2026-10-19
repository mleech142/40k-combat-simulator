"""
Rules and stratagems in use (stage 4): which entries can be ticked, and how ticked
ones change the Simulator, the Matchups matrix and the cell details.

Each test builds its own small data file in a temporary folder.
"""

import pytest

GUARD, ORKS = 1, 2

DATA = {
    'schema_version': 1,
    'source': None,
    'factions': [{'id': GUARD, 'name': 'Guard'}, {'id': ORKS, 'name': 'Orks'}],
    'units': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Guardsmen', 'toughness': 3, 'save': 5, 'wounds': 2,
         'default_models': 10, 'keywords': ['Infantry'], 'modifiers': [],
         'ranged_weapons': [{'weapon_id': 1, 'qty': 10}], 'melee_weapons': [{'weapon_id': 1, 'qty': 10}]},
        {'id': 2, 'faction_id': ORKS, 'name': 'Boyz', 'toughness': 5, 'save': 5, 'wounds': 1,
         'default_models': 10, 'keywords': ['Infantry'], 'modifiers': [],
         'ranged_weapons': [{'weapon_id': 2, 'qty': 10}], 'melee_weapons': []},
    ],
    'ranged_weapons': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Lasgun', 'attacks': '1', 'ballistic_skill': 4, 'strength': 3,
         'armor_penetration': 0, 'damage': '1', 'abilities': []},
        {'id': 2, 'faction_id': ORKS, 'name': 'Shoota', 'attacks': '2', 'ballistic_skill': 5, 'strength': 4,
         'armor_penetration': 0, 'damage': '1', 'abilities': []},
    ],
    'melee_weapons': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Bayonet', 'attacks': '1', 'weapon_skill': 4, 'strength': 3,
         'armor_penetration': 0, 'damage': '1', 'abilities': []},
    ],
    'faction_rules': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Take Aim', 'applies_to': 'Attacker', 'phase': 'Shooting',
         'modifier': 'Damage Modifier +1'},
        {'id': 2, 'faction_id': GUARD, 'name': 'Fix Bayonets', 'applies_to': 'Attacker', 'phase': 'Fighting',
         'modifier': 'Damage Modifier +1'},
        {'id': 3, 'faction_id': ORKS, 'name': 'Waaagh', 'applies_to': 'Attacker', 'phase': 'Both',
         'modifier': 'Damage Modifier +1'},
    ],
    'stratagems': [
        {'id': 1, 'faction_id': None, 'name': 'Smokescreen', 'cp': '1', 'applies_to': 'Target', 'phase': 'Shooting',
         'modifier': 'Stealth'},
        {'id': 2, 'faction_id': None, 'name': 'Command Re-roll', 'cp': '1', 'applies_to': 'Attacker', 'phase': 'Both',
         'modifier': None},
        {'id': 3, 'faction_id': ORKS, 'name': 'Ere We Go', 'cp': '1', 'applies_to': 'Attacker', 'phase': 'Both',
         'modifier': 'Precision'},
    ],
    'keywords': [
        {'id': 1, 'name': 'Damage Modifier', 'modelled': True},
        {'id': 2, 'name': 'Stealth', 'modelled': True},
        {'id': 3, 'name': 'Precision', 'modelled': False},
    ],
}


@pytest.fixture
def client(make_client):
    return make_client(DATA)[0]


def simulate(client, weapon_type='ranged', attacker_rules=None, target_rules=None, faction_id=GUARD):
    payload = {
        'attacker': {'attacks': '1', 'skill': 4, 'strength': 3, 'ap': 0, 'damage': '1', 'abilities': [],
                     'models': 10, 'type': weapon_type, 'faction_id': faction_id},
        'defender': {'toughness': 3, 'save': 7, 'wounds': 2, 'model_count': 10, 'faction_id': ORKS},
        'conditions': {}, 'method': 'analytical',
        'attacker_rules': attacker_rules or {}, 'target_rules': target_rules or {},
    }
    return client.post('/api/simulate', json=payload).get_json()


def test_available_lists_side_faction_and_core(client):
    attacker = client.get(f'/api/rules/available?side=Attacker&faction_id={GUARD}').get_json()
    assert {e['name'] for e in attacker} == {'Take Aim', 'Fix Bayonets', 'Command Re-roll'}
    effects = {e['name']: e['has_effect'] for e in attacker}
    assert effects == {'Take Aim': True, 'Fix Bayonets': True, 'Command Re-roll': False}

    target = client.get('/api/rules/available?side=Target').get_json()
    assert [(e['name'], e['kind'], e['has_effect']) for e in target] == [('Smokescreen', 'core', True)]

    orks = client.get(f'/api/rules/available?side=Attacker&faction_id={ORKS}').get_json()
    assert {e['name']: e['has_effect'] for e in orks}['Ere We Go'] is False  # Precision isn't modelled


def test_attacker_rule_applies_only_in_its_phase(client):
    base = simulate(client)['summary']['average_damage']
    shooting = simulate(client, attacker_rules={'rule_ids': [1]})
    assert shooting['applied']['attacker'] == ['Damage Modifier +1']
    assert shooting['summary']['average_damage'] == pytest.approx(base * 2, abs=0.02)

    fighting_rule_on_gun = simulate(client, attacker_rules={'rule_ids': [2]})
    assert fighting_rule_on_gun['applied']['attacker'] == []
    melee = simulate(client, weapon_type='melee', attacker_rules={'rule_ids': [2]})
    assert melee['applied']['attacker'] == ['Damage Modifier +1']


def test_target_stratagem_applies_to_shooting_only(client):
    base = simulate(client)['summary']['average_damage']
    smoked = simulate(client, target_rules={'stratagem_ids': [1]})
    assert smoked['applied']['target'] == ['Stealth']
    assert smoked['summary']['average_damage'] == pytest.approx(base * 2 / 3, abs=0.02)  # BS4+ -> 5+
    melee = simulate(client, weapon_type='melee', target_rules={'stratagem_ids': [1]})
    assert melee['applied']['target'] == []


def test_matrix_rules_apply_to_their_faction_only(client):
    request = {
        'attackers': [
            {'label': 'Guardsmen', 'unit_id': 1, 'weapons': [{'weapon_id': 1, 'weapon_type': 'ranged', 'models': 10}]},
            {'label': 'Boyz', 'unit_id': 2, 'weapons': [{'weapon_id': 2, 'weapon_type': 'ranged', 'models': 10}]},
        ],
        'defenders': [{'unit_id': 1, 'model_count': 10, 'label': 'Guardsmen'}],
        'conditions': {},
    }
    plain = client.post('/api/matrix/simulate', json=request).get_json()['matrix']
    waaagh = client.post('/api/matrix/simulate', json=dict(request, attacker_rules={'rule_ids': [3]})).get_json()['matrix']
    assert waaagh[0][0]['avg_damage'] == plain[0][0]['avg_damage']  # Guard unaffected by an Ork rule
    assert waaagh[1][0]['avg_damage'] == pytest.approx(plain[1][0]['avg_damage'] * 2, abs=0.02)

    smoked = client.post('/api/matrix/simulate', json=dict(request, defender_rules={'stratagem_ids': [1]})).get_json()['matrix']
    assert smoked[0][0]['avg_damage'] < plain[0][0]['avg_damage']


def test_cell_detail_applies_rules(client):
    request = {
        'weapons': [{'weapon_id': 2, 'weapon_type': 'ranged', 'models': 10, 'unit_id': 2}],
        'unit_id': 1, 'model_count': 10, 'conditions': {}, 'iterations': 4000,
    }
    plain = client.post('/api/matrix/cell', json=request).get_json()['summary']['average_damage']
    waaagh = client.post('/api/matrix/cell', json=dict(request, attacker_rules={'rule_ids': [3]})).get_json()
    assert waaagh['summary']['average_damage'] == pytest.approx(plain * 2, rel=0.08)


# --- Stage 5: several weapons at once ---------------------------------------

LASGUN = {'name': 'Lasgun', 'type': 'ranged', 'attacks': '1', 'skill': 4, 'strength': 3, 'ap': 0, 'damage': '1',
          'abilities': [], 'models': 10}
MELTA = {'name': 'Meltagun', 'type': 'ranged', 'attacks': '1', 'skill': 4, 'strength': 9, 'ap': 4, 'damage': 'D6',
         'abilities': ['Melta 2'], 'models': 2}
TARGET = {'toughness': 4, 'save': 3, 'wounds': 3, 'model_count': 5, 'faction_id': ORKS}


def simulate_weapons(client, weapons, method='analytical', **extra):
    payload = {'attacker': {'faction_id': GUARD, 'weapons': weapons}, 'defender': TARGET,
               'conditions': {'half_range': True}, 'method': method, 'iterations': 6000}
    payload.update(extra)
    return client.post('/api/simulate', json=payload).get_json()


def test_several_weapons_total_their_rows(client):
    both = simulate_weapons(client, [LASGUN, MELTA])
    las = simulate_weapons(client, [LASGUN])
    melta = simulate_weapons(client, [MELTA])

    assert [row['name'] for row in both['weapons']] == ['Lasgun', 'Meltagun']
    total = las['summary']['average_damage'] + melta['summary']['average_damage']
    assert both['summary']['average_damage'] == pytest.approx(total, abs=0.02)
    assert both['breakdown']['attacks'] == pytest.approx(10 + 2)
    # Models killed: the weapons' kills add up (Excel), capped at the unit size
    kills = sum(row['models_killed'] for row in both['weapons'])
    assert both['summary']['models_killed'] == pytest.approx(min(kills, 5), abs=0.02)
    assert both['weapons'][1]['total_damage'] < both['weapons'][1]['unsaved'] * 5.5  # Melta damage capped at W3
    assert both['weapons'][1]['damage_per_attack'] == pytest.approx(3.5 + 2)


def test_several_weapons_monte_carlo_rolls_together(client):
    analytical = simulate_weapons(client, [LASGUN, MELTA])
    monte_carlo = simulate_weapons(client, [LASGUN, MELTA], method='monte_carlo')
    assert monte_carlo['summary']['average_damage'] == pytest.approx(analytical['summary']['average_damage'], rel=0.06)
    assert 0 <= monte_carlo['summary']['wipe_probability'] <= monte_carlo['summary']['kill_probability'] <= 100
    assert len(monte_carlo['weapons']) == 2  # per-weapon rows are always averages


def test_single_weapon_payload_still_works(client):
    legacy = client.post('/api/simulate', json={
        'attacker': dict(LASGUN, weapon_name='Lasgun'), 'defender': TARGET,
        'conditions': {'half_range': True}, 'method': 'analytical'}).get_json()
    assert len(legacy['weapons']) == 1
    assert legacy['summary']['average_damage'] == simulate_weapons(client, [LASGUN])['summary']['average_damage']


def test_saved_scenario_rule_keys_apply(client):
    plain = simulate_weapons(client, [LASGUN])['summary']['average_damage']
    payload = {'attacker': {'faction_id': GUARD, 'weapons': [LASGUN], 'rules': ['rule:1']}, 'defender': TARGET,
               'conditions': {'half_range': True}, 'method': 'analytical'}
    with_rule = client.post('/api/simulate', json=payload).get_json()
    assert with_rule['applied']['attacker'] == ['Damage Modifier +1']
    assert with_rule['summary']['average_damage'] == pytest.approx(plain * 2, abs=0.02)
