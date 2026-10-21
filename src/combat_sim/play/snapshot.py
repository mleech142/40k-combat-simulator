"""
An army frozen into a game: plain data, read once from the database through the Armies page's
own pricing (armies.price_army), so a game never changes when the data or BSData is updated.
Needs an app context; everything else in the play package works on the plain data alone.
"""

from .. import armies
from ..models import Enhancement, Faction, MeleeWeapon, RangedWeapon, Unit, db

STATS = ('move', 'toughness', 'save', 'wounds', 'invulnerable_save', 'feel_no_pain', 'oc', 'leadership')
WEAPON_FIELDS = ('name', 'type', 'attacks', 'skill', 'strength', 'ap', 'damage', 'range', 'abilities')


def freeze_army(army):
    """
    An army (the Armies page's form: name, faction_id, units, warlord, ...) as
    {'name', 'faction', 'units': [{'uid', 'name', 'parts': [part, ...]}]}. A unit's first part is the
    unit itself ('bodyguard' when others join it, else 'unit'); its leaders and support units follow.
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
        parts = [_part(entry, role, catalogue, priced['warlord'])] + \
                [_part(e, r, catalogue, priced['warlord']) for e, r in attached]
        units.append({'uid': entry['uid'], 'name': entry['name'], 'parts': parts})
    return {'name': army['name'], 'faction': priced['faction'], 'units': units}


def _part(entry, role, catalogue, warlord):
    unit = db.session.get(Unit, entry['unit_id'])
    enhancement = Enhancement.query.filter_by(bsdata_id=entry['enhancement']).first() if entry['enhancement'] else None
    return {
        'uid': entry['uid'],
        'unit_id': unit.id,
        'bsdata_id': unit.bsdata_id,
        'name': unit.name,
        'role': role,
        'models': entry['models'],
        'stats': {name: getattr(unit, name) for name in STATS},
        'keywords': unit.target_keywords,
        'base': unit.base,
        'weapons': _weapons(unit, entry, catalogue),
        'leader_buffs': unit.effective_leader_buffs if role == 'leader' else [],
        'enhancement': {'name': enhancement.name, 'buffs': enhancement.buffs or []} if enhancement else None,
        'warlord': entry['uid'] == warlord,
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
