"""
Combat engine tests.

The Excel cases are the results 40KCombatSimulator_v2.xlsx calculated for its saved
Sim-Shooting / Sim-Fighting setups, so the app's analytical engine is checked against
the spreadsheet it replaces. Run with: python -m pytest tests
"""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from combat_sim.simulation import (
    AttackerProfile, BattleConditions, DefenderProfile,
    simulate_analytical, simulate_monte_carlo_combined,
)


def approx(value):
    return pytest.approx(value, abs=0.011)


# --- Excel parity -----------------------------------------------------------

# Sim-Shooting: Plague Marines (Ranged) vs Rogal Dorn Battle Tank (T12 W17 Sv2+),
# half range, stationary, no cover, 10 target models.
# (qty, A, BS, S, AP, D, abilities) -> Excel's Attacks, Hits, Wounds, Unsaved, Total Damage
EXCEL_SHOOTING = [
    ((2, 'D3', 3, 6, 1, '2', ['Blast', 'Lethal Hits']), (8, 5.3333, 2, 0.6667, 1.3333)),
    ((3, '2', 3, 5, 1, '1', ['Lethal Hits']), (6, 4, 1.5, 0.5, 0.5)),
    ((2, 'D6', None, 5, 1, '1', ['Anti-Infantry 2+', 'Torrent']), (7, 7, 1.1667, 0.3889, 0.3889)),
    ((2, '1', 3, 7, 2, '1', ['Rapid Fire 1']), (4, 2.6667, 0.8889, 0.4444, 0.4444)),
    ((1, '1', 3, 8, 3, '2', ['Rapid Fire 1']), (2, 1.3333, 0.4444, 0.2963, 0.5926)),
    ((1, '1', 3, 7, 2, '1', []), (1, 0.6667, 0.2222, 0.1111, 0.1111)),
    ((1, '1', 3, 8, 3, '2', []), (1, 0.6667, 0.2222, 0.1481, 0.2963)),
]


@pytest.mark.parametrize('weapon,expected', EXCEL_SHOOTING)
def test_matches_excel_sim_shooting(weapon, expected):
    qty, a, bs, s, ap, d, abilities = weapon
    attacker = AttackerProfile(a, bs, s, ap, d, abilities, models=qty)
    rogal_dorn = DefenderProfile(toughness=12, save=2, wounds=17, model_count=10)
    conditions = BattleConditions(half_range=True, stationary=True)

    result = simulate_analytical(attacker, rogal_dorn, conditions)

    attacks, hits, wounds, unsaved, damage = expected
    assert result.attacks_made == approx(attacks)
    assert result.hits == approx(hits)
    assert result.wounds == approx(wounds)
    assert result.failed_saves == approx(unsaved)
    assert result.average_damage == approx(damage)


def test_matches_excel_sim_fighting():
    # Chaos Spawn Hideous Mutations vs Intercessor (T5 W2 Sv3+), charged, 5 models
    attacker = AttackerProfile('D6+2', 4, 5, 1, '2', [], models=1)
    intercessor = DefenderProfile(toughness=5, save=3, wounds=2, model_count=5)

    result = simulate_analytical(attacker, intercessor, BattleConditions(charged=True))

    assert result.attacks_made == approx(5.5)
    assert result.hits == approx(2.75)
    assert result.wounds == approx(1.375)
    assert result.failed_saves == approx(0.6875)
    assert result.average_damage == approx(1.375)
    assert result.models_killed == approx(0.6875)


# --- Stage 1 engine fixes ---------------------------------------------------

def test_anti_x_lowers_critical_wound_roll():
    # S3 vs T10 normally wounds on 6+; Anti-Infantry 2+ makes 2+ a Critical Wound
    infantry = DefenderProfile(toughness=10, save=7, wounds=1, model_count=10, keywords=['Infantry'])
    vehicle = DefenderProfile(toughness=10, save=7, wounds=1, model_count=10, keywords=['Vehicle'])
    weapon = AttackerProfile('6', None, 3, 0, '1', ['Anti-Infantry 2+'])

    assert simulate_analytical(weapon, infantry, BattleConditions()).wounds == approx(5)
    assert simulate_analytical(weapon, vehicle, BattleConditions()).wounds == approx(1)


def test_anti_x_with_devastating_wounds_makes_mortals():
    infantry = DefenderProfile(toughness=4, save=2, wounds=1, model_count=10, keywords=['Infantry'])
    weapon = AttackerProfile('6', None, 4, 0, '1', ['Anti-Infantry 4+', 'Devastating Wounds'])
    # Critical Wounds on 4+ = half of 6 hits -> 3 mortal wounds; 4+ wounds otherwise too -> no normal wounds left
    result = simulate_analytical(weapon, infantry, BattleConditions())
    assert result.wounds == approx(3)
    assert result.failed_saves == approx(3)


def test_blast_counts_from_five_models_and_blast_x():
    five = DefenderProfile(toughness=4, save=3, wounds=1, model_count=5)
    ten = DefenderProfile(toughness=4, save=3, wounds=1, model_count=10)
    assert simulate_analytical(AttackerProfile('1', 3, 4, 0, '1', ['Blast']), five, BattleConditions()).attacks_made == approx(2)
    assert simulate_analytical(AttackerProfile('1', 3, 4, 0, '1', ['Blast']), ten, BattleConditions()).attacks_made == approx(3)
    assert simulate_analytical(AttackerProfile('1', 3, 4, 0, '1', ['Blast 2']), ten, BattleConditions()).attacks_made == approx(5)


def test_dice_keyword_values_average():
    target = DefenderProfile(toughness=4, save=7, wounds=1, model_count=1)
    # 6 attacks, 6 crits on average 1 -> Sustained Hits D3 adds 2 hits per crit
    result = simulate_analytical(AttackerProfile('6', 3, 4, 0, '1', ['Sustained Hits D3']), target, BattleConditions())
    assert result.hits == approx(6 * 4 / 6 + 1 * 2)
    rapid = simulate_analytical(AttackerProfile('1', 3, 4, 0, '1', ['Rapid Fire D3']), target, BattleConditions(half_range=True))
    assert rapid.attacks_made == approx(3)


def test_feel_no_pain_from_unit_modifiers():
    weapon = AttackerProfile('6', 2, 8, 6, '1', [])
    plain = DefenderProfile(toughness=4, save=7, wounds=1, model_count=10)
    fnp = DefenderProfile(toughness=4, save=7, wounds=1, model_count=10, keywords=['Infantry', 'Feel No Pain 5+'])
    base = simulate_analytical(weapon, plain, BattleConditions()).average_damage
    assert fnp.feel_no_pain == 5
    assert simulate_analytical(weapon, fnp, BattleConditions()).average_damage == approx(base * 4 / 6)


def test_invulnerable_save_from_modifiers():
    weapon = AttackerProfile('6', 2, 8, 4, '1', [])  # AP-4 against a 3+ save: only an invulnerable save helps
    plain = DefenderProfile(toughness=4, save=3, wounds=1, model_count=10)
    granted = DefenderProfile(toughness=4, save=3, wounds=1, model_count=10, keywords=['Invulnerable Save 4+'])
    assert granted.invulnerable_save == 4
    assert simulate_analytical(weapon, granted, BattleConditions()).average_damage == approx(
        simulate_analytical(weapon, plain, BattleConditions()).average_damage / 2)
    # The best one counts
    assert DefenderProfile(4, 3, 1, invulnerable_save=5, keywords=['Invulnerable Save 4+']).invulnerable_save == 4
    assert DefenderProfile(4, 3, 1, invulnerable_save=4, keywords=['Invulnerable Save 5+']).invulnerable_save == 4


def test_lowercase_keywords_still_count():
    target = DefenderProfile(toughness=4, save=3, wounds=1, model_count=5)
    upper = simulate_analytical(AttackerProfile('6', 3, 4, 1, '1', ['Lethal Hits']), target, BattleConditions())
    lower = simulate_analytical(AttackerProfile('6', 3, 4, 1, '1', ['Lethal hits']), target, BattleConditions())
    assert lower.average_damage == upper.average_damage


# --- Analytical and Monte Carlo agree ---------------------------------------

AGREEMENT_CASES = [
    ('plain', AttackerProfile('3', 3, 4, 1, '1', [], models=5), DefenderProfile(4, 3, 2, model_count=10), BattleConditions()),
    ('lethal+sustained', AttackerProfile('4', 3, 5, 1, '1', ['Lethal Hits', 'Sustained Hits 1'], models=5), DefenderProfile(5, 3, 2, model_count=10), BattleConditions()),
    ('sustained D3 + reroll hits', AttackerProfile('3', 4, 4, 0, '1', ['Sustained Hits D3'], models=5), DefenderProfile(4, 4, 1, model_count=10), BattleConditions(reroll_hits='all')),
    ('anti + dev + reroll ones', AttackerProfile('D6', None, 4, 1, '1', ['Anti-Infantry 2+', 'Devastating Wounds', 'Torrent'], models=2), DefenderProfile(6, 3, 2, model_count=10, keywords=['Infantry']), BattleConditions(reroll_wounds='ones')),
    ('blast + rapid fire + melta', AttackerProfile('D3', 3, 9, 4, 'D6', ['Blast', 'Rapid Fire 1', 'Melta 2'], models=2), DefenderProfile(10, 3, 12, model_count=10), BattleConditions(half_range=True)),
    ('heavy + lance + fnp', AttackerProfile('2', 4, 8, 2, '2', ['Heavy', 'Lance'], models=3), DefenderProfile(7, 2, 4, invulnerable_save=4, feel_no_pain=5, model_count=5), BattleConditions(stationary=True, charged=True)),
]


@pytest.mark.parametrize('name,attacker,defender,conditions', AGREEMENT_CASES, ids=[c[0] for c in AGREEMENT_CASES])
def test_monte_carlo_agrees_with_analytical(name, attacker, defender, conditions):
    random.seed(1234)
    analytical = simulate_analytical(attacker, defender, conditions)
    monte_carlo = simulate_monte_carlo_combined([attacker], defender, conditions, iterations=20000)

    for field in ('attacks_made', 'hits', 'wounds', 'failed_saves', 'average_damage'):
        expected = getattr(analytical, field)
        actual = getattr(monte_carlo, field)
        assert actual == pytest.approx(expected, rel=0.04, abs=0.05), f'{field}: MC {actual} vs analytical {expected}'


# --- Stage 2-3: modifier language and conditions -----------------------------
# Base case: 6 attacks, BS3+, S4 vs T4, no save, W10 -> 4 hits, 2 wounds, 2 damage.

def run(abilities=(), conditions=None, weapon_type='ranged', target_keywords=(), save=7, invuln=None,
        attacks='6', skill=3, strength=4, ap=0, damage='1', models=1, target_models=1, toughness=4, wounds=10):
    attacker = AttackerProfile(attacks, skill, strength, ap, damage, list(abilities), models=models, weapon_type=weapon_type)
    defender = DefenderProfile(toughness=toughness, save=save, wounds=wounds, invulnerable_save=invuln,
                               keywords=list(target_keywords), model_count=target_models)
    return simulate_analytical(attacker, defender, conditions or BattleConditions())


def test_hit_modifiers_are_capped_at_one():
    assert run(['Hit Modifier +1', 'Hit Modifier +1']).hits == approx(5)
    assert run(['Heavy', 'Hit Modifier +1'], BattleConditions(stationary=True)).hits == approx(5)
    assert run(target_keywords=['Hit Modifier -1'], conditions=BattleConditions(damaged=True)).hits == approx(3)


def test_attacker_damaged_is_minus_one_to_hit():
    assert run(conditions=BattleConditions(damaged=True)).hits == approx(3)
    assert run(['Hit Modifier +1'], BattleConditions(damaged=True)).hits == approx(4)


def test_cover_worsens_bs_for_shooting_only():
    cover = BattleConditions(cover=True)
    assert run(conditions=cover).hits == approx(3)
    assert run(['Ignores Cover'], cover).hits == approx(4)
    assert run(['Psychic'], cover).hits == approx(4)
    assert run(conditions=cover, weapon_type='melee').hits == approx(4)
    assert run(target_keywords=['Stealth']).hits == approx(3)
    # Cover no longer improves the save: an auto-hitting weapon is unaffected by it
    assert run(conditions=cover, skill=None, save=4).failed_saves == approx(run(skill=None, save=4).failed_saves)


def test_bs_modifier_is_not_capped_but_clamped():
    assert run(['BS Modifier -2']).hits == approx(5)
    assert run(['BS Modifier +1']).hits == approx(3)
    assert run(['WS Modifier -1'], weapon_type='melee').hits == approx(5)
    assert run(['WS Modifier -1']).hits == approx(4)  # WS Modifier has no effect on a ranged weapon


def test_psychic_ignores_negative_hit_modifiers():
    assert run(['Psychic'], BattleConditions(damaged=True)).hits == approx(4)
    assert run(['Psychic'], target_keywords=['Hit Modifier -1']).hits == approx(4)
    assert run(['Psychic', 'Hit Modifier +1']).hits == approx(5)


def test_rerolls_from_modifiers_and_twin_linked():
    assert run(['Reroll Hits']).hits == approx(6 * (4 / 6 + 2 / 6 * 4 / 6))
    assert run(['Reroll Hits 1']).hits == approx(6 * (4 / 6 + 4 / 36))
    assert run(['Twin-Linked']).wounds == approx(4 * 0.75)
    assert run(['Reroll Wounds 1']).wounds == approx(4 * (0.5 + 0.5 / 6))
    # The better of the dropdown and the modifier applies
    assert run(['Reroll Hits 1'], BattleConditions(reroll_hits='all')).hits == approx(6 * (4 / 6 + 2 / 6 * 4 / 6))


def test_critical_hits_and_wounds_thresholds():
    lethal = run(['Critical Hits 5+', 'Lethal Hits'])
    assert lethal.hits == approx(4)
    assert lethal.wounds == approx(2 + 2 * 0.5)
    dev = run(['Critical Wounds 5+', 'Devastating Wounds'], save=2)
    assert dev.wounds == approx(2)
    # 4 hits x 2/6 Critical Wounds = 1.33 mortal wounds (no save) + the rest saved on 2+
    assert dev.failed_saves == approx(4 * 2 / 6 + (2 - 4 * 2 / 6) * 1 / 6)


def test_snap_shooting():
    assert run(['Snap Shooting']).hits == approx(1)
    assert run(['Snap Shooting'], BattleConditions(stationary=True)).hits == approx(3)
    assert run(['Snap Shooting'], BattleConditions(reroll_hits='all')).hits == approx(1)


def test_strength_toughness_and_wound_modifiers():
    assert run(['Strength Modifier +1']).wounds == approx(4 * 4 / 6)
    assert run(target_keywords=['Toughness Modifier +1']).wounds == approx(4 * 2 / 6)
    charged = BattleConditions(charged=True)
    assert run(['Lance', 'Wound Modifier +1'], charged, weapon_type='melee').wounds == approx(4 * 4 / 6)
    assert run(['Lance'], charged, weapon_type='ranged').wounds == approx(2)


def test_save_modifiers():
    assert run(save=3).failed_saves == approx(2 * 2 / 6)
    assert run(['AP Modifier -1'], save=3).failed_saves == approx(2 * 3 / 6)
    assert run(target_keywords=['Save Modifier +1'], save=3).failed_saves == approx(2 * 1 / 6)
    assert run(['Save Modifier -1'], save=3).failed_saves == approx(2 * 3 / 6)
    assert run(target_keywords=['AP Modifier +1'], ap=1, save=3).failed_saves == approx(2 * 2 / 6)
    assert run(ap=3, save=3, invuln=4).failed_saves == approx(2 * 3 / 6)


def test_damage_and_attacks_modifiers():
    assert run(['Damage Modifier +1']).average_damage == approx(4)
    assert run(target_keywords=['Damage Modifier -1']).average_damage == approx(2)
    assert run(target_keywords=['Damage Modifier -1'], damage='2').average_damage == approx(2)
    assert run(['Attacks Modifier +1']).attacks_made == approx(7)
    assert run(['Attacks Modifier -5'], attacks='2').attacks_made == approx(1)


def test_phase_specific_keywords():
    assert run(['Cleave 1'], weapon_type='melee', attacks='2', target_models=10).attacks_made == approx(4)
    half = BattleConditions(half_range=True)
    assert run(['Rapid Fire 1'], half, weapon_type='melee').attacks_made == approx(6)
    assert run(['Melta 2'], half, weapon_type='melee', damage='1').average_damage == approx(2)
    assert run(['Melta 2'], half, damage='1').average_damage == approx(6)
    assert run(['Heavy'], BattleConditions(stationary=True), weapon_type='melee').hits == approx(4)


MODIFIER_AGREEMENT_CASES = [
    ('stat modifiers in cover',
     AttackerProfile('2', 3, 5, 1, '1', ['Hit Modifier +1', 'Wound Modifier +1', 'AP Modifier -1', 'Damage Modifier +1',
                                          'Attacks Modifier +1', 'Twin-Linked', 'Critical Hits 5+', 'Lethal Hits'], models=5),
     DefenderProfile(5, 3, 2, model_count=10, keywords=['Infantry', 'Toughness Modifier +1', 'Feel No Pain 6+']),
     BattleConditions(cover=True)),
    ('melee lance cleave',
     AttackerProfile('3', 3, 5, 1, 'D3', ['Lance', 'Cleave 1', 'Reroll Hits 1', 'Sustained Hits 1'], models=4, weapon_type='melee'),
     DefenderProfile(4, 3, 1, model_count=10), BattleConditions(charged=True)),
    ('snap shooting stationary',
     AttackerProfile('D6', 3, 6, 2, '2', ['Snap Shooting', 'Blast'], models=3),
     DefenderProfile(6, 3, 3, model_count=6), BattleConditions(stationary=True, reroll_hits='all')),
    ('psychic damaged in cover',
     AttackerProfile('D6+1', 3, 6, 1, 'D3+1', ['Psychic', 'Critical Wounds 5+', 'Devastating Wounds', 'Melta 1'], models=2),
     DefenderProfile(8, 3, 4, model_count=3, keywords=['Stealth', 'Damage Modifier -1']),
     BattleConditions(cover=True, damaged=True, half_range=True)),
    ('leader-granted invulnerable save',
     AttackerProfile('4', 3, 8, 3, '2', ['Lethal Hits'], models=3),
     DefenderProfile(5, 3, 2, model_count=5, keywords=['Invulnerable Save 4+', 'Feel No Pain 5+']),
     BattleConditions()),
]


@pytest.mark.parametrize('name,attacker,defender,conditions', MODIFIER_AGREEMENT_CASES,
                         ids=[c[0] for c in MODIFIER_AGREEMENT_CASES])
def test_monte_carlo_agrees_with_modifiers(name, attacker, defender, conditions):
    test_monte_carlo_agrees_with_analytical(name, attacker, defender, conditions)


# --- Stage 8: Excel's damage cap and models-killed method --------------------

def test_damage_per_attack_is_capped_at_target_wounds():
    # 6 auto-hits, S8 vs T4 wound on 2+ -> 5 unsaved; D3 vs W2 counts as 2 each, 1 lost per attack
    result = run(skill=None, strength=8, damage='3', wounds=2, target_models=10)
    assert result.average_damage == approx(10)
    assert result.overkill == approx(5)  # damage lost to the cap
    assert result.models_killed == approx(5)  # one attack per kill


def test_models_killed_uses_attacks_per_kill():
    # D1 vs W3: 3 attacks per kill; 5 unsaved -> 1.67 kills (total damage / W would agree here)
    assert run(skill=None, strength=8, damage='1', wounds=3, target_models=10).models_killed == approx(5 / 3)
    # D2 vs W3: still 2 attacks per kill (the rest of the second hit is lost) -> 2.5 kills, not 10/3
    assert run(skill=None, strength=8, damage='2', wounds=3, target_models=10).models_killed == approx(2.5)
    # Feel No Pain 5+ makes each attack count 4/6: 3 / (4/6) = 4.5 attacks per kill
    fnp = run(skill=None, strength=8, damage='1', wounds=3, target_models=10, target_keywords=['Feel No Pain 5+'])
    assert fnp.models_killed == approx(5 / 4.5)
    # Kills never exceed the unit size
    assert run(skill=None, strength=8, damage='3', wounds=1, target_models=2).models_killed == approx(2)


# --- Keyword parsing (one implementation, used by the app and the migration script) ---

from combat_sim.simulation import check_keywords, keyword_name


def test_keyword_name():
    assert keyword_name('Anti-Infantry 2+') == 'Anti'
    assert keyword_name('Sustained Hits D3') == 'Sustained Hits'
    assert keyword_name('Rapid Fire D6+1') == 'Rapid Fire'
    assert keyword_name('Hit Modifier +1') == 'Hit Modifier'
    assert keyword_name('Feel No Pain 5+') == 'Feel No Pain'
    assert keyword_name(' Lethal Hits ') == 'Lethal Hits'


def test_check_keywords():
    known = ['Lethal Hits', 'Anti', 'Feel No Pain', 'Stealth']
    assert check_keywords('Lethal Hits, anti-vehicle 4+, None, ', known) == {'unknown': [], 'capitals': []}
    assert check_keywords('lethal hits, Lethal Hitz', known) == {
        'unknown': ['Lethal Hitz'], 'capitals': [['lethal hits', 'Lethal Hits']]}
    # Unit mode: plain words are unit keywords for Anti; only entries with values are checked
    assert check_keywords('Infantry, Feel No Pain 5+, Toughnes Modifier +1', known, mode='unit') == {
        'unknown': ['Toughnes Modifier +1'], 'capitals': []}


# --- Attached units: bodyguard models first, then the leader -----------------------------------------

from combat_sim.simulation import simulate_attached_analytical, simulate_monte_carlo_attached  # noqa: E402


def test_attached_without_overflow_is_the_bodyguard_unit():
    weapon = AttackerProfile('2', 3, 4, 1, '1', ['Lethal Hits'], models=5)
    bodyguard = DefenderProfile(4, 3, 2, model_count=10)
    leader = DefenderProfile(4, 2, 5, invulnerable_save=4, model_count=1)
    combined, per_weapon = simulate_attached_analytical([weapon], bodyguard, leader, BattleConditions())
    plain = simulate_analytical(weapon, bodyguard, BattleConditions())
    assert combined.average_damage == plain.average_damage
    assert combined.bodyguards_killed == plain.models_killed and combined.leader_killed == 0
    assert per_weapon[0].models_killed == plain.models_killed


def test_attached_overflow_goes_to_the_leader_with_its_own_profile():
    # 12 auto-hitting attacks wounding on 2+: 10 unsaved against no-save, 1-wound bodyguards
    weapon = AttackerProfile('12', None, 10, 0, '1', [], models=1)
    bodyguard = DefenderProfile(1, 7, 1, model_count=2)
    leader = DefenderProfile(1, 4, 3, model_count=1)  # 4+ save, 3 wounds
    combined, per_weapon = simulate_attached_analytical([weapon], bodyguard, leader, BattleConditions())
    # 2 kills use 2/10 of the attacks; the other 9.6 attacks face the leader: 5/6 wound x 1/2 unsaved = 4 damage
    assert combined.bodyguards_killed == approx(2)
    assert combined.leader_killed == approx(1)  # 4 damage against 3 wounds (kills capped at the leader)
    assert combined.average_damage == approx(2 + 4)
    assert combined.models_killed == approx(3)
    assert per_weapon[0].leader_killed == approx(1)


def test_attached_weapons_in_order_later_ones_only_see_the_leader():
    small = AttackerProfile('12', None, 10, 0, '1', [], models=1)  # Wipes the 2 bodyguards
    big = AttackerProfile('2', None, 10, 3, '3', [], models=1)
    bodyguard = DefenderProfile(1, 7, 1, model_count=2)
    leader = DefenderProfile(1, 3, 6, model_count=1)
    _, per_weapon = simulate_attached_analytical([small, big], bodyguard, leader, BattleConditions())
    assert per_weapon[1].bodyguards_killed == 0
    alone = simulate_analytical(big, leader, BattleConditions())
    assert per_weapon[1].average_damage == approx(alone.average_damage)


# The bodyguards are wiped in nearly every trial, so both engines agree on them; on the leader the
# analytical engine gives expected kills (capped, as Excel does) and Monte Carlo the chance it dies,
# so they are only compared on damage
ATTACHED_CASES = [
    ('bodyguards wiped, leader hurt',
     [AttackerProfile('3', 3, 5, 1, '1', ['Sustained Hits 1'], models=20)],
     DefenderProfile(4, 4, 1, model_count=5), DefenderProfile(4, 3, 6, invulnerable_save=5, model_count=1)),
    ('two weapons, tough leader',
     [AttackerProfile('2', 3, 6, 2, '2', [], models=10), AttackerProfile('D6', None, 5, 1, '1', ['Torrent'], models=2)],
     DefenderProfile(5, 3, 2, model_count=3), DefenderProfile(5, 2, 8, feel_no_pain=5, model_count=1)),
]


@pytest.mark.parametrize('name,weapons,bodyguard,leader', ATTACHED_CASES, ids=[c[0] for c in ATTACHED_CASES])
def test_attached_monte_carlo_agrees_with_analytical(name, weapons, bodyguard, leader):
    random.seed(7)
    analytical, _ = simulate_attached_analytical(weapons, bodyguard, leader, BattleConditions())
    rolled = simulate_monte_carlo_attached(weapons, bodyguard, leader, BattleConditions(), iterations=6000)
    assert rolled.average_damage == pytest.approx(analytical.average_damage, rel=0.08)
    assert analytical.bodyguards_killed == bodyguard.model_count
    assert rolled.bodyguards_killed == pytest.approx(bodyguard.model_count, abs=0.1)
    assert analytical.leader_killed > 0 and rolled.leader_killed > 0  # Both send the overflow at the leader
    assert rolled.models_killed == pytest.approx(rolled.bodyguards_killed + rolled.leader_killed, abs=0.01)
    assert rolled.leader_kill_probability == pytest.approx(rolled.leader_killed * 100, abs=1)  # Rounded separately


def test_monte_carlo_leader_only_dies_after_every_bodyguard():
    random.seed(3)
    weapon = AttackerProfile('6', 3, 4, 0, '1', [], models=1)
    bodyguard = DefenderProfile(4, 4, 1, model_count=10)  # Never all dead from 6 attacks
    leader = DefenderProfile(4, 4, 1, model_count=1)
    rolled = simulate_monte_carlo_attached([weapon], bodyguard, leader, BattleConditions(), iterations=500)
    assert rolled.leader_killed == 0 and rolled.leader_kill_probability == 0
