"""
The Armies page's Match view (match_view.py, /armies/<id>/match): each unit's datasheet at its
size and wargear, the army's rules, enhancements and stratagems. Built on the made-up BSData
factions in tests/fixtures/bsdata and the made-up Wahapedia export in tests/fixtures/wahapedia.
"""

import pytest

from conftest import BSDATA_FIXTURES, WAHAPEDIA_FIXTURES
from combat_sim.match_view import split_abilities

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}


@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES, wahapedia=WAHAPEDIA_FIXTURES)[0]


def saved_match(client, **extra):
    factions = {f['name']: f['id'] for f in client.get('/api/data/factions').get_json()}
    units = {u['name']: u['id'] for u in client.get(f'/api/units?faction_id={factions["Starfarers"]}').get_json()}
    detachments = {d['name']: d['key'] for d in client.get(f'/api/detachments?faction_id={factions["Starfarers"]}').get_json()}
    body = dict({'name': 'Match army', 'faction_id': factions['Starfarers'], 'points_limit': 2000,
                 'detachment_keys': [detachments['Void Vanguard']], 'warlord': 'captain',
                 'units': [{'uid': 'warriors', 'unit_id': units['Void Warriors'], 'models': 10},
                           {'uid': 'captain', 'unit_id': units['Star Captain'], 'attached_to': 'warriors'},
                           {'uid': 'crew', 'unit_id': units['Gun Crew']}]}, **extra)
    saved = client.post('/api/armies', json=body).get_json()
    response = client.get(f'/api/armies/{saved["id"]}/match')
    assert response.status_code == 200, response.get_json()
    return saved['id'], response.get_json()


def test_split_abilities():
    text = 'Leader: This model can lead:\n■ **^^VOID WARRIORS^^**\n\nDamaged: 1-4 wounds remaining: subtract 1.\n\n' \
           'More of the same ability, on its own line.\n\nDeep Strike\n\nFeel No Pain 5+\n\nDeep Strike'
    abilities, rules = split_abilities(text)
    assert [a['name'] for a in abilities] == ['Leader', 'Damaged']
    assert abilities[1]['text'] == '1-4 wounds remaining: subtract 1.\n\nMore of the same ability, on its own line.'
    assert rules == ['Deep Strike', 'Feel No Pain 5+']
    assert split_abilities('') == ([], [])


def test_units_rules_and_stratagems(client):
    _, view = saved_match(client)
    assert view['name'] == 'Match army' and view['faction'] == 'Starfarers'
    sheets = {u['uid']: u for u in view['units']}
    assert view['groups'][0] == ['warriors', 'captain'] and ['crew'] in view['groups']
    warriors, captain = sheets['warriors'], sheets['captain']
    assert warriors['models'] == 10 and warriors['stats']['toughness'] and warriors['keywords']
    assert captain['warlord'] and captain['join_role'] == 'leads' and captain['attached_to'] == 'warriors'
    assert any(a['name'] == 'Leader' for a in captain['abilities'])
    assert all(w['qty'] >= 1 for kind in ('ranged', 'melee') for w in warriors['weapons'][kind])
    assert warriors['weapons']['ranged'] or warriors['weapons']['melee']

    assert [d['name'] for d in view['detachments']] == ['Void Vanguard']
    assert view['detachments'][0]['rules']
    names = {s['name']: s for s in view['stratagems']}
    assert 'Void Strike' in names and 'Lost Signal' not in names  # Another detachment's
    assert names['Void Strike']['category'] == 'Battle Tactic' and names['Void Strike']['turn'] == 'Your turn'
    assert names['Void Strike']['phases'] == ['Shooting phase'] and names['Void Strike']['detachment'] == 'Void Vanguard'
    assert names['Redeploy']['phases'] == ['Movement phase']  # Outside the Shooting and Fight phases: shown here all the same
    assert names['Command Re-Roll']['core'] and names['Command Re-Roll']['phases'] == []
    assert [s['core'] for s in view['stratagems']] == sorted(s['core'] for s in view['stratagems'])  # Faction's first


def test_page_and_missing_army(client):
    army_id, _ = saved_match(client)
    page = client.get(f'/armies/{army_id}/match')
    assert page.status_code == 200 and b'match.js' in page.data
    assert client.get('/armies/9999/match').status_code == 404
    assert client.get('/api/armies/9999/match').status_code == 404


def test_chosen_wargear_changes_the_datasheet(client):
    # Issue #13: a shield choice shows in the characteristics and abilities, not only the defaults' Slab shield
    factions = {f['name']: f['id'] for f in client.get('/api/data/factions').get_json()}
    units = {u['name']: u['id'] for u in client.get(f'/api/units?faction_id={factions["Starfarers"]}').get_json()}
    form = client.post('/api/armies/wargear', json={'unit_id': units['Shield Brutes'], 'models': 3}).get_json()['form']
    k = {m['name']: m['k'] for s in form for m in s['members']}

    def sheet(wargear):
        _, view = saved_match(client, warlord=None, units=[{'uid': 'b', 'unit_id': units['Shield Brutes'], 'models': 3,
                                                            'wargear': wargear}])
        return view['units'][0]

    plain = sheet(None)
    assert (plain['stats']['wounds'], plain['stats']['invulnerable_save']) == (4, None)  # Slab shields: W 4
    assert [a['name'] for a in plain['abilities']] == ['Slab Shield']
    brute = sheet({k['Brute shield']: 2})  # Most of the unit now has Brute shields
    assert (brute['stats']['wounds'], brute['stats']['invulnerable_save']) == (3, 4)
    assert [a['name'] for a in brute['abilities']] == ['Slab Shield', 'Brute Shield']
    assert brute['wargear_summary'] == '2× Brute shield'
