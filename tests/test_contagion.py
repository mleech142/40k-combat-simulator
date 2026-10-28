"""
Death Guard's Nurgle's Gift (contagion.py): Contagion Range, Afflicted units and the Plagues, the rule text read
for it (rules_text), effect rows that apply only against Afflicted targets (effects), and the Simulator and
Analytics requests that say a side is Afflicted. Play mode's side is in test_play_contagion.py.
"""

import pytest

from combat_sim import contagion, effects
from combat_sim.rules_text import (ability_buffs, afflicts_after_shooting, contagion_bonus, granted_ability,
                                   parse_effects, parse_leader_buffs, units_contagion_bonus)

GUARD, PLAGUE = 1, 2
DATA = {
    'schema_version': 1,
    'source': None,
    'factions': [{'id': GUARD, 'name': 'Guard'}, {'id': PLAGUE, 'name': 'Plague'}],
    'units': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Guardsmen', 'toughness': 3, 'save': 5, 'wounds': 1,
         'points_cost': 65, 'default_models': 10, 'keywords': ['Infantry'], 'modifiers': [],
         'ranged_weapons': [{'weapon_id': 1, 'qty': 10}], 'melee_weapons': []},
        {'id': 2, 'faction_id': PLAGUE, 'name': 'Rotbrute', 'toughness': 9, 'save': 3, 'wounds': 8,
         'points_cost': 150, 'default_models': 1, 'keywords': ['Vehicle'], 'modifiers': [],
         'abilities': 'Diseased Malice: Each time this model makes an attack that targets an Afflicted unit, add 1 '
                      'to the Wound roll.',
         'ranged_weapons': [{'weapon_id': 2, 'qty': 1}], 'melee_weapons': []},
        {'id': 3, 'faction_id': PLAGUE, 'name': 'Poxcaster', 'toughness': 4, 'save': 3, 'wounds': 4,
         'points_cost': 70, 'default_models': 1, 'keywords': ['Infantry', 'Character'], 'modifiers': [],
         'can_lead': ['Rotbrute'],
         'leader_buffs': [{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Hit Modifier +1', 'when': 'afflicted'}],
         'ranged_weapons': [], 'melee_weapons': []},
    ],
    'ranged_weapons': [
        {'id': 1, 'faction_id': GUARD, 'name': 'Lasgun', 'attacks': '1', 'ballistic_skill': 4, 'strength': 3,
         'armor_penetration': 0, 'damage': '1', 'abilities': []},
        {'id': 2, 'faction_id': PLAGUE, 'name': 'Bile gun', 'attacks': '10', 'ballistic_skill': 4, 'strength': 4,
         'armor_penetration': 0, 'damage': '1', 'abilities': []},
    ],
    'melee_weapons': [],
    'faction_rules': [],
    'stratagems': [],
    'keywords': [],
    'target_profiles': [],
}

LASGUN = {'name': 'Lasgun', 'type': 'ranged', 'attacks': '1', 'skill': 4, 'strength': 3, 'ap': 0, 'damage': '1', 'models': 60}
CHOPPA = dict(LASGUN, name='Choppa', type='melee')
BILE = {'name': 'Bile gun', 'type': 'ranged', 'attacks': '10', 'skill': 4, 'strength': 4, 'ap': 0, 'damage': '1', 'models': 6}
GUARDSMEN = {'toughness': 3, 'save': 5, 'wounds': 1, 'model_count': 100}


@pytest.fixture
def client(make_client):
    return make_client(DATA)[0]


def damage(client, attacker, defender):
    response = client.post('/api/simulate', json={'attacker': attacker, 'defender': defender,
                                                  'conditions': {}, 'method': 'analytical'})
    assert response.status_code == 200, response.get_json()
    return response.get_json()['summary']['average_damage']


# --- The rule -----------------------------------------------------------------------------------------------

def test_contagion_range_grows_by_battle_round_up_to_twelve_inches():
    assert [contagion.contagion_range(r) for r in (0, 1, 2, 3, 5)] == [3, 3, 6, 9, 9]
    assert contagion.contagion_range(2, 3) == 9
    assert contagion.contagion_range(3, 3) == 12 and contagion.contagion_range(4, 6) == 12  # Never more than 12"


def test_affliction_from_a_request_is_checked():
    assert [contagion.affliction(v) for v in (None, '', 'none', 'rattlejoint', 'bogus', True)] == \
        [None, None, 'none', 'rattlejoint', None, None]


def test_plague_modifiers():
    assert contagion.modifiers(None, None, 'ranged') == ([], [])
    assert contagion.modifiers(None, 'none', 'ranged') == ([], ['Toughness Modifier -1'])
    assert contagion.modifiers(None, 'rattlejoint', 'melee') == ([], ['Toughness Modifier -1', 'Save Modifier -1'])
    assert contagion.modifiers('skullsquirm', None, 'ranged') == ([], ['Stealth'])  # Its targets have cover
    assert contagion.modifiers('skullsquirm', None, 'melee') == (['Hit Modifier -1'], [])
    assert contagion.modifiers('scabrous', 'scabrous', 'ranged') == ([], ['Toughness Modifier -1'])  # Not the dice


def test_scabrous_soulrot_worsens_move_leadership_and_objective_control():
    assert contagion.worsen_move(6, 'scabrous') == 5 and contagion.worsen_move(6, 'none') == 6
    assert contagion.worsen_leadership(6, 'scabrous') == 7  # Leadership is the 2D6 target: worse is higher
    assert [contagion.worsen_oc(oc, 'scabrous') for oc in (0, 1, 2, 3)] == [0, 1, 1, 2]  # No lower than 1 (0 stays 0)
    assert contagion.worsen_oc(2, 'rattlejoint') == 2


def test_choices_list_no_plague_and_each_plague():
    keys = [c['key'] for c in contagion.choices()]
    assert keys == ['none', 'skullsquirm', 'rattlejoint', 'scabrous']


# --- Effect rows with a condition ------------------------------------------------------------------------------

def test_a_row_with_a_condition_applies_only_when_it_is_met():
    rows = [{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Lethal Hits'},
            {'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Sustained Hits 1', 'when': 'afflicted'}]
    assert effects.leader_modifiers(rows, 'Attacker', 'ranged') == ['Lethal Hits']
    assert effects.leader_modifiers(rows, 'Attacker', 'ranged', ('afflicted',)) == ['Lethal Hits', 'Sustained Hits 1']
    assert effects.enhancement_modifiers([(rows, 'unit')], 'Attacker', 'melee') == ['Lethal Hits']
    assert effects.enhancement_modifiers([(rows, 'unit')], 'Attacker', 'melee', conditions=('afflicted',)) == \
        ['Lethal Hits', 'Sustained Hits 1']


# --- Rule text --------------------------------------------------------------------------------------------------

def test_leader_bonus_against_afflicted_targets():
    buffs, skipped = parse_leader_buffs(
        'Gift of Contagion (Psychic): While this model is leading a unit, each time a model in that unit makes an attack '
        'which targets a unit which is Afflicted, that attack has the [Sustained Hits 1] ability.')
    assert buffs == [{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Sustained Hits 1', 'when': 'afflicted'}]
    assert skipped == []


@pytest.mark.parametrize('text, expected', [
    ('Diseased Malice: Each time this model makes an attack that targets an Afflicted unit, add 1 to the Wound roll.\n\n'
     'Deep Strike', [('Attacker', 'Both', 'Wound Modifier +1')]),
    ('Gift of Contagion (Psychic): Each time a model in this unit makes a ranged attack that targets a unit that is '
     'Afflicted, that attack has the [Sustained Hits 1] ability.', [('Attacker', 'Shooting', 'Sustained Hits 1')]),
    # Another condition first (Starting Strength, a leader): not read
    ('Blistering Fusillade: If this unit has a Starting Strength of 5 or more, then each time a model in this unit makes '
     'a ranged attack that targets an Afflicted unit, improve the Strength characteristic of that attack by 1.', []),
    ('Lethal Hits: Each time this model makes an attack, re-roll a Hit roll of 1.', []),  # Not conditional: not yet
    ('Gift of Contagion: While this model is leading a unit, each time a model in that unit makes an attack that '
     'targets an Afflicted unit, add 1 to the Hit roll.', []),  # A leader's: parse_leader_buffs
])
def test_ability_buffs_against_afflicted_targets(text, expected):
    buffs = ability_buffs(text)
    assert [(b['applies_to'], b['phase'], b['modifier']) for b in buffs] == expected
    assert all(b['when'] == 'afflicted' for b in buffs)


def test_rule_records_note_the_afflicted_condition_instead_of_keeping_it():
    found = parse_effects('Each time a model in your unit makes an attack that targets an Afflicted unit, add 1 to the Wound roll.')
    assert found['buffs'] == [{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Wound Modifier +1'}]
    assert found['conditions']


def test_contagion_rule_text():
    assert contagion_bonus('Gift of Poxes: Add 3" to the range of this model\'s Contagion Range.') == 3
    assert contagion_bonus('Lethal Hits') == 0
    assert units_contagion_bonus('Friendly **DEATH GUARD CHARACTER** units have +3" to their **Contagion Range** '
                                 '(to a maximum of 12").') == ('DEATH GUARD CHARACTER', 3)
    assert afflicts_after_shooting(
        'Infused with the Blessings of Nurgle: In your Shooting phase, each time this unit is selected to shoot, after '
        'this unit has shot, select one enemy unit hit by one or more of those attacks. Until the start of your next '
        'turn, that unit is Afflicted.')
    assert granted_ability('In the **Declare Battle Formations** step, you can select up to two friendly **PLAGUE '
                           'MARINES** units. Those units have **Infiltrators**.') == \
        {'ability': 'Infiltrators', 'keywords': 'PLAGUE MARINES', 'count': 2}


# --- Simulator ----------------------------------------------------------------------------------------------

def test_afflicted_defender_has_minus_one_toughness(client):
    plain = damage(client, {'weapons': [LASGUN]}, GUARDSMEN)
    afflicted = damage(client, {'weapons': [LASGUN]}, dict(GUARDSMEN, afflicted='none'))
    assert afflicted == pytest.approx(plain * 4 / 3, rel=0.01)  # S3 vs T3 wounds on 4+, vs T2 on 3+


def test_rattlejoint_ague_worsens_the_save(client):
    plain = damage(client, {'weapons': [LASGUN]}, dict(GUARDSMEN, afflicted='none'))
    ague = damage(client, {'weapons': [LASGUN]}, dict(GUARDSMEN, afflicted='rattlejoint'))
    assert ague == pytest.approx(plain * 5 / 4, rel=0.01)  # 5+ save to 6+: 4/6 to 5/6 get through


def test_skullsquirm_blight_on_the_attacker(client):
    shooting = damage(client, {'weapons': [LASGUN]}, GUARDSMEN)
    assert damage(client, {'weapons': [LASGUN], 'afflicted': 'skullsquirm'}, GUARDSMEN) == \
        pytest.approx(shooting * 2 / 3, rel=0.01)  # The target has cover: BS4+ hits on 5+
    melee = damage(client, {'weapons': [CHOPPA]}, GUARDSMEN)
    assert damage(client, {'weapons': [CHOPPA], 'afflicted': 'skullsquirm'}, GUARDSMEN) == \
        pytest.approx(melee * 2 / 3, rel=0.01)  # -1 to hit


def test_datasheet_bonus_against_afflicted_targets(client):
    target = {'toughness': 4, 'save': 7, 'wounds': 1, 'model_count': 100}
    plain = damage(client, {'unit_id': 2, 'weapons': [BILE]}, target)  # S4 vs T4: 4+
    afflicted = damage(client, {'weapons': [BILE]}, dict(target, afflicted='none'))  # T3: 3+
    with_bonus = damage(client, {'unit_id': 2, 'weapons': [BILE]}, dict(target, afflicted='none'))  # and +1: 2+
    assert afflicted == pytest.approx(plain * 4 / 3, rel=0.01)
    assert with_bonus == pytest.approx(plain * 5 / 3, rel=0.01)


def test_leader_bonus_against_afflicted_targets_in_the_simulator(client):
    target = {'toughness': 4, 'save': 7, 'wounds': 1, 'model_count': 100}
    plain = damage(client, {'leader_id': 3, 'weapons': [BILE]}, target)
    afflicted = damage(client, {'weapons': [BILE]}, dict(target, afflicted='none'))
    led = damage(client, {'leader_id': 3, 'weapons': [BILE]}, dict(target, afflicted='none'))
    assert plain == pytest.approx(damage(client, {'weapons': [BILE]}, target))  # Not Afflicted: no bonus
    assert led == pytest.approx(afflicted * 4 / 3, rel=0.01)  # +1 to hit: 4+ to 3+


def test_edited_leader_buffs_keep_their_condition(client):
    unit = client.get('/api/units/3').get_json()
    unit['leader_buffs'] = [{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Lethal Hits', 'when': 'afflicted'},
                            {'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Hit Modifier +1', 'when': 'bogus'}]
    assert client.put('/api/data/units/3', json=unit).status_code == 200
    assert client.get('/api/units/3').get_json()['leader_buffs'] == [
        {'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Lethal Hits', 'when': 'afflicted'},
        {'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Hit Modifier +1'}]


# --- Analytics ----------------------------------------------------------------------------------------------

def matrix(client, afflicted=None):
    response = client.post('/api/matrix/simulate', json={
        'attackers': [{'unit_id': 2, 'label': 'Rotbrute', 'weapons': [{'weapon_id': 2, 'weapon_type': 'ranged', 'models': 1}]}],
        'defenders': [{'unit_id': 1, 'model_count': 20}], 'conditions': {}, 'afflicted': afflicted or {}})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_matrix_with_afflicted_defenders(client):
    plain = matrix(client)['matrix'][0][0]['avg_damage']
    afflicted = matrix(client, {'defenders': 'none'})['matrix'][0][0]['avg_damage']
    assert afflicted == pytest.approx(plain * 5 / 4, rel=0.02)  # S4 vs T3 wounds on 3+, vs T2 on 2+ (+1 to wound adds nothing)


def test_reverse_durability_with_my_units_afflicted(client):
    plain = matrix(client)['analysis']['reverse']['rows'][0]['cells'][0]
    afflicted = matrix(client, {'attackers': 'rattlejoint'})['analysis']['reverse']['rows'][0]['cells'][0]
    assert afflicted > plain  # My Rotbrute at T8 with a 4+ save takes more from the lasguns


def test_cell_detail_with_afflicted_defenders(client):
    def cell(afflicted):
        return client.post('/api/matrix/cell', json={
            'weapons': [{'weapon_id': 2, 'weapon_type': 'ranged', 'models': 1, 'unit_id': 2}],
            'unit_id': 1, 'model_count': 20, 'conditions': {}, 'iterations': 4000, 'afflicted': afflicted}).get_json()
    assert cell({'defenders': 'none'})['summary']['average_damage'] > cell({})['summary']['average_damage']
