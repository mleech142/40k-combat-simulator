"""
BSData in the app: the import merged with the app data JSON, read-only BSData records,
stable IDs, app data saved against BSData factions and units, the import cache and status.
Uses the made-up catalogues in tests/fixtures/bsdata.
"""

import json
import shutil

import pytest

from combat_sim.app import create_app
from combat_sim.models import BSDATA_ID_BASE
from conftest import BSDATA_FIXTURES

STARFARERS = 'cat-star'

APP_DATA = {
    'schema_version': 2,
    'source': None,
    'factions': [{'id': 1, 'name': 'Homebrew'}],
    'units': [{'id': 1, 'faction_id': 1, 'name': 'Home Guard', 'toughness': 3, 'save': 5, 'wounds': 1,
               'default_models': 10, 'points_by_size': [{'models': 10, 'points': 60}], 'keywords': ['Infantry'],
               'ranged_weapons': [{'weapon_id': 1, 'qty': 10}], 'melee_weapons': []}],
    'ranged_weapons': [{'id': 1, 'faction_id': 1, 'name': 'Home rifle', 'attacks': '1', 'ballistic_skill': 4,
                        'strength': 3, 'armor_penetration': 0, 'damage': '1', 'abilities': []}],
    'melee_weapons': [],
    'faction_rules': [{'id': 1, 'faction_key': STARFARERS, 'faction_name': 'Starfarers', 'name': 'Star Fury',
                       'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Lethal Hits'}],
    'stratagems': [{'id': 1, 'faction_id': None, 'name': 'Smokescreen', 'cp': '1', 'applies_to': 'Target',
                    'phase': 'Shooting', 'modifier': 'Stealth', 'notes': None}],
    'unit_modifiers': [{'bsdata_id': 'u-captain', 'name': 'Star Captain', 'modifiers': ['Hit Modifier -1']},
                       {'bsdata_id': 'u-gone', 'name': 'Old Unit', 'modifiers': ['Stealth']}],
    'keywords': [{'id': 1, 'name': 'Lethal Hits', 'category': 'Weapon', 'applies_to': 'Both', 'format': 'Lethal Hits',
                  'summary': '', 'sim_step': 'Hit', 'modelled': True, 'rule_ref': ''}],
    'target_profiles': [],
}


@pytest.fixture
def app_client(make_client):
    client, data_file = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)
    return client, lambda: json.loads(data_file.read_text(encoding='utf-8'))


def factions(client):
    return {f['name']: f for f in client.get('/api/data/factions').get_json()}


def units(client, **params):
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    return {u['name']: u for u in client.get(f'/api/data/units?{query}').get_json()}


def restart():
    return create_app().test_client()


# --- Merged data ---------------------------------------------------------------------

def test_bsdata_and_custom_factions_side_by_side(app_client):
    client, _ = app_client
    by_name = factions(client)
    assert by_name['Homebrew']['source'] == 'custom' and by_name['Homebrew']['editable']
    assert by_name['Starfarers']['source'] == 'bsdata' and not by_name['Starfarers']['editable']
    assert by_name['Starfarers']['id'] >= BSDATA_ID_BASE
    assert by_name['Homebrew']['id'] == 1
    assert by_name['Starfarers']['rule_count'] == 1  # Saved by catalogue ID, linked on load


def test_bsdata_unit_detail(app_client):
    client, _ = app_client
    void = units(client)['Void Warriors']
    assert void['source'] == 'bsdata' and not void['editable']
    detail = client.get(f'/api/units/{void["id"]}').get_json()
    # Datasheet order, so the first profile of a weapon is the one ticked by default
    assert [(w['name'], w['default_use']) for w in detail['ranged_weapons']] == [
        ('Laser pistol', True), ('Laser rifle - standard', True), ('Laser rifle - overcharge', False)]
    assert [w['name'] for w in detail['ranged_options']] == ['Heavy laser']
    assert [w['name'] for w in detail['melee_options']] == ['Power axe']
    assert detail['points_by_model_count'][5:7] == [80, 160]


def test_legends_units_only_on_request(app_client):
    client, _ = app_client
    assert 'Star Walker [Legends]' not in units(client)
    assert units(client, legends=1)['Star Walker [Legends]']['legends'] is True
    assert not client.get('/api/units?search=walker').get_json()
    assert client.get('/api/units?search=walker&legends=1').get_json()[0]['name'] == 'Star Walker [Legends]'


def test_unit_list_within_a_faction_is_not_capped(app_client):
    client, _ = app_client
    star = factions(client)['Starfarers']['id']
    listed = client.get(f'/api/units?faction_id={star}').get_json()
    assert len(listed) == 6  # Every non-Legends Starfarers unit (the search cap is for searches only)


def test_status_reports_the_import(app_client):
    client, _ = app_client
    status = client.get('/api/data/status').get_json()
    assert status['loaded'] and status['error'] is None
    assert status['source']['game_system']['name'] == 'Test System'
    assert status['report_counts']['leader'] == 1
    assert status['report'] is None
    assert client.get('/api/data/status?report=1').get_json()['report']


# --- Read-only BSData records ------------------------------------------------------------

def test_bsdata_records_are_read_only(app_client):
    client, _ = app_client
    star = factions(client)['Starfarers']['id']
    void = units(client)['Void Warriors']['id']
    pistol = next(w for w in client.get(f'/api/data/weapons?faction_id={star}').get_json() if w['name'] == 'Laser pistol')
    assert pistol['editable'] is False

    assert client.put(f'/api/data/factions/{star}', json={'name': 'X'}).status_code == 403
    assert client.delete(f'/api/data/factions/{star}').status_code == 403
    assert client.put(f'/api/data/units/{void}', json={'name': 'X', 'faction_id': star}).status_code == 403
    assert client.delete(f'/api/data/units/{void}').status_code == 403
    assert client.post('/api/data/units', json={'faction_id': star, 'name': 'X', 'toughness': 4, 'save': 3,
                                                'wounds': 1}).status_code == 403
    assert client.put(f'/api/data/weapons/ranged/{pistol["id"]}', json={'name': 'X'}).status_code == 403
    assert client.delete(f'/api/data/weapons/ranged/{pistol["id"]}').status_code == 403
    assert client.post('/api/data/weapons', json={'faction_id': star, 'type': 'ranged', 'name': 'X', 'attacks': '1',
                                                  'strength': 4, 'ap': 0, 'damage': '1'}).status_code == 403


def test_custom_unit_cannot_move_into_a_bsdata_faction(app_client):
    client, _ = app_client
    star = factions(client)['Starfarers']['id']
    payload = {'faction_id': star, 'name': 'Home Guard', 'toughness': 3, 'save': 5, 'wounds': 1}
    assert client.put('/api/data/units/1', json=payload).status_code == 403


def test_new_custom_records_stay_below_the_bsdata_range(app_client):
    client, saved = app_client
    faction = client.post('/api/data/factions', json={'name': 'Second Homebrew'}).get_json()
    assert faction['id'] == 2
    unit = client.post('/api/data/units', json={'faction_id': 2, 'name': 'Brew', 'toughness': 4, 'save': 4,
                                                'wounds': 2}).get_json()
    assert unit['id'] == 2
    weapon = client.post('/api/data/weapons', json={'faction_id': 2, 'type': 'melee', 'name': 'Brew stick',
                                                    'attacks': '2', 'skill': 4, 'strength': 4, 'ap': 0,
                                                    'damage': '1'}).get_json()
    assert weapon['id'] == 1
    assert [f['name'] for f in saved()['factions']] == ['Homebrew', 'Second Homebrew']


# --- App data saved against BSData ---------------------------------------------------------

def test_saved_app_data_leaves_bsdata_out(app_client):
    client, saved = app_client
    client.post('/api/data/factions', json={'name': 'Another'})
    data = saved()
    assert data['schema_version'] == 2
    assert [f['name'] for f in data['factions']] == ['Homebrew', 'Another']
    assert [u['name'] for u in data['units']] == ['Home Guard']
    assert [w['name'] for w in data['ranged_weapons']] == ['Home rifle']
    assert 'bsdata_id' not in data['units'][0]
    assert client.get('/api/data/export').get_json()['factions'] == data['factions']


def test_rule_for_a_bsdata_faction_saved_by_catalogue_id(app_client):
    client, saved = app_client
    star = factions(client)['Starfarers']['id']
    client.post('/api/data/rules', json={'faction_id': star, 'name': 'Void Shields', 'applies_to': 'Target',
                                         'phase': 'Shooting', 'modifier': 'Save Modifier 1'})
    rules = {r['name']: r for r in saved()['faction_rules']}
    assert rules['Void Shields']['faction_key'] == STARFARERS
    assert rules['Void Shields']['faction_name'] == 'Starfarers'
    assert 'faction_id' not in rules['Void Shields']

    again = restart()
    star_again = factions(again)['Starfarers']
    assert star_again['id'] == star and star_again['rule_count'] == 2


def test_rules_ticked_for_a_bsdata_unit_apply(app_client):
    client, _ = app_client
    star = factions(client)['Starfarers']['id']
    rule = client.get(f'/api/data/rules?faction_id={star}').get_json()[0]
    available = client.get(f'/api/rules/available?side=Attacker&faction_id={star}').get_json()
    assert any(r['name'] == 'Star Fury' and r['has_effect'] for r in available)
    assert rule['name'] == 'Star Fury'


def test_added_modifiers_on_a_bsdata_unit(app_client):
    client, saved = app_client
    captain = units(client)['Star Captain']
    assert captain['added_modifiers'] == ['Hit Modifier -1']  # From the app data
    assert 'Hit Modifier -1' in captain['modifiers']

    void = units(client)['Void Warriors']
    assert client.put(f'/api/data/units/{void["id"]}', json={'added_modifiers': 'Feel No Pain 6+'}).status_code == 200
    assert units(client)['Void Warriors']['modifiers'] == ['Stealth', 'Feel No Pain 6+']
    entries = {m['bsdata_id']: m for m in saved()['unit_modifiers']}
    assert entries['u-void'] == {'bsdata_id': 'u-void', 'name': 'Void Warriors', 'modifiers': ['Feel No Pain 6+']}
    assert entries['u-gone']['modifiers'] == ['Stealth']  # Kept though its unit isn't in the folder
    assert units(restart())['Void Warriors']['added_modifiers'] == ['Feel No Pain 6+']


def test_added_modifiers_reach_the_maths(app_client):
    client, _ = app_client
    homebrew = factions(client)['Homebrew']['id']
    void = units(client)['Void Warriors']
    rifle = client.get(f'/api/data/weapons?type=ranged&faction_id={homebrew}').get_json()[0]
    body = {'attackers': [{'unit_id': 1, 'weapons': [{'weapon_id': rifle['id'], 'weapon_type': 'ranged',
                                                      'models': 10}]}],
            'defenders': [{'unit_id': void['id'], 'model_count': 5}], 'conditions': {}}

    def damage():
        return client.post('/api/matrix/simulate', json=body).get_json()['matrix'][0][0]['avg_damage']

    before = damage()
    client.put(f'/api/data/units/{void["id"]}', json={'added_modifiers': ['Toughness Modifier 4']})
    assert 0 < damage() < before  # S3 into T8 wounds on 6s instead of 5s


# --- Without the BSData folder ---------------------------------------------------------------

def test_app_runs_without_bsdata_and_keeps_app_data(make_client, tmp_path):
    client, data_file = make_client(APP_DATA)
    by_name = factions(client)
    assert set(by_name) == {'Homebrew', 'Starfarers'}
    assert by_name['Starfarers']['source'] == 'missing'  # Placeholder: its rule can still be edited
    assert by_name['Starfarers']['rule_count'] == 1
    assert client.get('/api/data/status').get_json()['loaded'] is False

    client.post('/api/data/factions', json={'name': 'Another'})  # Any save
    data = json.loads(data_file.read_text(encoding='utf-8'))
    assert data['faction_rules'][0]['faction_key'] == STARFARERS
    assert [m['bsdata_id'] for m in data['unit_modifiers']] == ['u-captain', 'u-gone']


def test_faction_ids_survive_bsdata_coming_and_going(make_client, tmp_path, monkeypatch):
    client, _ = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)
    star = factions(client)['Starfarers']['id']
    void = units(client)['Void Warriors']['id']
    monkeypatch.setenv('BSDATA_DIR', str(tmp_path / 'nothing'))
    assert factions(restart())['Starfarers']['id'] == star  # Placeholder keeps the ID
    monkeypatch.setenv('BSDATA_DIR', str(BSDATA_FIXTURES))
    again = restart()
    assert factions(again)['Starfarers']['id'] == star
    assert units(again)['Void Warriors']['id'] == void


def test_ids_stable_when_bsdata_changes(make_client, tmp_path, monkeypatch):
    folder = tmp_path / 'bsdata'
    shutil.copytree(BSDATA_FIXTURES, folder)
    client, _ = make_client(APP_DATA, bsdata=folder)
    before = {name: u['id'] for name, u in units(client).items()}

    # A BSData update removes a unit: the others keep their IDs
    path = folder / 'Xenos - Starfarers.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    content['catalogue']['entryLinks'] = [e for e in content['catalogue']['entryLinks'] if e['name'] != 'Beast Pack']
    path.write_text(json.dumps(content), encoding='utf-8')
    after = {name: u['id'] for name, u in units(restart()).items()}
    assert 'Beast Pack' not in after
    assert all(after[name] == before[name] for name in after)


def test_bsdata_faction_named_like_a_custom_one(make_client):
    data = dict(APP_DATA, factions=[{'id': 1, 'name': 'Starfarers'}])
    client, _ = make_client(data, bsdata=BSDATA_FIXTURES)
    by_name = factions(client)
    assert by_name['Starfarers']['source'] == 'custom'
    assert by_name['Starfarers (BSData)']['source'] == 'bsdata'


# --- Import cache and errors ---------------------------------------------------------------------

def test_import_is_cached_until_the_folder_changes(make_client, tmp_path):
    folder = tmp_path / 'bsdata'
    shutil.copytree(BSDATA_FIXTURES, folder)
    client, _ = make_client(APP_DATA, bsdata=folder)
    assert client.get('/api/data/status').get_json()['from_cache'] is False
    assert restart().get('/api/data/status').get_json()['from_cache'] is True

    path = folder / 'Library - Neutral Forces.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    content['catalogue']['revision'] = 99
    path.write_text(json.dumps(content), encoding='utf-8')
    status = restart().get('/api/data/status').get_json()
    assert status['from_cache'] is False
    assert status['source']['catalogues']['Library - Neutral Forces'] == 99


def test_broken_bsdata_folder_is_reported_not_fatal(make_client, tmp_path):
    folder = tmp_path / 'bsdata'
    folder.mkdir()
    (folder / 'Broken.json').write_text('{oops', encoding='utf-8')
    client, _ = make_client(APP_DATA, bsdata=folder)
    status = client.get('/api/data/status').get_json()
    assert status['loaded'] is False
    assert 'Broken.json' in status['error']
    assert 'Homebrew' in factions(client)


def test_pages_render_with_bsdata(app_client):
    client, _ = app_client
    for page in ('/', '/matchups', '/scenarios', '/data'):
        assert client.get(page).status_code == 200, page


# --- Detachments ---------------------------------------------------------------------------------

def test_detachments_and_reference_text(app_client):
    client, _ = app_client
    star = factions(client)['Starfarers']['id']
    detachments = client.get(f'/api/detachments?faction_id={star}').get_json()
    assert [(d['name'], d['key']) for d in detachments] == [('Star Bastion', 'd-bastion'), ('Void Vanguard', 'd-vanguard')]
    assert all(d['id'] >= BSDATA_ID_BASE for d in detachments)
    reference = client.get(f'/api/data/factions/{star}/reference').get_json()
    assert reference['army_rules'][0]['name'] == 'Star Pact'
    assert reference['detachments'][1]['rules'][0]['name'] == 'Swift Strike'
    assert client.get('/api/detachments').get_json() == []


def test_detachment_rules_offered_only_with_their_detachment(app_client):
    client, saved = app_client
    star = factions(client)['Starfarers']['id']
    created = client.post('/api/data/stratagems', json={
        'faction_id': star, 'name': 'Vanguard Strike', 'cp': '1', 'applies_to': 'Attacker', 'phase': 'Both',
        'modifier': 'Hit Modifier 1', 'detachment_key': 'd-vanguard'}).get_json()
    assert created['detachment_name'] == 'Void Vanguard'
    saved_strat = next(s for s in saved()['stratagems'] if s['name'] == 'Vanguard Strike')
    assert (saved_strat['detachment_key'], saved_strat['detachment_name']) == ('d-vanguard', 'Void Vanguard')

    def offered(*detachments):
        query = f'side=Attacker&faction_id={star}' + ''.join(f'&detachment={d}' for d in detachments)
        return {e['name']: e for e in client.get(f'/api/rules/available?{query}').get_json()}
    assert 'Vanguard Strike' not in offered()
    assert 'Vanguard Strike' not in offered('d-bastion')
    with_it = offered('d-vanguard')
    assert with_it['Vanguard Strike']['detachment'] == 'Void Vanguard'
    assert 'Star Fury' in with_it and 'Star Fury' in offered()  # Faction-wide rules always

    client.put(f'/api/data/stratagems/{created["id"]}', json=dict(created, detachment_key=None))
    assert 'Vanguard Strike' in offered()


def test_detachment_kept_when_bsdata_is_missing(make_client):
    data = dict(APP_DATA, faction_rules=[dict(APP_DATA['faction_rules'][0], detachment_key='d-vanguard',
                                              detachment_name='Void Vanguard')])
    client, data_file = make_client(data)
    client.post('/api/data/factions', json={'name': 'Another'})  # Any save
    rule = json.loads(data_file.read_text(encoding='utf-8'))['faction_rules'][0]
    assert (rule['detachment_key'], rule['detachment_name']) == ('d-vanguard', 'Void Vanguard')


# --- Custom weapons copied from BSData -----------------------------------------------------------

def test_bsdata_weapon_search(app_client):
    client, _ = app_client
    found = client.get('/api/data/weapons/bsdata?q=laser&type=ranged').get_json()
    names = [w['name'] for w in found]
    assert 'Heavy laser' in names and 'Laser rifle - standard' in names
    assert names.count('Laser pistol') == 2  # Starfarers' and Allies' profiles differ
    assert all(w['type'] == 'ranged' for w in found)
    assert not client.get('/api/data/weapons/bsdata?q=home').get_json()  # Custom weapons aren't offered


def test_linked_custom_weapon_follows_bsdata(make_client, tmp_path, monkeypatch):
    folder = tmp_path / 'bsdata'
    shutil.copytree(BSDATA_FIXTURES, folder)
    client, saved_file = make_client(APP_DATA, bsdata=folder)
    created = client.post('/api/data/weapons', json={
        'faction_id': 1, 'type': 'ranged', 'name': 'Borrowed laser', 'attacks': '9', 'strength': 1, 'ap': 0,
        'damage': '1', 'linked_bsdata_id': 'p-heavy'}).get_json()
    weapon = client.get(f'/api/data/weapons/ranged/{created["id"]}').get_json()
    assert (weapon['name'], weapon['attacks'], weapon['strength'], weapon['ap'], weapon['damage']) == (
        'Borrowed laser', '1', 9, 3, 'D6')  # Stats from BSData, name its own
    assert weapon['linked_bsdata_id'] == 'p-heavy' and weapon['editable']
    saved = json.loads(saved_file.read_text(encoding='utf-8'))
    assert next(w for w in saved['ranged_weapons'] if w['name'] == 'Borrowed laser')['linked_bsdata_id'] == 'p-heavy'

    # BSData changes the profile: the copy follows on the next start
    path = folder / 'Xenos - Starfarers.json'
    text = json.dumps(json.loads(path.read_text(encoding='utf-8')))
    assert text.count('"name": "S", "$text": "9"') == 1  # The heavy laser's strength
    path.write_text(text.replace('"name": "S", "$text": "9"', '"name": "S", "$text": "10"'), encoding='utf-8')
    again = restart().get(f'/api/data/weapons/ranged/{created["id"]}').get_json()
    assert again['strength'] == 10 and again['name'] == 'Borrowed laser'
