"""
Writes the hand-made BSData fixture set used by tests/test_bsdata.py to tests/fixtures/bsdata:
invented factions with the structure of the real BSData files. Run it after changing a fixture:

    python tests/fixtures/make_bsdata_fixtures.py
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / 'bsdata'
GS = 'gs-test'
PTS = '51b2-306e-1021-d207'
UNIT_T, RANGED_T, MELEE_T, ABIL_T = 'pt-unit', 'pt-ranged', 'pt-melee', 'pt-abil'


def cat_link(cid, name):
    return {'name': name, 'id': f'cl-{cid}', 'primary': False, 'targetId': cid}


def pts(value):
    return [{'name': 'pts', 'typeId': PTS, 'value': value}]


_ids = iter(range(1, 10000))


def cons(lo=None, hi=None, scope='parent'):
    out = []
    if lo is not None:
        out.append({'id': f'c-{next(_ids)}', 'field': 'selections', 'scope': scope, 'shared': True,
                    'type': 'min', 'value': lo})
    if hi is not None:
        out.append({'id': f'c-{next(_ids)}', 'field': 'selections', 'scope': scope, 'shared': True,
                    'type': 'max', 'value': hi})
    return out


def unit_profile(pid, name, m, t, sv, w, ld, oc, insv=''):
    chars = [('M', m), ('T', t), ('Sv', sv), ('W', w), ('LD', ld), ('OC', oc), ('InSv', insv)]
    return {'name': name, 'id': pid, 'hidden': False, 'typeId': UNIT_T, 'typeName': 'Unit',
            'characteristics': [{'name': n, '$text': v} for n, v in chars]}


def weapon(pid, name, kind, rng, a, skill, s, ap, d, keywords):
    skill_name = 'BS' if kind == 'ranged' else 'WS'
    chars = [('Range', rng), ('A', a), (skill_name, skill), ('S', s), ('AP', ap), ('D', d), ('Keywords', keywords)]
    return {'name': name, 'id': pid, 'hidden': False,
            'typeId': RANGED_T if kind == 'ranged' else MELEE_T,
            'typeName': 'Ranged Weapons' if kind == 'ranged' else 'Melee Weapons',
            'characteristics': [{'name': n, '$text': v} for n, v in chars]}


def ability(pid, name, text):
    return {'name': name, 'id': pid, 'hidden': False, 'typeId': ABIL_T, 'typeName': 'Abilities',
            'characteristics': [{'name': 'Description', '$text': text}]}


def upgrade(eid, name, profiles=(), constraints=(), costs=None, **extra):
    node = {'name': name, 'id': eid, 'hidden': False, 'type': 'upgrade', 'profiles': list(profiles),
            'constraints': list(constraints)}
    if costs is not None:
        node['costs'] = pts(costs)
    node.update(extra)
    return node


def link(lid, name, target, constraints=(), kind='selectionEntry'):
    return {'name': name, 'id': lid, 'hidden': False, 'targetId': target, 'type': kind,
            'constraints': list(constraints)}


def rule_link(lid, name, target, append=None):
    node = {'name': name, 'id': lid, 'hidden': False, 'targetId': target, 'type': 'rule'}
    if append:
        node['modifiers'] = [{'field': 'name', 'type': 'append', 'value': append}]
    return node


def model(eid, name, constraints=(), children=(), links=(), groups=(), profiles=(), costs=None):
    node = {'name': name, 'id': eid, 'hidden': False, 'type': 'model', 'constraints': list(constraints),
            'selectionEntries': list(children), 'entryLinks': list(links),
            'selectionEntryGroups': list(groups), 'profiles': list(profiles)}
    if costs is not None:
        node['costs'] = pts(costs)
    return node


def group(gid, name, constraints=(), children=(), links=(), default=None):
    node = {'name': name, 'id': gid, 'hidden': False, 'constraints': list(constraints),
            'selectionEntries': list(children), 'entryLinks': list(links)}
    if default:
        node['defaultSelectionEntryId'] = default
    return node


def write(name, content):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{name}.json').write_text(json.dumps(content, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')


# --- Game system ---------------------------------------------------------------

write('Test System', {'gameSystem': {
    'name': 'Test System', 'id': GS, 'battleScribeVersion': 2.03, 'revision': 7, 'type': 'gameSystem',
    'costTypes': [{'name': 'pts', 'id': PTS, 'defaultCostLimit': -1}],
    'profileTypes': [{'name': n, 'id': i} for n, i in
                     [('Unit', UNIT_T), ('Ranged Weapons', RANGED_T), ('Melee Weapons', MELEE_T), ('Abilities', ABIL_T)]],
    'sharedRules': [{'name': n, 'id': i, 'description': n} for n, i in
                    [('Feel No Pain', 'r-fnp'), ('Stealth', 'r-stealth'), ('Leader', 'r-leader'), ('Deep Strike', 'r-ds')]],
    'categoryEntries': [{'name': n, 'id': i} for n, i in
                        [('Infantry', 'k-inf'), ('Vehicle', 'k-veh'), ('Character', 'k-char'), ('Battleline', 'k-bl')]],
}})

# --- Starfarers: the faction under test ----------------------------------------

laser_pistol = upgrade('e-laser-pistol', 'Laser pistol', constraints=cons(1, 1), profiles=[
    weapon('p-laser-pistol', 'Laser pistol', 'ranged', '12"', '1', '3+', '4', '-1', '1', 'Pistol')])
laser_rifle = upgrade('e-laser-rifle', 'Laser rifle', constraints=cons(1, 1), profiles=[
    weapon('p-rifle-std', '➤ Laser rifle - standard', 'ranged', '24"', '2', '3+', '4', '0', '1', 'Rapid Fire 1'),
    weapon('p-rifle-over', '➤ Laser rifle - overcharge', 'ranged', '24"', '2', '3+', '7', '-2', '2', 'Hazardous')])
plasma_pistol = upgrade('e-plasma-pistol', 'Plasma pistol', profiles=[
    weapon('p-plasma-pistol', 'Plasma pistol', 'ranged', '12"', '1', '3+', '7', '-2', '1', 'Pistol')])
ccw = upgrade('e-ccw', 'Close combat weapon', constraints=cons(1, 1), profiles=[
    weapon('p-ccw', 'Close combat weapon', 'melee', 'Melee', '1', '4+', '4', '0', '1', None)])

void_warriors = {
    'name': 'Void Warriors', 'id': 'u-void', 'hidden': False, 'type': 'unit',
    'categoryLinks': [cat_link('k-faction-star', 'Faction: Starfarers'), cat_link('k-inf', 'Infantry'),
                      cat_link('k-bl', 'Battleline')],
    'profiles': [unit_profile('p-void', 'Void Warrior', '6"', '4', '3+', '2', '6+', '2'),
                 ability('a-void', 'Shield Wall', 'Models in this unit can do things.')],
    'infoLinks': [rule_link('il-void-stealth', 'Stealth', 'r-stealth')],
    'costs': pts(80),
    'modifiers': [
        # 80 points for 5 models, 160 for more
        {'field': PTS, 'type': 'set', 'value': 160,
         'conditions': [{'childId': 'model', 'field': 'selections', 'includeChildSelections': True,
                         'scope': 'u-void', 'shared': True, 'type': 'greaterThan', 'value': 5}]},
        # The surcharge for a third copy in an army: never applies to a lone unit
        {'field': PTS, 'type': 'increment', 'value': 10, 'conditionGroups': [{'type': 'and', 'localConditionGroups': [
            {'field': 'selections', 'includeChildSelections': True, 'repeats': 1, 'scope': 'parent',
             'type': 'atLeast', 'value': 2, 'conditions': [
                 {'childId': 'any', 'field': 'selections', 'scope': 'self', 'shared': True, 'type': 'before', 'value': 1},
                 {'childId': 'u-void', 'field': 'selections', 'scope': 'self', 'shared': True, 'type': 'instanceOf', 'value': 1}]}]}]},
    ],
    'selectionEntries': [
        model('m-void-sgt', 'Void Sergeant', constraints=cons(1, 1),
              links=[link('l-sgt-pistol', 'Laser pistol', 'e-laser-pistol')],
              groups=[group('g-sgt-melee', 'Melee weapon', constraints=cons(1, 1), default='e-blade', children=[
                  upgrade('e-blade', 'Blade', profiles=[
                      weapon('p-blade', 'Blade', 'melee', 'Melee', '3', '3+', '5', '-1', '1', 'Lethal Hits')]),
                  upgrade('e-axe', 'Power axe', costs=5, profiles=[
                      weapon('p-axe', 'Power axe', 'melee', 'Melee', '3', '3+', '6', '-2', '1', None)]),
              ])]),
    ],
    'selectionEntryGroups': [
        group('g-warriors', 'Warriors', constraints=cons(4, 9), default='m-void-warrior', children=[
            model('m-void-warrior', 'Void Warrior', constraints=cons(None, 9),
                  links=[link('l-war-rifle', 'Laser rifle', 'e-laser-rifle'), link('l-war-ccw', 'Close combat weapon', 'e-ccw')]),
            model('m-void-heavy', 'Void Warrior w/ heavy laser', constraints=cons(None, 1),
                  children=[upgrade('e-heavy', 'Heavy laser', constraints=cons(1, 1), profiles=[
                      weapon('p-heavy', 'Heavy laser', 'ranged', '36"', '1', '4+', '9', '-3', 'D6', 'Heavy, Anti-Vehicle 4+, Laser Burn')])],
                  links=[link('l-heavy-ccw', 'Close combat weapon', 'e-ccw')]),
        ]),
    ],
}

star_captain = {
    'name': 'Star Captain', 'id': 'u-captain', 'hidden': False, 'type': 'model',
    'categoryLinks': [cat_link('k-faction-star', 'Faction: Starfarers'), cat_link('k-inf', 'Infantry'),
                      cat_link('k-char', 'Character')],
    'profiles': [unit_profile('p-captain', 'Star Captain', '6"', '4', '2+', '5', '6+', '1', '4+'),
                 ability('a-captain-leader', 'Leader',
                         'This model can be attached to the following units:\n■ **^^VOID WARRIORS^^**\n■ Missing Squad'),
                 ability('a-captain-fnp', 'Iron Will', 'This model has the Feel No Pain 5+ ability.')],
    'infoLinks': [rule_link('il-captain-leader', 'Leader', 'r-leader')],
    'costs': pts(70),
    'selectionEntries': [upgrade('e-master-blade', 'Master blade', constraints=cons(1, 1), profiles=[
        weapon('p-master-blade', 'Master blade', 'melee', 'Melee', '5', '2+', '6', '-2', '2', 'Sustained Hits 1')])],
    # An option: not taken by default
    'selectionEntryGroups': [group('g-captain-pistol', 'Pistol', constraints=cons(None, 1), links=[
        link('l-captain-pistol', 'Plasma pistol', 'e-plasma-pistol')])],
}

star_walker = {
    'name': 'Star Walker [Legends]', 'id': 'u-walker', 'hidden': False, 'type': 'model',
    'categoryLinks': [cat_link('k-faction-star', 'Faction: Starfarers'), cat_link('k-veh', 'Vehicle')],
    'profiles': [unit_profile('p-walker', 'Star Walker', '8"', '9', '3+', '10', '7+', '3')],
    'infoLinks': [rule_link('il-walker-fnp', 'Feel No Pain', 'r-fnp', append='6+'),
                  rule_link('il-walker-ds', 'Deep Strike', 'r-ds')],
    'costs': pts(120),
    'selectionEntries': [
        upgrade('e-flamer', 'Walker flamer', constraints=cons(1, 1), profiles=[
            weapon('p-flamer', 'Walker flamer', 'ranged', '12"', 'D6', 'N/A', '5', '-1', '1', 'Ignores Cover, Torrent')]),
        upgrade('e-vortex', 'Vortex charge', constraints=cons(1, 1), profiles=[
            weapon('p-vortex', 'Vortex charge', 'ranged', '6"', '1', '2+', '*', '*', '*', None)]),
        upgrade('e-feet', 'Armoured feet', constraints=cons(1, 1), profiles=[
            weapon('p-feet', 'Armoured feet', 'melee', 'Melee', '4', '4+', '7', '0', '1', '-')]),
    ],
}

beast_pack = {
    'name': 'Beast Pack', 'id': 'u-beasts', 'hidden': False, 'type': 'unit',
    'categoryLinks': [cat_link('k-faction-star', 'Faction: Starfarers'), cat_link('k-inf', 'Infantry')],
    'costs': pts(60),
    'modifiers': [{'field': PTS, 'type': 'set', 'value': 999,
                   'conditions': [{'childId': 'u-beasts', 'field': 'selections', 'scope': 'roster', 'shared': True,
                                   'type': 'someUnknownCheck', 'value': 1}]}],
    'selectionEntries': [
        model('m-beastmaster', 'Beast Master', constraints=cons(1, 1),
              profiles=[unit_profile('p-beastmaster', 'Beast Master', '6"', '3', '5+', '1', '7+', '1')],
              links=[link('l-bm-ccw', 'Close combat weapon', 'e-ccw')]),
    ],
    'selectionEntryGroups': [group('g-beasts', 'Beasts', constraints=cons(3, 3), default='m-beast', children=[
        model('m-beast', 'Beast', profiles=[unit_profile('p-beast', 'Beast', '10"', '5', '6+', '2', '8+', '0')],
              children=[upgrade('e-claws', 'Claws', constraints=cons(1, 1), profiles=[
                  weapon('p-claws', 'Claws', 'melee', 'Melee', '3', '4+', '5', '-1', '1', None)])],
              costs=0)])],
}

# Points from per-model repeats, modifier groups, and the faction the unit is taken in
gun_crew = {
    'name': 'Gun Crew', 'id': 'u-crew', 'hidden': False, 'type': 'unit',
    'categoryLinks': [cat_link('k-faction-star', 'Faction: Starfarers'), cat_link('k-inf', 'Infantry')],
    'profiles': [unit_profile('p-crew', 'Gunner', '5"', '3', '5+', '1', '7+', '1')],
    'costs': pts(40),
    'modifiers': [
        {'field': PTS, 'type': 'decrement', 'value': 5, 'conditions': [
            {'childId': 'cat-allies', 'field': 'selections', 'scope': 'primary-catalogue', 'shared': True,
             'type': 'notInstanceOf', 'value': 1}]},
        {'field': PTS, 'type': 'increment', 'value': 100, 'conditions': [
            {'childId': 'cat-allies', 'field': 'selections', 'scope': 'primary-catalogue', 'shared': True,
             'type': 'instanceOf', 'value': 1}]},
    ],
    'modifierGroups': [{'type': 'and', 'modifiers': [
        {'field': PTS, 'type': 'increment', 'value': 15, 'repeats': [
            {'childId': 'm-gunner', 'field': 'selections', 'includeChildSelections': True, 'repeats': 1,
             'roundUp': False, 'scope': 'u-crew', 'shared': True, 'value': 1}]},
        # Counts the selections in a group, as BSData does for squads
        {'field': PTS, 'type': 'set', 'value': 150, 'conditions': [
            {'childId': 'g-crew', 'field': 'selections', 'includeChildSelections': True, 'scope': 'u-crew',
             'shared': True, 'type': 'atLeast', 'value': 4}]},
    ]}],
    'selectionEntryGroups': [group('g-crew', 'Crew', constraints=cons(2, 4), default='m-gunner', children=[
        model('m-gunner', 'Gunner', constraints=cons(None, 4), children=[
            upgrade('e-crew-gun', 'Crew gun', constraints=cons(1, 1), profiles=[
                weapon('p-crew-gun', 'Crew gun', 'ranged', '36"', '2', '4+', '8', '-2', '3', None)])])])],
}

def detachment_entry(eid, gid, options):
    """A root "Detachment" entry: a group whose options are the detachments, each with its rule."""
    return {'name': 'Detachment', 'id': eid, 'hidden': False, 'type': 'upgrade',
            'selectionEntryGroups': [group(gid, 'Detachment', constraints=cons(1, 1), children=[
                upgrade(oid, name, rules=[{'name': rule, 'id': f'{oid}-rule', 'hidden': False, 'description': text}])
                for oid, name, rule, text in options])]}


star_detachments = detachment_entry('e-star-det', 'g-star-det', [
    ('d-vanguard', 'Void Vanguard', 'Swift Strike', 'Units from this army can Advance and shoot.'),
    ('d-bastion', 'Star Bastion', 'Hold Fast', 'Units on objectives get +1 to their saves.'),
])

write('Xenos - Starfarers', {'catalogue': {
    'name': 'Xenos - Starfarers', 'id': 'cat-star', 'battleScribeVersion': 2.03, 'revision': 3,
    'gameSystemId': GS, 'gameSystemRevision': 7, 'library': False, 'type': 'catalogue',
    'catalogueLinks': [
        {'name': 'Starfarers Library', 'id': 'cl-star-lib', 'targetId': 'cat-star-lib', 'type': 'catalogue',
         'importRootEntries': True},
        {'name': 'Imperium - Allies', 'id': 'cl-allies', 'targetId': 'cat-allies', 'type': 'catalogue'},
    ],
    'categoryEntries': [{'name': 'Faction: Starfarers', 'id': 'k-faction-star'}],
    'sharedRules': [{'name': 'Star Pact', 'id': 'r-star-pact', 'hidden': False,
                     'description': 'Each time a unit from this army is selected to shoot, it can re-roll one Hit roll.'}],
    'sharedSelectionEntries': [laser_pistol, laser_rifle, plasma_pistol, ccw, void_warriors, star_captain, star_walker, beast_pack,
                               gun_crew, star_detachments],
    'entryLinks': [
        link('rl-star-det', 'Detachment', 'e-star-det'),
        link('rl-ally-det', 'Detachment', 'e-allies-det'),  # Another army's detachments: left out
        link('rl-void', 'Void Warriors', 'u-void'),
        link('rl-captain', 'Star Captain', 'u-captain'),
        link('rl-walker', 'Star Walker [Legends]', 'u-walker'),
        link('rl-beasts', 'Beast Pack', 'u-beasts'),
        link('rl-crew', 'Gun Crew', 'u-crew'),
        link('rl-agent', 'Ally Agent', 'u-agent'),  # An ally from another army: left out
        link('rl-hidden', 'Hidden Thing', 'u-captain') | {'hidden': True},
    ],
}})

write('Xenos - Starfarers Library', {'catalogue': {
    'name': 'Xenos - Starfarers Library', 'id': 'cat-star-lib', 'battleScribeVersion': 2.03, 'revision': 2,
    'gameSystemId': GS, 'gameSystemRevision': 7, 'library': True, 'type': 'catalogue',
    'sharedSelectionEntries': [
        {'name': 'Scout Drone', 'id': 'u-drone', 'hidden': False, 'type': 'model',
         'categoryLinks': [cat_link('k-faction-star', 'Faction: Starfarers'), cat_link('k-veh', 'Vehicle')],
         'profiles': [unit_profile('p-drone', 'Scout Drone', '12"', '4', '4+', '3', '8+', '0'),
                      ability('a-drone', 'Flicker Field', 'Each time an attack is allocated to this model, subtract 1 '
                                                         'from the Damage characteristic of that attack.')],
         'costs': pts(30),
         'selectionEntries': [upgrade('e-drone-gun', 'Drone gun', constraints=cons(1, 1), profiles=[
             weapon('p-drone-gun', 'Drone gun', 'ranged', '18"', '3', '4+', '4', '0', '1', 'Assault')])]},
        {'name': 'Field Bunker', 'id': 'u-bunker', 'hidden': False, 'type': 'model',
         'categoryLinks': [cat_link('k-veh', 'Vehicle')],
         'profiles': [unit_profile('p-bunker', 'Field Bunker', '-', '10', '3+', '15', '-', '0')],
         'costs': pts(100)},
    ],
    'entryLinks': [link('rl-drone', 'Scout Drone', 'u-drone'), link('rl-bunker', 'Field Bunker', 'u-bunker')],
}})

# --- Another army, a stub and a neutral library --------------------------------

write('Imperium - Allies', {'catalogue': {
    'name': 'Imperium - Allies', 'id': 'cat-allies', 'battleScribeVersion': 2.03, 'revision': 1,
    'gameSystemId': GS, 'gameSystemRevision': 7, 'library': False, 'type': 'catalogue',
    'sharedSelectionEntries': [
        {'name': 'Ally Agent', 'id': 'u-agent', 'hidden': False, 'type': 'model',
         'categoryLinks': [cat_link('k-faction-allies', 'Faction: Allies'), cat_link('k-inf', 'Infantry')],
         'profiles': [unit_profile('p-agent', 'Ally Agent', '6"', '3', '4+', '3', '6+', '1')],
         'costs': pts(50),
         'selectionEntries': [upgrade('e-agent-pistol', 'Laser pistol', constraints=cons(1, 1), profiles=[
             weapon('p-agent-pistol', 'Laser pistol', 'ranged', '12"', '1', '3+', '4', '-1', '1', 'Pistol')])]},
        detachment_entry('e-allies-det', 'g-allies-det', [
            ('d-task-force', 'Allied Task Force', 'Assigned', 'Agents can join any army.')]),
    ],
    'entryLinks': [link('rl-ally-agent', 'Ally Agent', 'u-agent'), link('rl-allies-det', 'Detachment', 'e-allies-det')],
}})

write('Imperium - Empty Stub', {'catalogue': {
    'name': 'Imperium - Empty Stub', 'id': 'cat-stub', 'battleScribeVersion': 2.03, 'revision': 1,
    'gameSystemId': GS, 'gameSystemRevision': 7, 'library': False, 'type': 'catalogue',
}})

write('Library - Neutral Forces', {'catalogue': {
    'name': 'Library - Neutral Forces', 'id': 'cat-neutral', 'battleScribeVersion': 2.03, 'revision': 4,
    'gameSystemId': GS, 'gameSystemRevision': 7, 'library': True, 'type': 'catalogue',
    'sharedSelectionEntries': [
        {'name': 'Neutral Wall', 'id': 'u-wall', 'hidden': False, 'type': 'model',
         'categoryLinks': [cat_link('k-veh', 'Vehicle')],
         'profiles': [unit_profile('p-wall', 'Neutral Wall', '-', '9', '4+', '12', '-', '0')],
         'costs': pts(40)},
    ],
    'entryLinks': [link('rl-wall', 'Neutral Wall', 'u-wall')],
}})
