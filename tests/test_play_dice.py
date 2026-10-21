"""
Game dice (src/combat_sim/play): every roll recorded for the game log, using the
simulation engine's resolve() only for what each roll needs. The Simulator and
Analytics never use these.
"""

import random

import pytest

from combat_sim.play.attack import roll_attack, roll_wound
from combat_sim.play.dice import Dice
from combat_sim.simulation import AttackerProfile, BattleConditions, DefenderProfile, simulate_analytical
from test_simulation import AGREEMENT_CASES, MODIFIER_AGREEMENT_CASES


class FixedRolls:
    """A random source that gives set faces in order (the rest of random.Random isn't needed)."""

    def __init__(self, *faces):
        self.faces = list(faces)

    def randint(self, low, high):
        face = self.faces.pop(0)
        assert low <= face <= high, f'face {face} is not on a D{high}'
        return face


# --- Dice ---------------------------------------------------------------------------

def test_roll_records_each_face_and_whether_it_succeeds():
    dice = Dice(FixedRolls(6, 2, 3))
    group = dice.roll(3, 'hit', need=3)
    assert [(d.face, d.success, d.critical) for d in group.dice] == [(6, True, True), (2, False, False),
                                                                    (3, True, False)]
    assert group.successes == 2 and group.criticals == 1
    assert dice.log == [group]


def test_critical_threshold_succeeds_even_below_the_roll_needed():
    group = Dice(FixedRolls(5, 4)).roll(2, 'hit', need=6, crit_on=5)
    assert [(d.success, d.critical) for d in group.dice] == [(True, True), (False, False)]


def test_reroll_ones_rerolls_only_failed_ones_and_keeps_the_first_face():
    group = Dice(FixedRolls(1, 3, 5)).roll(2, 'hit', need=4, reroll='ones')
    assert [(d.face, d.rerolled_from, d.success) for d in group.dice] == [(5, 1, True), (3, None, False)]


def test_reroll_all_rerolls_every_failure_once():
    group = Dice(FixedRolls(2, 6, 1)).roll(2, 'wound', need=4, reroll='all')
    assert [(d.face, d.rerolled_from, d.success) for d in group.dice] == [(1, 2, False), (6, None, True)]


def test_dice_expressions_are_rolled_and_recorded():
    dice = Dice(FixedRolls(2, 3, 4))
    assert dice.roll_value('D3+1', 'attacks') == 3
    assert dice.roll_value('2D6', 'charge') == 7
    assert [(g.purpose, g.size, [d.face for d in g.dice], g.modifier, g.total) for g in dice.log] == [
        ('attacks', 3, [2], 1, 3), ('charge', 6, [3, 4], 0, 7)]


def test_fixed_values_roll_no_dice():
    dice = Dice(FixedRolls())
    assert dice.roll_value('3', 'damage') == 3
    assert dice.log == []


# --- Attacks: hit and wound rolls to the wound pool --------------------------------

def weapon(attacks='4', skill=3, strength=4, ap=0, damage='1', abilities=(), models=1, kind='ranged'):
    return AttackerProfile(attacks, skill, strength, ap, damage, list(abilities), models, kind)


def target(toughness=4, save=4, wounds=1, models=10, keywords=()):
    return DefenderProfile(toughness, save, wounds, keywords=list(keywords), model_count=models)


def test_hits_and_wounds_with_sustained_hits():
    # Hits on 3+: 6 (critical, +1 Sustained hit), 3, 2 (miss), 5 -> 4 hits; S4 vs T4 wounds on 4+: 4, 4, 1, 6 -> 3
    dice = Dice(FixedRolls(6, 3, 2, 5, 4, 4, 1, 6))
    pool = roll_attack(weapon(abilities=['Sustained Hits 1']), target(), BattleConditions(), dice)
    assert (pool.attacks, pool.hits, pool.wounds, pool.mortal) == (4, 4, 3, 0)
    assert [(g.purpose, g.need) for g in dice.log] == [('hit', 3), ('wound', 4)]


def test_sustained_hits_d3_is_rolled_for_each_critical():
    # 2 attacks: 6 (critical, D3 rolls 2 extra hits), 1 -> 3 hits; wounds 4, 4, 4
    dice = Dice(FixedRolls(6, 1, 2, 4, 4, 4))
    pool = roll_attack(weapon(attacks='2', abilities=['Sustained Hits D3']), target(), BattleConditions(), dice)
    assert (pool.hits, pool.wounds) == (3, 3)
    assert [g.purpose for g in dice.log] == ['hit', 'sustained hits', 'wound']


def test_lethal_hits_wound_automatically():
    # 3 attacks: 6 (critical: wounds without a roll), 4, 1 -> 2 hits, only the 4 rolls to wound: 2 fails
    dice = Dice(FixedRolls(6, 4, 1, 2))
    pool = roll_attack(weapon(attacks='3', abilities=['Lethal Hits']), target(), BattleConditions(), dice)
    assert (pool.hits, pool.wounds) == (2, 1)
    assert len(dice.log[-1].dice) == 1


def test_devastating_wounds_critical_wounds_skip_the_save():
    # 3 hits; wounds 6 (critical: mortal), 5, 3 (fails on 4+)
    dice = Dice(FixedRolls(4, 4, 4, 6, 5, 3))
    pool = roll_attack(weapon(attacks='3', abilities=['Devastating Wounds']), target(), BattleConditions(), dice)
    assert (pool.wounds, pool.mortal) == (1, 1)


def test_torrent_hits_without_rolling():
    dice = Dice(FixedRolls(4, 1))
    pool = roll_attack(weapon(attacks='2', abilities=['Torrent']), target(), BattleConditions(), dice)
    assert (pool.hits, pool.wounds) == (2, 1)
    assert [g.purpose for g in dice.log] == ['wound']


def test_attacks_rolled_per_model_with_blast_and_rapid_fire():
    # 2 models with D3 attacks roll 1 and 3; each gets +2 Blast (10 targets) and +1 Rapid Fire at half range
    dice = Dice(FixedRolls(1, 3, *[1] * 10))
    shooter = weapon(attacks='D3', models=2, abilities=['Blast', 'Rapid Fire 1'])
    pool = roll_attack(shooter, target(models=10), BattleConditions(half_range=True), dice)
    assert pool.attacks == 10


def test_attacks_modifier_never_takes_a_model_below_one_attack():
    # D6 attacks -2: a 4 gives 2 attacks, a 1 still gives 1
    modified = weapon(attacks='D6', abilities=['Attacks Modifier -2'])
    assert roll_attack(modified, target(), BattleConditions(), Dice(FixedRolls(4, 1, 1))).attacks == 2
    assert roll_attack(modified, target(), BattleConditions(), Dice(FixedRolls(1, 1))).attacks == 1


def test_rerolls_and_anti_come_from_resolve():
    # Re-roll all hits: 2 -> 5. Anti-Infantry 4+ with Devastating Wounds: S3 vs T4 wounds on 5+, a 4 is critical
    dice = Dice(FixedRolls(2, 5, 4))
    shooter = weapon(attacks='1', strength=3, abilities=['Anti-Infantry 4+', 'Devastating Wounds'])
    pool = roll_attack(shooter, target(keywords=['Infantry']), BattleConditions(reroll_hits='all'), dice)
    assert dice.log[0].dice[0].rerolled_from == 2
    assert (pool.wounds, pool.mortal) == (0, 1)


# --- One allocated wound: save, damage, Feel No Pain --------------------------------

def test_saved_wound_does_no_damage():
    dice = Dice(FixedRolls(4))
    result = roll_wound(weapon(damage='2'), target(save=4), BattleConditions(), dice, wounds_left=2)
    assert (result.saved, result.damage) == (True, 0)
    assert [(g.purpose, g.need) for g in dice.log] == [('save', 4)]


def test_failed_save_takes_rolled_damage():
    dice = Dice(FixedRolls(3, 2))  # Save 3 fails on 4+; D3 damage rolls 2
    result = roll_wound(weapon(damage='D3'), target(save=4), BattleConditions(), dice, wounds_left=3)
    assert (result.saved, result.damage) == (False, 2)


def test_damage_beyond_the_models_wounds_is_lost():
    result = roll_wound(weapon(damage='3'), target(save=4), BattleConditions(), Dice(FixedRolls(1)), wounds_left=2)
    assert result.damage == 2


def test_no_save_possible_rolls_no_save_dice():
    # Save 6+ against AP-2: nothing to roll, even a 6 wouldn't save
    dice = Dice(FixedRolls())
    result = roll_wound(weapon(ap=2), target(save=6), BattleConditions(), dice, wounds_left=1)
    assert (result.saved, result.damage) == (False, 1)
    assert dice.log == []


def test_melta_adds_its_rolled_bonus_at_half_range():
    # Failed save (1), D6 damage rolls 3, Melta D3 rolls 2 -> 5
    melta = weapon(damage='D6', abilities=['Melta D3'])
    result = roll_wound(melta, target(save=4), BattleConditions(half_range=True), Dice(FixedRolls(1, 3, 2)),
                        wounds_left=12)
    assert result.damage == 5


def test_mortal_wound_skips_the_save():
    dice = Dice(FixedRolls())
    result = roll_wound(weapon(damage='2'), target(save=2), BattleConditions(), dice, wounds_left=3, mortal=True)
    assert (result.saved, result.damage) == (False, 2)


def test_feel_no_pain_rolls_for_each_point_of_damage():
    # Failed save (1); 3 damage; Feel No Pain 5+ rolls 5, 1, 6 -> 1 damage
    defender = DefenderProfile(4, 4, 3, feel_no_pain=5, model_count=1)
    dice = Dice(FixedRolls(1, 5, 1, 6))
    result = roll_wound(weapon(damage='3'), defender, BattleConditions(), dice, wounds_left=3)
    assert result.damage == 1
    assert [(g.purpose, g.need) for g in dice.log] == [('save', 4), ('feel no pain', 5)]


# --- Game dice agree with the Simulator's averages ----------------------------------
# Read-only use of the analytical engine: over many games, the average of game rolls must match it.

def game_average(attacker, defender, conditions, games):
    dice = Dice(random.Random(1234))
    totals = {'attacks': 0, 'hits': 0, 'wounds': 0, 'damage': 0}
    for _ in range(games):
        pool = roll_attack(attacker, defender, conditions, dice)
        totals['attacks'] += pool.attacks
        totals['hits'] += pool.hits
        totals['wounds'] += pool.wounds + pool.mortal
        for mortal in [False] * pool.wounds + [True] * pool.mortal:
            totals['damage'] += roll_wound(attacker, defender, conditions, dice, defender.wounds, mortal).damage
        dice.log.clear()
    return {k: v / games for k, v in totals.items()}


@pytest.mark.parametrize('name,attacker,defender,conditions', AGREEMENT_CASES + MODIFIER_AGREEMENT_CASES,
                         ids=[c[0] for c in AGREEMENT_CASES + MODIFIER_AGREEMENT_CASES])
def test_game_dice_agree_with_the_simulator(name, attacker, defender, conditions):
    analytical = simulate_analytical(attacker, defender, conditions)
    game = game_average(attacker, defender, conditions, games=3000)
    for field, expected in (('attacks', analytical.attacks_made), ('hits', analytical.hits),
                            ('wounds', analytical.wounds), ('damage', analytical.average_damage)):
        assert game[field] == pytest.approx(expected, rel=0.04, abs=0.05), f'{field}: game {game[field]} vs {expected}'
