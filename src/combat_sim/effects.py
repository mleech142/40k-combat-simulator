"""
Which effect rows apply to an attack: leader buffs and enhancement buffs, as plain rows
({'applies_to': 'Attacker'|'Target', 'phase': 'Shooting'|'Fighting'|'Both', 'modifier', 'scope'}).
Shared by the Simulator's routes (app.py) and Play mode's game state, so both read them the same way.
"""

# Phases a rule must have to apply to a weapon of each type
RULE_PHASES = {'ranged': ('Shooting', 'Both'), 'melee': ('Fighting', 'Both')}


def applies(row, side, weapon_type):
    """Whether an effect row is for this side and the attacking weapon's phase."""
    return ((row.get('applies_to') or 'Attacker') == side
            and (row.get('phase') or 'Both') in RULE_PHASES['melee' if weapon_type == 'melee' else 'ranged'])


def split_modifiers(text):
    return [m.strip() for m in (text or '').split(',') if m.strip()]


def leader_modifiers(buffs, side, weapon_type):
    """Modifier text a leader's buffs grant the unit it leads, for one side and the attacking weapon's phase."""
    return [m for buff in buffs if applies(buff, side, weapon_type) for m in split_modifiers(buff.get('modifier'))]


def enhancement_modifiers(carried, side, weapon_type, leader_weapon=False):
    """
    Modifier text from enhancements a unit carries, [(buffs, bearer)] with bearer 'unit' or 'leader',
    for one side and the attacking weapon's phase. An effect for the bearer only applies to the
    bearer's own weapons (the leader's when the leader carries it) and, as a target, only when the
    unit is the bearer.
    """
    modifiers = []
    for buffs, bearer in carried:
        for buff in buffs or []:
            if not applies(buff, side, weapon_type):
                continue
            if buff.get('scope') == 'bearer':
                if side == 'Attacker' and (bearer == 'leader') != bool(leader_weapon):
                    continue
                if side == 'Target' and bearer != 'unit':
                    continue
            modifiers += split_modifiers(buff.get('modifier'))
    return modifiers
