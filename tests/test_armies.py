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


def test_later_copies_of_a_datasheet_cost_what_bsdata_adds(client):
    # Issue #15: BSData's "+10 pts with two copies before it" applies to the third Void Warriors in list order
    factions, units = ids(client)
    priced = price(client, army(factions, *[{'uid': u, 'unit_id': units['Void Warriors']} for u in 'abc'],
                                {'uid': 'd', 'unit_id': units['Void Warriors'], 'models': 7}))
    entries = {e['uid']: e for e in priced['units']}
    assert [entries[u]['points'] for u in 'abcd'] == [80, 80, 90, 170]
    assert entries['c']['size_points'][:2] == [90, 170]
    assert priced['total'] == 420
    # With wargear chosen too
    choices = client.post('/api/armies/wargear', json={'unit_id': units['Void Warriors'], 'models': 5}).get_json()['wargear']
    assert choices
    chosen = price(client, army(factions, *[{'uid': u, 'unit_id': units['Void Warriors'], 'wargear': choices} for u in 'abc']))
    assert [e['points'] for e in chosen['units']] == [80, 80, 90]


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
                                {'uid': 'crew', 'unit_id': units['Gun Crew']}, warlord='captain'))
    entries = {e['uid']: e for e in priced['units']}
    assert entries['captain']['is_leader'] and entries['captain']['can_join'] == ['warriors']
    assert entries['warriors']['led_by'] == ['captain'] and priced['problems'] == []
    assert priced['roster'] == [
        {'unit_id': units['Void Warriors'], 'models': 5, 'leader_id': units['Star Captain'], 'enhancements': [],
         'loadout': None, 'leader_loadout': None, 'stat_changes': None, 'leader_stat_changes': None},
        {'unit_id': units['Gun Crew'], 'models': 2, 'leader_id': None, 'enhancements': [],
         'loadout': None, 'leader_loadout': None, 'stat_changes': None, 'leader_stat_changes': None},
    ]

    wrong = price(client, army(factions, {'uid': 'crew', 'unit_id': units['Gun Crew']},
                               {'uid': 'captain', 'unit_id': units['Star Captain'], 'attached_to': 'crew'},
                               {'uid': 'drone', 'unit_id': units['Scout Drone'], 'attached_to': 'crew'}, warlord='captain'))
    assert wrong['problems'] == ["Star Captain: Can't join Gun Crew.",
                                 'Scout Drone: Only leaders and support units can join another unit.']

    gone = price(client, army(factions, {'uid': 'captain', 'unit_id': units['Star Captain'], 'attached_to': 'removed'},
                              warlord='captain'))
    assert gone['units'][0]['attached_to'] is None and gone['problems'] == []


def test_second_leader_takes_the_field_on_its_own_in_analytics(client):
    factions, units = ids(client)
    priced = price(client, army(factions, {'uid': 'w', 'unit_id': units['Void Warriors']},
                                {'uid': 'c1', 'unit_id': units['Star Captain'], 'attached_to': 'w'},
                                {'uid': 'c2', 'unit_id': units['Star Captain'], 'attached_to': 'w'}, warlord='c1'))
    assert priced['roster'] == [
        {'unit_id': units['Void Warriors'], 'models': 5, 'leader_id': units['Star Captain'], 'enhancements': [],
         'loadout': None, 'leader_loadout': None, 'stat_changes': None, 'leader_stat_changes': None},
        {'unit_id': units['Star Captain'], 'models': 1, 'leader_id': None, 'enhancements': [],
         'loadout': None, 'leader_loadout': None, 'stat_changes': None, 'leader_stat_changes': None},
    ]
    assert priced['problems'] == ['Star Captain: Void Warriors already has a leader (one each, unless a datasheet says otherwise).']


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
        {'uid': 'g', 'unit_id': units['Gun Crew'], 'models': 4, 'enhancement': 'enh-sights'},
        {'uid': 'm', 'unit_id': units['Field Medic'], 'attached_to': 'w'}, limit=200,
        detachment_key='d-vanguard', name='Patrol', warlord='c')).get_json()['text']
    assert text == ('Patrol\nStarfarers - Void Vanguard (2 DP)\n350 / 200 points\n\n'
                    'Void Warriors (5 models) - 80 pts\n  + led by Star Captain - 70 pts [Warlord]\n'
                    '  + supported by Field Medic - 40 pts\nGun Crew (4 models) - 160 pts [Targeting Sights]\n\n'
                    'Problems:\n- 350 points is 150 over the 200 point limit.\n')


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


# --- Warlord, copies, support units, enhancements (BSData fixtures) --------------------------------

def test_warlord_must_be_a_character(client):
    factions, units = ids(client)
    body = army(factions, {'uid': 'c', 'unit_id': units['Star Captain']}, {'uid': 'g', 'unit_id': units['Gun Crew']})
    assert price(client, body)['problems'] == ['Choose a Warlord (one of your Characters).']
    assert price(client, dict(body, warlord='c'))['warlord'] == 'c'
    assert price(client, dict(body, warlord='c'))['problems'] == []
    assert price(client, dict(body, warlord='g'))['problems'] == ["Gun Crew can't be the Warlord: choose a Character."]
    assert price(client, army(factions, {'unit_id': units['Gun Crew']}))['problems'] == []  # No Characters, no Warlord


@pytest.mark.parametrize('name, count, problem', [
    ('Gun Crew', 4, 'Gun Crew: 4 in the army; at most 3.'),  # BSData's own limit
    ('Void Warriors', 7, 'Void Warriors: 7 in the army; at most 6.'),  # Battleline, as BSData has it
    ('Beast Pack', 4, 'Beast Pack: 4 in the army; at most 3.'),  # No limit in BSData: the rule of three
    ('Star Lord', 2, 'Star Lord: 2 in the army; at most 1.'),  # An Epic Hero
])
def test_copies_of_a_datasheet(client, name, count, problem):
    factions, units = ids(client)
    allowed = price(client, army(factions, *[{'unit_id': units[name]}] * (count - 1), warlord='u1'))['problems']
    over = price(client, army(factions, *[{'unit_id': units[name]}] * count, warlord='u1'))['problems']
    assert problem not in allowed and problem in over


def test_support_units_join_alongside_a_leader(client):
    factions, units = ids(client)
    priced = price(client, army(factions, {'uid': 'w', 'unit_id': units['Void Warriors']},
                                {'uid': 'c', 'unit_id': units['Star Captain'], 'attached_to': 'w'},
                                {'uid': 'm', 'unit_id': units['Field Medic'], 'attached_to': 'w'}, warlord='c'))
    entries = {e['uid']: e for e in priced['units']}
    assert entries['m']['is_support'] and entries['m']['can_join'] == ['w']
    assert (entries['w']['led_by'], entries['w']['supported_by'], priced['problems']) == (['c'], ['m'], [])
    assert [r['unit_id'] for r in priced['roster']] == [units['Void Warriors'], units['Field Medic']]  # On its own in Analytics

    two = price(client, army(factions, {'uid': 'w', 'unit_id': units['Void Warriors']},
                             {'uid': 'm1', 'unit_id': units['Field Medic'], 'attached_to': 'w'},
                             {'uid': 'm2', 'unit_id': units['Field Medic'], 'attached_to': 'w'}))
    assert two['problems'] == ['Field Medic: Void Warriors already has a support unit (one each, unless a datasheet says otherwise).']


def test_enhancements_offered_and_checked(client):
    factions, units = ids(client)
    body = army(factions, {'uid': 'c', 'unit_id': units['Star Captain'], 'enhancement': 'enh-void-blade'},
                {'uid': 'l', 'unit_id': units['Star Lord']}, {'uid': 'g', 'unit_id': units['Gun Crew']},
                detachment_keys=['d-vanguard'], warlord='c')
    priced = price(client, body)
    entries = {e['uid']: e for e in priced['units']}
    assert [o['key'] for o in entries['c']['enhancement_options']] == ['enh-void-blade']  # Its detachment's only
    assert entries['l']['enhancement_options'] == []  # An Epic Hero
    assert [o['key'] for o in entries['g']['enhancement_options']] == ['enh-line-banner', 'enh-sights']  # Its Upgrade too
    assert (entries['c']['unit_points'], entries['c']['enhancement_points'], entries['c']['points']) == (70, 20, 90)
    assert priced['total'] == 90 + 120 + 65 and priced['problems'] == []
    assert (priced['enhancement_count'], priced['enhancement_limit']) == (1, 4)

    no_detachment = price(client, dict(body, detachment_keys=['d-bastion']))
    assert no_detachment['problems'] == ['Star Captain: Void Blade needs its detachment.']
    # Bastion Ward: Infantry or the Star Lord only (BSData: hidden unless the bearer is either)
    bastion = dict(body, detachment_keys=['d-bastion'], units=[
        {'uid': 'c', 'unit_id': units['Star Captain'], 'enhancement': 'enh-bastion-ward'},
        {'uid': 'l', 'unit_id': units['Star Lord'], 'enhancement': 'enh-bastion-ward'}])
    problems = price(client, bastion)['problems']
    assert "Star Lord: Epic Heroes can't be given enhancements." in problems
    assert 'Bastion Ward is taken 2 times; at most 1.' in problems
    assert not any(p.startswith('Star Captain') for p in problems)


def test_enhancements_for_given_non_character_units(client):
    # Issue #11: Warptide's enhancements go on its Battleline units (only theirs link to them), not on Characters,
    # up to three in an army, counting as one enhancement
    factions, units = ids(client)
    lines = [{'uid': f'v{i}', 'unit_id': units['Void Warriors'], 'enhancement': 'enh-line-banner'} for i in range(3)]
    priced = price(client, army(factions, *lines, {'uid': 'c', 'unit_id': units['Star Captain']},
                                detachment_keys=['d-vanguard'], warlord='c'))
    entries = {e['uid']: e for e in priced['units']}
    assert [o['key'] for o in entries['v0']['enhancement_options']] == ['enh-line-banner']
    assert 'enh-line-banner' not in [o['key'] for o in entries['c']['enhancement_options']]
    assert priced['problems'] == [] and priced['enhancement_count'] == 1
    assert entries['v0']['points'] == 90

    four = price(client, army(factions, *lines, dict(lines[0], uid='v3'), detachment_keys=['d-vanguard']))
    assert 'Line Banner is taken 4 times; at most 3.' in four['problems']
    captain = price(client, army(factions, {'uid': 'c', 'unit_id': units['Star Captain'], 'enhancement': 'enh-line-banner'},
                                 detachment_keys=['d-vanguard'], warlord='c'))
    assert captain['problems'] == ["Star Captain: Line Banner isn't for Star Captain."]
    assert price(client, army(factions, lines[0]))['problems'] == ['Void Warriors: Line Banner needs its detachment.']


def test_enhancement_limit_and_upgrades(client):
    factions, units = ids(client)
    captains = [{'unit_id': units['Star Captain'], 'enhancement': key}
                for key in ('enh-void-blade', 'enh-armada-relic', 'enh-void-blade')]
    priced = price(client, army(factions, *captains, detachment_keys=['d-vanguard', 'd-armada'], limit=1000, warlord='u1'))
    assert priced['enhancement_count'] == 3 and priced['enhancement_limit'] == 2
    assert '3 enhancements; the limit is 2.' in priced['problems']
    assert 'Void Blade is taken 2 times; at most 1.' in priced['problems']

    crews = [{'unit_id': units['Gun Crew'], 'enhancement': 'enh-sights'}] * 3
    upgrades = price(client, army(factions, *crews, detachment_keys=['d-vanguard']))
    assert upgrades['enhancement_count'] == 0 and upgrades['problems'] == []  # Up to three of an Upgrade, not counted
    wrong_unit = price(client, army(factions, {'unit_id': units['Void Warriors'], 'enhancement': 'enh-sights'},
                                    detachment_keys=['d-vanguard']))
    assert wrong_unit['problems'] == ['Void Warriors: Targeting Sights is an Upgrade for another unit.']


def test_enhancements_reach_the_analytics_roster(client):
    factions, units = ids(client)
    priced = price(client, army(factions, {'uid': 'w', 'unit_id': units['Void Warriors']},
                                {'uid': 'c', 'unit_id': units['Star Captain'], 'attached_to': 'w', 'enhancement': 'enh-void-blade'},
                                {'uid': 'g', 'unit_id': units['Gun Crew'], 'enhancement': 'enh-sights'},
                                detachment_keys=['d-vanguard'], warlord='c'))
    enhancements = {e['name']: e['id'] for e in client.get(f'/api/enhancements?faction_id={factions["Starfarers"]}').get_json()}
    assert [r['enhancements'] for r in priced['roster']] == [
        [{'id': enhancements['Void Blade'], 'name': 'Void Blade', 'bearer': 'leader', 'unit_id': units['Star Captain']}],
        [{'id': enhancements['Targeting Sights'], 'name': 'Targeting Sights', 'bearer': 'unit', 'unit_id': units['Gun Crew']}]]



def test_enhancement_effects_in_the_matrix(client):
    """Void Blade gives the bearer's melee attacks Lethal Hits; Bastion Ward a 4+ invulnerable save to the unit it leads."""
    factions, units = ids(client)
    enhancements = {e['name']: e['id'] for e in client.get(f'/api/enhancements?faction_id={factions["Starfarers"]}').get_json()}
    captain = client.get(f'/api/units/{units["Star Captain"]}').get_json()
    blade = next(w for w in captain['melee_weapons'] if w['name'] == 'Master blade')

    def matrix(attacker_enhancements, leader_weapon=False, defender_enhancements=()):
        weapon = {'weapon_id': blade['id'], 'weapon_type': 'melee', 'models': 1, 'leader': leader_weapon,
                  'enhancements': attacker_enhancements}
        result = client.post('/api/matrix/simulate', json={
            'attackers': [{'unit_id': units['Star Captain'], 'weapons': [weapon], 'enhancements': attacker_enhancements}],
            'defenders': [{'unit_id': units['Void Warriors'], 'model_count': 5, 'enhancements': list(defender_enhancements)}],
            'conditions': {}}).get_json()
        return result['matrix'][0][0], result['attackers'][0]['points']

    plain, plain_points = matrix([])
    bladed, bladed_points = matrix([{'id': enhancements['Void Blade'], 'bearer': 'unit'}])
    assert bladed['avg_damage'] > plain['avg_damage'] and bladed_points == plain_points + 20
    # The blade is the bearer's: carried by a leader, it doesn't reach the bodyguard's weapons
    elsewhere, _ = matrix([{'id': enhancements['Void Blade'], 'bearer': 'leader'}])
    assert elsewhere['avg_damage'] == plain['avg_damage']
    # Bastion Ward: while its bearer leads the unit, that unit has a 4+ invulnerable save
    warded, _ = matrix([], defender_enhancements=[{'id': enhancements['Bastion Ward'], 'bearer': 'leader'}])
    assert warded['avg_damage'] < plain['avg_damage']


# --- Force disposition ---------------------------------------------------------------------------

def test_the_force_disposition_is_one_of_the_chosen_detachments(client):
    factions, _ = ids(client)
    both = price(client, army(factions, detachment_keys=['d-vanguard', 'd-bastion'], disposition='Take and Hold'))
    assert both['disposition_options'] == ['Purge the Foe', 'Take and Hold']
    assert (both['disposition'], both['disposition_unconfirmed']) == ('Take and Hold', False)
    # Not chosen: the first, to be confirmed (as lists saved before dispositions open)
    unset = price(client, army(factions, detachment_keys=['d-vanguard', 'd-bastion']))
    assert (unset['disposition'], unset['disposition_unconfirmed']) == ('Purge the Foe', True)
    wrong = price(client, army(factions, detachment_keys=['d-vanguard'], disposition='Disruption'))
    assert wrong['disposition'] is None
    assert "Disruption isn't a force disposition of this army's detachments." in wrong['problems']
    none = price(client, army(factions, detachment_keys=[]))
    assert (none['disposition'], none['disposition_options']) == (None, [])


def test_the_force_disposition_is_saved_with_the_list(client):
    factions, _ = ids(client)
    created = client.post('/api/armies', json=army(factions, detachment_keys=['d-vanguard', 'd-bastion'],
                                                   disposition='Take and Hold', name='Holders')).get_json()
    assert created['disposition'] == 'Take and Hold'
    assert client.get(f'/api/armies/{created["id"]}').get_json()['disposition'] == 'Take and Hold'
    copy = client.post(f'/api/armies/{created["id"]}/copy').get_json()
    assert copy['disposition'] == 'Take and Hold'
