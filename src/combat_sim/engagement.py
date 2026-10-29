"""
Setting up an attack (architecture review, candidate 6): every source that adds modifier text to one side of an
attack, in one place and one order, for the Simulator, the Analytics matrix and its cell detail, reverse
durability and Play (play/game.py `_attacker` / `_defender`).

One side's sources are a `Sources`: effect rows from ticked rules and stratagems (or, in Play, the unit's game
effects), its leader's buffs, the enhancements it carries, the attacking unit's and leader's own datasheet ability
buffs, its Death Guard affliction, and one-off target modifiers. Two calls turn them into modifier text:

- `attack_modifiers(attacker, target_affliction, weapon_type, leader_weapon, faction_id)`
- `target_modifiers(target, attacker_affliction, weapon_type, faction_id)`

Rows carrying a `faction_id` apply only to that faction's units (a core stratagem's is None). A new source of
effects goes here, and reaches every caller and attached targets with it. The database adapters (`sources_for`,
`selected_rules`, `load_*`) the profile builders (`weapon_attack`, `unit_target`, `leader_target`) and the one
characteristics reader with wargear's changes (`unit_stats`, `stat_changes`) are here too.
"""

from dataclasses import dataclass, field

from . import contagion, effects
from .models import Enhancement, FactionRule, MeleeWeapon, RangedWeapon, Stratagem, Unit, db
from .rules_text import ability_buffs
from .simulation import AttackerProfile, DefenderProfile


@dataclass
class Sources:
    rows: list = field(default_factory=list)  # effect rows: {'applies_to', 'phase', 'modifier'[, 'faction_id']}
    leader_buffs: list = field(default_factory=list)  # the leader's buff rows (for the unit it leads)
    carried: list = field(default_factory=list)  # [(enhancement buff rows, 'unit' | 'leader')]
    unit_buffs: list = field(default_factory=list)  # the unit's own datasheet ability buffs (its own weapons)
    leader_own_buffs: list = field(default_factory=list)  # the leader's own ability buffs (the leader's weapons)
    affliction: object = None  # Death Guard (contagion.affliction)
    extra: list = field(default_factory=list)  # one-off target modifiers


def _rows(rows, faction_id):
    return [r for r in rows if r.get('faction_id') is None or faction_id is None or r['faction_id'] == faction_id]


def attack_modifiers(attacker, target_affliction, weapon_type, leader_weapon=False, faction_id=None):
    """The modifier text one attacking weapon gets: rules, the leader's buffs, enhancements, the weapon's own
    datasheet's abilities (the leader's for its own weapons) and Death Guard's (the target's affliction met)."""
    met = contagion.conditions(target_affliction)
    return (effects.leader_modifiers(_rows(attacker.rows, faction_id), 'Attacker', weapon_type, met)
            + effects.leader_modifiers(attacker.leader_buffs, 'Attacker', weapon_type, met)
            + effects.enhancement_modifiers(attacker.carried, 'Attacker', weapon_type, bool(leader_weapon), met)
            + effects.leader_modifiers(attacker.leader_own_buffs if leader_weapon else attacker.unit_buffs,
                                       'Attacker', weapon_type, met)
            + contagion.modifiers(attacker.affliction, target_affliction, weapon_type)[0])


def target_modifiers(target, attacker_affliction, weapon_type, faction_id=None):
    """The modifier text a target gets against one weapon: rules, its leader's buffs, its enhancements, one-off
    modifiers and Death Guard's."""
    return (effects.leader_modifiers(_rows(target.rows, faction_id), 'Target', weapon_type)
            + effects.leader_modifiers(target.leader_buffs, 'Target', weapon_type)
            + effects.enhancement_modifiers(target.carried, 'Target', weapon_type)
            + list(target.extra)
            + contagion.modifiers(attacker_affliction, target.affliction, weapon_type)[1])


# --- From the database (the Simulator and Analytics) ------------------------------------------------

def selected_rules(selection):
    """Ticked faction rules and stratagems from {'rule_ids': [...], 'stratagem_ids': [...]} (not superseded ones)."""
    selection = selection or {}
    rule_ids = [int(i) for i in selection.get('rule_ids', []) or []]
    strat_ids = [int(i) for i in selection.get('stratagem_ids', []) or []]
    rules = FactionRule.query.filter(FactionRule.id.in_(rule_ids)).all() if rule_ids else []
    strats = Stratagem.query.filter(Stratagem.id.in_(strat_ids)).all() if strat_ids else []
    return [r for r in rules + strats if not r.superseded]


def rule_rows(records):
    """Ticked rules and stratagems as effect rows ('none' entries dropped)."""
    rows = []
    for record in records:
        modifier = ', '.join(m.strip() for m in (record.modifier or '').split(',') if m.strip() and m.strip().lower() != 'none')
        if modifier:
            rows.append({'applies_to': record.applies_to or 'Attacker', 'phase': record.phase or 'Both',
                         'modifier': modifier, 'faction_id': record.faction_id})
    return rows


def load_leader(leader_id):
    """The unit named as leading another in a request (or any unit by ID), or None."""
    try:
        return db.session.get(Unit, int(leader_id)) if leader_id not in (None, '') else None
    except (TypeError, ValueError):
        return None


def load_enhancements(items):
    """[(Enhancement, bearer)] from a request's [{'id', 'bearer'}]: 'leader' when the unit's leader carries it, else 'unit'."""
    carried = []
    for item in items or []:
        try:
            enhancement = db.session.get(Enhancement, int(item.get('id')))
        except (TypeError, ValueError, AttributeError):
            continue
        if enhancement is not None:
            carried.append((enhancement, 'leader' if item.get('bearer') == 'leader' else 'unit'))
    return carried


def enhancement_points(carried):
    return sum(enhancement.points or 0 for enhancement, _ in carried)


def afflictions(data):
    """(attackers' affliction, defenders' affliction) from a matrix request's {'afflicted': {'attackers', 'defenders'}}."""
    found = data.get('afflicted') or {}
    return contagion.affliction(found.get('attackers')), contagion.affliction(found.get('defenders'))


def sources_for(unit=None, leader=None, enhancements=(), rules=(), affliction=None, extra=()):
    """One side's sources from database records: its unit, leader, carried enhancements (load_enhancements),
    ticked rules (selected_rules), affliction and one-off modifiers."""
    return Sources(rows=rule_rows(rules),
                   leader_buffs=list(leader.effective_leader_buffs) if leader is not None else [],
                   carried=[(enhancement.buffs, bearer) for enhancement, bearer in enhancements],
                   unit_buffs=ability_buffs(unit.abilities) if unit is not None else [],
                   leader_own_buffs=ability_buffs(leader.abilities) if leader is not None else [],
                   affliction=affliction, extra=list(extra))


# --- Characteristics, with the chosen wargear's changes ----------------------------------------

STAT_FIELDS = {'M': 'move', 'T': 'toughness', 'Sv': 'save', 'W': 'wounds', 'LD': 'leadership', 'OC': 'oc'}
STATS = ('move', 'toughness', 'save', 'wounds', 'leadership', 'oc', 'invulnerable_save', 'feel_no_pain')


def stat_changes(effects):
    """The part of the chosen wargear's effects (wargear.effects) that changes characteristics, cleaned (it also
    comes back in requests): {'stats': {'W': 1, ...}, 'invulnerable_save': 4 or None}, or None for no change."""
    effects = effects if isinstance(effects, dict) else {}
    raw = effects.get('stats') if isinstance(effects.get('stats'), dict) else {}
    stats = {name: change for name, change in raw.items()
             if name in STAT_FIELDS and isinstance(change, int) and not isinstance(change, bool) and change}
    invulnerable = effects.get('invulnerable_save')
    invulnerable = invulnerable if isinstance(invulnerable, int) and not isinstance(invulnerable, bool) and 2 <= invulnerable <= 6 else None
    return {'stats': stats, 'invulnerable_save': invulnerable} if stats or invulnerable else None


def unit_stats(unit, changes=None):
    """A datasheet's (or target profile's) characteristics with the chosen wargear's changes (stat_changes)
    applied: the one reader for the Match view, Analytics and Play. A taken option's invulnerable save counts
    when it is better than the datasheet's."""
    stats = {name: getattr(unit, name, None) for name in STATS}
    changes = stat_changes(changes) or {}
    for name, change in changes.get('stats', {}).items():
        field_name = STAT_FIELDS[name]
        current = stats.get(field_name)
        if field_name == 'move' and current is not None and str(current).isdigit():
            stats[field_name] = str(int(current) + change)
        elif isinstance(current, int):
            stats[field_name] = current + change
    invulnerable = changes.get('invulnerable_save')
    if invulnerable and (stats['invulnerable_save'] is None or invulnerable < stats['invulnerable_save']):
        stats['invulnerable_save'] = invulnerable
    return stats


def weapon_attack(weapon_type, weapon_id, models=1, modifiers=()):
    """(the library weapon, its AttackerProfile with `modifiers`), or (None, None) if it no longer exists."""
    model = RangedWeapon if weapon_type == 'ranged' else MeleeWeapon
    weapon = db.session.get(model, weapon_id) if weapon_id is not None else None
    if not weapon:
        return None, None
    skill = weapon.ballistic_skill if weapon_type == 'ranged' else weapon.weapon_skill
    return weapon, AttackerProfile(attacks=weapon.attacks, skill=skill, strength=weapon.strength,
                                   ap=weapon.armor_penetration, damage=weapon.damage,
                                   abilities=list(weapon.abilities or []) + list(modifiers),
                                   models=max(1, int(models or 1)), weapon_type=weapon_type)


def unit_target(unit, model_count, modifiers=(), changes=None):
    """A unit (or a generic target profile) as a target, with `modifiers` and its wargear's stat `changes`."""
    stats = unit_stats(unit, changes)
    return DefenderProfile(toughness=stats['toughness'], save=stats['save'], wounds=stats['wounds'],
                           invulnerable_save=stats['invulnerable_save'], feel_no_pain=stats['feel_no_pain'],
                           keywords=unit.target_keywords + list(modifiers), model_count=model_count)


def leader_target(leader, modifiers=(), changes=None):
    """A leader's own profile as the last stage of an attached-unit target (its models after the bodyguard)."""
    return unit_target(leader, leader.default_models or 1, modifiers, changes)
