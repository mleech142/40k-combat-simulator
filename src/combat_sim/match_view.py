"""
The Armies page's Match view: everything a player needs at the table for one army list, in
one answer (`match_view`). Each unit's datasheet (characteristics, the weapons it carries at its
size and chosen wargear, its abilities, keywords, enhancement and who it leads or is led by),
the army's rules (faction and chosen detachments), the enhancements taken and the stratagems
it can use (core, faction and chosen detachments'), each stratagem with when it is used.

Unit abilities are stored as one text (bsdata.py: "Name: text" paragraphs, then the core and
faction rules the datasheet links to, by name); `split_abilities` reads it back.
"""

import re

from . import armies
from .models import db, Detachment, Enhancement, Faction, FactionRule, Keyword, MeleeWeapon, RangedWeapon, Stratagem, Unit
from .simulation import keyword_name
from .wahapedia import name_key

PHASES = ['Command phase', 'Movement phase', 'Shooting phase', 'Charge phase', 'Fight phase']
# A stratagem written in the app has only the maths' phase ('Both' = any attack, so no phase of its own)
APP_TIMING = {'Shooting': 'Shooting phase', 'Fighting': 'Fight phase'}
ABILITY = re.compile(r'^([^:\n*^]{1,60}):\s*(.*)$', re.S)


def split_abilities(text):
    """
    A unit's ability text as ([{'name', 'text'}], [rule names]): "Name: text" paragraphs are
    abilities, short lines without one are the rules it links to (Deep Strike, Stealth...), and
    any other paragraph continues the ability before it.
    """
    abilities, rules = [], []
    for block in re.split(r'\n{2,}', (text or '').replace('\r', '')):
        block = block.strip()
        if not block:
            continue
        m = ABILITY.match(block)
        if m:
            abilities.append({'name': m.group(1).strip(), 'text': m.group(2).strip()})
        elif '\n' not in block and len(block) <= 40 and not block.endswith('.'):
            if block not in rules:
                rules.append(block)
        elif abilities:
            abilities[-1]['text'] += '\n\n' + block
        else:
            abilities.append({'name': '', 'text': block})
    return abilities, rules


def keyword_hints():
    """{lowercase keyword name: its summary} from the keyword glossary."""
    return {k.name.lower(): k.summary for k in Keyword.query if k.summary}


def hint_for(entry, hints):
    return hints.get(keyword_name(entry).lower())


def weapon_row(weapon, qty, hints):
    data = weapon.to_dict()
    return {'name': data['name'], 'qty': qty, 'range': data['range'], 'attacks': data['attacks'], 'skill': data['skill'],
            'strength': data['strength'], 'ap': data['ap'], 'damage': data['damage'],
            'abilities': [{'text': a, 'hint': hint_for(a, hints)} for a in data['abilities']]}


def unit_weapons(unit, entry, catalogue, hints):
    """{'ranged': [...], 'melee': [...]}: what the unit carries at its size and chosen wargear, qty = models carrying it."""
    if unit.wargear is not None:  # BSData's own rules build the loadout (its defaults when nothing is chosen)
        rows = entry.get('loadout') or armies.unit_wargear(unit, entry.get('wargear'), entry['models'], catalogue)['loadout']
        found = {}
        for kind, model in (('ranged', RangedWeapon), ('melee', MeleeWeapon)):
            ids = [r['weapon_id'] for r in rows if r['weapon_type'] == kind]
            found.update({(kind, w.id): w for w in model.query.filter(model.id.in_(ids))} if ids else {})
        out = {'ranged': [], 'melee': []}
        for row in rows:
            weapon = found.get((row['weapon_type'], row['weapon_id']))
            if weapon is not None:
                out[row['weapon_type']].append(weapon_row(weapon, row['qty'], hints))
        return out
    scale = entry['models'] / unit.default_models if unit.default_models and entry['models'] else 1
    return {kind: [weapon_row(w, max(1, round(qty * scale)), hints) for w, qty in unit.loadout(kind)]
            for kind in ('ranged', 'melee')}


HELPER_KEYWORD = re.compile(r'\bnon-', re.I)  # BSData's own categories, e.g. "Khorne non-Battleline"
STAT_FIELDS = {'M': 'move', 'T': 'toughness', 'Sv': 'save', 'W': 'wounds', 'LD': 'leadership', 'OC': 'oc'}


def sheet_stats(unit, effects):
    """The datasheet's characteristics with the chosen wargear's changes (wargear.effects) applied."""
    stats = {'move': unit.move, 'toughness': unit.toughness, 'save': unit.save, 'wounds': unit.wounds,
             'leadership': unit.leadership, 'oc': unit.oc, 'invulnerable_save': unit.invulnerable_save,
             'feel_no_pain': unit.feel_no_pain}
    for name, change in (effects or {}).get('stats', {}).items():
        field = STAT_FIELDS.get(name)
        current = stats.get(field)
        if field == 'move' and current is not None and str(current).isdigit():
            stats[field] = str(int(current) + change)
        elif isinstance(current, int):
            stats[field] = current + change
    invulnerable = (effects or {}).get('invulnerable_save')
    if invulnerable and (stats['invulnerable_save'] is None or invulnerable < stats['invulnerable_save']):
        stats['invulnerable_save'] = invulnerable
    return stats


def sheet_abilities(abilities, effects):
    """The unit's abilities with those of options no longer taken left out and those of options taken added."""
    if not effects:
        return abilities
    dropped = {name.lower() for name in effects.get('dropped_abilities', [])}
    out = [a for a in abilities if a['name'].lower() not in dropped]
    shown = {a['name'].lower() for a in out}
    out += [{'name': name, 'text': text} for name, text in effects.get('abilities', []) if name.lower() not in shown]
    return out


def unit_sheet(unit, entry, catalogue, hints, faction_rule_names, enhancements):
    weapons = unit_weapons(unit, entry, catalogue, hints)
    effects = entry.get('wargear_effects')
    abilities, rules = split_abilities(unit.abilities)
    abilities = sheet_abilities(abilities, effects)
    # Weapon abilities the importer lists with the unit's rules (Sustained Hits...) belong on the weapons
    option_ids = {'ranged': unit.ranged_options or [], 'melee': unit.melee_options or []}
    carried = [a['text'] for rows in weapons.values() for w in rows for a in w['abilities']]
    for kind, model in (('ranged', RangedWeapon), ('melee', MeleeWeapon)):
        if option_ids[kind]:
            carried += [a for w in model.query.filter(model.id.in_(option_ids[kind])) for a in (w.abilities or [])]
    weapon_keywords = {keyword_name(a).lower() for a in carried}
    core, faction = [], []
    for rule in rules:
        if name_key(rule) in faction_rule_names:
            faction.append(rule)
        elif keyword_name(rule).lower() not in weapon_keywords:
            core.append({'name': rule, 'hint': hint_for(rule, hints)})
    enhancement = enhancements.get(entry.get('enhancement')) if entry.get('enhancement') else None
    return {
        'name': unit.name, 'legends': bool(unit.legends),
        'stats': sheet_stats(unit, effects),
        'base': (unit.base or {}).get('text'),
        'weapons': weapons,
        'abilities': abilities, 'core_abilities': core, 'faction_abilities': faction,
        'keywords': list(dict.fromkeys((unit.keywords or []) + [k for k in (effects or {}).get('keywords', [])
                                                                 if not HELPER_KEYWORD.search(k)])),
        'enhancement': {'name': enhancement.name, 'points': enhancement.points, 'text': enhancement.text or ''}
        if enhancement else None,
    }


def rule_record(name, text, source, modifier=None):
    return {'name': name, 'text': text or '', 'source': source, 'modifier': modifier if not text else None}


def faction_rules(faction, detachments):
    """(army rules, [chosen detachment with its 'rules']): BSData's, then those written in the app or from Wahapedia not already there."""
    rows = FactionRule.query.filter_by(faction_id=faction.id).order_by(FactionRule.name).all() if faction else []
    rows = [r for r in rows if not r.superseded]

    def merged(own, extra):
        seen = {name_key(r['name']) for r in own}
        return own + [rule_record(r.name, r.text, r.source, r.modifier) for r in extra if name_key(r.name) not in seen]

    army_rules = merged([rule_record(r.get('name', ''), r.get('text'), 'bsdata') for r in (faction.army_rules or [])] if faction else [],
                        [r for r in rows if not r.detachment_key])
    found = {d.bsdata_id: d for d in Detachment.query.filter(Detachment.bsdata_id.in_([d['key'] for d in detachments]))} \
        if detachments else {}
    chosen = []
    for d in detachments:
        record = found.get(d['key'])
        own = [rule_record(r.get('name', ''), r.get('text'), 'bsdata') for r in (record.rules or [])] if record else []
        chosen.append(dict(d, rules=merged(own, [r for r in rows if r.detachment_key == d['key']])))
    return army_rules, chosen


def stratagem_record(s, detachment_names):
    timing = s.timing or APP_TIMING.get(s.phase or '')
    return {'id': s.id, 'name': s.name, 'cp': s.cp, 'category': s.category, 'core': s.faction_id is None,
            'detachment': detachment_names.get(s.detachment_key) or s.detachment_name,
            'turn': s.turn,
            'timing': timing, 'phases': [p for p in PHASES if p.split()[0].lower() in (timing or '').lower()],
            'text': s.text or s.notes or '', 'modifier': s.modifier if not (s.text or s.notes) else None,
            'source': s.source}


def stratagems(faction, detachments):
    """Core stratagems and the faction's (for the whole faction, or for a chosen detachment): detachments' first."""
    keys = {d['key']: d['name'] for d in detachments}
    query = Stratagem.query.filter(db.or_(Stratagem.faction_id.is_(None), Stratagem.faction_id == (faction.id if faction else -1)))
    rows = [s for s in query.order_by(Stratagem.name) if not s.superseded and (not s.detachment_key or s.detachment_key in keys)]
    rank = {key: i for i, key in enumerate(keys)}
    rows.sort(key=lambda s: (s.faction_id is None, rank.get(s.detachment_key, len(rank)), s.name))
    return [stratagem_record(s, keys) for s in rows]


def match_view(army):
    """The Match view of an army (see the module docstring); `army` as armies.clean_army returns it."""
    priced = armies.price_army(army)
    faction = db.session.get(Faction, army['faction_id'])
    catalogue = faction.bsdata_id if faction else None
    army_rules, detachments = faction_rules(faction, priced['detachments'])
    rule_names = {name_key(r['name']) for r in army_rules}
    enhancements = {e.bsdata_id: e for e in Enhancement.query.filter_by(faction_id=army['faction_id'])}
    hints = keyword_hints()
    units = {u.id: u for u in Unit.query.filter(Unit.id.in_({e['unit_id'] for e in priced['units']}))} if priced['units'] else {}

    sheets = []
    for entry in priced['units']:
        unit = units.get(entry['unit_id'])
        sheet = {'uid': entry['uid'], 'unit_id': entry['unit_id'], 'name': entry['name'], 'models': entry['models'],
                 'points': entry['points'], 'warlord': entry['uid'] == priced['warlord'], 'attached_to': entry['attached_to'],
                 'led_by': entry['led_by'], 'supported_by': entry['supported_by'],
                 'join_role': next(('supports' if entry['uid'] in e['supported_by'] else 'leads'
                                    for e in priced['units'] if entry['uid'] in e['led_by'] + e['supported_by']), None),
                 'wargear_summary': entry.get('wargear_summary') or '', 'problems': entry['problems']}
        if unit is not None:
            sheet.update(unit_sheet(unit, entry, catalogue, hints, rule_names, enhancements))
        sheets.append(sheet)

    # Units as they take the field: each unit, then the leaders and support units joining it
    by_uid = {s['uid']: s for s in sheets}
    groups = [[s['uid']] + s['led_by'] + s['supported_by'] for s in sheets if not (s['join_role'] and s['attached_to'] in by_uid)]

    strats = stratagems(faction, priced['detachments'])
    taken = [{'name': s['enhancement']['name'], 'points': s['enhancement']['points'], 'text': s['enhancement']['text'],
              'bearer': s['uid']} for s in sheets if s.get('enhancement')]
    return {
        'name': army['name'], 'faction': priced['faction'], 'points': priced['total'], 'points_limit': priced['points_limit'],
        'detachments': detachments, 'dp_total': priced['dp_total'], 'dp_limit': priced['dp_limit'],
        'disposition': priced['disposition'], 'warlord': priced['warlord'], 'problems': priced['problems'],
        'units': sheets, 'groups': groups, 'army_rules': army_rules, 'enhancements': taken,
        'stratagems': strats,
        'wahapedia': any(s['source'] == 'wahapedia' for s in strats),  # Else only what the app's own records say
    }
