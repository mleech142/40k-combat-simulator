"""
Army lists (armies.py and the /api/armies routes): pricing, the checks, leaders attached to the
units they lead, the roster Analytics takes, saving, copying, deleting and the text export.
Built on the made-up BSData factions in tests/fixtures/bsdata.
"""

import json

import pytest

from conftest import BSDATA_FIXTURES

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}


@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]


def ids(client):
    """{faction name: id} and {unit name: id}."""
    factions = {f['name']: f['id'] for f in client.get('/api/data/factions').get_json()}
    units = {u['name']: u['id'] for u in client.get(f'/api/units?faction_id={factions["Starfarers"]}').get_json()}
    units.update({u['name']: u['id'] for u in client.get(f'/api/units?faction_id={factions["Allies"]}').get_json()})
    return factions, units


def army(factions, *units, limit=2000, **extra):
    return dict({'name': 'Test army', 'faction_id': factions['Starfarers'], 'points_limit': limit,
                 'units': [dict(u) for u in units]}, **extra)


def price(client, body):
    response = client.post('/api/armies/price', json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_pricing_and_sizes(client):
    factions, units = ids(client)
    priced = price(client, army(factions, {'uid': 'a', 'unit_id': units['Void Warriors']},
                                {'uid': 'b', 'unit_id': units['Void Warriors'], 'models': 7},
                                {'uid': 'c', 'unit_id': units['Gun Crew'], 'models': 3}))
    entries = {e['uid']: e for e in priced['units']}
    assert (entries['a']['models'], entries['a']['points']) == (5, 80)  # The smallest size when none is given
    assert entries['b']['points'] == 160 and entries['a']['sizes'] == [5, 6, 7, 8, 9, 10]
    assert entries['a']['size_points'][:2] == [80, 160]
    assert entries['c']['points'] == 80
    assert priced['total'] == 320 and priced['problems'] == []
    assert priced['faction'] == 'Starfarers'


def test_checks(client):
    factions, units = ids(client)
    over = price(client, army(factions, {'unit_id': units['Gun Crew'], 'models': 4}, limit=100))
    assert over['problems'] == ['150 points is 50 over the 100 point limit.']
    assert price(client, army(factions, {'unit_id': units['Gun Crew'], 'models': 4}, limit=None))['problems'] == []

    odd = price(client, army(factions, {'unit_id': units['Gun Crew'], 'models': 7},
                             {'unit_id': units['Ally Agent']}, {'unit_id': 999999}))
    assert odd['problems'] == [
        "Gun Crew: 7 models isn't a size this datasheet lists (2, 3, 4).",
        'Ally Agent: Not a Starfarers unit.',
        'Unknown unit: This unit is no longer in the data.',
    ]


def test_leaders_join_the_units_they_can_lead(client):
    factions, units = ids(client)
    priced = price(client, army(factions,
                                {'uid': 'warriors', 'unit_id': units['Void Warriors']},
                                {'uid': 'captain', 'unit_id': units['Star Captain'], 'attached_to': 'warriors'},
                                {'uid': 'crew', 'unit_id': units['Gun Crew']}))
    entries = {e['uid']: e for e in priced['units']}
    assert entries['captain']['is_leader'] and entries['captain']['can_join'] == ['warriors']
    assert entries['warriors']['led_by'] == ['captain'] and priced['problems'] == []
    assert priced['roster'] == [
        {'unit_id': units['Void Warriors'], 'models': 5, 'leader_id': units['Star Captain']},
        {'unit_id': units['Gun Crew'], 'models': 2, 'leader_id': None},
    ]

    wrong = price(client, army(factions, {'uid': 'crew', 'unit_id': units['Gun Crew']},
                               {'uid': 'captain', 'unit_id': units['Star Captain'], 'attached_to': 'crew'},
                               {'uid': 'drone', 'unit_id': units['Scout Drone'], 'attached_to': 'crew'}))
    assert wrong['problems'] == ["Star Captain: Can't lead Gun Crew.", 'Scout Drone: Only leaders can be attached to another unit.']

    gone = price(client, army(factions, {'uid': 'captain', 'unit_id': units['Star Captain'], 'attached_to': 'removed'}))
    assert gone['units'][0]['attached_to'] is None and gone['problems'] == []


def test_second_leader_takes_the_field_on_its_own_in_analytics(client):
    factions, units = ids(client)
    priced = price(client, army(factions, {'uid': 'w', 'unit_id': units['Void Warriors']},
                                {'uid': 'c1', 'unit_id': units['Star Captain'], 'attached_to': 'w'},
                                {'uid': 'c2', 'unit_id': units['Star Captain'], 'attached_to': 'w'}))
    assert priced['roster'] == [
        {'unit_id': units['Void Warriors'], 'models': 5, 'leader_id': units['Star Captain']},
        {'unit_id': units['Star Captain'], 'models': 1, 'leader_id': None},
    ]


def test_bad_requests(client):
    assert client.post('/api/armies/price', json={'units': []}).status_code == 400  # No faction
    response = client.post('/api/armies', json={'faction_id': 1, 'units': [{'models': 3}]})
    assert response.status_code == 400 and 'unit_id' in response.get_json()['error']
    assert client.post('/api/armies', data='not json', content_type='application/json').status_code == 400


def test_save_open_copy_delete(client, make_client):
    factions, units = ids(client)
    body = army(factions, {'uid': 'w', 'unit_id': units['Void Warriors'], 'models': 10},
                {'uid': 'c', 'unit_id': units['Star Captain'], 'attached_to': 'w'},
                detachment_key='d-vanguard', name='Vanguard')
    created = client.post('/api/armies', json=body).get_json()
    assert created['name'] == 'Vanguard' and created['total'] == 230 and created['detachment'] == 'Void Vanguard'

    body['name'] = 'Vanguard 2'
    body['units'][0]['models'] = 5
    updated = client.put(f'/api/armies/{created["id"]}', json=body).get_json()
    assert (updated['id'], updated['total']) == (created['id'], 150)

    copy = client.post(f'/api/armies/{created["id"]}/copy').get_json()
    assert copy['name'] == 'Vanguard 2 (copy)' and copy['id'] != created['id'] and copy['total'] == 150
    listed = client.get('/api/armies').get_json()
    assert [(a['name'], a['faction'], a['total'], a['unit_count']) for a in listed] == [
        ('Vanguard 2 (copy)', 'Starfarers', 150, 2), ('Vanguard 2', 'Starfarers', 150, 2)]

    restarted, _ = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)  # Armies live in the database, kept on restart
    again = restarted.get(f'/api/armies/{created["id"]}').get_json()
    assert again['units'][1]['attached_to'] == 'w' and again['roster'][0]['leader_id'] == units['Star Captain']

    assert restarted.delete(f'/api/armies/{copy["id"]}').status_code == 200
    assert [a['name'] for a in restarted.get('/api/armies').get_json()] == ['Vanguard 2']
    assert restarted.get(f'/api/armies/{copy["id"]}').status_code == 404


def test_armies_are_not_written_to_the_app_data_json(make_client):
    client, data_file = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)
    factions, units = ids(client)
    client.post('/api/armies', json=army(factions, {'unit_id': units['Gun Crew']}))
    client.post('/api/data/factions', json={'name': 'Homebrew'})  # Any data write rewrites the file
    assert 'armies' not in json.loads(data_file.read_text(encoding='utf-8'))


def test_text_export(client):
    factions, units = ids(client)
    text = client.post('/api/armies/text', json=army(
        factions, {'uid': 'w', 'unit_id': units['Void Warriors']},
        {'uid': 'c', 'unit_id': units['Star Captain'], 'attached_to': 'w'},
        {'uid': 'g', 'unit_id': units['Gun Crew'], 'models': 4}, limit=200,
        detachment_key='d-vanguard', name='Patrol')).get_json()['text']
    assert text == ('Patrol\nStarfarers - Void Vanguard (2 DP)\n300 / 200 points\n\n'
                    'Void Warriors (5 models) - 80 pts\n  + led by Star Captain - 70 pts\nGun Crew (4 models) - 150 pts\n\n'
                    'Problems:\n- 300 points is 100 over the 200 point limit.\n')


def test_pages(client):
    page = client.get('/armies').get_data(as_text=True)
    assert 'Strike Force (2000 pts, 3 DP)' in page and 'Starfarers' in page and 'js/armies.js' in page
    assert 'href="/armies"' in client.get('/analytics').get_data(as_text=True)


# --- Detachments ------------------------------------------------------------------------------------

def detachments(client, factions, keys, limit, units=()):
    return price(client, army(factions, *units, limit=limit, detachment_keys=keys))


@pytest.mark.parametrize('keys, limit, problems', [
    (['d-vanguard', 'd-bastion'], 2000, []),  # 2 + 1 = 3 DP: a Strike Force's budget
    (['d-vanguard', 'd-bastion'], 1000, ['The detachments cost 3 DP; Incursion allows 2 (or a single 3 DP detachment).']),
    (['d-armada'], 1000, []),  # A single 3 DP detachment at Incursion
    (['d-armada', 'd-citadel'], 2000, ['The detachments cost 4 DP; Strike Force allows 3.']),
    (['d-armada', 'd-citadel'], 3000, []),  # Onslaught: 4 DP
    (['d-armada', 'd-vanguard'], None, []),  # No limit, no budget
    (['d-bastion', 'd-citadel'], 2000, ['Star Bastion and Star Citadel share the Unique Tag "Fortress Detachment": choose one.']),
    (['d-task-force'], 2000, ["Allied Task Force isn't a Starfarers detachment."]),
    (['d-gone'], 2000, ['A detachment of this army is no longer in the data.']),
])
def test_detachment_rules(client, keys, limit, problems):
    factions, _ = ids(client)
    assert detachments(client, factions, keys, limit)['problems'] == problems


def test_detachments_are_priced_and_listed(client):
    factions, _ = ids(client)
    priced = detachments(client, factions, ['d-vanguard', 'd-bastion', 'd-vanguard'], 2000)
    assert [d['name'] for d in priced['detachments']] == ['Void Vanguard', 'Star Bastion']  # Each once
    assert (priced['dp_total'], priced['dp_limit']) == (3, 3)
    assert priced['detachment'] == 'Void Vanguard, Star Bastion'
    assert priced['detachments'][1] == {'key': 'd-bastion', 'name': 'Star Bastion', 'dp': 1,
                                        'dispositions': ['Take and Hold'], 'tags': ['Fortress Detachment']}
    assert detachments(client, factions, [], 1500)['dp_limit'] is None


def test_armies_saved_with_one_detachment_still_open(client, make_client, tmp_path):
    """The first saved form had one detachment_key column: databases from then gain detachment_keys on start."""
    import sqlite3
    factions, units = ids(client)
    db_file = tmp_path / 'test.db'
    connection = sqlite3.connect(db_file)
    connection.execute('DROP TABLE saved_armies')
    connection.execute('CREATE TABLE saved_armies (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL, faction_id INTEGER '
                       'NOT NULL, detachment_key VARCHAR(40), points_limit INTEGER, units JSON NOT NULL, '
                       'created_at DATETIME, updated_at DATETIME)')
    connection.execute("INSERT INTO saved_armies (name, faction_id, detachment_key, points_limit, units) VALUES "
                       "('Old', ?, 'd-vanguard', 2000, ?)", (factions['Starfarers'], json.dumps([{'uid': 'a', 'unit_id': units['Gun Crew'], 'models': 2}])))
    connection.commit()
    connection.close()

    restarted, _ = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)
    response = restarted.get('/api/armies')
    assert response.status_code == 200, response.get_data(as_text=True)[-1500:]
    old = response.get_json()[0]
    assert (old['name'], old['detachment']) == ('Old', 'Void Vanguard')
    saved = restarted.put(f'/api/armies/{old["id"]}', json=dict(
        restarted.get(f'/api/armies/{old["id"]}').get_json(), detachment_keys=['d-vanguard', 'd-bastion'])).get_json()
    assert [d['name'] for d in saved['detachments']] == ['Void Vanguard', 'Star Bastion']

