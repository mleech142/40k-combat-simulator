"""
Army lists: one faction's units at their sizes, leaders and support units attached to the units
they join, enhancements and a Warlord, priced and checked. The Armies page sends a list here on
every change (price_army) so the game rules stay in Python; Analytics takes a saved list as its
attackers or defenders (`roster`).

An army is {'name', 'faction_id', 'detachment_keys', 'points_limit', 'warlord', 'disposition', 'units': [{'uid',
'unit_id', 'models', 'attached_to', 'enhancement', 'wargear'}]}. `wargear` is the unit's chosen
options ({k: count}, see wargear.py; None = BSData's defaults). `uid` names an entry within the list (the
same datasheet can be taken more than once); a leader's or support unit's `attached_to` is the
uid of the unit it joins; `enhancement` is an enhancement's BSData ID; `warlord` is an entry's uid.

What is checked (an army that breaks a rule can still be saved; the problems are listed):
- the points limit (a battle size, or none), enhancements' points included;
- detachments: their Detachment Points (DP) within the battle size's budget (Incursion 2, or a
  single 3 DP detachment on its own; Strike Force 3; Onslaught 4, as BSData has it), each from
  the army's faction, and no two sharing a Unique Tag (DP costs and tags come from BSData);
- unit sizes: one the datasheet lists; copies of a datasheet: its own limit in BSData, else the
  core rule (one Epic Hero, six Battleline or Dedicated Transport, three of anything else);
  later copies cost what BSData adds for them (`Unit.copy_points`, in list order);
- units from another faction, or no longer in the data;
- leaders and support units: attached only to units their Leader / Support ability names, at
  most one leader and one support unit per unit;
- a Warlord: one of the army's Characters;
- the force disposition: one of its detachments' (the first, to be confirmed, when none is chosen);
- enhancements: at most 2 at Incursion and 4 otherwise, each once, on Characters but not Epic
  Heroes, with their detachment chosen and their bearer allowed (BSData's own conditions:
  enhancement_hidden); Upgrades go on their own unit, up to three of the same, and don't count
  towards the limit; those only non-Characters link to (Warptide's) go on those units, as many
  as BSData allows, its copies counting once when BSData shares their cost.

- wargear: each unit's chosen options, priced, sized and checked by BSData's own rules
  (wargear.evaluate), its loadout passed to Analytics.
"""

from collections import Counter

from . import wargear as wargear_engine
from .models import db, Detachment, Enhancement, Faction, Unit, RangedWeapon, MeleeWeapon

# (name, points limit, Detachment Points) as BSData's game system has them; Onslaught is provisional there
BATTLE_SIZES = [('Incursion', 1000, 2), ('Strike Force', 2000, 3), ('Onslaught', 3000, 4)]
SINGLE_DETACHMENT_DP = {1000: 3}  # Incursion: one 3 DP detachment on its own is allowed too
ENHANCEMENT_LIMITS = {1000: 2}  # Incursion: 2; every other battle size: 4 (BSData's game system)
DEFAULT_ENHANCEMENT_LIMIT = 4
MAX_SAME_UPGRADE = 3
MAX_UNITS = 100


class ArmyError(ValueError):
    """The request isn't an army this module can read."""


def _short(value):
    return str(value)[:40] if value else None


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
        entries.append({'uid': uid, 'unit_id': unit_id, 'models': models, 'attached_to': _short(entry.get('attached_to')),
                        'enhancement': _short(entry.get('enhancement')), 'wargear': clean_wargear(entry.get('wargear'))})
    keys = data.get('detachment_keys')
    if keys is None:  # One detachment, as armies were first saved
        keys = [data['detachment_key']] if data.get('detachment_key') else []
    if not isinstance(keys, list):
        raise ArmyError('detachment_keys must be a list.')
    keys = list(dict.fromkeys(str(k)[:40] for k in keys if k))  # Each detachment once, in order
    return {'name': name[:200], 'faction_id': faction_id, 'detachment_keys': keys, 'points_limit': limit,
            'warlord': _short(data.get('warlord')), 'disposition': _short(data.get('disposition')), 'units': entries}


def clean_wargear(value):
    """A unit's chosen options from a request: {k: count} (None = BSData's defaults)."""
    if not value:
        return None
    if not isinstance(value, dict) or len(value) > 500:
        raise ArmyError('wargear must be an object of option numbers and counts.')
    try:
        return {str(int(k)): max(0, min(int(v), 100)) for k, v in value.items()}
    except (TypeError, ValueError):
        raise ArmyError('wargear must be an object of option numbers and counts.')


def unit_wargear(unit, choices, models, catalogue_id):
    """wargear.evaluate for a Unit, with its loadout's weapon names ([{'weapon_id', 'weapon_type', 'name', 'qty', 'default_use'}])."""
    result = wargear_engine.evaluate(unit.wargear, choices, models, catalogue_id)
    names = {}
    for kind, model in (('ranged', RangedWeapon), ('melee', MeleeWeapon)):
        ids = [weapon_id for k, weapon_id, _ in result['loadout'] if k == kind]
        names.update({(kind, w.id): w.name for w in model.query.filter(model.id.in_(ids))} if ids else {})
    rows = [{'weapon_id': weapon_id, 'weapon_type': kind, 'name': names.get((kind, weapon_id), '?'), 'qty': qty}
            for kind, weapon_id, qty in result['loadout']]
    for row, used in zip(rows, wargear_engine.default_used([r['name'] for r in rows])):
        row['default_use'] = used  # Later profiles of a "Weapon - Profile" family start unticked in Analytics
    result['loadout'] = rows
    return result


def sizes_of(unit):
    """Model counts the datasheet allows (its listed sizes; its default size if it lists none)."""
    sizes = [b['models'] for b in unit.size_brackets()]
    return sizes or [unit.default_models or 1]


def has_keyword(unit, keyword):
    return keyword.lower() in {k.lower() for k in (unit.keywords or [])}


def copies_allowed(unit):
    """How many of a datasheet an army may include: BSData's own limit, else the core rule."""
    if unit.max_in_army:
        return unit.max_in_army
    if has_keyword(unit, 'Epic Hero'):
        return 1
    if has_keyword(unit, 'Battleline') or has_keyword(unit, 'Dedicated Transport'):
        return 6
    return 3


def enhancement_limit(points_limit):
    return ENHANCEMENT_LIMITS.get(points_limit, DEFAULT_ENHANCEMENT_LIMIT)


# ---------------------------------------------------------------------------
# Enhancements: BSData's conditions for showing one, evaluated for a bearer
# ---------------------------------------------------------------------------

COMPARE = {'lessThan': lambda a, b: a < b, 'atMost': lambda a, b: a <= b, 'equalTo': lambda a, b: a == b,
           'notEqualTo': lambda a, b: a != b, 'atLeast': lambda a, b: a >= b, 'greaterThan': lambda a, b: a > b}


def _condition_holds(condition, context):
    """
    One BSData condition for a bearer in an army: roster/force counts are the chosen detachments
    (anything else, such as a Crusade force, counts 0); "ancestor" instance tests check the
    bearer (its BSData ID, name or keywords; faction keywords hold); "primary-catalogue" the
    army's faction. Conditions it can't evaluate don't hold, so they never hide an enhancement.
    """
    test, scope, value = condition.get('test'), condition.get('scope'), condition.get('value') or 0
    if test in ('instanceOf', 'notInstanceOf'):
        if scope == 'primary-catalogue':
            found = condition.get('id') == context['catalogue']
        elif scope in ('ancestor', 'self', 'parent'):
            name = (condition.get('name') or '').lower()
            found = (condition.get('id') in context['bearer_ids'] or name in context['bearer_names']
                     or name.startswith('faction:'))
        else:
            return False
        return found if test == 'instanceOf' else not found
    if scope in ('roster', 'force') and test in COMPARE:
        count = 1 if condition.get('id') in context['detachments'] else 0
        return COMPARE[test](count, value)
    return False


def _tree_holds(node, context):
    if 'items' not in node:
        return _condition_holds(node, context)
    results = [_tree_holds(item, context) for item in node['items']]
    if not results:
        return False
    return any(results) if node.get('op') == 'or' else all(results)


def enhancement_hidden(enhancement, bearer, detachment_keys, catalogue_id, keywords=()):
    """
    True if BSData hides `enhancement` for this bearer (a Unit) with these detachments chosen;
    `keywords`: those its chosen wargear adds (a Daemon Prince's allegiance).
    """
    context = {'detachments': set(detachment_keys), 'catalogue': catalogue_id, 'bearer_ids': {bearer.bsdata_id},
               'bearer_names': {bearer.name.lower()} | {k.lower() for k in list(bearer.keywords or []) + list(keywords)}}
    return any(_tree_holds(tree, context) for tree in enhancement.hidden_when or [])


def enhancement_allowed(enhancement, bearer, detachment_keys, catalogue_id, keywords=()):
    """
    (allowed, reason) for giving `enhancement` to `bearer` (a Unit), core rules and BSData's
    conditions; `keywords`: those the bearer's chosen wargear adds.
    """
    if enhancement.unit_bsdata_id:  # An Upgrade: its own unit only
        if enhancement.unit_bsdata_id != bearer.bsdata_id:
            return False, f'{enhancement.name} is an Upgrade for another unit.'
    elif enhancement.bearer_bsdata_ids:  # For given units, not Characters (Warptide's Battleline enhancements)
        if bearer.bsdata_id not in enhancement.bearer_bsdata_ids:
            return False, f"{enhancement.name} isn't for {bearer.name}."
    else:
        if not has_keyword(bearer, 'Character'):
            return False, 'Only Characters can be given enhancements.'
        if has_keyword(bearer, 'Epic Hero'):
            return False, "Epic Heroes can't be given enhancements."
    if enhancement.detachment_keys and not set(enhancement.detachment_keys) & set(detachment_keys):
        return False, f'{enhancement.name} needs its detachment.'
    if enhancement_hidden(enhancement, bearer, detachment_keys, catalogue_id, keywords):
        return False, f"{bearer.name} can't take {enhancement.name}."
    return True, None


# ---------------------------------------------------------------------------
# Pricing and checking
# ---------------------------------------------------------------------------

def _plus_points(points, extra):
    return points + extra if points is not None else None


def price_army(army):
    """
    The army priced and checked: {'units': [entry with 'name', 'points' (enhancement included),
    'unit_points', 'sizes', 'size_points', 'is_leader', 'is_support', 'is_character',
    'is_epic_hero', 'can_join', 'led_by', 'supported_by', 'enhancement_points',
    'enhancement_options', 'problems'], 'total', 'points_limit', 'problems', 'roster', 'faction',
    'detachments', 'detachment' (their names), 'dp_total', 'dp_limit', 'warlord',
    'enhancement_count', 'enhancement_limit'}. `roster` is what Analytics takes.
    """
    ids = {e['unit_id'] for e in army['units']}
    units = {u.id: u for u in Unit.query.filter(Unit.id.in_(ids))} if ids else {}
    faction = db.session.get(Faction, army['faction_id'])
    catalogue = faction.bsdata_id if faction else None
    detachments, problems = check_detachments(army, faction)
    chosen_keys = [d['key'] for d in detachments]
    faction_enhancements = Enhancement.query.filter_by(faction_id=army['faction_id']).order_by(Enhancement.name).all()
    enhancements = {e.bsdata_id: e for e in faction_enhancements}

    entries, copies = [], Counter()
    for e in army['units']:
        unit = units.get(e['unit_id'])
        entry = dict({'models': None, 'attached_to': None, 'enhancement': None, 'wargear': None}, **e,
                     name=unit.name if unit else 'Unknown unit', points=None, unit_points=None, sizes=[], size_points=[],
                     is_leader=False, is_support=False, is_character=False, is_epic_hero=False, can_join=[], join_roles={},
                     led_by=[],
                     supported_by=[], enhancement_points=0, enhancement_options=[], problems=[], has_wargear=False,
                     wargear_summary='', loadout=None, wargear_effects=None)
        entries.append(entry)
        if unit is None:
            entry['problems'].append('This unit is no longer in the data.')
            continue
        surcharge = unit.copy_surcharge(copies[unit.id])  # BSData's extra points for a later copy of the datasheet
        copies[unit.id] += 1
        entry['sizes'] = sizes_of(unit)
        entry['size_points'] = [_plus_points(unit.points_for(n), surcharge) for n in entry['sizes']]
        if not entry['models']:
            entry['models'] = entry['sizes'][0]
        if entry['models'] not in entry['sizes']:
            entry['problems'].append(f'{entry["models"]} models isn\'t a size this datasheet lists '
                                     f'({", ".join(map(str, entry["sizes"]))}).')
        entry['unit_points'] = _plus_points(unit.points_for(entry['models']), surcharge)
        entry['has_wargear'] = unit.wargear is not None
        if entry['wargear'] and unit.wargear is not None:  # Chosen options: BSData's rules price and size the unit
            chosen = unit_wargear(unit, entry['wargear'], entry['models'], catalogue)
            entry.update(models=chosen['models'], unit_points=_plus_points(chosen['points'], surcharge), wargear=chosen['choices'],
                         wargear_summary=chosen['summary'], loadout=chosen['loadout'], wargear_effects=chosen['effects'])
            entry['problems'] += chosen['problems']
        elif entry['wargear']:
            entry['wargear'] = None  # The unit has nothing to choose (any more)
        if entry['wargear_effects'] is None and unit.wargear is not None and wargear_engine.has_keywords(unit.wargear):
            # Keywords from BSData's default options (a Daemon Prince's first allegiance until one is chosen)
            entry['wargear_effects'] = unit_wargear(unit, None, entry['models'], catalogue)['effects']
        keywords = (entry['wargear_effects'] or {}).get('keywords', [])
        if entry['unit_points'] is None:
            entry['problems'].append('This unit has no points.')
        if faction and unit.faction_id != faction.id:
            entry['problems'].append(f'Not a {faction.name} unit.')
        entry.update(is_leader=bool(unit.can_lead), is_support=bool(unit.can_support),
                     is_character=has_keyword(unit, 'Character'), is_epic_hero=has_keyword(unit, 'Epic Hero'))
        entry['enhancement_options'] = [
            {'key': x.bsdata_id, 'name': x.name, 'points': x.points, 'upgrade': bool(x.unit_bsdata_id)}
            for x in faction_enhancements if enhancement_allowed(x, unit, chosen_keys, catalogue, keywords)[0]]
        chosen = enhancements.get(entry['enhancement']) if entry['enhancement'] else None
        if entry['enhancement'] and chosen is None:
            entry['problems'].append('Its enhancement is no longer in the data.')
        elif chosen is not None:
            entry['enhancement_points'] = chosen.points
            allowed, reason = enhancement_allowed(chosen, unit, chosen_keys, catalogue, keywords)
            if not allowed:
                entry['problems'].append(reason)
            if chosen.bsdata_id not in {o['key'] for o in entry['enhancement_options']}:
                entry['enhancement_options'].append({'key': chosen.bsdata_id, 'name': chosen.name, 'points': chosen.points,
                                                     'upgrade': bool(chosen.unit_bsdata_id)})
        entry['points'] = (entry['unit_points'] or 0) + entry['enhancement_points'] if entry['unit_points'] is not None else None

    check_attachments(entries, units)
    problems += check_copies(entries, units)
    warlord, warlord_problems = check_warlord(army, entries)
    problems += warlord_problems
    disposition, options, disposition_problems = check_disposition(army, detachments)
    problems += disposition_problems
    count, enhancement_problems = check_enhancements(entries, enhancements, army['points_limit'])
    problems += enhancement_problems

    total = sum(e['points'] or 0 for e in entries)
    limit = army['points_limit']
    if limit and total > limit:
        problems.insert(0, f'{total} points is {total - limit} over the {limit} point limit.')
    for entry in entries:
        problems += [f'{entry["name"]}: {p}' for p in entry['problems']]
    return {'units': entries, 'total': total, 'points_limit': limit, 'problems': problems,
            'roster': roster(entries, enhancements), 'faction': faction.name if faction else None,
            'detachments': detachments, 'detachment': ', '.join(d['name'] for d in detachments) or None,
            'dp_total': sum(d['dp'] or 0 for d in detachments), 'dp_limit': dp_limit(limit), 'warlord': warlord,
            'enhancement_count': count, 'enhancement_limit': enhancement_limit(limit),
            'disposition': disposition, 'disposition_options': options,
            'disposition_unconfirmed': not army.get('disposition') and len(options) > 1}


def check_attachments(entries, units):
    """Leaders and support units joining other units: who can join what, one of each per unit."""
    by_uid = {e['uid']: e for e in entries}
    for entry in entries:
        unit = units.get(entry['unit_id'])
        if unit is not None:
            names = set(unit.can_lead or []) | set(unit.can_support or [])
            entry['can_join'] = [other['uid'] for other in entries if other is not entry and other['name'] in names]
            entry['join_roles'] = {uid: 'leads' if by_uid[uid]['name'] in (unit.can_lead or []) else 'supports'
                                   for uid in entry['can_join']}
        target = by_uid.get(entry['attached_to']) if entry['attached_to'] else None
        if entry['attached_to'] and target is None:
            entry['attached_to'] = None  # Its unit was removed
            continue
        if target is None:
            continue
        if not (entry['is_leader'] or entry['is_support']):
            entry['problems'].append('Only leaders and support units can join another unit.')
            continue
        leads = unit is not None and target['name'] in (unit.can_lead or [])
        supports = unit is not None and target['name'] in (unit.can_support or [])
        if not (leads or supports):
            entry['problems'].append(f"Can't join {target['name']}.")
        role = 'led_by' if leads or not supports else 'supported_by'
        if target[role]:
            kind = 'leader' if role == 'led_by' else 'support unit'
            entry['problems'].append(f'{target["name"]} already has a {kind} (one each, unless a datasheet says otherwise).')
        target[role].append(entry['uid'])


def check_copies(entries, units):
    """Copies of each datasheet against its limit (rule of three, six for Battleline...)."""
    problems = []
    for unit_id, count in Counter(e['unit_id'] for e in entries).items():
        unit = units.get(unit_id)
        if unit is not None and count > copies_allowed(unit):
            problems.append(f'{unit.name}: {count} in the army; at most {copies_allowed(unit)}.')
    return problems


def check_warlord(army, entries):
    """(the Warlord's uid or None, problems): one of the army's Characters."""
    by_uid = {e['uid']: e for e in entries}
    warlord = army.get('warlord') if army.get('warlord') in by_uid else None
    if warlord and not by_uid[warlord]['is_character']:
        return warlord, [f'{by_uid[warlord]["name"]} can\'t be the Warlord: choose a Character.']
    if warlord is None and any(e['is_character'] for e in entries):
        return None, ['Choose a Warlord (one of your Characters).']
    return warlord, []


def check_disposition(army, detachments):
    """(the force disposition, the ones its detachments offer, problems): one of those offered; when none
    is chosen yet (lists saved before dispositions), the first: the page asks to confirm it when there are several."""
    options = list(dict.fromkeys(d for detachment in detachments for d in detachment['dispositions']))
    chosen = army.get('disposition')
    if chosen and chosen not in options:
        return None, options, [f"{chosen} isn't a force disposition of this army's detachments."]
    return chosen or (options[0] if options else None), options, []


def check_enhancements(entries, enhancements, points_limit):
    """
    (enhancements counting towards the limit, problems): the limit, each once (or as many as BSData
    allows: Bane-forged weapons up to three, counting as one), Upgrades up to three.
    """
    problems = []
    taken = Counter(e['enhancement'] for e in entries if e['enhancement'] in enhancements)
    count = sum(1 if enhancements[key].counts_once else n for key, n in taken.items() if not enhancements[key].unit_bsdata_id)
    limit = enhancement_limit(points_limit)
    if count > limit:
        problems.append(f'{count} enhancements; the limit is {limit}.')
    for key, n in taken.items():
        enhancement = enhancements[key]
        most = MAX_SAME_UPGRADE if enhancement.unit_bsdata_id else (enhancement.max_taken or 1)
        if n > most:
            problems.append(f'{enhancement.name} is taken {n} times; at most {most}.')
    return count, problems


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


def roster(entries, enhancements=None):
    """
    Analytics' units: [{'unit_id', 'models', 'leader_id', 'enhancements': [{'id', 'name', 'bearer', 'unit_id'}],
    'loadout', 'leader_loadout'}] (a loadout: chosen wargear's weapons and counts, None = defaults), one
    per unit that isn't joining another, with the first leader attached to it (Analytics joins one
    leader to a unit; a second leader and support units take the field on their own). An
    enhancement's `bearer` is 'unit' when the unit itself carries it, 'leader' when its leader does
    (`unit_id` is the bearer's).
    """
    enhancements = enhancements or {}
    by_uid = {e['uid']: e for e in entries}

    def carried(entry, bearer):
        chosen = enhancements.get(entry.get('enhancement'))
        return [{'id': chosen.id, 'name': chosen.name, 'bearer': bearer, 'unit_id': entry['unit_id']}] if chosen is not None else []

    attached, out = set(), []
    for entry in entries:
        if entry['attached_to'] in by_uid and entry['uid'] in by_uid[entry['attached_to']]['led_by']:
            continue  # Leading another unit: part of that one
        leader = next((by_uid[u] for u in entry['led_by'] if u not in attached), None)
        if leader:
            attached.add(leader['uid'])
        out.append({'unit_id': entry['unit_id'], 'models': entry['models'], 'leader_id': leader['unit_id'] if leader else None,
                    'enhancements': carried(entry, 'unit') + (carried(leader, 'leader') if leader else []),
                    'loadout': entry.get('loadout'), 'leader_loadout': leader.get('loadout') if leader else None})
    # A second leader on the same unit takes the field on its own
    out += [{'unit_id': e['unit_id'], 'models': e['models'], 'leader_id': None, 'enhancements': carried(e, 'unit'),
             'loadout': e.get('loadout'), 'leader_loadout': None}
            for e in entries if e['attached_to'] in by_uid and e['uid'] in by_uid[e['attached_to']]['led_by']
            and e['uid'] not in attached]
    return out


def army_text(army, priced):
    """The army as plain text, to paste into a message."""
    limit = f' / {priced["points_limit"]}' if priced['points_limit'] else ''
    dp = f' ({priced["dp_total"]} DP)' if priced['detachments'] else ''
    lines = [army['name'], ' - '.join(x for x in (priced['faction'], priced['detachment']) if x) + dp,
             f'{priced["total"]}{limit} points', '']
    by_uid = {e['uid']: e for e in priced['units']}
    names = {o['key']: o['name'] for e in priced['units'] for o in e['enhancement_options']}

    def line(entry, prefix=''):
        size = f' ({entry["models"]} models)' if entry['models'] and entry['models'] > 1 else ''
        points = f' - {entry["points"]} pts' if entry['points'] is not None else ''
        extras = [x for x in ('Warlord' if entry['uid'] == priced['warlord'] else None,
                              names.get(entry['enhancement']), entry.get('wargear_summary')) if x]
        return f'{prefix}{entry["name"]}{size}{points}' + (f' [{", ".join(extras)}]' if extras else '')

    for entry in priced['units']:
        if entry['attached_to'] in by_uid:
            continue
        lines.append(line(entry))
        lines += [line(by_uid[u], '  + led by ') for u in entry['led_by']]
        lines += [line(by_uid[u], '  + supported by ') for u in entry['supported_by']]
    if priced['problems']:
        lines += ['', 'Problems:'] + [f'- {p}' for p in priced['problems']]
    return '\n'.join(lines).strip() + '\n'
