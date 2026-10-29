"""
Roster features (stage 6): points by unit size, and generic target profiles in Matchups.
"""

import pytest

from combat_sim.models import Unit

GUARD = 1

DATA = {
    'schema_version': 1,
    'source': None,
    'factions': [{'id': GUARD, 'name': 'Guard'}],
    'units': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Guardsmen', 'toughness': 3, 'save': 5, 'wounds': 1,
         'points_cost': 65, 'default_models': 10,
         'points_by_size': [{'models': 10, 'points': 65}, {'models': 20, 'points': 120}],
         'keywords': ['Infantry'], 'modifiers': [],
         'ranged_weapons': [{'weapon_id': 1, 'qty': 10}], 'melee_weapons': []},
    ],
    'ranged_weapons': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Lasgun', 'attacks': '1', 'ballistic_skill': 4, 'strength': 3,
         'armor_penetration': 0, 'damage': '1', 'abilities': []},
    ],
    'melee_weapons': [],
    'faction_rules': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Hold the Line', 'applies_to': 'Target', 'phase': 'Both',
         'modifier': 'Toughness Modifier +1'},
    ],
    'stratagems': [
        {'id': 1, 'faction_id': None, 'name': 'Smokescreen', 'cp': '1', 'applies_to': 'Target', 'phase': 'Shooting',
         'modifier': 'Stealth'},
    ],
    'keywords': [],
    'target_profiles': [
        {'id': 1, 'kind': 'generic', 'name': 'MEQ', 'models': 5, 'toughness': 4, 'wounds': 2, 'save': 3,
         'keywords': ['Infantry'], 'weight': 2, 'use': True},
        {'id': 2, 'kind': 'generic', 'name': 'Tank', 'models': 1, 'toughness': 11, 'wounds': 13, 'save': 3,
         'keywords': ['Vehicle'], 'weight': 1, 'use': True},
        {'id': 3, 'kind': 'role', 'name': 'Anti-Horde', 'models': 10, 'toughness': 3, 'wounds': 1, 'save': 5,
         'keywords': ['Infantry']},
    ],
}


@pytest.fixture
def client(make_client):
    return make_client(DATA)[0]


# --- Points by unit size ----------------------------------------------------

def test_points_for_listed_and_unlisted_sizes():
    unit = Unit(points_cost=65, default_models=10,
                points_by_size=[{'models': 10, 'points': 65}, {'models': 20, 'points': 120}])
    assert unit.points_for(10) == 65
    assert unit.points_for(20) == 120  # the bigger unit's discount, not 130
    assert unit.points_for(15) == 98  # 65 + 5 x 6.5
    assert unit.points_for(25) == 150  # 120 + 5 x 6
    assert unit.points_for(5) == 33  # pro rata from the smallest size (32.5 rounded half up)


def test_points_fall_back_to_datasheet_pair():
    unit = Unit(points_cost=175, default_models=10, points_by_size=[])
    assert unit.points_for(10) == 175
    assert unit.points_for(5) == 88
    assert Unit(points_by_size=[]).points_for(5) is None


def test_unit_form_saves_points_by_size(client):
    unit = client.get('/api/units/1').get_json()
    unit.update(points_by_size=[{'models': 5, 'points': 40}, {'models': 10, 'points': 70}],
                ranged_weapons=[{'weapon_id': 1, 'qty': 5}], melee_weapons=[])
    assert client.put('/api/data/units/1', json=unit).status_code == 200
    saved = client.get('/api/units/1').get_json()
    assert saved['points_by_size'] == [{'models': 5, 'points': 40}, {'models': 10, 'points': 70}]
    assert saved['points_cost'] == 40 and saved['default_models'] == 5  # first row = datasheet size


def matrix(client, unit_size=None, defenders=None, **extra):
    attacker = {'label': 'Guardsmen', 'unit_id': 1, 'weapons': [{'weapon_id': 1, 'weapon_type': 'ranged', 'models': 20}]}
    if unit_size:
        attacker['unit_size'] = unit_size
    request = {'attackers': [attacker], 'defenders': defenders or [{'unit_id': 1, 'model_count': 10}], 'conditions': {},
               'weapon_filter': 'both'}
    request.update(extra)
    return client.post('/api/matrix/simulate', json=request).get_json()


def test_matrix_points_follow_unit_size(client):
    assert matrix(client)['attackers'][0]['points'] == 65
    assert matrix(client, unit_size=20)['attackers'][0]['points'] == 120
    assert matrix(client, defenders=[{'unit_id': 1, 'model_count': 20}])['defenders'][0]['points'] == 120


# --- Generic target profiles ----------------------------------------------------

def test_target_profiles_are_listed_read_only(client):
    profiles = client.get('/api/data/target-profiles').get_json()
    assert [(t['kind'], t['name']) for t in profiles] == [('generic', 'MEQ'), ('generic', 'Tank'), ('role', 'Anti-Horde')]
    assert client.post('/api/data/target-profiles', json={}).status_code == 405


def test_matrix_against_generic_profiles(client):
    result = matrix(client, defenders=[{'profile_id': 1, 'label': 'MEQ'}, {'profile_id': 2, 'label': 'Tank'}])
    assert [(d['label'], d['weight']) for d in result['defenders']] == [('MEQ', 2), ('Tank', 1)]
    meq, tank = result['matrix'][0]
    # 20 lasgun shots, BS4+: 10 hits; S3 vs T4 wounds on 5+, Sv3+ saves 2/3
    assert meq['avg_damage'] == pytest.approx(20 * 0.5 * (2 / 6) * (2 / 6), abs=0.01)
    assert meq['models_killed'] <= 5  # profile's own model count (5) is the default
    assert tank['avg_damage'] < meq['avg_damage']


def test_generic_profiles_get_core_but_not_faction_target_rules(client):
    meq = [{'profile_id': 1, 'label': 'MEQ'}]
    plain = matrix(client, defenders=meq)['matrix'][0][0]['avg_damage']
    faction_rule = matrix(client, defenders=meq, defender_rules={'rule_ids': [1]})['matrix'][0][0]['avg_damage']
    smoke = matrix(client, defenders=meq, defender_rules={'stratagem_ids': [1]})['matrix'][0][0]['avg_damage']
    assert faction_rule == plain  # a Guard rule doesn't apply to a generic profile
    assert smoke < plain  # a core stratagem does


def test_cell_detail_against_generic_profile(client):
    detail = client.post('/api/matrix/cell', json={
        'weapons': [{'weapon_id': 1, 'weapon_type': 'ranged', 'models': 20, 'unit_id': 1}],
        'profile_id': 1, 'conditions': {}, 'iterations': 4000}).get_json()
    assert detail['summary']['average_damage'] == pytest.approx(20 * 0.5 * (2 / 6) * (2 / 6), rel=0.15)


# --- Army analysis (stage 7) ---------------------------------------------------

from combat_sim.app import default_used


def test_default_used_skips_alternate_profiles():
    names = ['Boltgun', 'Plasma Gun - Standard', 'Plasma Gun - Supercharged', 'Power Fist']
    assert default_used(names) == [True, True, False, True]


def test_percent_destroyed_and_wiped(client):
    result = matrix(client, defenders=[{'unit_id': 1, 'model_count': 1}, {'unit_id': 1, 'model_count': 50}])
    small, big = result['total']['cells']
    assert small['wiped'] is True and small['pct_destroyed'] == 100
    assert big['wiped'] is False and 0 < big['pct_destroyed'] < 100
    assert result['matrix'][0][1]['pct_per_100'] == pytest.approx(result['matrix'][0][1]['pct_destroyed'] / 65 * 100, abs=0.1)  # pct shown rounded


def test_generic_mode_averages_are_weighted(client):
    result = matrix(client, defenders=[{'profile_id': 1, 'label': 'MEQ'}, {'profile_id': 2, 'label': 'Tank'}])
    meq, tank = (cell['pct_destroyed'] for cell in result['total']['cells'])
    summary = result['analysis']['summary']
    assert summary['weighted'] is True
    assert summary['avg_pct_destroyed'] == pytest.approx((2 * meq + 1 * tank) / 3, abs=0.1)
    assert summary['best_unit']['label'] == 'Guardsmen'
    assert result['analysis']['reverse'] is None  # reverse durability needs real units


def test_gaps_roles_and_coverage(client):
    result = matrix(client, defenders=[{'unit_id': 1, 'model_count': 1}, {'unit_id': 1, 'model_count': 50}])
    analysis = result['analysis']
    assert [g['models'] for g in analysis['gaps']] == [50]  # only the unit the army can't wipe
    assert analysis['gaps'][0]['survivors'] > 0

    assert [r['name'] for r in analysis['roles']] == ['Anti-Horde']
    assert analysis['roles'][0]['score'] > 0

    coverage = analysis['coverage']
    assert len(coverage['damage']) == 8 and len(coverage['damage'][0]) == 5
    assert coverage['damage'][0][-1] > coverage['damage'][-1][0]  # T3 Sv6+ takes more than T12 Sv2+


def test_reverse_durability_in_real_mode(client):
    result = matrix(client, unit_size=10, defenders=[{'unit_id': 1, 'model_count': 20}])
    reverse = result['analysis']['reverse']
    assert reverse['enemies'] == ['Guardsmen (x20)']
    row = reverse['rows'][0]
    assert row['label'] == 'Guardsmen' and row['models'] == 10 and 0 < row['cells'][0] <= 100
    # The 20-model enemy unit fires twice the lasguns of a 10-model one
    half = matrix(client, unit_size=10, defenders=[{'unit_id': 1, 'model_count': 10}])['analysis']['reverse']
    assert row['cells'][0] == pytest.approx(2 * half['rows'][0]['cells'][0], abs=0.2)


# --- Extra modifiers: one-off target modifiers for a single defender -----------------

def test_extra_modifiers_apply_to_their_defender_only(client):
    defenders = [{'unit_id': 1, 'model_count': 10, 'label': 'A'},
                 {'unit_id': 1, 'model_count': 10, 'label': 'B', 'extra_modifiers': 'Stealth, Feel No Pain 5+'}]
    plain, buffed = matrix(client, defenders=defenders)['matrix'][0]
    assert buffed['avg_damage'] < plain['avg_damage']
    # Stealth worsens BS4+ to 5+ (x2/3) and Feel No Pain 5+ lets 4/6 through
    assert buffed['avg_damage'] == pytest.approx(plain['avg_damage'] * 2 / 3 * 4 / 6, abs=0.02)


def test_extra_modifiers_ignored_for_generic_profiles(client):
    plain = matrix(client, defenders=[{'profile_id': 1}])['matrix'][0][0]['avg_damage']
    with_extra = matrix(client, defenders=[{'profile_id': 1, 'extra_modifiers': 'Stealth'}])['matrix'][0][0]['avg_damage']
    assert with_extra == plain


def test_extra_modifiers_not_used_in_reverse_durability(client):
    base = {'unit_id': 1, 'model_count': 20, 'label': 'Guardsmen (x20)'}
    plain = matrix(client, unit_size=10, defenders=[base])['analysis']['reverse']['rows'][0]['cells']
    extra = matrix(client, unit_size=10, defenders=[dict(base, extra_modifiers='Hit Modifier +1, Damage Modifier +1')])
    assert extra['analysis']['reverse']['rows'][0]['cells'] == plain


def test_extra_modifiers_in_simulator_and_cell_detail(client):
    defender = {'toughness': 3, 'save': 5, 'wounds': 1, 'model_count': 10}
    weapon = {'name': 'Lasgun', 'type': 'ranged', 'attacks': '1', 'skill': 4, 'strength': 3, 'ap': 0, 'damage': '1', 'models': 20}
    run = lambda d: client.post('/api/simulate', json={'attacker': {'weapons': [weapon]}, 'defender': d,
                                                       'conditions': {}, 'method': 'analytical'}).get_json()
    assert run(dict(defender, extra_modifiers=['Stealth']))['summary']['average_damage'] == pytest.approx(
        run(defender)['summary']['average_damage'] * 2 / 3, abs=0.02)

    cell = lambda extra: client.post('/api/matrix/cell', json={
        'weapons': [{'weapon_id': 1, 'weapon_type': 'ranged', 'models': 20, 'unit_id': 1}],
        'unit_id': 1, 'model_count': 10, 'conditions': {}, 'iterations': 4000, 'extra_modifiers': extra}).get_json()
    assert cell('Stealth')['summary']['average_damage'] < cell('')['summary']['average_damage']


# --- Wargear's characteristic changes (an army's units, engagement.stat_changes) -----------------

def wargear_matrix(client, defender, attacker=None):
    return client.post('/api/matrix/simulate', json={
        'attackers': [dict({'unit_id': 1, 'weapons': [{'weapon_id': 1, 'weapon_type': 'ranged', 'models': 10}]}, **(attacker or {}))],
        'defenders': [dict({'unit_id': 1, 'model_count': 10}, **defender)], 'conditions': {}}).get_json()


def test_a_defenders_wargear_changes_its_characteristics_in_the_matrix(client):
    plain = wargear_matrix(client, {})['matrix'][0][0]['avg_damage']
    shielded = wargear_matrix(client, {'stat_changes': {'stats': {'T': 1}, 'invulnerable_save': 4}})['matrix'][0][0]['avg_damage']
    assert shielded < plain  # Toughness 4: wounds on 5s; the 4+ invulnerable beats the 5+ save
    junk = wargear_matrix(client, {'stat_changes': {'stats': {'T': 'x', 'Q': 3}, 'invulnerable_save': 9}})['matrix'][0][0]['avg_damage']
    assert junk == plain  # What isn't a characteristic change is ignored


def test_the_cell_detail_uses_the_same_changes(client):
    body = {'weapons': [{'weapon_id': 1, 'weapon_type': 'ranged', 'models': 10, 'unit_id': 1}], 'unit_id': 1,
            'model_count': 10, 'conditions': {}, 'iterations': 2000}
    plain = client.post('/api/matrix/cell', json=body).get_json()['summary']['average_damage']
    tough = client.post('/api/matrix/cell', json=dict(body, stat_changes={'stats': {'T': 5}})).get_json()['summary']['average_damage']
    assert tough < plain * 0.7  # Toughness 8 against Strength 3: wounds on 6s instead of 4s
