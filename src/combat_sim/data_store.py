"""
The data JSON: the distributed source of factions, units, weapons, rules and stratagems.

On every start the reference tables are rebuilt from the file; every confirmed edit in
the app is written straight back to it. The file is produced initially by
scripts/migrate_excel.py.

Format (schema_version 1):
{
  "schema_version": 1,
  "source": {"workbook": "...", "generated": "..."},
  "factions": [{"id", "name"}],
  "units": [{"id", "faction_id", "name", "toughness", ..., "abilities",
             "ranged_weapons": [{"weapon_id", "qty"}], "melee_weapons": [{"weapon_id", "qty"}]}],
  "ranged_weapons": [{"id", "faction_id", "name", "attacks", "ballistic_skill", ...}],
  "melee_weapons": [{"id", "faction_id", "name", "attacks", "weapon_skill", ...}],
  "faction_rules": [{"id", "faction_id", "name", "applies_to", "phase", "modifier"}],
  "stratagems": [{"id", "faction_id" (null = core), "name", "cp", "applies_to", "phase", "modifier", "notes"}],
  "keywords": [{"id", "name", "category", "applies_to", "format", "summary", "sim_step", "modelled", "rule_ref"}],
  "target_profiles": [{"id", "kind" ("generic"/"role"), "name", "models", "toughness", "wounds", "save",
                       "invulnerable_save", "feel_no_pain", "keywords", "weight", "use"}]
}
Units also carry "points_by_size": [{"models", "points"}] (first entry = the datasheet size).
Record keys are the database column names.
"""

import json
import os
from pathlib import Path

from .models import (db, Faction, Unit, RangedWeapon, MeleeWeapon, FactionRule, Stratagem, Keyword, TargetProfile,
                     unit_ranged_weapons, unit_melee_weapons, REFERENCE_TABLES)

SCHEMA_VERSION = 1

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / '40k_data.json'

# JSON section -> model, in insert order
SECTIONS = [
    ('factions', Faction),
    ('units', Unit),
    ('ranged_weapons', RangedWeapon),
    ('melee_weapons', MeleeWeapon),
    ('faction_rules', FactionRule),
    ('stratagems', Stratagem),
    ('keywords', Keyword),  # Read-only reference (from the Keywords tab of the workbook)
    ('target_profiles', TargetProfile),  # Read-only generic opponents and role benchmarks
]

LOADOUTS = [('ranged_weapons', unit_ranged_weapons), ('melee_weapons', unit_melee_weapons)]


class DataFileError(Exception):
    """The data JSON is missing required structure or is from a newer version of the app."""


def empty_data():
    return {'schema_version': SCHEMA_VERSION, 'source': None, **{name: [] for name, _ in SECTIONS}}


def read_data_file(path):
    """Read and check the data JSON. A missing file gives empty data."""
    path = Path(path)
    if not path.exists():
        return empty_data()

    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    check_data(data)
    return data


def check_data(data):
    if not isinstance(data, dict):
        raise DataFileError('Data JSON must be an object')
    version = data.get('schema_version')
    if not isinstance(version, int):
        raise DataFileError('Data JSON has no schema_version')
    if version > SCHEMA_VERSION:
        raise DataFileError(f'Data JSON is schema_version {version}; this app supports up to {SCHEMA_VERSION}')
    for name, _ in SECTIONS:
        if not isinstance(data.get(name, []), list):
            raise DataFileError(f'"{name}" must be a list')


def rebuild_reference_tables(data):
    """Drop and recreate the reference tables, then fill them from `data`."""
    db.metadata.drop_all(bind=db.engine, tables=REFERENCE_TABLES)
    db.metadata.create_all(bind=db.engine, tables=REFERENCE_TABLES)

    for name, model in SECTIONS:
        # Every row needs every column for the batch insert; missing optional fields get the column default
        defaults = {
            c.name: c.default.arg if c.default is not None and c.default.is_scalar else None
            for c in model.__table__.columns
        }
        rows = [{col: record.get(col, default) for col, default in defaults.items()}
                for record in data.get(name, [])]
        if rows:
            db.session.execute(model.__table__.insert(), rows)

    for key, table in LOADOUTS:
        rows = [
            {'unit_id': unit['id'], 'weapon_id': entry['weapon_id'], 'qty': entry.get('qty') or 1}
            for unit in data.get('units', [])
            for entry in unit.get(key, [])
        ]
        if rows:
            db.session.execute(table.insert(), rows)

    db.session.commit()


def export_data(source=None):
    """Current reference tables as a data JSON dict, in a stable order."""
    data = {'schema_version': SCHEMA_VERSION, 'source': source}

    for name, model in SECTIONS:
        columns = [c.name for c in model.__table__.columns]
        records = []
        for obj in model.query.order_by(model.id).all():
            record = {col: getattr(obj, col) for col in columns}
            if model is Unit:
                for key, table in LOADOUTS:
                    rows = db.session.execute(
                        table.select().where(table.c.unit_id == obj.id).order_by(table.c.weapon_id)
                    ).all()
                    record[key] = [{'weapon_id': r.weapon_id, 'qty': r.qty} for r in rows]
            records.append(record)
        data[name] = records

    return data


def write_data_file(path, data):
    """Write the data JSON atomically (temp file then rename), pretty-printed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, path)
