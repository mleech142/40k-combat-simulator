"""
An army frozen into a game: plain data, read once from the database through the Armies page's
own pricing (armies.price_army), so a game never changes when the data or BSData is updated.
Needs an app context; everything else in the play package works on the plain data alone.
"""

from .. import armies, contagion
from ..engagement import unit_stats
from ..models import Detachment, Enhancement, Faction, FactionRule, MeleeWeapon, RangedWeapon, Stratagem, Unit, db
from ..rules_text import (ability_buffs, afflicts_after_shooting, contagion_bonus, damaged_threshold, deadly_demise, firing_deck,
                          granted_ability, has_core_ability, parse_transport, plain_text, scouts_distance, units_contagion_bonus)

STATS = ('move', 'toughness', 'save', 'wounds', 'invulnerable_save', 'feel_no_pain', 'oc', 'leadership')
WEAPON_FIELDS = ('name', 'type', 'attacks', 'skill', 'strength', 'ap', 'damage', 'range', 'abilities')


def freeze_army(army):
    """
    An army (the Armies page's form: name, faction_id, units, warlord, ...) as
    {'name', 'faction', 'disposition', 'units': [{'uid', 'name', 'parts': [part, ...]}]}. A unit's first part is the
    unit itself ('bodyguard' when others join it, else 'unit'); its leaders and support units follow. The chosen
    detachments' rules the game applies are frozen as data: 'contagion_bonuses' and 'grants' (_detachment_effects).
    """
    army = armies.clean_army(army)
    priced = armies.price_army(army)
    faction = db.session.get(Faction, army['faction_id'])
    catalogue = faction.bsdata_id if faction else None
    entries = [e for e in priced['units'] if e['problems'] != ['This unit is no longer in the data.']]
    by_uid = {e['uid']: e for e in entries}
    joined = {uid for e in entries for uid in e['led_by'] + e['supported_by']}

    units = []
    for entry in entries:
        if entry['uid'] in joined:
            continue
        attached = [(by_uid[uid], 'leader') for uid in entry['led_by']] + \
                   [(by_uid[uid], 'support') for uid in entry['supported_by']]
        role = 'bodyguard' if attached else 'unit'
        parts = [_part(entry, role, catalogue, priced['warlord'], army['faction_id'])] + \
                [_part(e, r, catalogue, priced['warlord'], army['faction_id']) for e, r in attached]
        units.append({'uid': entry['uid'], 'name': entry['name'], 'parts': parts})
    return {'name': army['name'], 'faction': priced['faction'], 'disposition': priced['disposition'], 'units': units,
            'points_limit': army.get('points_limit'),
            'stratagems': _stratagems(army), 'rules': _rules(army), **_detachment_effects(army)}


def _detachment_effects(army):
    """
    What the chosen detachments' rules (BSData) give that the game applies itself: 'contagion_bonuses'
    ([{'rule', 'keywords', 'inches'}]: Hypervirulent Strains) and 'grants' ([{'rule', 'ability', 'keywords',
    'count'}]: Verminous Haze). Only the data is frozen, never the text.
    """
    bonuses, grants = [], []
    keys = army.get('detachment_keys') or []
    for detachment in Detachment.query.filter(Detachment.bsdata_id.in_(keys)) if keys else []:
        for rule in detachment.rules or []:
            bonus = units_contagion_bonus(rule.get('text'))
            if bonus:
                bonuses.append({'rule': rule['name'], 'keywords': bonus[0], 'inches': bonus[1]})
            grant = granted_ability(rule.get('text'))
            if grant:
                grants.append(dict(grant, rule=rule['name']))
    return {'contagion_bonuses': bonuses, 'grants': grants}


def _offered(record, army):
    return not record.superseded and (not record.detachment_key or record.detachment_key in army['detachment_keys'])


def _entry(record, kind):
    """
    A stratagem or rule as the game uses it: its effect (side, phase, modifier text) if the app reads one, and
    `ref`, which finds its text on any PC (rule_texts). The text itself is never frozen into a game, so games
    passed between players and the relay carry no Wahapedia or rule text (docs/play-mode-design.md, Connection).
    """
    try:
        cp = int(str(record.cp or 0).strip()) if kind == 'stratagem' else 0
    except ValueError:
        cp = 0
    ref = {'wahapedia_id': record.wahapedia_id} if record.wahapedia_id else {'name': record.name}
    return {'key': f'{kind}:{record.id}', 'kind': kind, 'name': record.name, 'cp': cp,
            'applies_to': record.applies_to, 'phase': record.phase, 'modifier': record.modifier or '',
            'detachment': record.detachment_name, 'ref': ref}


def rule_texts(frozen):
    """
    {key: text} for the stratagems and rules in a game's frozen armies, read from this PC's data by each entry's
    `ref` (a Wahapedia ID, else its name in the army's faction or the core stratagems). Entries frozen before refs
    keep the text they were frozen with; ones this PC has no text for are left out.
    """
    texts, wanted = {}, []
    for army in frozen:
        for entry in army.get('stratagems', []) + army.get('rules', []):
            if entry.get('text'):
                texts[entry['key']] = entry['text']
            elif isinstance(entry.get('ref'), dict):
                wanted.append((army, entry))
    by_id = {kind: {} for kind in ('stratagem', 'rule')}
    for kind, model in (('stratagem', Stratagem), ('rule', FactionRule)):
        ids = {e['ref']['wahapedia_id'] for _, e in wanted if e['kind'] == kind and e['ref'].get('wahapedia_id')}
        if ids:
            by_id[kind] = {r.wahapedia_id: r for r in model.query.filter(model.wahapedia_id.in_(ids)) if not r.superseded}
    factions = {a.get('faction'): {f.id for f in Faction.query.filter_by(name=a.get('faction'))} for a, _ in wanted}
    for army, entry in wanted:
        model = Stratagem if entry['kind'] == 'stratagem' else FactionRule
        record = by_id[entry['kind']].get(entry['ref'].get('wahapedia_id'))
        if record is None and entry['ref'].get('name'):
            matches = [r for r in model.query.filter_by(name=entry['ref']['name']) if not r.superseded]
            record = next((r for r in matches if r.faction_id is None or r.faction_id in factions[army.get('faction')]), None)
        if record is not None and (record.text or record.notes):
            texts[entry['key']] = plain_text(record.text or record.notes)
    return texts


def _stratagems(army):
    """Core stratagems, and the faction's for the whole faction or a chosen detachment."""
    records = Stratagem.query.filter((Stratagem.faction_id.is_(None)) | (Stratagem.faction_id == army['faction_id']))
    return [_entry(r, 'stratagem') for r in records.order_by(Stratagem.name) if _offered(r, army)]


def _rules(army):
    """The faction's army rules and the chosen detachments' rules."""
    records = FactionRule.query.filter_by(faction_id=army['faction_id']).order_by(FactionRule.name)
    return [_entry(r, 'rule') for r in records if _offered(r, army)]


def _part(entry, role, catalogue, warlord, faction_id=None):
    unit = db.session.get(Unit, entry['unit_id'])
    enhancement = Enhancement.query.filter_by(bsdata_id=entry['enhancement']).first() if entry['enhancement'] else None
    stats = unit_stats(unit, entry.get('wargear_effects'))
    return {
        'uid': entry['uid'],
        'unit_id': unit.id,
        'bsdata_id': unit.bsdata_id,
        'name': unit.name,
        'role': role,
        'models': entry['models'],
        'points': entry['points'] or 0,  # Including its enhancement
        'stats': {name: stats[name] for name in STATS},  # With its chosen wargear's changes
        'keywords': unit.target_keywords,
        'base': unit.base,
        'weapons': _weapons(unit, entry, catalogue),
        'leader_buffs': unit.effective_leader_buffs if role == 'leader' else [],
        'enhancement': {'name': enhancement.name, 'buffs': enhancement.buffs or []} if enhancement else None,
        'warlord': entry['uid'] == warlord,
        'fights_first': has_core_ability(unit.abilities, 'Fights First'),
        'deep_strike': has_core_ability(unit.abilities, 'Deep Strike'),
        'infiltrators': has_core_ability(unit.abilities, 'Infiltrators'),
        'scouts': scouts_distance(unit.abilities),
        'transport': parse_transport(unit.abilities) if 'Transport' in (unit.keywords or []) else None,
        'firing_deck': firing_deck(unit.abilities),
        'deadly_demise': deadly_demise(unit.abilities),
        'damaged': damaged_threshold(unit.abilities),
        # Death Guard (contagion.py): Nurgle's Gift counts for a model of the army's own faction
        'contagion': has_core_ability(unit.abilities, contagion.RULE_NAME) and unit.faction_id == faction_id,
        'contagion_bonus': contagion_bonus(unit.abilities),
        'afflicts': afflicts_after_shooting(unit.abilities),
        'ability_buffs': ability_buffs(unit.abilities),  # Its own attacks' bonuses (so far only against Afflicted units)
    }


def _weapons(unit, entry, catalogue):
    """Each weapon the unit's models carry: its profile, how many models carry it, and whether it starts ticked."""
    if unit.wargear is not None:
        rows = armies.unit_wargear(unit, entry['wargear'], entry['models'], catalogue)['loadout']
        found = {}
        for kind, model in (('ranged', RangedWeapon), ('melee', MeleeWeapon)):
            ids = [r['weapon_id'] for r in rows if r['weapon_type'] == kind]
            found.update({(kind, w.id): w for w in model.query.filter(model.id.in_(ids))} if ids else {})
        carried = [(found[(r['weapon_type'], r['weapon_id'])], r['qty'], r['default_use']) for r in rows
                   if (r['weapon_type'], r['weapon_id']) in found]
    else:  # The datasheet loadout, scaled to the unit's size as Analytics does
        default = unit.default_models or entry['models']
        carried = [(weapon, max(1, round(qty * entry['models'] / default)), True)
                   for kind in ('ranged', 'melee') for weapon, qty in unit.loadout(kind)]
    out = []
    for weapon, qty, default_use in carried:
        data = weapon.to_dict()
        out.append(dict({k: data.get(k) for k in WEAPON_FIELDS}, qty=qty, default_use=default_use))
    return out
