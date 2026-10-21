"""
One weapon's attacks in a game, rolled step by step with game dice. What each roll needs comes
from simulation.resolve(), so the game and the Simulator read keywords and modifiers the same way.
"""

from dataclasses import dataclass

from ..simulation import _value_avg, has_keyword, keyword_avg, keyword_entries, resolve


@dataclass
class WoundPool:
    """What a weapon's attacks did before saves: wounds to allocate, and mortal wounds (no save)."""
    attacks: int
    hits: int
    wounds: int
    mortal: int


def roll_attack(attacker, defender, conditions, dice):
    """Roll a weapon's attacks, hits and wounds against `defender` (simulation profiles)."""
    r = resolve(attacker, defender, conditions)
    attacks = sum(_model_attacks(attacker, defender, r, dice) for _ in range(attacker.models))

    if r.auto_hit:  # Torrent, or no BS/WS: every attack hits, never critically
        hits, lethal = attacks, 0
    else:
        hit_roll = dice.roll(attacks, 'hit', r.hit_need, r.crit_hit_on, r.hit_reroll)
        lethal = hit_roll.criticals if r.lethal else 0  # Critical hits that wound without a roll
        hits = hit_roll.successes - lethal
        if r.sustained:
            hits += sum(keyword_value(attacker.abilities, 'Sustained Hits', dice) for _ in range(hit_roll.criticals))

    wound_roll = dice.roll(hits, 'wound', r.wound_need, r.crit_wound_on, r.wound_reroll)
    mortal = wound_roll.criticals if r.devastating else 0  # Critical wounds with no save
    return WoundPool(attacks, hits + lethal, wound_roll.successes - mortal + lethal, mortal)


@dataclass
class WoundResult:
    """One allocated wound: whether it was saved, and the damage the model took."""
    saved: bool
    damage: int


def roll_wound(attacker, defender, conditions, dice, wounds_left, mortal=False):
    """
    Resolve one wound allocated to a model with `wounds_left`: its save (none for a mortal wound
    from Devastating Wounds), the weapon's damage (excess is lost), then Feel No Pain per point.
    """
    r = resolve(attacker, defender, conditions)
    if not mortal and r.save_need <= 6 and dice.roll(1, 'save', r.save_need, crit_on=7).successes:
        return WoundResult(True, 0)  # (resolve() already chose the better of armour and invulnerable)

    # Damage: Melta's bonus is rolled at half range; resolve()'s damage_bonus holds its average
    melta = not attacker.is_melee and conditions.half_range and has_keyword(attacker.abilities, 'Melta')
    bonus = r.damage_bonus - (keyword_avg(attacker.abilities, 'Melta') if melta else 0)
    damage = dice.roll_value(attacker.damage, 'damage') + bonus
    if melta:
        damage += keyword_value(attacker.abilities, 'Melta', dice)
    damage = min(max(1, int(round(damage))), wounds_left)
    if defender.feel_no_pain:
        damage -= dice.roll(damage, 'feel no pain', defender.feel_no_pain, crit_on=7).successes
    return WoundResult(False, damage)


def _model_attacks(attacker, defender, r, dice):
    """One model's attacks: its A (at least 1 after Attacks Modifiers), plus Blast / Cleave per 5
    target models and Rapid Fire when resolve() counts it (half range)."""
    attacks = max(1, dice.roll_value(attacker.attacks, 'attacks') + round(r.attacks_bonus_flat))
    for key in r.extra_attacks:
        value = keyword_value(attacker.abilities, key, dice)
        attacks += value * (defender.model_count // 5) if key in ('Blast', 'Cleave') else value
    return attacks


def keyword_value(abilities, key, dice):
    """A keyword's value, rolled if it is dice ('Sustained Hits D3'); the largest entry; bare = 1; absent = 0."""
    entries = keyword_entries(abilities, key)
    if not entries:
        return 0
    value = max(entries, key=_value_avg)
    return 1 if value == '' else dice.roll_value(value, key.lower())
