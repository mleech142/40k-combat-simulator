"""
Army lists: one faction's units at their sizes, leaders attached to the units they lead, priced
and checked. The Armies page sends a list here on every change (price_army) so the game rules
stay in Python; Analytics takes a saved list as its attackers or defenders (`roster`).

An army is {'name', 'faction_id', 'detachment_keys', 'points_limit', 'units': [{'uid', 'unit_id',
'models', 'attached_to'}]}. `uid` names an entry within the list (the same datasheet can be
taken more than once); a leader's `attached_to` is the uid of the unit it leads.

What is checked (an army that breaks a rule can still be saved; the problems are listed):
- the points limit (a battle size, or none);
- detachments: their Detachment Points (DP) within the battle size's budget (Incursion 2, or a
  single 3 DP detachment on its own; Strike Force 3; Onslaught 4, as BSData has it), each from
  the army's faction, and no two sharing a Unique Tag (DP costs and tags come from BSData);
- unit sizes: one the datasheet lists;
- units from another faction, or no longer in the data;
- leaders: attached only to a unit their Leader ability names, and only leaders attached.

Not yet: enhancements, Warlord, the rule of three, Epic Heroes, wargear choices.
"""

from .models import db, Detachment, Faction, Unit

# (name, points limit, Detachment Points) as BSData's game system has them; Onslaught is provisional there
BATTLE_SIZES = [('Incursion', 1000, 2), ('Strike Force', 2000, 3), ('Onslaught', 3000, 4)]
SINGLE_DETACHMENT_DP = {1000: 3}  # Incursion: one 3 DP detachment on its own is allowed too
MAX_UNITS = 100


class ArmyError(ValueError):
    """The request isn't an army this module can read."""


def clean_army(data):
    """An army from a request, with its fields checked and its entries' uids filled in."""
    if not isinstance(data, dict):
        raise ArmyError('Send the army as a JSON object.')
    name = str(data.get('name') or '').strip() or 'New army'
    try:
        faction_id = int(data['faction_id'])
    except (KeyError, TypeError, ValueError):
        raise ArmyError('Choose a faction.')
    limit = data.get('points_limit')
    try:
        limit = int(limit) if limit not in (None, '', 0, '0') else None
    except (TypeError, ValueError):
        raise ArmyError('The points limit must be a number.')
    units = data.get('units') or []
    if not isinstance(units, list) or len(units) > MAX_UNITS:
        raise ArmyError(f'An army holds up to {MAX_UNITS} units.')
    entries, seen = [], set()
    for i, entry in enumerate(units):
        try:
            unit_id, models = int(entry['unit_id']), int(entry.get('models') or 0) or None
        except (KeyError, TypeError, ValueError):
            raise ArmyError('Each unit needs a unit_id and a model count.')
        uid = str(entry.get('uid') or f'u{i + 1}')[:40]
        while uid in seen:
            uid += '+'
        seen.add(uid)
        entries.append({'uid': uid, 'unit_id': unit_id, 'models': models,
                        'attached_to': str(entry['attached_to'])[:40] if entry.get('attached_to') else None})
    keys = data.get('detachment_keys')
    if keys is None:  # One detachment, as armies were first saved
        keys = [data['detachment_key']] if data.get('detachment_key') else []
    if not isinstance(keys, list):
        raise ArmyError('detachment_keys must be a list.')
    keys = list(dict.fromkeys(str(k)[:40] for k in keys if k))  # Each detachment once, in order
    return {'name': name[:200], 'faction_id': faction_id, 'detachment_keys': keys,
            'points_limit': limit, 'units': entries}


def sizes_of(unit):
    """Model counts the datasheet allows (its listed sizes; its default size if it lists none)."""
    sizes = [b['models'] for b in unit.size_brackets()]
    return sizes or [unit.default_models or 1]


def price_army(army):
    """
    The army priced and checked: {'units': [entry with 'name', 'points', 'sizes', 'size_points', 'is_leader',
    'can_join', 'led_by', 'problems'], 'total', 'points_limit', 'problems', 'roster', 'faction',
    'detachments': [{'key', 'name', 'dp', 'dispositions', 'tags'}], 'detachment' (their names),
    'dp_total', 'dp_limit'}. `roster` is what Analytics takes: one entry per unit with its leader
    folded in.
    """
    ids = {e['unit_id'] for e in army['units']}
    units = {u.id: u for u in Unit.query.filter(Unit.id.in_(ids))} if ids else {}
    faction = db.session.get(Faction, army['faction_id'])
    detachments, problems = check_detachments(army, faction)

    entries = []
    for e in army['units']:
        unit = units.get(e['unit_id'])
        entry = dict({'models': None, 'attached_to': None}, **e, name=unit.name if unit else 'Unknown unit', points=None,
                     sizes=[], size_points=[], is_leader=False, can_join=[], led_by=[], problems=[])
        entries.append(entry)
        if unit is None:
            entry['problems'].append('This unit is no longer in the data.')
            continue
        entry['sizes'] = sizes_of(unit)
        entry['size_points'] = [unit.points_for(n) for n in entry['sizes']]
        if not entry['models']:
            entry['models'] = entry['sizes'][0]
        if entry['models'] not in entry['sizes']:
            entry['problems'].append(f'{entry["models"]} models isn\'t a size this datasheet lists '
                                     f'({", ".join(map(str, entry["sizes"]))}).')
        entry['points'] = unit.points_for(entry['models'])
        if entry['points'] is None:
            entry['problems'].append('This unit has no points.')
        if faction and unit.faction_id != faction.id:
            entry['problems'].append(f'Not a {faction.name} unit.')
        entry['is_leader'] = bool(unit.can_lead)

    by_uid = {e['uid']: e for e in entries}
    for entry in entries:
        unit = units.get(entry['unit_id'])
        if entry['is_leader']:
            entry['can_join'] = [other['uid'] for other in entries if other is not entry and other['name'] in unit.can_lead]
        target = by_uid.get(entry['attached_to']) if entry['attached_to'] else None
        if entry['attached_to'] and target is None:
            entry['attached_to'] = None  # Its unit was removed
        elif target is not None:
            if not entry['is_leader']:
                entry['problems'].append('Only leaders can be attached to another unit.')
            elif target['uid'] not in entry['can_join']:
                entry['problems'].append(f'Can\'t lead {target["name"]}.')
            target['led_by'].append(entry['uid'])

    total = sum(e['points'] or 0 for e in entries)
    limit = army['points_limit']
    if limit and total > limit:
        problems.insert(0, f'{total} points is {total - limit} over the {limit} point limit.')
    for entry in entries:
        problems += [f'{entry["name"]}: {p}' for p in entry['problems']]
    return {'units': entries, 'total': total, 'points_limit': limit, 'problems': problems,
            'roster': roster(entries), 'faction': faction.name if faction else None,
            'detachments': detachments, 'detachment': ', '.join(d['name'] for d in detachments) or None,
            'dp_total': sum(d['dp'] or 0 for d in detachments), 'dp_limit': dp_limit(limit)}


def dp_limit(points_limit):
    """The Detachment Points a battle size allows (None without a limit or for another limit)."""
    return next((dp for _, points, dp in BATTLE_SIZES if points == points_limit), None)


def check_detachments(army, faction):
    """([{'key', 'name', 'dp', 'dispositions', 'tags'}], problems) for the army's chosen detachments."""
    chosen, problems = [], []
    for key in army['detachment_keys']:
        found = Detachment.query.filter_by(bsdata_id=key, faction_id=army['faction_id']).first()
        if found is None:
            other = Detachment.query.filter_by(bsdata_id=key).first()
            whose = faction.name if faction else 'this faction'
            problems.append(f"{other.name} isn't a {whose} detachment." if other
                            else 'A detachment of this army is no longer in the data.')
            continue
        chosen.append({'key': key, 'name': found.name, 'dp': found.dp, 'dispositions': found.dispositions or [],
                       'tags': found.tags or []})

    budget = dp_limit(army['points_limit'])
    spent = sum(d['dp'] or 0 for d in chosen)
    single = SINGLE_DETACHMENT_DP.get(army['points_limit'])
    if budget is not None and spent > budget and not (len(chosen) == 1 and single and spent <= single):
        size = next(name for name, points, _ in BATTLE_SIZES if points == army['points_limit'])
        extra = f' (or a single {single} DP detachment)' if single else ''
        problems.append(f'The detachments cost {spent} DP; {size} allows {budget}{extra}.')
    for i, first in enumerate(chosen):
        for second in chosen[i + 1:]:
            for tag in sorted(set(first['tags']) & set(second['tags'])):
                problems.append(f'{first["name"]} and {second["name"]} share the Unique Tag "{tag}": choose one.')
    return chosen, problems


def roster(entries):
    """
    Analytics' units: [{'unit_id', 'models', 'leader_id'}], one per unit that isn't leading
    another, with the first leader attached to it (Analytics joins one leader to a unit).
    """
    by_uid = {e['uid']: e for e in entries}
    attached = set()
    out = []
    for entry in entries:
        if entry['attached_to'] in by_uid and entry['attached_to'] != entry['uid']:
            continue
        leader = next((by_uid[u] for u in entry['led_by'] if u not in attached), None)
        if leader:
            attached.add(leader['uid'])
        out.append({'unit_id': entry['unit_id'], 'models': entry['models'], 'leader_id': leader['unit_id'] if leader else None})
    # A second leader on the same unit takes the field on its own
    out += [{'unit_id': e['unit_id'], 'models': e['models'], 'leader_id': None}
            for e in entries if e['attached_to'] in by_uid and e['uid'] not in attached]
    return out


def army_text(army, priced):
    """The army as plain text, to paste into a message."""
    limit = f' / {priced["points_limit"]}' if priced['points_limit'] else ''
    dp = f' ({priced["dp_total"]} DP)' if priced['detachments'] else ''
    lines = [army['name'], ' - '.join(x for x in (priced['faction'], priced['detachment']) if x) + dp,
             f'{priced["total"]}{limit} points', '']
    by_uid = {e['uid']: e for e in priced['units']}

    def line(entry, prefix=''):
        size = f' ({entry["models"]} models)' if entry['models'] and entry['models'] > 1 else ''
        points = f' - {entry["points"]} pts' if entry['points'] is not None else ''
        return f'{prefix}{entry["name"]}{size}{points}'

    for entry in priced['units']:
        if entry['attached_to'] in by_uid:
            continue
        lines.append(line(entry))
        lines += [line(by_uid[u], '  + led by ') for u in entry['led_by']]
    if priced['problems']:
        lines += ['', 'Problems:'] + [f'- {p}' for p in priced['problems']]
    return '\n'.join(lines).strip() + '\n'
