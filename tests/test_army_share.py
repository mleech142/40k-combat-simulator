"""
Army share codes (army_share.py and /api/armies/share, /api/armies/import): a list survives the
round trip with its sizes, wargear, leaders, enhancements, detachments and Warlord; codes carry
BSData IDs only; lists with custom units are refused; damaged codes and missing data are reported.
Built on the made-up BSData factions in tests/fixtures/bsdata.
"""

import base64
import copy
import json
import zlib

import pytest

from combat_sim import army_share, bsdata
from conftest import BSDATA_FIXTURES
from test_armies import APP_DATA, army, ids, price


@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]


def share(client, body):
    response = client.post('/api/armies/share', json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()['code']


def import_code(client, code):
    response = client.post('/api/armies/import', json={'code': code})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def payload(code):
    body = code[len(army_share.PREFIX):]
    return json.loads(zlib.decompress(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))))


def pack(data):
    raw = json.dumps(data).encode()
    return army_share.PREFIX + base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip('=')


def full_army(client):
    factions, units = ids(client)
    form = client.post('/api/armies/wargear', json={'unit_id': units['Void Warriors'], 'models': 5}).get_json()['form']
    k = {m['name']: m['k'] for s in form for m in s['members']}
    return army(factions,
                {'uid': 'w', 'unit_id': units['Void Warriors'], 'models': 8,
                 'wargear': {k['Power axe']: 1, k['Blade']: 0, k['Void Warrior w/ heavy laser']: 1, k['Void Warrior']: 3}},
                {'uid': 'c', 'unit_id': units['Star Captain'], 'attached_to': 'w', 'enhancement': 'enh-void-blade'},
                {'uid': 'g', 'unit_id': units['Gun Crew'], 'models': 3},
                name='Shared host', detachment_keys=['d-vanguard', 'd-bastion'], warlord='c')


def test_round_trip_keeps_the_whole_list(client):
    body = full_army(client)
    before = price(client, body)
    code = share(client, body)
    assert code.startswith(army_share.PREFIX) and len(code) < 1000

    imported = import_code(client, code)
    assert imported['notes'] == []
    got = imported['army']
    assert (got['name'], got['faction_id'], got['points_limit'], got['detachment_keys']) == \
        ('Shared host', body['faction_id'], 2000, ['d-vanguard', 'd-bastion'])
    after = price(client, got)
    assert after['total'] == before['total'] and after['problems'] == []
    for old, new in zip(before['units'], after['units']):
        assert (new['unit_id'], new['models'], new['wargear'], new['enhancement'], new['wargear_summary'], new['loadout']) == \
            (old['unit_id'], old['models'], old['wargear'], old['enhancement'], old['wargear_summary'], old['loadout'])
    entries = after['units']
    assert entries[1]['attached_to'] == entries[0]['uid'] and after['warlord'] == entries[1]['uid']
    assert entries[0]['wargear_summary'] == 'Power axe, Void Warrior w/ heavy laser'


def test_codes_carry_bsdata_ids_not_database_ids(client):
    data = payload(share(client, full_army(client)))
    assert data['faction'] == 'cat-star' and data['faction_name'] == 'Starfarers'
    assert all(isinstance(u['id'], str) and not u['id'].isdigit() for u in data['units'])
    assert data['units'][1]['joins'] == 0 and data['warlord'] == 1
    assert 'Power axe' not in json.dumps(data) and all(not ref.isdigit() for ref in data['units'][0]['wargear'])


def test_wargear_survives_a_renumbered_tree():
    """Another import can number the option tree differently: choices follow the options, not their numbers."""
    tree = next(u for u in bsdata.import_folder(BSDATA_FIXTURES).data['units'] if u['name'] == 'Void Warriors')['wargear']
    renumbered = copy.deepcopy(tree)

    def shift(node):
        node['k'] += 100
        for child in node.get('children', []):
            shift(child)
    shift(renumbered)
    refs = army_share._option_refs(tree)
    assert len(set(refs.values())) == len(refs)  # Every option has its own reference
    choices = {str(k): i % 3 for i, k in enumerate(refs)}
    moved, missing = army_share._wargear_in(renumbered, army_share._wargear_out(tree, choices))
    assert missing == 0 and moved == {str(int(k) + 100): n for k, n in choices.items()}


def test_lists_with_custom_units_cant_be_shared(make_client):
    data = dict(APP_DATA, factions=[{'id': 1, 'name': 'Homebrew'}],
                units=[{'id': 1, 'faction_id': 1, 'name': 'Home Guard', 'toughness': 3, 'save': 5, 'wounds': 1,
                        'default_models': 10, 'points_by_size': [{'models': 10, 'points': 60}], 'keywords': ['Infantry'],
                        'ranged_weapons': [], 'melee_weapons': []}])
    client = make_client(data, bsdata=BSDATA_FIXTURES)[0]
    response = client.post('/api/armies/share', json={'faction_id': 1, 'units': [{'unit_id': 1}]})
    assert response.status_code == 400
    assert response.get_json()['error'] == 'Homebrew is a custom faction: only lists of BSData units can be shared.'

    factions, units = ids(client)
    mixed = army(factions, {'unit_id': units['Void Warriors']}, {'unit_id': 1})
    response = client.post('/api/armies/share', json=mixed)
    assert response.status_code == 400
    assert response.get_json()['error'] == "Lists with custom units can't be shared: Home Guard is only in your data."


def test_missing_data_is_left_out_and_named(client):
    data = payload(share(client, full_army(client)))
    data['units'][0]['wargear']['gone-option'] = 1
    data['units'][1]['enh'] = 'enh-gone'
    data['units'].append({'id': 'unit-gone', 'name': 'Lost Squad', 'models': 5})
    data['detachments'].append('d-gone')
    imported = import_code(client, pack(data))
    assert imported['notes'] == [
        "Void Warriors: 1 of its wargear choices isn't in your data; check its wargear.",
        "Star Captain: its enhancement isn't in your data, so it was left out.",
        "Lost Squad isn't in your data, so it was left out.",
        "A detachment in this list isn't in your data, so it was left out.",
    ]
    got = imported['army']
    assert len(got['units']) == 3 and got['detachment_keys'] == ['d-vanguard', 'd-bastion']
    assert got['units'][1]['enhancement'] is None and got['units'][0]['wargear']

    data['faction'] = 'cat-gone'
    response = client.post('/api/armies/import', json={'code': pack(data)})
    assert response.status_code == 400 and 'Starfarers' in response.get_json()['error']


@pytest.mark.parametrize('code', [None, '', 'hello', army_share.PREFIX, army_share.PREFIX + '!!!!',
                                  army_share.PREFIX + base64.urlsafe_b64encode(zlib.compress(b'[1, 2]')).decode(),
                                  army_share.PREFIX + base64.urlsafe_b64encode(b'not zlib').decode()])
def test_damaged_codes(client, code):
    response = client.post('/api/armies/import', json={'code': code})
    assert response.status_code == 400
    assert response.get_json()['error'] == "That isn't an army share code (or part of it is missing)."


def test_pasted_codes_and_newer_versions(client):
    code = share(client, full_army(client))
    assert import_code(client, f'  {code[:20]}\n{code[20:]} \n')['army']['name'] == 'Shared host'  # Wrapped when pasted
    newer = dict(payload(code), v=army_share.VERSION + 1)
    response = client.post('/api/armies/import', json={'code': pack(newer)})
    assert response.status_code == 400 and 'newer version' in response.get_json()['error']


def test_a_code_that_inflates_without_end_is_refused(client):
    bomb = army_share.PREFIX + base64.urlsafe_b64encode(zlib.compress(b'{"units": [' + b' ' * 5_000_000 + b']}', 9)).decode()
    assert client.post('/api/armies/import', json={'code': bomb}).status_code == 400
