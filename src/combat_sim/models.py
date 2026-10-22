"""
Database models for 40K Combat Simulator.

Schema designed for Warhammer 40K 11th Edition rules.

Reference data (factions, units, weapons, rules, stratagems) is rebuilt on every start
from the BSData import and the app's own data JSON; see data_store.py. Saved scenarios,
comparisons and the BSData ID map live only in the database.

Factions with `source` 'bsdata' come from the BSData catalogues: their units and weapons
are read-only. 'custom' factions (and their units and weapons) are made in the app. BSData
records get IDs from BSDATA_ID_BASE up, kept stable across imports by StableId; custom
records keep their own, below it.

Faction rules and stratagems with `source` 'wahapedia' come from the Wahapedia export
(wahapedia.py), with their text and what was parsed from it; only their applies to / phase /
modifier can be edited. Hand-written ones ('app') that Wahapedia has too are `superseded`.
"""

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON
import math
from datetime import datetime

db = SQLAlchemy()

BSDATA_ID_BASE = 1_000_000  # First ID given to BSData records; custom records stay below it


class Faction(db.Model):
    """A Warhammer 40K faction (e.g., Death Guard, Space Marines)."""
    __tablename__ = 'factions'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    # 'custom' (made in the app), 'bsdata', or 'missing' (named by app data, not in the BSData folder)
    source = db.Column(db.String(10), nullable=False, default='custom')
    bsdata_id = db.Column(db.String(40), nullable=True)  # Catalogue ID
    army_rules = db.Column(JSON, default=list)  # BSData: [{"name", "text"}] (text only)

    # Relationships
    units = db.relationship('Unit', backref='faction', lazy='dynamic')
    faction_rules = db.relationship('FactionRule', backref='faction', lazy='dynamic')
    stratagems = db.relationship('Stratagem', backref='faction', lazy='dynamic')

    def __repr__(self):
        return f'<Faction {self.name}>'

    @property
    def editable(self):
        """Its units and weapons can be edited (rules and stratagems always can)."""
        return self.source == 'custom'


class Detachment(db.Model):
    """A BSData detachment of a faction, with its rule text. Read-only; rules and stratagems
    name it by `bsdata_id` (the same detachment can belong to several factions)."""
    __tablename__ = 'detachments'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=False)
    bsdata_id = db.Column(db.String(40), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    rules = db.Column(JSON, default=list)  # [{"name", "text"}]
    dp = db.Column(db.Integer, nullable=True)  # Detachment Points cost (None = not given)
    dispositions = db.Column(JSON, default=list)  # Force dispositions, e.g. ["Take and Hold"]
    tags = db.Column(JSON, default=list)  # Unique Tags: an army can't have two detachments sharing one

    faction = db.relationship('Faction')

    def to_dict(self):
        return {'id': self.id, 'key': self.bsdata_id, 'name': self.name, 'faction_id': self.faction_id,
                'faction': self.faction.name if self.faction else None, 'rules': self.rules or [],
                'dp': self.dp, 'dispositions': self.dispositions or [], 'tags': self.tags or []}


# A unit's loadout: which of its faction's weapons it carries, and how many models carry each.
unit_ranged_weapons = db.Table(
    'unit_ranged_weapons',
    db.Column('unit_id', db.Integer, db.ForeignKey('units.id'), primary_key=True),
    db.Column('weapon_id', db.Integer, db.ForeignKey('ranged_weapons.id'), primary_key=True),
    db.Column('qty', db.Integer, nullable=False, default=1),
    db.Column('position', db.Integer, nullable=False, default=0),  # Order on the datasheet
)

unit_melee_weapons = db.Table(
    'unit_melee_weapons',
    db.Column('unit_id', db.Integer, db.ForeignKey('units.id'), primary_key=True),
    db.Column('weapon_id', db.Integer, db.ForeignKey('melee_weapons.id'), primary_key=True),
    db.Column('qty', db.Integer, nullable=False, default=1),
    db.Column('position', db.Integer, nullable=False, default=0),  # Order on the datasheet
)


class Unit(db.Model):
    """A unit datasheet."""
    __tablename__ = 'units'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)

    # Defensive stats
    toughness = db.Column(db.Integer, nullable=False)
    save = db.Column(db.Integer, nullable=False)  # 2+ = 2, 3+ = 3, etc.
    wounds = db.Column(db.Integer, nullable=False)
    invulnerable_save = db.Column(db.Integer, nullable=True)  # null = no invuln
    feel_no_pain = db.Column(db.Integer, nullable=True)  # null = no FNP

    # Other datasheet stats
    move = db.Column(db.String(10), nullable=True)  # e.g. "5" or "20+"
    oc = db.Column(db.Integer, nullable=True)
    leadership = db.Column(db.Integer, nullable=True)  # 6+ = 6
    base = db.Column(JSON, nullable=True)  # BSData: bsdata.parse_base of the stat line's Base, e.g. {"shape": "round", ...}
    points_cost = db.Column(db.Integer, nullable=True)  # Points at the datasheet model count
    default_models = db.Column(db.Integer, nullable=True)  # Models in the unit per the datasheet
    # Cost of each legal unit size, e.g. [{"models": 5, "points": 90}, {"models": 10, "points": 170}].
    # The first entry is the datasheet size (points_cost / default_models).
    points_by_size = db.Column(JSON, default=list)

    keywords = db.Column(JSON, default=list)  # e.g. ["Infantry", "Chaos", "Nurgle"]
    modifiers = db.Column(JSON, default=list)  # Unit-level keywords used by the maths, e.g. ["Feel No Pain 5+"]
    # Modifiers added in the app to a read-only BSData unit (saved as the app data's unit_modifiers)
    added_modifiers = db.Column(JSON, default=list)
    can_lead = db.Column(JSON, default=list)  # Names of units this unit can lead
    can_support = db.Column(JSON, default=list)  # Names of units this unit can join as a Support unit
    max_in_army = db.Column(db.Integer, nullable=True)  # BSData's limit on copies in an army (None = not given)
    wargear = db.Column(JSON, nullable=True)  # BSData units: the option tree (bsdata.freeze; None = nothing to choose)
    # What this unit grants the unit it leads, as rows like faction rules:
    # [{"applies_to": "Attacker"|"Target", "phase": "Shooting"|"Fighting"|"Both", "modifier": "Lethal Hits"}]
    leader_buffs = db.Column(JSON, default=list)  # BSData: parsed from its Leader text; custom: edited
    leader_buffs_override = db.Column(JSON, nullable=True)  # BSData units: edited in the app (null = parsed)
    abilities = db.Column(db.Text, nullable=True)  # Free text from the datasheet

    bsdata_id = db.Column(db.String(40), nullable=True)  # Selection entry ID (BSData units only)
    legends = db.Column(db.Boolean, default=False)
    # Wargear options not in the default loadout: weapon IDs of the unit's faction
    ranged_options = db.Column(JSON, default=list)
    melee_options = db.Column(JSON, default=list)

    # Loadout (read through these; write qty via the link tables)
    ranged_weapons = db.relationship('RangedWeapon', secondary=unit_ranged_weapons, viewonly=True,
                                     backref='units', order_by='RangedWeapon.name')
    melee_weapons = db.relationship('MeleeWeapon', secondary=unit_melee_weapons, viewonly=True,
                                    backref='units', order_by='MeleeWeapon.name')

    def __repr__(self):
        return f'<Unit {self.name}>'

    def size_brackets(self):
        """Points brackets sorted by size (falls back to points_cost / default_models)."""
        brackets = [b for b in (self.points_by_size or []) if b.get('models') and b.get('points') is not None]
        if not brackets and self.points_cost is not None and self.default_models:
            brackets = [{'models': self.default_models, 'points': self.points_cost}]
        return sorted(brackets, key=lambda b: b['models'])

    def points_for(self, models):
        """
        Points for a unit of `models` models: the exact cost of a listed size; otherwise the nearest
        listed size below plus its per-model rate for the extra models (pro rata from the smallest
        size if none is below). None if the unit has no points.
        """
        brackets = self.size_brackets()
        if not brackets or not models:
            return None
        below = [b for b in brackets if b['models'] <= models]
        base = below[-1] if below else brackets[0]
        rate = base['points'] / base['models']
        if base['models'] == models:
            return base['points']
        points = base['points'] + (models - base['models']) * rate if below else models * rate
        return math.floor(points + 0.5)  # round half up, as Excel and the browser do

    @property
    def all_modifiers(self):
        """Datasheet modifiers plus those added in the app."""
        own = list(self.modifiers or [])
        return own + [m for m in (self.added_modifiers or []) if m not in own]

    @property
    def target_keywords(self):
        """Keywords checked by Anti-X abilities: datasheet keywords plus unit modifiers."""
        return list(self.keywords or []) + self.all_modifiers

    @property
    def editable(self):
        return self.faction.editable

    @property
    def effective_leader_buffs(self):
        """The buffs it grants when leading: edited in the app if so, else its own."""
        return list(self.leader_buffs_override if self.leader_buffs_override is not None else (self.leader_buffs or []))

    def leaders(self):
        """Units of the same faction that can lead this one (by name, as their Leader lists say)."""
        candidates = Unit.query.filter(Unit.faction_id == self.faction_id, Unit.id != self.id).order_by(Unit.name)
        return [u for u in candidates if self.name in (u.can_lead or [])]

    def loadout(self, weapon_type):
        """[(weapon, qty)] for this unit's ranged or melee weapons, in datasheet order."""
        table = unit_ranged_weapons if weapon_type == 'ranged' else unit_melee_weapons
        model = RangedWeapon if weapon_type == 'ranged' else MeleeWeapon
        rows = db.session.query(model, table.c.qty) \
            .join(table, table.c.weapon_id == model.id) \
            .filter(table.c.unit_id == self.id) \
            .order_by(table.c.position, model.name).all()
        return [(weapon, qty) for weapon, qty in rows]

    def set_loadout(self, weapon_type, entries):
        """Replace the loadout of one weapon type with [{'weapon_id': .., 'qty': ..}], kept in that order."""
        table = unit_ranged_weapons if weapon_type == 'ranged' else unit_melee_weapons
        db.session.execute(table.delete().where(table.c.unit_id == self.id))
        rows = {}
        for entry in entries or []:
            rows[int(entry['weapon_id'])] = max(1, int(entry.get('qty') or 1))
        if rows:
            db.session.execute(table.insert(), [
                {'unit_id': self.id, 'weapon_id': wid, 'qty': qty, 'position': i}
                for i, (wid, qty) in enumerate(rows.items())
            ])

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'faction': self.faction.name,
            'faction_id': self.faction_id,
            'toughness': self.toughness,
            'save': self.save,
            'wounds': self.wounds,
            'invulnerable_save': self.invulnerable_save,
            'feel_no_pain': self.feel_no_pain,
            'move': self.move,
            'oc': self.oc,
            'leadership': self.leadership,
            'base': self.base,
            'points_cost': self.points_cost,
            'default_models': self.default_models,
            'points_by_size': self.size_brackets(),
            'keywords': self.keywords or [],
            'modifiers': self.all_modifiers,
            'datasheet_modifiers': self.modifiers or [],
            'added_modifiers': self.added_modifiers or [],
            'can_lead': self.can_lead or [],
            'can_support': self.can_support or [],
            'max_in_army': self.max_in_army,
            'has_wargear': self.wargear is not None,
            'leader_buffs': self.effective_leader_buffs,
            'datasheet_leader_buffs': self.leader_buffs or [],
            'leader_buffs_edited': self.leader_buffs_override is not None,
            'abilities': self.abilities or '',
            'source': self.faction.source,
            'editable': self.editable,
            'bsdata_id': self.bsdata_id,
            'legends': bool(self.legends),
        }


class RangedWeapon(db.Model):
    """A ranged weapon profile, owned by a faction and shared by its units."""
    __tablename__ = 'ranged_weapons'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)

    # Weapon stats
    attacks = db.Column(db.String(10), nullable=False)  # Can be "2", "D6", "D3+1", etc.
    ballistic_skill = db.Column(db.Integer, nullable=True)  # 3+ = 3; null = no roll ("-", e.g. Torrent)
    strength = db.Column(db.Integer, nullable=False)
    armor_penetration = db.Column(db.Integer, nullable=False)  # Stored as positive: AP-2 = 2
    damage = db.Column(db.String(10), nullable=False)  # Can be "1", "D6", "D3+3", etc.

    # Range in inches, for reference only (never used in the maths)
    range_inches = db.Column(db.Integer, nullable=True)

    # Abilities stored as JSON array of strings
    abilities = db.Column(JSON, default=list)
    bsdata_id = db.Column(db.String(40), nullable=True)  # Profile ID (BSData weapons only)
    # Custom weapons copied from BSData: the profile ID their stats are refreshed from on every start
    linked_bsdata_id = db.Column(db.String(40), nullable=True)

    faction = db.relationship('Faction')

    def __repr__(self):
        return f'<RangedWeapon {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': 'ranged',
            'faction_id': self.faction_id,
            'attacks': self.attacks,
            'skill': self.ballistic_skill,
            'strength': self.strength,
            'ap': self.armor_penetration,
            'damage': self.damage,
            'range': self.range_inches,
            'abilities': self.abilities or [],
            'editable': self.faction.editable if self.faction else True,
            'bsdata_id': self.bsdata_id,
            'linked_bsdata_id': self.linked_bsdata_id,
        }


class MeleeWeapon(db.Model):
    """A melee weapon profile, owned by a faction and shared by its units."""
    __tablename__ = 'melee_weapons'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)

    # Weapon stats
    attacks = db.Column(db.String(10), nullable=False)
    weapon_skill = db.Column(db.Integer, nullable=True)  # 3+ = 3; null = no roll
    strength = db.Column(db.Integer, nullable=False)
    armor_penetration = db.Column(db.Integer, nullable=False)
    damage = db.Column(db.String(10), nullable=False)

    # Abilities stored as JSON array of strings
    abilities = db.Column(JSON, default=list)
    bsdata_id = db.Column(db.String(40), nullable=True)  # Profile ID (BSData weapons only)
    # Custom weapons copied from BSData: the profile ID their stats are refreshed from on every start
    linked_bsdata_id = db.Column(db.String(40), nullable=True)

    faction = db.relationship('Faction')

    def __repr__(self):
        return f'<MeleeWeapon {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': 'melee',
            'faction_id': self.faction_id,
            'attacks': self.attacks,
            'skill': self.weapon_skill,
            'strength': self.strength,
            'ap': self.armor_penetration,
            'damage': self.damage,
            'range': None,
            'abilities': self.abilities or [],
            'editable': self.faction.editable if self.faction else True,
            'bsdata_id': self.bsdata_id,
            'linked_bsdata_id': self.linked_bsdata_id,
        }


class FactionRule(db.Model):
    """A faction rule: army-wide or for one detachment. Ticked on the Simulator and Analytics pages."""
    __tablename__ = 'faction_rules'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    applies_to = db.Column(db.String(20), nullable=True)  # "Attacker" or "Target"
    phase = db.Column(db.String(20), nullable=True)  # "Shooting", "Fighting" or "Both"
    modifier = db.Column(db.Text, nullable=True)  # Keyword-format modifier text
    # Only with this BSData detachment (its bsdata_id; null = the whole faction)
    detachment_key = db.Column(db.String(40), nullable=True)
    detachment_name = db.Column(db.String(100), nullable=True)
    # 'app' (written in the app) or 'wahapedia' (imported; see the module docstring)
    source = db.Column(db.String(10), nullable=False, default='app')
    wahapedia_id = db.Column(db.String(20), nullable=True)
    text = db.Column(db.Text, nullable=True)  # Wahapedia: the rule's text, with BSData-style markup
    # Wahapedia: {'status': 'modelled'|'partial'|'not_modelled', 'skipped', 'conditions', 'parsed': {applies_to, phase, modifier}}
    parse = db.Column(JSON, nullable=True)
    edited = db.Column(db.Boolean, default=False)  # Wahapedia: applies to / phase / modifier changed in the app
    superseded = db.Column(db.Boolean, default=False)  # App: replaced by Wahapedia's (hidden, but kept in the data JSON)

    def __repr__(self):
        return f'<FactionRule {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'faction_id': self.faction_id,
            'name': self.name,
            'applies_to': self.applies_to,
            'phase': self.phase,
            'modifier': self.modifier,
            'detachment_key': self.detachment_key,
            'detachment_name': self.detachment_name,
            'source': self.source,
            'text': self.text,
            'parse': self.parse,
            'edited': bool(self.edited),
        }


class Stratagem(db.Model):
    """A stratagem: core (no faction) or faction-specific. Ticked on the Simulator and Analytics pages."""
    __tablename__ = 'stratagems'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=True)  # null = core stratagem
    name = db.Column(db.String(100), nullable=False)
    cp = db.Column(db.String(10), nullable=True)
    applies_to = db.Column(db.String(20), nullable=True)
    phase = db.Column(db.String(20), nullable=True)
    modifier = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    detachment_key = db.Column(db.String(40), nullable=True)  # As for FactionRule
    detachment_name = db.Column(db.String(100), nullable=True)
    source = db.Column(db.String(10), nullable=False, default='app')  # As for FactionRule, and the columns below
    wahapedia_id = db.Column(db.String(20), nullable=True)
    text = db.Column(db.Text, nullable=True)
    parse = db.Column(JSON, nullable=True)
    edited = db.Column(db.Boolean, default=False)
    superseded = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f'<Stratagem {self.name}>'

    @property
    def is_core(self):
        return self.faction_id is None

    def to_dict(self):
        return {
            'id': self.id,
            'faction_id': self.faction_id,
            'name': self.name,
            'cp': self.cp,
            'applies_to': self.applies_to,
            'phase': self.phase,
            'modifier': self.modifier,
            'notes': self.notes,
            'detachment_key': self.detachment_key,
            'detachment_name': self.detachment_name,
            'source': self.source,
            'text': self.text,
            'parse': self.parse,
            'edited': bool(self.edited),
        }


class Keyword(db.Model):
    """A rules keyword or modifier from the Keywords reference (read-only in the app)."""
    __tablename__ = 'keywords'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    category = db.Column(db.String(20), nullable=True)  # Weapon, Core (unit) or Stat (modifier)
    applies_to = db.Column(db.String(20), nullable=True)  # Ranged, Melee, Both or Unit
    format = db.Column(db.String(100), nullable=True)  # How to write it, e.g. "Sustained Hits X"
    summary = db.Column(db.Text, nullable=True)
    sim_step = db.Column(db.String(20), nullable=True)  # Attacks, Hit, Wound, Save, Damage or None
    modelled = db.Column(db.Boolean, default=False)  # Used in the calculations
    rule_ref = db.Column(db.String(20), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category': self.category,
            'applies_to': self.applies_to,
            'format': self.format,
            'summary': self.summary,
            'sim_step': self.sim_step,
            'modelled': bool(self.modelled),
            'rule_ref': self.rule_ref,
        }


class TargetProfile(db.Model):
    """
    A read-only benchmark target: a generic opponent profile (MEQ, TEQ...) for Analytics'
    Generic mode, or a role-score benchmark (Anti-Horde, Anti-Tank...).
    """
    __tablename__ = 'target_profiles'

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)  # "generic" or "role"
    name = db.Column(db.String(60), nullable=False)
    models = db.Column(db.Integer, nullable=False, default=1)
    toughness = db.Column(db.Integer, nullable=False)
    wounds = db.Column(db.Integer, nullable=False)
    save = db.Column(db.Integer, nullable=False)
    invulnerable_save = db.Column(db.Integer, nullable=True)
    feel_no_pain = db.Column(db.Integer, nullable=True)
    keywords = db.Column(JSON, default=list)
    weight = db.Column(db.Float, nullable=True)  # Generic profiles: weight in the averages
    use = db.Column(db.Boolean, default=True)  # Generic profiles: included by default

    # Lets the matrix treat a profile like a unit; faction 0 matches no faction's rules (core only)
    faction_id = 0
    points_cost = None

    @property
    def target_keywords(self):
        return list(self.keywords or [])

    def points_for(self, models):
        return None

    def to_dict(self):
        return {
            'id': self.id,
            'kind': self.kind,
            'name': self.name,
            'models': self.models,
            'toughness': self.toughness,
            'wounds': self.wounds,
            'save': self.save,
            'invulnerable_save': self.invulnerable_save,
            'feel_no_pain': self.feel_no_pain,
            'keywords': self.keywords or [],
            'weight': self.weight,
            'use': bool(self.use),
        }


# Tables rebuilt from the data JSON on every start (everything except saved scenarios/comparisons)
class Enhancement(db.Model):
    """
    A BSData enhancement of a faction (read-only; see bsdata.enhancements): its points, text, the
    effects the maths models (`buffs`, each for the bearer or its unit) and the conditions BSData
    hides it under (`hidden_when`, evaluated by armies.enhancement_hidden). One defined in a unit's
    own entry (`unit_bsdata_id`) is an Upgrade for that unit.
    """
    __tablename__ = 'enhancements'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=False)
    bsdata_id = db.Column(db.String(40), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    points = db.Column(db.Integer, nullable=False, default=0)
    text = db.Column(db.Text, nullable=True)
    buffs = db.Column(JSON, default=list)  # [{"applies_to", "phase", "modifier", "scope": "bearer"|"unit"}]
    detachment_keys = db.Column(JSON, default=list)
    hidden_when = db.Column(JSON, default=list)
    unit_bsdata_id = db.Column(db.String(40), nullable=True)

    faction = db.relationship('Faction')

    def to_dict(self):
        return {'id': self.id, 'key': self.bsdata_id, 'name': self.name, 'faction_id': self.faction_id,
                'points': self.points, 'text': self.text or '', 'buffs': self.buffs or [],
                'detachment_keys': self.detachment_keys or [], 'upgrade': bool(self.unit_bsdata_id)}


REFERENCE_TABLES = [
    unit_ranged_weapons, unit_melee_weapons,
    RangedWeapon.__table__, MeleeWeapon.__table__,
    FactionRule.__table__, Stratagem.__table__, Enhancement.__table__,
    Unit.__table__, Detachment.__table__, Faction.__table__, Keyword.__table__, TargetProfile.__table__,
]


class StableId(db.Model):
    """
    The database ID given to each BSData record, by key, so IDs (and so saved scenarios and
    comparisons) survive re-imports. Keys: 'faction:<catalogue>', 'unit:<catalogue>/<entry>',
    'ranged:<catalogue>/<profile>', 'melee:<catalogue>/<profile>'. Not a reference table.
    """
    __tablename__ = 'stable_ids'

    key = db.Column(db.String(100), primary_key=True)
    table = db.Column(db.String(20), nullable=False)
    id = db.Column(db.Integer, nullable=False)


class SavedScenario(db.Model):
    """A saved simulation scenario for comparison."""
    __tablename__ = 'saved_scenarios'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)

    # Store the full scenario configuration as JSON
    attacker_config = db.Column(JSON, nullable=False)
    defender_config = db.Column(JSON, nullable=False)
    battle_conditions = db.Column(JSON, nullable=False)

    # Cached results
    results = db.Column(JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SavedScenario {self.name}>'


class SavedArmy(db.Model):
    """An army list built on the Armies page (see armies.py). Kept in the database only, like scenarios."""
    __tablename__ = 'saved_armies'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    faction_id = db.Column(db.Integer, nullable=False)  # Stable across BSData imports (stable_ids)
    detachment_key = db.Column(db.String(40), nullable=True)  # First saved form: one detachment (read only)
    detachment_keys = db.Column(JSON, nullable=True)  # BSData detachment IDs
    points_limit = db.Column(db.Integer, nullable=True)  # None = no limit
    warlord = db.Column(db.String(40), nullable=True)  # The uid of the Warlord's entry
    disposition = db.Column(db.String(40), nullable=True)  # Its force disposition (None: not chosen yet)
    # [{"uid", "unit_id", "models", "attached_to" (uid of the unit it joins, or null), "enhancement" (BSData ID)}]
    units = db.Column(JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        keys = self.detachment_keys if self.detachment_keys is not None else ([self.detachment_key] if self.detachment_key else [])
        return {'id': self.id, 'name': self.name, 'faction_id': self.faction_id, 'detachment_keys': keys,
                'points_limit': self.points_limit, 'warlord': self.warlord, 'disposition': self.disposition,
                'units': self.units or [],
                'updated_at': (self.updated_at or self.created_at).isoformat() if (self.updated_at or self.created_at) else None}


class SavedGame(db.Model):
    """A Play mode game: its frozen armies and its event log (the state is their replay; see play/game.py).
    Kept in the database only, like armies."""
    __tablename__ = 'saved_games'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    armies = db.Column(JSON, nullable=False)  # [snapshot.freeze_army(...), ...], one per player
    events = db.Column(JSON, nullable=False, default=list)
    battlefield = db.Column(JSON, nullable=True)  # The game's own copy of its battlefield (None: the default map)
    setup = db.Column(db.Boolean, nullable=True)  # Starts with the mission sequence (None: saved before it existed)
    twist = db.Column(JSON, nullable=True)  # The Twist card the players agreed on: {'key', 'primary'} (None: none)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SavedBattlefield(db.Model):
    """A battlefield saved in the Play page's editor (play/battlefields.py's format). Database only."""
    __tablename__ = 'saved_battlefields'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    data = db.Column(JSON, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SavedComparison(db.Model):
    """A saved matrix comparison for army analysis."""
    __tablename__ = 'saved_comparisons'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)

    # Store attacker and defender configurations as JSON
    # Each is a list of {unit_id, weapon_ids[], model_count (for defenders)}
    attackers = db.Column(JSON, nullable=False)
    defenders = db.Column(JSON, nullable=False)

    # Battle conditions
    battle_conditions = db.Column(JSON, nullable=False)

    # Cached results matrix
    results = db.Column(JSON, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<SavedComparison {self.name}>'


def init_db(app):
    """Initialize the database with the Flask app."""
    db.init_app(app)
    with app.app_context():
        db.create_all()
        add_missing_columns()


def add_missing_columns():
    """
    Add new nullable columns of the persistent tables to a database made by an older version
    (create_all only makes missing tables). Anything else still needs a manual ALTER TABLE.
    """
    inspector = db.inspect(db.engine)
    for model in (SavedScenario, SavedComparison, SavedArmy, SavedGame, SavedBattlefield, StableId):
        table = model.__table__
        if not inspector.has_table(table.name):
            continue
        have = {c['name'] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in have and column.nullable:
                kind = column.type.compile(db.engine.dialect)
                db.session.execute(db.text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {kind}'))
    db.session.commit()
