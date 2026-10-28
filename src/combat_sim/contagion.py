"""
Death Guard's army rule, Nurgle's Gift (Aura): Contagion Range, Afflicted units and the Plagues.

While an enemy unit is within Contagion Range of a Death Guard model from the army, it is Afflicted:
-1 Toughness, plus the effect of the Plague the Death Guard player chose. Abilities can also make a unit
Afflicted for a while (Plague Marines' Infused with the Blessings of Nurgle), and some attacks are better
against Afflicted units (effect rows with 'when': AFFLICTED; effects.applies).

The rule's text stays in BSData; its effects are written here, so the Simulator, Analytics (app.py) and
Play mode read them the same way. An affliction is None (not Afflicted), NO_PLAGUE (Afflicted, no Plague
chosen) or a key of PLAGUES.
"""

AFFLICTED = 'afflicted'  # The condition effect rows name ('when') for attacks that target an Afflicted unit
NO_PLAGUE = 'none'
PLAGUES = {
    'skullsquirm': {'name': 'Skullsquirm Blight',
                    'summary': 'its ranged attacks give their targets the benefit of cover; -1 to hit with its melee attacks'},
    'rattlejoint': {'name': 'Rattlejoint Ague', 'summary': 'Save worsened by 1'},
    'scabrous': {'name': 'Scabrous Soulrot',
                 'summary': 'Move, Leadership and Objective Control worsened by 1 (Objective Control to no less than 1)'},
}
RULE_NAME = "Nurgle's Gift (Aura)"  # The datasheet ability that makes a model's Contagion Range count
RANGES = ((3, 9), (2, 6), (1, 3))  # (from battle round, Contagion Range in inches)
MAX_RANGE = 12  # After modifiers


def choices():
    """The afflictions a page can offer: [{'key', 'name', 'summary'}], Afflicted with no Plague first."""
    return [{'key': NO_PLAGUE, 'name': 'Afflicted (no Plague)', 'summary': '-1 Toughness'}] + [
        {'key': key, 'name': f'Afflicted: {plague["name"]}', 'summary': f'-1 Toughness; {plague["summary"]}'}
        for key, plague in PLAGUES.items()]


def affliction(value):
    """A request's affliction made safe: None, NO_PLAGUE or a Plague's key (anything else is None)."""
    return value if value == NO_PLAGUE or value in PLAGUES else None


def contagion_range(battle_round, bonus=0):
    """Contagion Range in a battle round (before the battle counts as the first), with `bonus` inches added."""
    base = next(inches for start, inches in RANGES if (battle_round or 1) >= start)
    return min(MAX_RANGE, base + bonus)


def conditions(target_affliction):
    """The conditions an attack meets against a target (for effects.applies)."""
    return (AFFLICTED,) if target_affliction is not None else ()


def modifiers(attacker_affliction, target_affliction, weapon_type):
    """
    (attacker modifiers, target modifiers) the afflictions add to one attack: an Afflicted target has -1 Toughness
    (and a worse Save with Rattlejoint Ague); an attacker with Skullsquirm Blight gives its targets cover against
    ranged attacks (Stealth, which the maths reads as cover) and has -1 to hit in melee.
    """
    attacker, target = [], []
    if target_affliction is not None:
        target.append('Toughness Modifier -1')
        if target_affliction == 'rattlejoint':
            target.append('Save Modifier -1')
    if attacker_affliction == 'skullsquirm':
        if weapon_type == 'melee':
            attacker.append('Hit Modifier -1')
        else:
            target.append('Stealth')
    return attacker, target


def gives_cover(attacker_affliction, weapon_type):
    """Skullsquirm Blight: the targets of an Afflicted unit's ranged attacks have the benefit of cover."""
    return attacker_affliction == 'skullsquirm' and weapon_type != 'melee'


def worsen_move(move, affliction_key):
    """Scabrous Soulrot: Move worsened by 1."""
    return max(0, move - 1) if affliction_key == 'scabrous' else move


def worsen_leadership(leadership, affliction_key):
    """Scabrous Soulrot: Leadership (a 2D6 target) worsened by 1."""
    return leadership + 1 if affliction_key == 'scabrous' else leadership


def worsen_oc(oc, affliction_key):
    """Scabrous Soulrot: Objective Control worsened by 1, but never below 1 by this rule (0 stays 0)."""
    return max(min(oc, 1), oc - 1) if affliction_key == 'scabrous' else oc
