"""
Share codes: an army list as a short text code that another install of the app can import.

A saved army refers to units by database ID and to wargear choices by `k` (bsdata.freeze's
numbering of a unit's option tree); both are local to one install and one import of BSData.
A code carries BSData IDs instead, so it means the same list on any install with the same
BSData files:

- the faction by catalogue ID, each unit by its entry ID (with its catalogue when that isn't
  the army's), detachments and enhancements by their BSData IDs (as armies already store them);
- each wargear choice by its option's BSData ID, or the path of IDs from the unit down to it
  when the ID appears more than once in the unit's tree; only choices that differ from the
  datasheet's defaults at the unit's size are written (null: a choice the defaults have and the
  list doesn't), and import adds them back to this install's defaults;
- attachments and the Warlord by position in the list (uids are rebuilt on import).

Lists with units from custom factions can't be shared: those units live in the sharer's own
app data. A code is `PREFIX` + URL-safe base64 of zlib-compressed JSON. On import anything not
found in this install's data is left out and named in `notes`; the page then prices the list
as usual, so points follow the importer's own data.
"""

import base64
import binascii
import json
import zlib
from types import SimpleNamespace

from . import wargear as wargear_engine
from .armies import ArmyError, clean_army, price_army
from .models import db, Detachment, Enhancement, Faction, Unit

PREFIX = '40KCS1:'
VERSION = 1
MAX_CODE_LENGTH = 100_000
MAX_JSON_BYTES = 1_000_000  # Decompressed: guards against a code that inflates without end


# ---------------------------------------------------------------------------
# Wargear choices: {k: count} <-> {option reference: count}
# ---------------------------------------------------------------------------

def _option_refs(tree):
    """{k: reference} for every node in a unit's option tree: its BSData ID if unique in the tree, else its ID path."""
    paths, counts = {}, {}

    def walk(node, path):
        for child in node.get('children', []):
            child_path = path + [child['id']]
            key = '/'.join(child_path)
            while key in paths.values():  # The same entry twice under one parent: number the repeats
                key += '#'
            paths[child['k']] = key
            counts[child['id']] = counts.get(child['id'], 0) + 1
            walk(child, child_path)

    walk(tree, [])
    return {k: (path.rsplit('/', 1)[-1] if counts[path.rsplit('/', 1)[-1]] == 1 else path) for k, path in paths.items()}


def _defaults(tree, models, catalogue_id):
    """The full state of a unit's choices with BSData's defaults at `models` models ({str k: count})."""
    sel = wargear_engine.build(tree, None, models, SimpleNamespace(id=catalogue_id))
    return {str(k): count for k, count in wargear_engine.choices_of(sel).items()}


def _wargear_out(tree, choices, defaults=None):
    """The choices by reference: those that differ from `defaults` (None for one the defaults have and the choices don't)."""
    refs = _option_refs(tree)
    choices = {str(k): count for k, count in choices.items()}
    defaults = defaults or {}
    changed = {k: count for k, count in choices.items() if defaults.get(k) != count}
    changed.update({k: None for k in defaults if k not in choices})
    return {refs[int(k)]: count for k, count in changed.items() if int(k) in refs}


def _wargear_in(tree, choices, defaults=None):
    """The choices by `k` (`defaults` with the code's changes), and how many references this tree doesn't have."""
    by_ref = {ref: str(k) for k, ref in _option_refs(tree).items()}
    found = dict(defaults or {})
    missing = 0
    for ref, count in choices.items():
        if ref not in by_ref:
            missing += 1
        elif count is None:
            found.pop(by_ref[ref], None)
        else:
            found[by_ref[ref]] = count
    return found, missing


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode(army):
    """The share code for an army (as clean_army gives it). Raises ArmyError for lists that can't be shared."""
    faction = db.session.get(Faction, army['faction_id'])
    if faction is None:
        raise ArmyError('Choose a faction first.')
    if faction.source != 'bsdata' or not faction.bsdata_id:
        raise ArmyError(f'{faction.name} is a custom faction: only lists of BSData units can be shared.')
    priced = price_army(army)  # Sizes filled in, wargear in full, attachments to removed units dropped
    entries = priced['units']
    units = {u.id: u for u in Unit.query.filter(Unit.id.in_({e['unit_id'] for e in entries}))} if entries else {}
    custom = sorted({units[e['unit_id']].name for e in entries
                     if e['unit_id'] in units and (units[e['unit_id']].faction.source != 'bsdata' or not units[e['unit_id']].bsdata_id)})
    if custom:
        raise ArmyError(f'Lists with custom units can\'t be shared: {", ".join(custom)} '
                        f'{"is" if len(custom) == 1 else "are"} only in your data.')
    if any(e['unit_id'] not in units for e in entries):
        raise ArmyError('Remove the units that are no longer in the data before sharing this list.')

    position = {e['uid']: i for i, e in enumerate(entries)}
    out_units = []
    for e in entries:
        unit = units[e['unit_id']]
        row = {'id': unit.bsdata_id, 'name': unit.name, 'models': e['models']}
        if unit.faction.bsdata_id != faction.bsdata_id:
            row['cat'] = unit.faction.bsdata_id
        if e.get('attached_to') in position:
            row['joins'] = position[e['attached_to']]
        if e.get('enhancement'):
            row['enh'] = e['enhancement']
        if e.get('wargear') and unit.wargear is not None:
            row['wargear'] = _wargear_out(unit.wargear, e['wargear'], _defaults(unit.wargear, e['models'], faction.bsdata_id))
        out_units.append(row)
    payload = {'v': VERSION, 'faction': faction.bsdata_id, 'faction_name': faction.name, 'name': army['name'],
               'limit': army['points_limit'], 'detachments': army['detachment_keys'],
               'warlord': position.get(priced['warlord']), 'units': out_units}
    raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode('ascii').rstrip('=')


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _payload(code):
    bad = ArmyError('That isn\'t an army share code (or part of it is missing).')
    code = ''.join(str(code or '').split())  # Line breaks and spaces picked up when pasting
    if not code.startswith(PREFIX) or len(code) > MAX_CODE_LENGTH:
        raise bad
    body = code[len(PREFIX):]
    try:
        packed = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
        inflater = zlib.decompressobj()
        raw = inflater.decompress(packed, MAX_JSON_BYTES)
        if inflater.unconsumed_tail or not inflater.eof:
            raise bad
        payload = json.loads(raw.decode('utf-8'))
    except (binascii.Error, ValueError, zlib.error, UnicodeDecodeError):
        raise bad
    if not isinstance(payload, dict) or not isinstance(payload.get('units'), list):
        raise bad
    if payload.get('v') != VERSION:
        raise ArmyError('This code was made by a newer version of the app: update the app to import it.')
    return payload


def decode(code):
    """
    An army from a share code, in this install's IDs: {'army' (as clean_army gives it, not saved),
    'notes' (what wasn't found in this install's data)}. Raises ArmyError for codes it can't read.
    """
    payload = _payload(code)
    faction = Faction.query.filter_by(bsdata_id=str(payload.get('faction')), source='bsdata').first()
    if faction is None:
        name = payload.get('faction_name') or 'its faction'
        raise ArmyError(f'This list is for {name}, which isn\'t in your BSData data. '
                        'Download the latest BSData on the Setup page, then try again.')
    factions = {f.bsdata_id: f.id for f in Faction.query.filter_by(source='bsdata')}
    enhancements = {e.bsdata_id for e in Enhancement.query.filter_by(faction_id=faction.id)}
    notes, units, kept = [], [], {}
    for i, row in enumerate(payload['units']):
        if not isinstance(row, dict):
            continue
        name = str(row.get('name') or 'A unit')[:200]
        faction_id = factions.get(str(row.get('cat') or faction.bsdata_id))
        unit = Unit.query.filter_by(faction_id=faction_id, bsdata_id=str(row.get('id'))).first() if faction_id else None
        if unit is None:
            notes.append(f'{name} isn\'t in your data, so it was left out.')
            continue
        uid = f'u{i + 1}'
        kept[i] = uid
        wargear = None
        if isinstance(row.get('wargear'), dict) and unit.wargear is not None:
            models = row.get('models') if isinstance(row.get('models'), int) else None
            wargear, missing = _wargear_in(unit.wargear, row['wargear'], _defaults(unit.wargear, models, faction.bsdata_id))
            if missing:
                notes.append(f'{unit.name}: {missing} of its wargear choices {"isn" if missing == 1 else "aren"}\'t '
                             'in your data; check its wargear.')
        enhancement = row.get('enh')
        if enhancement and str(enhancement) not in enhancements:
            notes.append(f'{unit.name}: its enhancement isn\'t in your data, so it was left out.')
            enhancement = None
        units.append({'uid': uid, 'unit_id': unit.id, 'models': row.get('models'), 'joins': row.get('joins'),
                      'enhancement': enhancement, 'wargear': wargear or None})
    for entry in units:
        joins = entry.pop('joins')
        entry['attached_to'] = kept.get(joins) if isinstance(joins, int) else None
    known = {d.bsdata_id for d in Detachment.query.filter_by(faction_id=faction.id)}
    detachments = [k for k in (payload.get('detachments') or []) if isinstance(k, str)]
    if any(k not in known for k in detachments):
        notes.append('A detachment in this list isn\'t in your data, so it was left out.')
        detachments = [k for k in detachments if k in known]
    warlord = payload.get('warlord')
    army = clean_army({'name': payload.get('name'), 'faction_id': faction.id, 'points_limit': payload.get('limit'),
                       'detachment_keys': detachments, 'units': units,
                       'warlord': kept.get(warlord) if isinstance(warlord, int) else None})
    return {'army': army, 'notes': notes}
