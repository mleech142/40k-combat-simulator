"""
Wargear (wargear.py and its use in army lists): BSData's option trees rebuilt into selections,
fitted to a size, priced, checked and turned into loadouts; the popup's endpoint; Analytics'
loadouts. Built on the made-up BSData fixtures (Void Warriors: a sergeant's melee weapon, a
heavy laser for one warrior; Star Captain: an optional plasma pistol).
"""

import pytest

from combat_sim import bsdata, wargear
from conftest import BSDATA_FIXTURES

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}


@pytest.fixture(scope='module')
def imported():
    return bsdata.import_folder(BSDATA_FIXTURES)


def unit(imported, name):
    return next(u for u in imported.data['units'] if u['name'] == name)


def member(result, name):
    """The `k` of an option in the popup's form, by name."""
    return next(m['k'] for section in result['form'] for m in section['members'] if m['name'] == name)


def weapons(imported, result):
    names = {(kind, w['id']): w['name'] for kind in ('ranged', 'melee') for w in imported.data[f'{kind}_weapons']}
    return {names[(kind, weapon_id)]: qty for kind, weapon_id, qty in result['loadout']}


def test_only_units_with_choices_keep_a_tree(imported):
    assert unit(imported, 'Void Warriors')['wargear'] is not None
    assert unit(imported, 'Star Captain')['wargear'] is not None  # An optional pistol
    assert unit(imported, 'Scout Drone')['wargear'] is None  # Nothing to choose


def test_defaults_match_the_importers_points_and_loadout(imported):
    void = unit(imported, 'Void Warriors')
    for size in void['points_by_size']:
        result = wargear.evaluate(void['wargear'], None, size['models'], 'cat-star')
        assert (result['models'], result['points'], result['problems']) == (size['models'], size['points'], [])
    defaults = wargear.evaluate(void['wargear'], None, 5, 'cat-star')
    assert weapons(imported, defaults) == {'Laser pistol': 1, 'Blade': 1, 'Laser rifle - standard': 4,
                                           'Laser rifle - overcharge': 4, 'Close combat weapon': 4}
    assert defaults['summary'] == ''


def test_choices_change_points_loadout_and_summary(imported):
    tree = unit(imported, 'Void Warriors')['wargear']
    defaults = wargear.evaluate(tree, None, 5, 'cat-star')
    axe = wargear.evaluate(tree, {member(defaults, 'Power axe'): 1, member(defaults, 'Blade'): 0}, 5, 'cat-star')
    assert axe['points'] == 85 and axe['problems'] == []  # The axe costs 5
    assert 'Power axe' in weapons(imported, axe) and 'Blade' not in weapons(imported, axe)
    assert axe['summary'] == 'Power axe'


def test_a_variant_takes_the_place_of_a_default_model(imported):
    tree = unit(imported, 'Void Warriors')['wargear']
    defaults = wargear.evaluate(tree, None, 5, 'cat-star')
    heavy, plain = member(defaults, 'Void Warrior w/ heavy laser'), member(defaults, 'Void Warrior')
    one = wargear.evaluate(tree, {heavy: 1, plain: 4}, 5, 'cat-star')  # 6 asked for: a plain warrior makes way
    assert one['models'] == 5 and one['choices'][plain] == 3 and one['choices'][heavy] == 1
    assert weapons(imported, one)['Heavy laser'] == 1 and one['problems'] == []
    assert one['summary'] == 'Void Warrior w/ heavy laser'

    bigger = wargear.evaluate(tree, one['choices'], 8, 'cat-star')  # Growing adds default (plain) warriors
    assert (bigger['models'], bigger['choices'][plain], bigger['choices'][heavy], bigger['points']) == (8, 6, 1, 160)


def test_limits_are_checked(imported):
    tree = unit(imported, 'Void Warriors')['wargear']
    defaults = wargear.evaluate(tree, None, 5, 'cat-star')
    two = wargear.evaluate(tree, {member(defaults, 'Void Warrior w/ heavy laser'): 2, member(defaults, 'Void Warrior'): 2},
                           5, 'cat-star')
    assert two['problems'] == ['Void Warrior w/ heavy laser: 2 chosen; at most 1.']
    both = wargear.evaluate(tree, {member(defaults, 'Power axe'): 1, member(defaults, 'Blade'): 1}, 5, 'cat-star')
    assert both['problems'] == ['Melee weapon: 2 chosen; at most 1.']


def test_the_popups_form(imported):
    result = wargear.evaluate(unit(imported, 'Void Warriors')['wargear'], None, 5, 'cat-star')
    sections = {s['title']: s for s in result['form']}
    melee = sections['Void Sergeant › Melee weapon']
    assert melee['kind'] == 'one' and [(m['name'], m['count'], m['default']) for m in melee['members']] == [
        ('Blade', 1, True), ('Power axe', 0, False)]
    warriors = sections['Warriors']
    assert (warriors['kind'], warriors['min'], warriors['max'], warriors['total']) == ('counts', 4, 9, 4)
    assert [(m['name'], m['max']) for m in warriors['members']] == [('Void Warrior', 9), ('Void Warrior w/ heavy laser', 1)]
    pistol = wargear.evaluate(unit(imported, 'Star Captain')['wargear'], None, 1, 'cat-star')['form']
    assert [(s['title'], [m['name'] for m in s['members']]) for s in pistol] == [('Pistol', ['Plasma pistol'])]


# --- In the app --------------------------------------------------------------------------------------

@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]


def ids(client):
    star = next(f['id'] for f in client.get('/api/data/factions').get_json() if f['name'] == 'Starfarers')
    return star, {u['name']: u for u in client.get(f'/api/units?faction_id={star}').get_json()}


def test_wargear_endpoint(client):
    star, units = ids(client)
    assert units['Void Warriors']['has_wargear'] and not units['Scout Drone']['has_wargear']
    assert 'wargear' not in client.get(f'/api/units/{units["Void Warriors"]["id"]}').get_json()  # The tree stays on the server
    result = client.post('/api/armies/wargear', json={'unit_id': units['Void Warriors']['id'], 'faction_id': star,
                                                      'models': 5}).get_json()
    assert (result['name'], result['points'], result['sizes'][0]) == ('Void Warriors', 80, 5)
    assert {w['name']: w['qty'] for w in result['loadout']}['Laser pistol'] == 1
    axe = next(m['k'] for s in result['form'] for m in s['members'] if m['name'] == 'Power axe')
    blade = next(m['k'] for s in result['form'] for m in s['members'] if m['name'] == 'Blade')
    chosen = client.post('/api/armies/wargear', json={'unit_id': units['Void Warriors']['id'], 'faction_id': star,
                                                      'models': 5, 'wargear': {axe: 1, blade: 0}}).get_json()
    assert chosen['points'] == 85 and chosen['wargear'][str(axe)] == 1
    assert client.post('/api/armies/wargear', json={'unit_id': units['Scout Drone']['id']}).status_code == 404
    assert client.post('/api/armies/wargear', json={'unit_id': units['Void Warriors']['id'], 'wargear': 'axe'}).status_code == 400


def test_army_entries_with_wargear(client):
    star, units = ids(client)
    form = client.post('/api/armies/wargear', json={'unit_id': units['Void Warriors']['id'], 'models': 5}).get_json()['form']
    k = {m['name']: m['k'] for s in form for m in s['members']}
    priced = client.post('/api/armies/price', json={'faction_id': star, 'units': [
        {'uid': 'w', 'unit_id': units['Void Warriors']['id'], 'models': 8,
         'wargear': {k['Power axe']: 1, k['Blade']: 0, k['Void Warrior w/ heavy laser']: 1, k['Void Warrior']: 3}}]}).get_json()
    entry = priced['units'][0]
    assert entry['has_wargear'] and (entry['models'], entry['unit_points']) == (8, 165)
    assert entry['wargear_summary'] == 'Power axe, Void Warrior w/ heavy laser'
    assert {w['name']: w['qty'] for w in entry['loadout']}['Heavy laser'] == 1
    assert priced['roster'][0]['loadout'] == entry['loadout'] and priced['problems'] == []

    plain = client.post('/api/armies/price', json={'faction_id': star, 'units': [
        {'unit_id': units['Void Warriors']['id']}]}).get_json()['units'][0]
    assert (plain['loadout'], plain['wargear'], plain['unit_points']) == (None, None, 80)  # Defaults: no tree needed


def test_an_allegiance_adds_its_keyword_for_enhancements(client):
    # Issue #10: a Daemon Prince's allegiance has no weapons or points, but its keyword decides its enhancements
    star, units = ids(client)
    herald = units['Star Herald']['id']
    form = client.post('/api/armies/wargear', json={'unit_id': herald, 'models': 1}).get_json()['form']
    allegiance = next(s for s in form if s['title'] == 'Allegiance')
    assert allegiance['kind'] == 'one' and [m['name'] for m in allegiance['members']] == ['Sun God', 'Moon God']
    k = {m['name']: m['k'] for m in allegiance['members']}

    def options(wargear):
        priced = client.post('/api/armies/price', json={'faction_id': star, 'detachment_keys': ['d-vanguard'], 'units': [
            {'uid': 'h', 'unit_id': herald, 'wargear': wargear}]}).get_json()
        return [o['name'] for o in priced['units'][0]['enhancement_options']], priced['units'][0]['wargear_summary']

    assert options({k['Sun God']: 1, k['Moon God']: 0}) == (['Sun Crown', 'Void Blade'], 'Sun God')
    assert options({k['Sun God']: 0, k['Moon God']: 1}) == (['Void Blade'], 'Moon God')
    assert options(None)[0] == ['Sun Crown', 'Void Blade']  # BSData's first option until one is chosen


def test_each_model_chooses_its_own_option(imported):
    # Issue #16: three Brutes are one model entry ×3; choosing a Brute shield gives it to one of them, not all three
    tree = unit(imported, 'Shield Brutes')['wargear']
    defaults = wargear.evaluate(tree, None, 3, 'cat-star')
    shield = next(s for s in defaults['form'] if s['title'] == 'Brute › Shield')
    assert (shield['kind'], shield['min'], shield['max']) == ('counts', 3, 3)
    assert [(m['name'], m['count'], m['max'], m['default']) for m in shield['members']] == [
        ('Slab shield', 3, 3, True), ('Brute shield', 0, 2, False)]  # At most 2 Brute shields in the unit
    k = {m['name']: m['k'] for m in shield['members']}

    one = wargear.evaluate(tree, {k['Brute shield']: 1}, 3, 'cat-star')
    assert {m['name']: m['count'] for s in one['form'] if s['title'] == 'Brute › Shield' for m in s['members']} == {
        'Slab shield': 2, 'Brute shield': 1}  # The rest keep the default
    assert (one['choices'][k['Slab shield']], one['choices'][k['Brute shield']]) == (2, 1)
    assert one['problems'] == [] and one['summary'] == 'Brute shield'
    assert weapons(imported, one) == {'Brute club': 3}
    assert one['effects']['stats'] == {}  # The datasheet shows the most numerous Brutes: still Slab shields
    assert {a[0] for a in one['effects']['abilities']} == {'Slab Shield', 'Brute Shield'}

    two = wargear.evaluate(tree, one['choices'] | {k['Brute shield']: 2}, 3, 'cat-star')
    assert two['effects']['stats'] == {'W': -1} and two['effects']['invulnerable_save'] == 4
    three = wargear.evaluate(tree, {k['Brute shield']: 3}, 3, 'cat-star')
    assert three['problems'] == ['Brute shield: 3 in the unit; at most 2.']
