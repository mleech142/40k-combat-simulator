"""
Setting up an attack (engagement.py) at its own interface: every source of modifier text on each side, which
rows apply to which faction and phase, the leader's own weapons, Death Guard's conditions, and the database
adapter giving the same text Play's frozen data does.
"""

from combat_sim import contagion
from combat_sim.engagement import Sources, attack_modifiers, rule_rows, target_modifiers

SHOOT = {'applies_to': 'Attacker', 'phase': 'Shooting', 'modifier': 'Lethal Hits'}
FIGHT = {'applies_to': 'Attacker', 'phase': 'Fighting', 'modifier': 'Sustained Hits 1'}
COVER = {'applies_to': 'Target', 'phase': 'Both', 'modifier': 'Cover'}


def test_every_source_reaches_the_attack_in_one_order():
    side = Sources(rows=[SHOOT], leader_buffs=[{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Re-roll Hits 1'}],
                   carried=[([{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Ignores Cover'}], 'unit')],
                   unit_buffs=[{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Hit Modifier +1'}])
    assert attack_modifiers(side, None, 'ranged') == ['Lethal Hits', 'Re-roll Hits 1', 'Ignores Cover', 'Hit Modifier +1']
    assert attack_modifiers(side, None, 'melee') == ['Re-roll Hits 1', 'Ignores Cover', 'Hit Modifier +1']  # not the shooting rule


def test_rows_apply_to_their_own_faction_and_core_ones_to_all():
    side = Sources(rows=[dict(SHOOT, faction_id=1), dict(FIGHT, phase='Both', faction_id=None)])
    assert attack_modifiers(side, None, 'ranged', faction_id=1) == ['Lethal Hits', 'Sustained Hits 1']
    assert attack_modifiers(side, None, 'ranged', faction_id=2) == ['Sustained Hits 1']


def test_a_leaders_own_weapons_use_the_leaders_abilities_and_enhancement():
    side = Sources(unit_buffs=[{'applies_to': 'Attacker', 'modifier': 'Unit Ability'}],
                   leader_own_buffs=[{'applies_to': 'Attacker', 'modifier': 'Leader Ability'}],
                   carried=[([{'applies_to': 'Attacker', 'modifier': 'Bearer Only', 'scope': 'bearer'}], 'leader')])
    assert attack_modifiers(side, None, 'ranged') == ['Unit Ability']
    assert attack_modifiers(side, None, 'ranged', leader_weapon=True) == ['Bearer Only', 'Leader Ability']


def test_bonuses_against_afflicted_targets_need_an_afflicted_target():
    side = Sources(unit_buffs=[{'applies_to': 'Attacker', 'modifier': 'Lethal Hits', 'when': contagion.AFFLICTED}])
    assert attack_modifiers(side, None, 'ranged') == []
    assert 'Lethal Hits' in attack_modifiers(side, 'none', 'ranged')  # afflicted, no Plague


def test_the_target_gets_its_rules_leader_enhancements_one_offs_and_affliction():
    side = Sources(rows=[COVER], leader_buffs=[{'applies_to': 'Target', 'modifier': 'Stealth'}],
                   carried=[([{'applies_to': 'Target', 'modifier': 'Feel No Pain 6'}], 'unit')], extra=['Damage Reduction 1'],
                   affliction='none')
    mods = target_modifiers(side, None, 'ranged')
    assert mods[:4] == ['Cover', 'Stealth', 'Feel No Pain 6', 'Damage Reduction 1']
    assert len(mods) > 4  # Afflicted: -1 Toughness


def test_ticked_rules_become_rows_without_none_entries():
    class Record:
        def __init__(self, **fields):
            self.__dict__.update(dict({'applies_to': None, 'phase': None, 'faction_id': None}, **fields))
    rows = rule_rows([Record(modifier='Lethal Hits, none'), Record(modifier='None'), Record(modifier='Cover', applies_to='Target', faction_id=3)])
    assert rows == [{'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Lethal Hits', 'faction_id': None},
                    {'applies_to': 'Target', 'phase': 'Both', 'modifier': 'Cover', 'faction_id': 3}]


def test_one_reader_applies_the_wargears_characteristic_changes():
    from types import SimpleNamespace
    from combat_sim.engagement import stat_changes, unit_stats, unit_target
    unit = SimpleNamespace(move='6', toughness=4, save=3, wounds=2, leadership=6, oc=2, invulnerable_save=5,
                           feel_no_pain=None, target_keywords=['Infantry'])
    stats = unit_stats(unit, {'stats': {'M': 2, 'W': 1}, 'invulnerable_save': 4})
    assert (stats['move'], stats['wounds'], stats['invulnerable_save'], stats['toughness']) == ('8', 3, 4, 4)
    assert unit_stats(unit, {'invulnerable_save': 6})['invulnerable_save'] == 5  # The datasheet's is better
    assert unit_stats(unit, None) == unit_stats(unit, {})
    assert stat_changes({'stats': {'W': 0, 'T': True, 'Sv': -1}, 'invulnerable_save': 1}) == {'stats': {'Sv': -1}, 'invulnerable_save': None}
    assert stat_changes({'keywords': ['Grenades']}) is None and stat_changes('junk') is None
    assert unit_target(unit, 5, ['Cover'], {'invulnerable_save': 4}).invulnerable_save == 4
