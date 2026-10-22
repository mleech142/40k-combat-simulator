"""
Rules and stratagems from the Wahapedia export: reading the files, turning their text into
modifiers (rules_text.parse_effects), matching them to BSData, the pages' API, editing how the
maths reads them, replacing the app data's own, and "Download latest" (Wahapedia faked).

The export here is made up (tests/fixtures/wahapedia, from make_wahapedia_fixtures.py) for the
made-up BSData factions in tests/fixtures/bsdata.
"""

import json
import shutil

import pytest

from combat_sim import wahapedia, wahapedia_download
from combat_sim.rules_text import damaged_threshold, has_core_ability, parse_effects, parse_leader_buffs
from conftest import BSDATA_FIXTURES, WAHAPEDIA_FIXTURES

APP_DATA = {
    'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
    'faction_rules': [{'id': 1, 'faction_key': 'cat-star', 'faction_name': 'Starfarers', 'name': 'Home Rule',
                       'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Lance'}],
    'stratagems': [{'id': 1, 'faction_id': None, 'name': 'Command Re-roll', 'cp': '1', 'applies_to': 'Attacker',
                    'phase': 'Both', 'modifier': None, 'notes': 'Hand-written placeholder'},
                   {'id': 2, 'faction_id': None, 'name': 'My Own Gambit', 'cp': '1', 'applies_to': 'Attacker',
                    'phase': 'Both', 'modifier': 'Lethal Hits', 'notes': None}],
    'unit_modifiers': [], 'keywords': [], 'target_profiles': [],
}


def buffs(text):
    return [(b['applies_to'], b['phase'], b['modifier']) for b in parse_effects(text)['buffs']]


# --- Reading the files -------------------------------------------------------------------------

def test_read_table_handles_unquoted_line_breaks(tmp_path):
    path = tmp_path / 'T.csv'
    path.write_text('﻿id|name|text|\n1|One|two\nlines|\n2|Two|plain|\n', encoding='utf-8')
    assert wahapedia.read_table(path) == [{'id': '1', 'name': 'One', 'text': 'two\nlines'},
                                          {'id': '2', 'name': 'Two', 'text': 'plain'}]


def test_read_table_rejects_a_cut_off_file(tmp_path):
    path = tmp_path / 'T.csv'
    path.write_text('id|name|\n1|One|\n2|Tw', encoding='utf-8')
    with pytest.raises(wahapedia.WahapediaError, match='part-way'):
        wahapedia.read_table(path)


def test_markup_turns_the_websites_html_into_rule_text():
    html = ('<b>WHEN:</b> Your  Shooting phase.<br><br><b>TARGET:</b> One <span class="kwb">INFANTRY</span> unit.'
            '<ul><li>First &amp; best.</li><li>Second.</li></ul>')
    assert wahapedia.markup(html) == ('**WHEN:** Your Shooting phase.\n\n**TARGET:** One ^^INFANTRY^^ unit.\n\n'
                                      '■ First & best.\n■ Second.')


def test_title_case_names():
    assert wahapedia.title_case('THREAT-COGITATION TARGETERS') == 'Threat-Cogitation Targeters'
    assert wahapedia.title_case("BURST OF SPEED") == 'Burst of Speed'
    assert wahapedia.title_case("YRIEL'S EXAMPLE") == "Yriel's Example"
    assert wahapedia.title_case('Already Mixed') == 'Already Mixed'


def test_import_folder():
    result = wahapedia.import_folder(WAHAPEDIA_FIXTURES)
    assert result.source == {'last_update': '2026-10-01 12:00:00'}
    strats = {s['name']: s for s in result.data['stratagems']}
    assert 'Normal Move' not in strats  # A core ability, not a stratagem
    assert 'Go To Ground' not in strats and 'All In' not in strats  # The older core set, a mission pack's card
    assert strats['Smokescreen']['wahapedia_id'] == '202' and strats['Smokescreen']['cp'] == '1'  # The newest of that name
    assert (strats['Void Strike']['applies_to'], strats['Void Strike']['phase'], strats['Void Strike']['modifier']) == \
        ('Attacker', 'Shooting', 'Lethal Hits')
    bastion = strats['Bastion of Stars']  # Its legend has a line break in the file
    assert (bastion['applies_to'], bastion['phase'], bastion['modifier']) == ('Target', 'Both', 'Wound Modifier -1')
    assert bastion['text'].startswith("**WHEN:** Your opponent's Shooting phase")
    assert strats['Redeploy']['phase'] is None and strats['Redeploy']['parse']['status'] == 'not_modelled'
    combined = strats['Combined Arms']
    assert combined['modifier'] == 'Lance, AP Modifier -1'
    assert combined['parse']['conditions'] == ['If your unit is within range of an objective marker']
    assert strats['Command Re-Roll']['faction_id'] is None

    rules = {r['name']: r for r in result.data['rules']}
    assert set(rules) == {'Vanguard Doctrine', 'Lost Doctrine', 'Star Oath', 'Ghostly'}  # Not core or datasheet abilities
    assert rules['Vanguard Doctrine']['modifier'] == 'Hit Modifier +1' and rules['Vanguard Doctrine']['phase'] == 'Fighting'
    assert rules['Star Oath']['detachment_id'] is None and rules['Star Oath']['modifier'] == 'Reroll Hits 1'


def test_import_folder_needs_every_file(tmp_path):
    shutil.copytree(WAHAPEDIA_FIXTURES, tmp_path / 'w')
    (tmp_path / 'w' / 'Abilities.csv').unlink()
    with pytest.raises(wahapedia.WahapediaError, match='Abilities.csv'):
        wahapedia.import_folder(tmp_path / 'w')
    result, status = wahapedia.load_folder(tmp_path / 'w')
    assert result is None and 'Abilities.csv' in status['error']
    assert wahapedia.load_folder(tmp_path / 'nothing-here')[1] == {
        'folder': str(tmp_path / 'nothing-here'), 'loaded': False, 'error': None, 'source': None, 'report': [], 'counts': {}}


# --- Reading effects from text -----------------------------------------------------------------

@pytest.mark.parametrize('text, expected', [
    ('Until the end of the phase, ranged weapons equipped by models in your unit have the [SUSTAINED HITS 1] ability.',
     [('Attacker', 'Shooting', 'Sustained Hits 1')]),
    ("Your unit's melee attacks have [LETHAL HITS].", [('Attacker', 'Fighting', 'Lethal Hits')]),
    ("Your unit’s ranged attacks have [IGNORES COVER] and +1 AP.",
     [('Attacker', 'Shooting', 'Ignores Cover'), ('Attacker', 'Shooting', 'AP Modifier -1')]),
    ("Your unit's attacks can re-roll hit rolls of 1 and wound rolls of 1.",
     [('Attacker', 'Both', 'Reroll Hits 1'), ('Attacker', 'Both', 'Reroll Wounds 1')]),
    ("Your unit’s ranged attacks:\nCan re-roll wound rolls of 1.\nHave [IGNORES COVER].",
     [('Attacker', 'Shooting', 'Reroll Wounds 1'), ('Attacker', 'Shooting', 'Ignores Cover')]),
    ('Until the end of the phase, each time a model in your unit makes an attack, improve the Strength and Armour '
     'Penetration characteristics of that attack by 1.',
     [('Attacker', 'Both', 'Strength Modifier +1'), ('Attacker', 'Both', 'AP Modifier -1')]),
    ('Until the end of the phase, each time your model makes a melee attack, add 1 to the Wound roll.',
     [('Attacker', 'Fighting', 'Wound Modifier +1')]),
    ('Your unit has 4+ InSv.', [('Target', 'Both', 'Invulnerable Save 4+')]),
    ('Until the end of the phase, models in your unit have the Feel No Pain 5+ ability.',
     [('Target', 'Both', 'Feel No Pain 5+')]),
    ('Attacks that target your unit have -1 AP until that enemy unit has attacked.', [('Target', 'Both', 'AP Modifier +1')]),
    ('Ranged attacks that target your unit have -1 to hit rolls.', [('Target', 'Shooting', 'Hit Modifier -1')]),
    ('Until the attacking unit has finished making its attacks, each time an attack targets your unit, worsen the Armour '
     'Penetration characteristic of that attack by 1.', [('Target', 'Both', 'AP Modifier +1')]),
    ('Until the end of the phase, each time an attack targets a model in your unit, subtract 1 from the Damage '
     'characteristic of that attack.', [('Target', 'Both', 'Damage Modifier -1')]),
    ('Until the end of the phase, each time an attack targets either your ^^SMOKE^^ unit, or a unit that is not **fully '
     'visible** because of it, the target has the **benefit of cover** against that attack (13.08).',
     [('Target', 'Shooting', 'Stealth')]),
    ('Your unit shoots using **snap shooting**.', [('Attacker', 'Shooting', 'Snap Shooting')]),
    ("Until the end of the phase, that model's melee weapons have the [PRECISION] ability.",
     [('Attacker', 'Fighting', 'Precision')]),
])
def test_parse_effects_reads(text, expected):
    found = parse_effects(text)
    assert buffs(text) == expected
    assert found['skipped'] == []


def test_has_core_ability_finds_a_core_ability_on_its_own_line():
    text = 'Deep Strike\n\n**Fights First**\n\nBlade Dance: After this unit ends a Charge move, it does not have the Fights First ability.'
    assert has_core_ability(text, 'Fights First') and has_core_ability(text, 'deep strike')
    assert not has_core_ability('Blade Dance: this unit has the Fights First ability while leading.', 'Fights First')
    assert not has_core_ability(None, 'Fights First')


def test_damaged_threshold_reads_x_when_damaged_worsens_hit_rolls():
    tenth = ('Deep Strike\n\nDamaged: 1-5 wounds remaining: While this model has 1-5 wounds remaining, '
             'each time this model makes an attack, subtract 1 from the Hit roll.')
    assert damaged_threshold(tenth) == 5
    assert damaged_threshold('**Damaged 4**') == 4  # The 11th edition core ability
    oc_only = 'Damaged: 1-8 wounds remaining: While this model has 1-8 wounds remaining, subtract 4 from its Objective Control.'
    assert damaged_threshold(oc_only) is None and damaged_threshold(None) is None


def test_parse_effects_keeps_conditions_and_skips_the_rest():
    found = parse_effects('If your unit is within range of an objective marker, until the end of the phase, ranged '
                          'weapons equipped by models in your unit have the [LETHAL HITS] ability. Your unit can make a '
                          'surge move of up to D6".')
    assert [b['modifier'] for b in found['buffs']] == ['Lethal Hits']
    assert found['conditions'] == ['If your unit is within range of an objective marker']
    assert found['skipped'] == ['Your unit can make a surge move of up to D6".']

    targeted = parse_effects('Until the end of the phase, each time a model in your unit makes an attack that targets a '
                             'unit that is Below Half-strength, add 1 to the Hit roll.')
    assert [b['modifier'] for b in targeted['buffs']] == ['Hit Modifier +1']
    assert targeted['conditions'] == ['that targets a unit that is Below Half-strength']


@pytest.mark.parametrize('text', [
    'Your unit has Feel No Pain 5+ against mortal wounds.',  # Not the attacks the maths rolls
    "Your unit's attacks have: [Sustained Hits 1: non-Monster/Vehicle].",  # Only against some targets
    'Select the [SUSTAINED HITS 1] or [LETHAL HITS] ability.',  # A choice
    'Remove your unit from the battlefield and place it into Strategic Reserves.',
])
def test_parse_effects_leaves_what_it_cannot_model(text):
    assert parse_effects(text)['buffs'] == []


def test_choices_take_the_first_option_and_report_the_others():
    found = parse_effects("Your unit's melee attacks have:\n[Lance].\nOr: If the assault doctrine is active, [Lance] and +1 AP.")
    assert [b['modifier'] for b in found['buffs']] == ['Lance']
    assert found['skipped'] == ['Or: If the assault doctrine is active, [Lance] and +1 AP.']


def test_leader_parser_reads_improved_characteristics_of_that_attack():
    found, skipped = parse_leader_buffs('While this model is leading a unit, each time a model in that unit makes a '
                                        'melee attack, improve the Strength and Armour Penetration characteristics of '
                                        'that attack by 1.')
    assert [b['modifier'] for b in found] == ['Strength Modifier +1', 'AP Modifier -1'] and skipped == []


def test_interpret_picks_one_side_and_keeps_the_stratagems_phase():
    read = wahapedia.interpret('Each time an attack targets your unit, subtract 1 from the Hit roll. Your unit has 4+ '
                               "InSv. Your unit's melee attacks have [LANCE].", 'Shooting')
    assert (read['applies_to'], read['phase'], read['modifier']) == ('Target', 'Shooting', 'Hit Modifier -1, Invulnerable Save 4+')
    assert read['parse']['status'] == 'partial'
    assert read['parse']['skipped'] == ['Applies to the attacker: Lance']


# --- The app ---------------------------------------------------------------------------------------

@pytest.fixture
def app_client(make_client):
    client, data_file = make_client(APP_DATA, bsdata=BSDATA_FIXTURES, wahapedia=WAHAPEDIA_FIXTURES)
    return client, lambda: json.loads(data_file.read_text(encoding='utf-8'))


def faction_ids(client):
    return {f['name']: f['id'] for f in client.get('/api/data/factions').get_json()}


def available(client, side, faction=None, detachment=None):
    query = f'side={side}' + (f'&faction_id={faction}' if faction else '') + (f'&detachment={detachment}' if detachment else '')
    return {e['name']: e for e in client.get(f'/api/rules/available?{query}').get_json()}


def test_stratagems_are_offered_with_their_detachment(app_client):
    client, _ = app_client
    star = faction_ids(client)['Starfarers']
    plain = available(client, 'Attacker', star)
    assert 'Void Strike' not in plain and plain['Star Oath']['modifier'] == 'Reroll Hits 1'  # Army rule: whole faction

    vanguard = available(client, 'Attacker', star, 'd-vanguard')
    assert vanguard['Void Strike']['modifier'] == 'Lethal Hits' and vanguard['Void Strike']['has_effect'] is True
    assert vanguard['Void Strike']['source'] == 'wahapedia' and vanguard['Void Strike']['detachment'] == 'Void Vanguard'
    assert 'Redeploy' not in vanguard  # Used in the Movement phase
    assert vanguard['Vanguard Doctrine']['kind'] == 'rule'

    bastion = available(client, 'Target', star, 'd-bastion')
    assert bastion['Bastion of Stars']['modifier'] == 'Wound Modifier -1'
    assert bastion['Smokescreen']['kind'] == 'core'


def test_wahapedia_replaces_the_app_datas_own_of_the_same_name(app_client):
    client, saved = app_client
    core = {s['name']: s for s in client.get('/api/data/stratagems?core=1').get_json()}
    assert 'Command Re-roll' not in core and core['Command Re-Roll']['source'] == 'wahapedia'
    assert core['My Own Gambit']['source'] == 'app'  # Nothing of that name in Wahapedia
    names = [s['name'] for s in saved()['stratagems']]
    assert names == ['Command Re-roll', 'My Own Gambit']  # Superseded, but kept in the file
    star = faction_ids(client)['Starfarers']
    assert 'Home Rule' in available(client, 'Attacker', star)  # No Wahapedia rule of that name

    old = client.post('/api/simulate', json={
        'attacker': {'attacks': '1', 'skill': 4, 'strength': 3, 'ap': 0, 'damage': '1', 'abilities': [], 'models': 10,
                     'type': 'ranged'},
        'defender': {'toughness': 3, 'save': 7, 'wounds': 1, 'model_count': 10},
        'conditions': {}, 'method': 'analytical', 'attacker_rules': {'stratagem_ids': [2, 1]}}).get_json()
    assert old['applied']['attacker'] == ['Lethal Hits']  # A saved tick of the replaced one does nothing


def test_matching_to_bsdata(app_client):
    client, _ = app_client
    ids = faction_ids(client)
    allied = available(client, 'Attacker', ids['Allies'], 'd-task-force')['Combined Arms']
    assert allied['modifier'] == 'Lance, AP Modifier -1'
    assert allied['conditions'] == ['If your unit is within range of an objective marker']
    status = client.get('/api/data/status?report=1').get_json()['wahapedia']
    assert status['loaded'] and status['last_update'] == '2026-10-01 12:00:00'
    assert (status['stratagems'], status['rules']) == (6, 2)  # Not Lost Signal or Lost Doctrine (no BSData detachment), or Ghostly
    assert status['report_counts'] == {'wahapedia_detachment': 1, 'wahapedia_faction': 1}
    assert {(r['faction'], r['detail']) for r in status['report']} == {
        ('Starfarers', 'Detachment "Lost Fleet" is not in BSData'),
        ('Ghost Legion', 'No BSData faction of this name or with its detachments')}


def test_ticked_stratagems_change_the_maths_for_attached_targets(app_client):
    client, _ = app_client
    units = {u['name']: u for u in client.get(f'/api/data/units?faction_id={faction_ids(client)["Starfarers"]}').get_json()}
    void, captain = units['Void Warriors'], units['Star Captain']
    bastion = available(client, 'Target', void['faction_id'], 'd-bastion')['Bastion of Stars']

    def shoot(stratagems):
        return client.post('/api/simulate', json={
            'attacker': {'weapons': [{'name': 'Big gun', 'type': 'ranged', 'attacks': '4', 'skill': 3, 'strength': 10,
                                      'ap': 4, 'damage': '2', 'abilities': [], 'models': 5}]},
            'defender': {'toughness': void['toughness'], 'save': void['save'], 'wounds': void['wounds'], 'model_count': 5,
                         'keywords': [], 'leader_id': captain['id'], 'faction_id': void['faction_id']},
            'target_rules': {'stratagem_ids': stratagems}, 'conditions': {}, 'method': 'analytical'}).get_json()
    plain, covered = shoot([]), shoot([bastion['id']])
    assert 'Wound Modifier -1' in covered['applied']['target'] and 'Wound Modifier -1' not in plain['applied']['target']
    assert covered['summary']['leader_damage'] < plain['summary']['leader_damage']
    assert covered['summary']['average_damage'] < plain['summary']['average_damage']


def test_data_tab_lists_them_read_only(app_client):
    client, _ = app_client
    star = faction_ids(client)['Starfarers']
    strats = {s['name']: s for s in client.get(f'/api/data/stratagems?faction_id={star}').get_json()}
    redeploy = strats['Redeploy']
    assert redeploy['phase'] is None and redeploy['parse']['status'] == 'not_modelled'
    assert redeploy['text'].startswith('**WHEN:** Your Movement phase.')
    assert client.delete(f'/api/data/stratagems/{redeploy["id"]}').status_code == 403
    rules = {r['name']: r for r in client.get(f'/api/data/rules?faction_id={star}').get_json()}
    assert client.delete(f'/api/data/rules/{rules["Star Oath"]["id"]}').status_code == 403
    counts = {f['name']: f for f in client.get('/api/data/factions').get_json()}['Starfarers']
    assert (counts['rule_count'], counts['stratagem_count']) == (3, 3)  # Home Rule, Vanguard Doctrine, Star Oath


def test_editing_how_the_maths_reads_one(app_client, make_client):
    client, saved = app_client
    star = faction_ids(client)['Starfarers']
    redeploy = {s['name']: s for s in client.get(f'/api/data/stratagems?faction_id={star}').get_json()}['Redeploy']
    edited = client.put(f'/api/data/stratagems/{redeploy["id"]}', json={
        'name': 'Ignored', 'applies_to': 'Attacker', 'phase': 'Shooting', 'modifier': 'Hit Modifier +1'}).get_json()
    assert (edited['name'], edited['modifier'], edited['edited']) == ('Redeploy', 'Hit Modifier +1', True)
    assert saved()['wahapedia_overrides'] == [{'kind': 'stratagem', 'wahapedia_id': '103', 'name': 'Redeploy',
                                               'applies_to': 'Attacker', 'phase': 'Shooting', 'modifier': 'Hit Modifier +1'}]
    assert [s['name'] for s in saved()['stratagems']] == ['Command Re-roll', 'My Own Gambit']  # Wahapedia's are never saved
    assert 'Redeploy' in available(client, 'Attacker', star, 'd-vanguard')  # Now has a phase

    restarted, _ = make_client(saved(), bsdata=BSDATA_FIXTURES, wahapedia=WAHAPEDIA_FIXTURES)
    again = {s['name']: s for s in restarted.get(f'/api/data/stratagems?faction_id={star}').get_json()}['Redeploy']
    assert (again['id'], again['modifier'], again['edited']) == (redeploy['id'], 'Hit Modifier +1', True)  # Same ID

    reset = restarted.put(f'/api/data/stratagems/{redeploy["id"]}', json={'reset': True}).get_json()
    assert (reset['modifier'], reset['phase'], reset['edited']) == (None, None, False)
    assert restarted.put(f'/api/data/stratagems/{redeploy["id"]}', json={'applies_to': 'Nobody'}).status_code == 400


def test_edits_are_kept_while_wahapedia_is_not_loaded(make_client):
    data = dict(APP_DATA, wahapedia_overrides=[{'kind': 'rule', 'wahapedia_id': '401', 'name': 'Star Oath',
                                                 'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Reroll Hits'}])
    client, data_file = make_client(data, bsdata=BSDATA_FIXTURES)
    client.post('/api/data/stratagems', json={'faction_id': None, 'name': 'New One', 'applies_to': 'Attacker'})
    assert json.loads(data_file.read_text(encoding='utf-8'))['wahapedia_overrides'] == data['wahapedia_overrides']
    core = {s['name'] for s in client.get('/api/data/stratagems?core=1').get_json()}
    assert core == {'Command Re-roll', 'My Own Gambit', 'New One'}  # The app data's own, without Wahapedia


def test_core_stratagems_load_without_bsdata(make_client):
    client, _ = make_client(APP_DATA, wahapedia=WAHAPEDIA_FIXTURES)
    core = {s['name'] for s in client.get('/api/data/stratagems?core=1').get_json()}
    assert core == {'Command Re-Roll', 'Smokescreen', 'My Own Gambit'}
    assert 'Rules and stratagems powered by' in client.get('/setup').get_data(as_text=True)


# --- Download latest -------------------------------------------------------------------------------

@pytest.fixture
def fake_wahapedia(monkeypatch):
    files = {p.name: p.read_bytes() for p in WAHAPEDIA_FIXTURES.glob('*.csv')}
    monkeypatch.setattr(wahapedia_download, 'fetch', lambda name: files[name])
    return files


def test_download_latest(make_client, tmp_path, fake_wahapedia):
    target = tmp_path / 'downloaded'
    client, data_file = make_client(APP_DATA, bsdata=BSDATA_FIXTURES, wahapedia=target)
    assert client.get('/api/data/status').get_json()['wahapedia']['loaded'] is False
    status = client.post('/api/wahapedia/download').get_json()['wahapedia']
    assert status['loaded'] and status['stratagems'] == 6
    assert sorted(p.name for p in target.iterdir()) == sorted(wahapedia.FILES)
    settings = json.loads((data_file.parent / 'settings.json').read_text(encoding='utf-8'))
    assert settings['wahapedia_download']['last_update'] == '2026-10-01 12:00:00'
    assert '2026-10-01' in client.get('/data').get_data(as_text=True)  # Footer credit


def test_failed_download_keeps_the_current_files(make_client, tmp_path, monkeypatch, fake_wahapedia):
    target = tmp_path / 'downloaded'
    shutil.copytree(WAHAPEDIA_FIXTURES, target)
    client, _ = make_client(APP_DATA, bsdata=BSDATA_FIXTURES, wahapedia=target)

    def offline(name):
        raise wahapedia_download.DownloadError('Could not reach Wahapedia: offline')
    monkeypatch.setattr(wahapedia_download, 'fetch', offline)
    response = client.post('/api/wahapedia/download')
    assert response.status_code == 502 and 'offline' in response.get_json()['error']

    monkeypatch.setattr(wahapedia_download, 'fetch', lambda name: b'not|a|table' if name == 'Stratagems.csv' else fake_wahapedia[name])
    response = client.post('/api/wahapedia/download')
    assert response.status_code == 502 and 'could not be read' in response.get_json()['error']

    assert client.get('/api/data/status').get_json()['wahapedia']['stratagems'] == 6
    assert not list(target.parent.glob('wahapedia-new-*'))  # The half-done downloads were cleared away
