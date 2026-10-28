"""
Changes to the shared app data (architecture review, candidate 5): custom factions, units and weapons, faction rules
and stratagems (and how the maths reads Wahapedia's), and the modifiers and leader buffs added to BSData units.

An edit is a value, `Edit(kind, action, ident, fields)`: kind 'faction' | 'rule' | 'stratagem' | 'unit' | 'weapon',
action 'add' | 'change' | 'delete', the record's ID (none for 'add') and the fields the Data tab sent. Three calls:

- `apply(app, edit)`: make it and save the app data (reference.save_app_data); answers what the Data tab shows.
- `preview(edit)`: make it, read what it changed (each column of the record, before and after), and undo it;
  answers a `Preview` (summary, changes, the record before). Proposals use it when a player proposes (so a change
  that can't be made is refused then, not when the host approves) and when the host reviews.
- `snapshot(edit)`: the record as it is now (to show what changed since a proposal was made).

Both raise `EditError` (with the status for the web answer) for a change that can't be made: a record that's gone,
a BSData or Wahapedia record that's read-only, a field missing or wrong. The Data-tab routes are labelled with
`edit_route(kind, action)`, which also makes them @proposable (access.py), so proposals rebuild an edit from a route.
"""

from dataclasses import dataclass, field

from . import contagion, reference
from .access import proposable
from .data_store import LINKED_FIELDS
from .models import (BSDATA_ID_BASE, Detachment, Faction, FactionRule, MeleeWeapon, RangedWeapon, Stratagem, Unit, db,
                     unit_melee_weapons, unit_ranged_weapons)

BUFF_SIDES = ('Attacker', 'Target')
BUFF_PHASES = ('Shooting', 'Fighting', 'Both')
BUFF_CONDITIONS = (contagion.AFFLICTED,)  # Conditions a buff row can name ('when')
WEAPON_MODELS = {'ranged': RangedWeapon, 'melee': MeleeWeapon}
WAHAPEDIA_READ_ONLY = "Rules and stratagems from Wahapedia can't be deleted; change how the maths reads them instead."
NOUNS = {'faction': 'faction', 'rule': 'rule', 'stratagem': 'stratagem', 'unit': 'unit',
         'ranged': 'ranged weapon', 'melee': 'melee weapon'}
VERBS = {'add': 'Add', 'change': 'Edit', 'delete': 'Delete'}
METHODS = {'add': 'POST', 'change': 'PUT', 'delete': 'DELETE'}


class EditError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass
class Edit:
    kind: str
    action: str
    ident: int = None
    fields: dict = field(default_factory=dict)
    weapon_type: str = None  # weapons: 'ranged' or 'melee'

    def to_dict(self):
        return {'kind': self.kind, 'action': self.action, 'ident': self.ident, 'fields': self.fields,
                'weapon_type': self.weapon_type}

    @classmethod
    def from_dict(cls, data):
        return cls(data['kind'], data['action'], data.get('ident'), data.get('fields') or {}, data.get('weapon_type'))


def make(kind, action, ident=None, fields=None, weapon_type=None):
    fields = fields if isinstance(fields, dict) else {}
    if kind == 'weapon':
        weapon_type = 'ranged' if (weapon_type or fields.get('type')) == 'ranged' else 'melee'
    return Edit(kind, action, ident, fields, weapon_type)


# --- Routes ---------------------------------------------------------------------------------

def edit_route(kind, action):
    """Label a Data-tab write route with the edit it makes (and make it @proposable)."""
    def mark(view):
        view.edit = (kind, action)
        return proposable(view)
    return mark


def from_route(view, view_args, body):
    """The edit a labelled route makes for this request (proposals.py)."""
    kind, action = view.edit
    ident = next((v for k, v in (view_args or {}).items() if isinstance(v, int)), None)
    return make(kind, action, ident, body, (view_args or {}).get('weapon_type'))


# --- Shared field readers -------------------------------------------------------------------

def int_or_none(value):
    if value in (None, '', '-'):
        return None
    return int(value)


def text_list(value):
    """A list of strings from a list or a comma-separated string."""
    if isinstance(value, str):
        value = value.split(',')
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def clean_buffs(rows):
    """Leader buff rows from a request: side and phase checked, empty modifiers dropped, a known condition kept."""
    cleaned = []
    for row in rows or []:
        modifier = str(row.get('modifier') or '').strip()
        if not modifier:
            continue
        buff = {
            'applies_to': row.get('applies_to') if row.get('applies_to') in BUFF_SIDES else 'Attacker',
            'phase': row.get('phase') if row.get('phase') in BUFF_PHASES else 'Both',
            'modifier': modifier,
        }
        if row.get('when') in BUFF_CONDITIONS:
            buff['when'] = row['when']
        cleaned.append(buff)
    return cleaned


def next_custom_id(model):
    """IDs for records made in the app stay below the BSData range."""
    top = db.session.query(db.func.max(model.id)).filter(model.id < BSDATA_ID_BASE).scalar()
    return (top or 0) + 1


def editable_faction(faction_id):
    faction = db.session.get(Faction, int(faction_id)) if faction_id not in (None, '') else None
    return faction is not None and faction.editable


def bsdata_profile(weapon_type, bsdata_id):
    """A BSData weapon with this profile ID (any faction: the profile is the same)."""
    if not bsdata_id:
        return None
    model = WEAPON_MODELS[weapon_type]
    return model.query.join(Faction).filter(model.bsdata_id == bsdata_id, Faction.source == 'bsdata').first()


# --- The records ----------------------------------------------------------------------------

def _model(edit):
    return {'faction': Faction, 'rule': FactionRule, 'stratagem': Stratagem, 'unit': Unit,
            'weapon': WEAPON_MODELS.get(edit.weapon_type)}[edit.kind]


def _record(edit):
    record = db.session.get(_model(edit), edit.ident) if edit.ident is not None else None
    if record is None:
        raise EditError('That record no longer exists.', 404)
    return record


def _snapshot(record):
    """A record's columns (a unit's loadout too), to compare."""
    if record is None:
        return None
    values = {}
    for column in record.__table__.columns:
        value = getattr(record, column.name)
        values[column.name] = value if isinstance(value, (str, int, float, bool, list, dict, type(None))) else str(value)
    if isinstance(record, Unit):
        for weapon_type in ('ranged', 'melee'):
            values[f'{weapon_type}_weapons'] = [{'weapon_id': w.id, 'qty': qty} for w, qty in record.loadout(weapon_type)]
    return values


def snapshot(edit):
    try:
        return _snapshot(_record(edit)) if edit.action != 'add' else None
    except EditError:
        return None


def _apply_detachment(record, data):
    """detachment_key from the request; its name is looked up (kept if the detachment isn't loaded)."""
    key = (data.get('detachment_key') or '').strip() or None
    known = Detachment.query.filter_by(bsdata_id=key).first() if key else None
    if not key:
        record.detachment_name = None
    elif known:
        record.detachment_name = known.name
    elif key != record.detachment_key:
        record.detachment_name = data.get('detachment_name') or key
    record.detachment_key = key


def _rule_fields(rule, data):
    rule.name = data['name'].strip()
    rule.applies_to = data.get('applies_to') or None
    rule.phase = data.get('phase') or None
    rule.modifier = (data.get('modifier') or '').strip() or None
    _apply_detachment(rule, data)


def _stratagem_fields(strat, data):
    strat.name = data['name'].strip()
    strat.cp = (str(data.get('cp') or '')).strip() or None
    strat.applies_to = data.get('applies_to') or None
    strat.phase = data.get('phase') or None
    strat.modifier = (data.get('modifier') or '').strip() or None
    strat.notes = (data.get('notes') or '').strip() or None
    _apply_detachment(strat, data)


def _wahapedia_reading(model, record, data):
    """How the maths reads a Wahapedia rule or stratagem (applies to, phase, modifier), in every faction that has
    it, or {'reset': true} to go back to what was parsed from its text."""
    if data.get('reset'):
        values, edited = (record.parse or {}).get('parsed') or {}, False
    else:
        values, edited = data, True
    applies_to = values.get('applies_to') or 'Attacker'
    phase = values.get('phase') or None
    if applies_to not in BUFF_SIDES or (phase is not None and phase not in BUFF_PHASES):
        raise EditError('Applies to must be Attacker or Target, and phase Shooting, Fighting or Both.')
    for copy in model.query.filter_by(source='wahapedia', wahapedia_id=record.wahapedia_id):
        copy.applies_to = applies_to
        copy.phase = phase
        copy.modifier = (values.get('modifier') or '').strip() or None
        copy.edited = edited


def _unit_fields(unit, data):
    unit.faction_id = int(data['faction_id'])
    unit.name = data['name'].strip()
    unit.toughness = int(data['toughness'])
    unit.save = int(data['save'])
    unit.wounds = int(data['wounds'])
    unit.invulnerable_save = int_or_none(data.get('invulnerable_save'))
    unit.feel_no_pain = int_or_none(data.get('feel_no_pain'))
    unit.move = (str(data.get('move') or '')).strip() or None
    unit.oc = int_or_none(data.get('oc'))
    unit.leadership = int_or_none(data.get('leadership'))
    brackets = []
    for row in data.get('points_by_size') or []:
        models, points = int_or_none(row.get('models')), int_or_none(row.get('points'))
        if models and points is not None:
            brackets.append({'models': models, 'points': points})
    if brackets or 'points_by_size' in data:
        unit.points_by_size = brackets
        unit.points_cost = brackets[0]['points'] if brackets else None
        unit.default_models = brackets[0]['models'] if brackets else int_or_none(data.get('default_models'))
    else:
        unit.points_cost = int_or_none(data.get('points_cost'))
        unit.default_models = int_or_none(data.get('default_models'))
    unit.keywords = text_list(data.get('keywords'))
    unit.modifiers = text_list(data.get('modifiers'))
    unit.can_lead = text_list(data.get('can_lead'))
    if 'leader_buffs' in data:
        unit.leader_buffs = clean_buffs(data.get('leader_buffs'))
    unit.abilities = (data.get('abilities') or '').strip() or None


def _unit_loadout(unit, data):
    """Set the loadout from [{'weapon_id', 'qty'}], keeping only the unit's own faction's weapons."""
    for weapon_type, model in WEAPON_MODELS.items():
        key = f'{weapon_type}_weapons'
        if key not in data:
            continue
        entries = data[key] or []
        ids = [int(e['weapon_id']) for e in entries]
        allowed = {w.id for w in model.query.filter(model.id.in_(ids), model.faction_id == unit.faction_id)} if ids else set()
        unit.set_loadout(weapon_type, [e for e in entries if int(e['weapon_id']) in allowed])


def _weapon_fields(weapon, weapon_type, data):
    weapon.faction_id = int(data['faction_id'])
    weapon.name = data['name'].strip()
    linked = bsdata_profile(weapon_type, (data.get('linked_bsdata_id') or '').strip() or None)
    weapon.linked_bsdata_id = linked.bsdata_id if linked else None
    if linked:  # Stats come from BSData (and are refreshed from it on every start)
        for name in LINKED_FIELDS[weapon_type]:
            setattr(weapon, name, getattr(linked, name))
        return
    weapon.attacks = str(data['attacks']).strip()
    weapon.strength = int(data['strength'])
    weapon.armor_penetration = abs(int(data['ap'] or 0))
    weapon.damage = str(data['damage']).strip()
    weapon.abilities = text_list(data.get('abilities'))
    skill = int_or_none(data.get('skill'))
    if weapon_type == 'ranged':
        weapon.ballistic_skill = skill
        weapon.range_inches = int_or_none(data.get('range'))
    else:
        weapon.weapon_skill = skill


def _make(edit):
    """Make the edit in the session (nothing committed): (the record it made or changed, or None, and the answer
    for the Data tab, a function to call once it's saved)."""
    data, kind, action = edit.fields, edit.kind, edit.action

    if kind == 'faction':
        if action == 'add':
            faction = Faction(id=next_custom_id(Faction), name=data['name'].strip(), source='custom')
            db.session.add(faction)
            return faction, lambda: {'id': faction.id, 'name': faction.name}
        faction = _record(edit)
        if not faction.editable:
            raise EditError('BSData records are read-only.', 403)
        if action == 'change':
            faction.name = data.get('name', faction.name).strip()
            return faction, lambda: {'id': faction.id, 'name': faction.name}
        for unit in faction.units:
            unit.set_loadout('ranged', [])
            unit.set_loadout('melee', [])
            db.session.delete(unit)
        for model in (RangedWeapon, MeleeWeapon, FactionRule, Stratagem):
            model.query.filter_by(faction_id=faction.id).delete()
        db.session.delete(faction)
        return None, lambda: {'message': 'Faction deleted'}

    if kind in ('rule', 'stratagem'):
        model, fields = (FactionRule, _rule_fields) if kind == 'rule' else (Stratagem, _stratagem_fields)
        if action == 'add':
            record = model(faction_id=int(data['faction_id']) if kind == 'rule' else int_or_none(data.get('faction_id')))
            fields(record, data)
            db.session.add(record)
            db.session.flush()
            return record, record.to_dict
        record = _record(edit)
        if action == 'change':
            if record.source == 'wahapedia':
                _wahapedia_reading(model, record, data)
            else:
                fields(record, data)
            return record, record.to_dict
        if record.source == 'wahapedia':
            raise EditError(WAHAPEDIA_READ_ONLY, 403)
        db.session.delete(record)
        return None, lambda: {'message': f'{kind.capitalize()} deleted'}

    if kind == 'unit':
        if action == 'add':
            if not editable_faction(data.get('faction_id')):
                raise EditError('Units can only be added to custom factions.', 403)
            unit = Unit(id=next_custom_id(Unit))
            _unit_fields(unit, data)
            db.session.add(unit)
            db.session.flush()
            _unit_loadout(unit, data)
            return unit, lambda: {'id': unit.id, 'message': 'Unit created'}
        unit = _record(edit)
        if action == 'change':
            if not unit.editable:  # A BSData unit only takes added modifiers and leader buffs, on every copy
                if set(data) - {'added_modifiers', 'leader_buffs'}:
                    raise EditError('BSData units are read-only apart from added modifiers and leader buffs.', 403)
                for same in Unit.query.filter_by(bsdata_id=unit.bsdata_id):
                    if 'added_modifiers' in data:
                        same.added_modifiers = text_list(data.get('added_modifiers'))
                    if 'leader_buffs' in data:
                        same.leader_buffs_override = None if data['leader_buffs'] is None else clean_buffs(data['leader_buffs'])
                return unit, lambda: {'id': unit.id, 'message': 'Unit updated'}
            if not editable_faction(data.get('faction_id', unit.faction_id)):
                raise EditError('Units can only be moved to custom factions.', 403)
            _unit_fields(unit, data)
            db.session.flush()
            _unit_loadout(unit, data)
            return unit, lambda: {'id': unit.id, 'message': 'Unit updated'}
        if not unit.editable:
            raise EditError('BSData records are read-only.', 403)
        unit.set_loadout('ranged', [])
        unit.set_loadout('melee', [])
        db.session.delete(unit)
        return None, lambda: {'message': 'Unit deleted'}

    if kind == 'weapon':
        weapon_type, model = edit.weapon_type, WEAPON_MODELS[edit.weapon_type]
        table = unit_ranged_weapons if weapon_type == 'ranged' else unit_melee_weapons
        if action == 'add':
            if not editable_faction(data.get('faction_id')):
                raise EditError('Weapons can only be added to custom factions.', 403)
            weapon = model(id=next_custom_id(model))
            _weapon_fields(weapon, weapon_type, data)
            db.session.add(weapon)
            return weapon, lambda: {'id': weapon.id, 'message': 'Weapon created'}
        weapon = _record(edit)
        if action == 'change':
            if not weapon.faction.editable or not editable_faction(data.get('faction_id', weapon.faction_id)):
                raise EditError('BSData records are read-only.', 403)
            _weapon_fields(weapon, weapon_type, data)
            db.session.flush()
            other_units = [u.id for u in Unit.query.filter(Unit.faction_id != weapon.faction_id)]
            if other_units:  # Moving it to another faction drops it from other factions' units
                db.session.execute(table.delete().where((table.c.weapon_id == weapon.id) & (table.c.unit_id.in_(other_units))))
            return weapon, lambda: {'id': weapon.id, 'message': 'Weapon updated'}
        if not weapon.faction.editable:
            raise EditError('BSData records are read-only.', 403)
        db.session.execute(table.delete().where(table.c.weapon_id == weapon.id))
        db.session.delete(weapon)
        return None, lambda: {'message': 'Weapon deleted'}

    raise EditError('That kind of change is unknown.')


def _attempt(edit):
    try:
        return _make(edit)
    except EditError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        detail = f"'{error.args[0]}' is missing" if isinstance(error, KeyError) and error.args else str(error)
        raise EditError(f'That change has a field missing or wrong: {detail}.') from error


# --- The three calls ------------------------------------------------------------------------

def apply(app, edit):
    """Make the edit and save the app data: the answer for the Data tab (EditError if it can't be made)."""
    try:
        _, answer = _attempt(edit)
    except Exception:
        db.session.rollback()
        raise
    reference.save_app_data(app)
    return answer()


@dataclass
class Preview:
    summary: str
    changes: list  # [{'field', 'now', 'proposed'}] by column
    before: dict  # the record before (None for a new one)


def preview(edit):
    """What the edit would do, read by making it and undoing it (EditError if it can't be made)."""
    before = snapshot(edit)
    try:
        with db.session.no_autoflush:
            record, _ = _attempt(edit)
        db.session.flush()
        after = _snapshot(record) if record is not None else None
        name = (edit.fields.get('name') or (before or {}).get('name') or (after or {}).get('name'))
        faction_id = (after or before or {}).get('faction_id')
    finally:
        db.session.rollback()
    keys = list((after or {}).keys()) or list((before or {}).keys())
    changes = [] if after is None else [
        {'field': key, 'now': (before or {}).get(key), 'proposed': after.get(key)}
        for key in keys if key != 'id' and (before or {}).get(key) != after.get(key)
        and not (before is None and after.get(key) in (None, '', [], {}))]
    return Preview(describe(edit, name, faction_id), changes, before)


def describe(edit, name=None, faction_id=None):
    """'Edit stratagem 'X' (Faction)'."""
    noun = NOUNS.get(edit.weapon_type if edit.kind == 'weapon' else edit.kind, edit.kind)
    faction = db.session.get(Faction, int(faction_id)) if edit.kind != 'faction' and str(faction_id or '').isdigit() else None
    text = f'{VERBS[edit.action]} {noun}' + (f" '{str(name)[:120]}'" if name else '') + (f' ({faction.name})' if faction else '')
    return text[:300]
