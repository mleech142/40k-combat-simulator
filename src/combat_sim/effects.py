"""
Which effect rows apply to an attack: leader buffs, enhancement buffs and datasheet abilities, as plain rows
({'applies_to': 'Attacker'|'Target', 'phase': 'Shooting'|'Fighting'|'Both', 'modifier', 'scope', 'when'}).
A row with 'when' applies only to attacks that meet that condition (contagion.AFFLICTED: the target is
Afflicted); callers pass the conditions an attack meets, so a row whose condition nobody checks never applies.
Shared by the Simulator's routes (app.py) and Play mode's game state, so both read them the same way.
"""

# Phases a rule must have to apply to a weapon of each type
RULE_PHASES = {'ranged': ('Shooting', 'Both'), 'melee': ('Fighting', 'Both')}


def applies(row, side, weapon_type, conditions=()):
    """Whether an effect row is for this side and the attacking weapon's phase, and its condition (if any) is met."""
    return ((row.get('applies_to') or 'Attacker') == side
            and (row.get('phase') or 'Both') in RULE_PHASES['melee' if weapon_type == 'melee' else 'ranged']
            and (not row.get('when') or row['when'] in conditions))


def split_modifiers(text):
    return [m.strip() for m in (text or '').split(',') if m.strip()]


def leader_modifiers(buffs, side, weapon_type, conditions=()):
    """Modifier text from effect rows (a leader's buffs, a datasheet's ability buffs), for one side, the attacking
    weapon's phase and the conditions the attack meets."""
    return [m for buff in buffs or [] if applies(buff, side, weapon_type, conditions)
            for m in split_modifiers(buff.get('modifier'))]


def enhancement_modifiers(carried, side, weapon_type, leader_weapon=False, conditions=()):
    """
    Modifier text from enhancements a unit carries, [(buffs, bearer)] with bearer 'unit' or 'leader',
    for one side and the attacking weapon's phase. An effect for the bearer only applies to the
    bearer's own weapons (the leader's when the leader carries it) and, as a target, only when the
    unit is the bearer.
    """
    modifiers = []
    for buffs, bearer in carried:
        for buff in buffs or []:
            if not applies(buff, side, weapon_type, conditions):
                continue
            if buff.get('scope') == 'bearer':
                if side == 'Attacker' and (bearer == 'leader') != bool(leader_weapon):
                    continue
                if side == 'Target' and bearer != 'unit':
                    continue
            modifiers += split_modifiers(buff.get('modifier'))
    return modifiers
