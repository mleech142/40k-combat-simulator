"""
The app's data: BSData catalogues (read-only) plus the app's own data JSON.

On every start the reference tables are rebuilt from both. The BSData folder is imported by
bsdata.py, cached in a JSON file until the folder's contents change. The app data JSON holds
everything the app owns, and every confirmed edit in the app is written straight back to it:

{
  "schema_version": 2,
  "source": {...},
  "factions": [{"id", "name"}],                           # Custom factions only
  "units": [{"id", "faction_id", "name", "toughness", ..., "abilities",
             "ranged_weapons": [{"weapon_id", "qty"}], "melee_weapons": [{"weapon_id", "qty"}]}],
  "ranged_weapons": [{"id", "faction_id", "name", "attacks", "ballistic_skill", ...}],
  "melee_weapons": [{"id", "faction_id", "name", "attacks", "weapon_skill", ...}],
  "faction_rules": [{"id", "faction_id" | "faction_key" + "faction_name", "name", "applies_to", "phase", "modifier",
                     "detachment_key", "detachment_name"}],
  "stratagems": [{"id", "faction_id" | "faction_key" + "faction_name" (neither = core), "name", "cp", ...,
                  "detachment_key", "detachment_name"}],
  "unit_modifiers": [{"bsdata_id", "name", "modifiers"}],  # Added to BSData units, by datasheet
  "leader_buffs": [{"bsdata_id", "name", "buffs": [{"applies_to", "phase", "modifier"}]}],  # Edited, by datasheet
  "wahapedia_overrides": [{"kind" ("stratagem"/"rule"), "wahapedia_id", "name", "applies_to", "phase", "modifier"}],
  "keywords": [{"id", "name", "category", "applies_to", "format", "summary", "sim_step", "modelled", "rule_ref"}],
  "target_profiles": [{"id", "kind" ("generic"/"role"), "name", "models", "toughness", "wounds", "save",
                       "invulnerable_save", "feel_no_pain", "keywords", "weight", "use"}]
}

Custom units also carry "points_by_size": [{"models", "points"}] (first entry = the datasheet
size). A custom weapon with "linked_bsdata_id" takes its stats from that BSData weapon profile
on every start. A rule or stratagem with a "detachment_key" (a BSData detachment's ID) is
offered only with that detachment. Detachments themselves come from BSData only. Record keys are the database column names. Rules and stratagems of a BSData faction
name it by catalogue ID (`faction_key`), since database IDs differ between installs; one whose
faction isn't in the BSData folder keeps a placeholder faction so it can still be edited.
Schema version 1 files (all-custom data) load unchanged.

Stratagems and rules imported from Wahapedia (wahapedia.py) are added the same way as BSData
records, with stable IDs, and are never saved in the data JSON: only edits to how the maths
reads them are ("wahapedia_overrides", by Wahapedia ID). With Wahapedia loaded, the data
JSON's rules and stratagems that Wahapedia has too (same faction, or core, and name) are
superseded: hidden on the pages, but kept in the file.
"""

import hashlib
import json
import os
from pathlib import Path

from . import bsdata, wahapedia as wahapedia_import
from .models import (db, Faction, Detachment, Enhancement, Unit, RangedWeapon, MeleeWeapon, FactionRule, Stratagem, Keyword,
                     TargetProfile,
                     StableId, BSDATA_ID_BASE, unit_ranged_weapons, unit_melee_weapons, REFERENCE_TABLES)

SCHEMA_VERSION = 2

# JSON section -> model, in insert order
SECTIONS = [
    ('factions', Faction),
    ('detachments', Detachment),  # BSData only; never saved in the data JSON
    ('enhancements', Enhancement),  # BSData only; never saved in the data JSON
    ('units', Unit),
    ('ranged_weapons', RangedWeapon),
    ('melee_weapons', MeleeWeapon),
    ('faction_rules', FactionRule),
    ('stratagems', Stratagem),
    ('keywords', Keyword),  # Read-only reference
    ('target_profiles', TargetProfile),  # Read-only generic opponents and role benchmarks
]
OWN_SECTIONS = ['unit_modifiers', 'leader_buffs', 'wahapedia_overrides']  # Saved in the data JSON, not a table of their own

LOADOUTS = [('ranged_weapons', unit_ranged_weapons), ('melee_weapons', unit_melee_weapons)]
WEAPON_SECTIONS = {'ranged': 'ranged_weapons', 'melee': 'melee_weapons'}


class DataFileError(Exception):
    """The data JSON is missing required structure or is from a newer version of the app."""


def empty_data():
    return {'schema_version': SCHEMA_VERSION, 'source': None,
            **{name: [] for name, _ in SECTIONS}, **{name: [] for name in OWN_SECTIONS}}


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
    for name in [n for n, _ in SECTIONS] + OWN_SECTIONS:
        if not isinstance(data.get(name, []), list):
            raise DataFileError(f'"{name}" must be a list')


# ---------------------------------------------------------------------------
# BSData: import (cached) and merge with the app data
# ---------------------------------------------------------------------------

def load_bsdata(folder, cache_path, known_keywords=None):
    """
    Import the BSData folder, or reuse the cached import if the folder's files haven't changed.

    Returns (ImportResult or None, status). status: {'folder', 'loaded', 'error', 'from_cache',
    'source', 'report'}. A missing or empty folder isn't an error: the app runs on its own data.
    """
    folder = Path(folder) if folder else None
    status = {'folder': str(folder) if folder else None, 'loaded': False, 'error': None, 'from_cache': False,
              'source': None, 'report': []}
    files = sorted(folder.glob('*.json')) if folder and folder.is_dir() else []
    if not files:
        return None, status

    digest = hashlib.sha1(f'{bsdata.IMPORTER_VERSION}|{sorted(known_keywords or [])}'.encode())
    for path in files:
        digest.update(path.name.encode('utf-8'))
        digest.update(path.read_bytes())
    fingerprint = digest.hexdigest()

    cache_path = Path(cache_path) if cache_path else None
    result = None
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            if cached.get('fingerprint') == fingerprint:
                result = bsdata.ImportResult(**cached['result'])
                status['from_cache'] = True
        except (OSError, ValueError, TypeError, KeyError):
            result = None
    if result is None:
        try:
            result = bsdata.import_folder(folder, known_keywords=known_keywords)
        except bsdata.BSDataError as err:
            status['error'] = str(err)
            return None, status
        if cache_path:
            write_json(cache_path, {'fingerprint': fingerprint, 'result': {
                'data': result.data, 'report': result.report, 'source': result.source}}, indent=None)

    status.update(loaded=True, source=result.source, report=result.report)
    return result, status


def stable_ids(keys):
    """Database IDs for (key, table) pairs: the ones given before, new ones from BSDATA_ID_BASE up."""
    known = {key: id_ for key, id_ in db.session.query(StableId.key, StableId.id)}
    next_ids = {}
    new_rows = []
    for key, table in keys:
        if key in known:
            continue
        if table not in next_ids:
            top = db.session.query(db.func.max(StableId.id)).filter(StableId.table == table).scalar()
            next_ids[table] = max(top or 0, BSDATA_ID_BASE - 1) + 1
        known[key] = next_ids[table]
        next_ids[table] += 1
        new_rows.append({'key': key, 'table': table, 'id': known[key]})
    if new_rows:
        db.session.execute(StableId.__table__.insert(), new_rows)
        db.session.commit()  # Before the reference tables are dropped on another connection
    return known


def merge_data(app_data, imported, wahapedia=None, report=None):
    """
    The reference data to load: the app data plus the imported BSData records (with stable
    database IDs), rules and stratagems linked to their factions, unit modifiers applied, and
    Wahapedia's stratagems and rules (a WahapediaResult) with the app data's edits to them.
    Returns (data, {section: unmatched entries}) for the unit_modifiers, leader_buffs and
    wahapedia_overrides entries that match nothing loaded, to keep in the data JSON. Notes on
    matching Wahapedia to BSData are added to the list `report`.
    """
    data = {name: [dict(r) for r in app_data.get(name, [])] for name, _ in SECTIONS}
    data['schema_version'], data['source'] = SCHEMA_VERSION, app_data.get('source')
    for faction in data['factions']:
        faction['source'] = 'custom'

    data['detachments'] = []  # Only BSData has detachments and enhancements
    data['enhancements'] = []
    by_key = {}  # faction_key -> faction record
    if imported is not None:
        profiles = _add_bsdata(data, imported.data, by_key)
        _refresh_linked_weapons(data, profiles)

    # Rules and stratagems saved against a BSData faction: link it, or keep a placeholder
    taken = {f['name'] for f in data['factions']}
    missing = []
    for section in ('faction_rules', 'stratagems'):
        for record in data[section]:
            key = record.pop('faction_key', None)
            name = record.pop('faction_name', None)
            if not key:
                continue
            if key not in by_key:
                placeholder = {'name': unique_name(name or key, taken, 'not in BSData'),
                               'source': 'missing', 'bsdata_id': key}
                taken.add(placeholder['name'])
                by_key[key] = placeholder
                missing.append(placeholder)
            record['_faction'] = by_key[key]
    if missing:
        ids = stable_ids([(f'faction:{f["bsdata_id"]}', 'factions') for f in missing])
        for f in missing:
            f['id'] = ids[f'faction:{f["bsdata_id"]}']
        data['factions'].extend(missing)
    for section in ('faction_rules', 'stratagems'):
        for record in data[section]:
            if '_faction' in record:
                record['faction_id'] = record.pop('_faction')['id']

    # Modifiers added to BSData units, by datasheet (every faction that has it)
    units_by_entry = {}
    for unit in data['units']:
        if unit.get('bsdata_id'):
            units_by_entry.setdefault(unit['bsdata_id'], []).append(unit)
    unmatched = {'unit_modifiers': [], 'leader_buffs': [], 'wahapedia_overrides': list(app_data.get('wahapedia_overrides', []))}
    for entry in app_data.get('unit_modifiers', []):
        units = units_by_entry.get(entry.get('bsdata_id'))
        if not units:
            unmatched['unit_modifiers'].append(entry)
            continue
        for unit in units:
            unit['added_modifiers'] = list(entry.get('modifiers') or [])
    # Leader buffs edited for BSData leaders replace the parsed ones
    for entry in app_data.get('leader_buffs', []):
        units = units_by_entry.get(entry.get('bsdata_id'))
        if not units:
            unmatched['leader_buffs'].append(entry)
            continue
        for unit in units:
            unit['leader_buffs_override'] = list(entry.get('buffs') or [])

    if wahapedia is not None:
        unmatched['wahapedia_overrides'] = _add_wahapedia(data, wahapedia, unmatched['wahapedia_overrides'],
                                                          report if report is not None else [])
    return data, unmatched


OVERRIDE_FIELDS = ('applies_to', 'phase', 'modifier')


def _add_wahapedia(data, wahapedia, overrides, report):
    """
    Append Wahapedia's stratagems and rules (with stable IDs and the app data's edits), and
    mark the app data's records they replace (same table, faction and name). Returns the
    overrides that matched nothing.
    """
    factions = [f for f in data['factions'] if f.get('source') == 'bsdata']
    rows, notes = wahapedia_import.link(wahapedia.data, factions, data['detachments'])
    report.extend(notes)
    ids = stable_ids([(key, table) for table, key, _ in rows])
    edits = {(o.get('kind'), str(o.get('wahapedia_id'))): o for o in overrides}
    used = set()
    for table, key, record in rows:
        record = dict(record, id=ids[key])
        edit = edits.get(('stratagem' if table == 'stratagems' else 'rule', record['wahapedia_id']))
        if edit:
            record.update({f: edit.get(f) for f in OVERRIDE_FIELDS}, edited=True)
            used.add(id(edit))
        data[table].append(record)

    names = {(table, record['faction_id'], wahapedia_import.name_key(record['name'])) for table, _, record in rows}
    for section in ('faction_rules', 'stratagems'):
        for record in data[section]:
            if record.get('source', 'app') == 'app' and                     (section, record.get('faction_id'), wahapedia_import.name_key(record['name'])) in names:
                record['superseded'] = True
    return [o for o in overrides if id(o) not in used]


def unique_name(name, taken, label):
    """`name`, or 'name (label)', 'name (label 2)'... if a faction already has it."""
    candidate, n = name, 1
    while candidate in taken:
        candidate = f'{name} ({label})' if n == 1 else f'{name} ({label} {n})'
        n += 1
    return candidate


def _refresh_linked_weapons(data, profiles):
    """Custom weapons copied from BSData take the current stats of the profile they link to."""
    for kind, section in WEAPON_SECTIONS.items():
        for weapon in data[section]:
            source = profiles.get((kind, weapon.get('linked_bsdata_id')))
            if weapon.get('linked_bsdata_id') and source:
                for field in LINKED_FIELDS[kind]:
                    weapon[field] = source.get(field)


# Weapon fields a linked custom weapon takes from BSData (its name stays its own)
LINKED_FIELDS = {
    'ranged': ['attacks', 'ballistic_skill', 'strength', 'armor_penetration', 'damage', 'range_inches', 'abilities'],
    'melee': ['attacks', 'weapon_skill', 'strength', 'armor_penetration', 'damage', 'abilities'],
}


def _add_bsdata(data, imported, by_key):
    """
    Append the imported factions, detachments, units and weapons to `data` with stable IDs.
    Returns {(kind, profile bsdata_id): weapon record} for linking custom weapons.
    """
    cat_of = {f['id']: f['bsdata_id'] for f in imported['factions']}
    keys = [(f'faction:{f["bsdata_id"]}', 'factions') for f in imported['factions']]
    keys += [(f'unit:{cat_of[u["faction_id"]]}/{u["bsdata_id"]}', 'units') for u in imported['units']]
    keys += [(f'detachment:{cat_of[d["faction_id"]]}/{d["bsdata_id"]}', 'detachments')
             for d in imported.get('detachments', [])]
    keys += [(f'enhancement:{cat_of[e["faction_id"]]}/{e["bsdata_id"]}', 'enhancements')
             for e in imported.get('enhancements', [])]
    for kind, section in WEAPON_SECTIONS.items():
        keys += [(f'{kind}:{cat_of[w["faction_id"]]}/{w["bsdata_id"]}', section) for w in imported[section]]
    ids = stable_ids(keys)

    taken = {f['name'] for f in data['factions']}
    faction_ids = {}
    for f in imported['factions']:
        name = unique_name(f['name'], taken, 'BSData')
        record = {'id': ids[f'faction:{f["bsdata_id"]}'], 'name': name, 'source': 'bsdata', 'bsdata_id': f['bsdata_id'],
                  'army_rules': f.get('army_rules', [])}
        taken.add(name)
        faction_ids[f['id']] = record['id']
        by_key[f['bsdata_id']] = record
        data['factions'].append(record)

    for d in imported.get('detachments', []):
        data['detachments'].append(dict(d, id=ids[f'detachment:{cat_of[d["faction_id"]]}/{d["bsdata_id"]}'],
                                        faction_id=faction_ids[d['faction_id']]))
    for e in imported.get('enhancements', []):
        data['enhancements'].append(dict(e, id=ids[f'enhancement:{cat_of[e["faction_id"]]}/{e["bsdata_id"]}'],
                                         faction_id=faction_ids[e['faction_id']]))

    weapon_ids, profiles = {}, {}
    for kind, section in WEAPON_SECTIONS.items():
        for w in imported[section]:
            record = dict(w, id=ids[f'{kind}:{cat_of[w["faction_id"]]}/{w["bsdata_id"]}'],
                          faction_id=faction_ids[w['faction_id']])
            weapon_ids[(kind, w['id'])] = record['id']
            profiles.setdefault((kind, w['bsdata_id']), record)
            data[section].append(record)

    for u in imported['units']:
        record = dict(u, id=ids[f'unit:{cat_of[u["faction_id"]]}/{u["bsdata_id"]}'],
                      faction_id=faction_ids[u['faction_id']])
        for kind in WEAPON_SECTIONS:
            record[f'{kind}_weapons'] = [dict(e, weapon_id=weapon_ids[(kind, e['weapon_id'])])
                                         for e in u.get(f'{kind}_weapons', [])]
            record[f'{kind}_options'] = [weapon_ids[(kind, i)] for i in u.get(f'{kind}_options', [])]
        if u.get('wargear'):
            record['wargear'] = _remap_tree_weapons(u['wargear'], weapon_ids)
        data['units'].append(record)
    return profiles


def _remap_tree_weapons(tree, weapon_ids):
    """A copy of a unit's option tree with its weapons' import IDs replaced by database IDs."""
    node = dict(tree)
    if 'weapons' in tree:
        node['weapons'] = [[kind, weapon_ids[(kind, i)]] for kind, i in tree['weapons'] if (kind, i) in weapon_ids]
    node['children'] = [_remap_tree_weapons(c, weapon_ids) for c in tree.get('children', [])]
    return node


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def rebuild_reference_tables(data):
    """Drop and recreate the reference tables, then fill them from `data` (as merge_data returns it)."""
    db.metadata.drop_all(bind=db.engine, tables=REFERENCE_TABLES)
    db.metadata.create_all(bind=db.engine, tables=REFERENCE_TABLES)

    for name, model in SECTIONS:
        # Every row needs every column for the batch insert; missing optional fields get the column default
        defaults = {
            c.name: c.default.arg if c.default is not None and c.default.is_scalar else None
            for c in model.__table__.columns
        }
        if model is Faction:
            defaults['source'] = 'custom'
        rows = [{col: record.get(col, default) for col, default in defaults.items()}
                for record in data.get(name, [])]
        if rows:
            db.session.execute(model.__table__.insert(), rows)

    for key, table in LOADOUTS:
        rows = [
            {'unit_id': unit['id'], 'weapon_id': entry['weapon_id'], 'qty': entry.get('qty') or 1, 'position': i}
            for unit in data.get('units', [])
            for i, entry in enumerate(unit.get(key, []))
        ]
        if rows:
            db.session.execute(table.insert(), rows)

    db.session.commit()


def export_data(source=None, unmatched=None):
    """The app data JSON from the current tables, in a stable order (BSData records left out)."""
    data = {'schema_version': SCHEMA_VERSION, 'source': source}
    custom = {f.id for f in Faction.query.filter_by(source='custom')}

    for name, model in SECTIONS:
        columns = [c.name for c in model.__table__.columns]
        records = []
        if model in (Detachment, Enhancement):
            continue  # BSData only
        for obj in model.query.order_by(model.id).all():
            if model is Faction:
                if obj.source != 'custom':
                    continue
                record = {'id': obj.id, 'name': obj.name}
            elif model in (Unit, RangedWeapon, MeleeWeapon):
                if obj.faction_id not in custom:
                    continue
                record = {col: getattr(obj, col) for col in columns if col not in BSDATA_COLUMNS}
            else:
                record = {col: getattr(obj, col) for col in columns}
            if model is Unit:
                for key, table in LOADOUTS:
                    rows = db.session.execute(
                        table.select().where(table.c.unit_id == obj.id).order_by(table.c.position, table.c.weapon_id)
                    ).all()
                    record[key] = [{'weapon_id': r.weapon_id, 'qty': r.qty} for r in rows]
            if model in (FactionRule, Stratagem):
                if obj.source == 'wahapedia':
                    continue
                for col in WAHAPEDIA_COLUMNS:
                    record.pop(col, None)
            if model in (FactionRule, Stratagem) and obj.faction_id is not None and obj.faction_id not in custom:
                faction = obj.faction
                record.pop('faction_id')
                record['faction_key'] = faction.bsdata_id
                record['faction_name'] = faction.name
            records.append(record)
        data[name] = records

    unmatched = unmatched or {}
    modifiers, buffs, seen_modifiers, seen_buffs = [], [], set(), set()
    for unit in Unit.query.filter(Unit.bsdata_id.isnot(None)).order_by(Unit.name, Unit.id):
        if unit.added_modifiers and unit.bsdata_id not in seen_modifiers:
            seen_modifiers.add(unit.bsdata_id)
            modifiers.append({'bsdata_id': unit.bsdata_id, 'name': unit.name, 'modifiers': list(unit.added_modifiers)})
        if unit.leader_buffs_override is not None and unit.bsdata_id not in seen_buffs:
            seen_buffs.add(unit.bsdata_id)
            buffs.append({'bsdata_id': unit.bsdata_id, 'name': unit.name, 'buffs': list(unit.leader_buffs_override)})
    data['unit_modifiers'] = modifiers + [m for m in unmatched.get('unit_modifiers', []) if m.get('bsdata_id') not in seen_modifiers]
    data['leader_buffs'] = buffs + [b for b in unmatched.get('leader_buffs', []) if b.get('bsdata_id') not in seen_buffs]

    edits, seen_edits = [], set()
    for kind, model in (('stratagem', Stratagem), ('rule', FactionRule)):
        for obj in model.query.filter_by(source='wahapedia', edited=True).order_by(model.name, model.id):
            if (kind, obj.wahapedia_id) not in seen_edits:
                seen_edits.add((kind, obj.wahapedia_id))
                edits.append({'kind': kind, 'wahapedia_id': obj.wahapedia_id, 'name': obj.name,
                              **{f: getattr(obj, f) for f in OVERRIDE_FIELDS}})
    data['wahapedia_overrides'] = edits + [o for o in unmatched.get('wahapedia_overrides', [])
                                           if (o.get('kind'), str(o.get('wahapedia_id'))) not in seen_edits]
    return data


# Rule and stratagem columns only Wahapedia records use (or set on load), left out of the data JSON
WAHAPEDIA_COLUMNS = ('source', 'wahapedia_id', 'text', 'parse', 'edited', 'superseded', 'category', 'turn', 'timing')

# Unit columns only BSData units use, left out of custom units in the data JSON
BSDATA_COLUMNS = {'bsdata_id', 'legends', 'ranged_options', 'melee_options', 'added_modifiers', 'leader_buffs_override',
                  'wargear', 'max_in_army', 'base'}


def write_json(path, data, indent=2):
    """Write JSON atomically (temp file then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
        f.write('\n')
    os.replace(tmp, path)


def write_data_file(path, data):
    """Write the data JSON atomically, pretty-printed."""
    write_json(path, data)
