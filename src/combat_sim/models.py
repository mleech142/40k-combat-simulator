"""
Database models for 40K Combat Simulator.

Schema designed for Warhammer 40K 11th Edition rules.
"""

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON
from datetime import datetime

db = SQLAlchemy()


class Faction(db.Model):
    """A Warhammer 40K faction (e.g., Death Guard, Space Marines)."""
    __tablename__ = 'factions'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    units = db.relationship('Unit', backref='faction', lazy='dynamic')
    faction_rules = db.relationship('FactionRule', backref='faction', lazy='dynamic')
    stratagems = db.relationship('Stratagem', backref='faction', lazy='dynamic')

    def __repr__(self):
        return f'<Faction {self.name}>'


class Unit(db.Model):
    """A unit profile with defensive stats."""
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

    # Keywords stored as JSON array
    keywords = db.Column(JSON, default=list)

    # Points cost (optional, for efficiency calculations)
    points_cost = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    ranged_weapons = db.relationship('RangedWeapon', backref='unit', lazy='dynamic')
    melee_weapons = db.relationship('MeleeWeapon', backref='unit', lazy='dynamic')

    def __repr__(self):
        return f'<Unit {self.name}>'

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
            'keywords': self.keywords or [],
            'points_cost': self.points_cost,
        }


class RangedWeapon(db.Model):
    """A ranged weapon profile."""
    __tablename__ = 'ranged_weapons'

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey('units.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)

    # Weapon stats
    attacks = db.Column(db.String(10), nullable=False)  # Can be "2", "D6", "D3+1", etc.
    ballistic_skill = db.Column(db.Integer, nullable=False)  # 3+ = 3, 4+ = 4, etc.
    strength = db.Column(db.Integer, nullable=False)
    armor_penetration = db.Column(db.Integer, nullable=False)  # Stored as positive: AP-2 = 2
    damage = db.Column(db.String(10), nullable=False)  # Can be "1", "D6", "D3+3", etc.

    # Range in inches (0 = melee)
    range_inches = db.Column(db.Integer, default=24)

    # Abilities stored as JSON array of strings
    abilities = db.Column(JSON, default=list)

    # Points cost for this weapon upgrade (optional)
    points_cost = db.Column(db.Integer, nullable=True, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<RangedWeapon {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': 'ranged',
            'unit_id': self.unit_id,
            'unit_name': self.unit.name if self.unit else None,
            'attacks': self.attacks,
            'skill': self.ballistic_skill,
            'strength': self.strength,
            'ap': self.armor_penetration,
            'damage': self.damage,
            'range': self.range_inches,
            'abilities': self.abilities or [],
            'points_cost': self.points_cost or 0,
        }


class MeleeWeapon(db.Model):
    """A melee weapon profile."""
    __tablename__ = 'melee_weapons'

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey('units.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)

    # Weapon stats
    attacks = db.Column(db.String(10), nullable=False)
    weapon_skill = db.Column(db.Integer, nullable=False)  # 3+ = 3, 4+ = 4, etc.
    strength = db.Column(db.Integer, nullable=False)
    armor_penetration = db.Column(db.Integer, nullable=False)
    damage = db.Column(db.String(10), nullable=False)

    # Abilities stored as JSON array of strings
    abilities = db.Column(JSON, default=list)

    # Points cost for this weapon upgrade (optional)
    points_cost = db.Column(db.Integer, nullable=True, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<MeleeWeapon {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': 'melee',
            'unit_id': self.unit_id,
            'unit_name': self.unit.name if self.unit else None,
            'attacks': self.attacks,
            'skill': self.weapon_skill,
            'strength': self.strength,
            'ap': self.armor_penetration,
            'damage': self.damage,
            'abilities': self.abilities or [],
            'points_cost': self.points_cost or 0,
        }


class FactionRule(db.Model):
    """A faction-specific rule that modifies combat."""
    __tablename__ = 'faction_rules'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Effect stored as JSON for flexibility
    # e.g., {"reroll_wounds": "ones", "bonus_ap": 1, "condition": "half_range"}
    effect = db.Column(JSON, default=dict)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<FactionRule {self.name}>'


class Stratagem(db.Model):
    """A stratagem that can be activated during combat."""
    __tablename__ = 'stratagems'

    id = db.Column(db.Integer, primary_key=True)
    faction_id = db.Column(db.Integer, db.ForeignKey('factions.id'), nullable=True)  # null = core stratagem
    name = db.Column(db.String(100), nullable=False)
    cost_cp = db.Column(db.Integer, default=1)
    description = db.Column(db.Text, nullable=True)

    # When it can be used: "shooting", "fight", "both"
    phase = db.Column(db.String(20), default='both')

    # Who it affects: "attacker", "defender", "both"
    affects = db.Column(db.String(20), default='attacker')

    # Effect stored as JSON
    effect = db.Column(JSON, default=dict)

    is_core = db.Column(db.Boolean, default=False)  # Core stratagems available to all

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<Stratagem {self.name}>'


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
