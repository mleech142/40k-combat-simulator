"""
The BSData importer (src/combat_sim/bsdata.py) against the hand-made catalogues in
tests/fixtures/bsdata: invented factions with the structure of the real BSData files.
"""

import json
import shutil
from pathlib import Path

import pytest

from combat_sim.bsdata import BSDataError, import_folder

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'bsdata'
KNOWN = ['Pistol', 'Rapid Fire', 'Hazardous', 'Heavy', 'Anti', 'Lethal Hits', 'Sustained Hits', 'Torrent',
         'Ignores Cover', 'Assault']


@pytest.fixture(scope='module')
def result():
    return import_folder(FIXTURES, known_keywords=KNOWN)


def faction(result, name):
    return next(f for f in result.data['factions'] if f['name'] == name)


def unit(result, name):
    return next(u for u in result.data['units'] if u['name'] == name)


def loadout(result, u, kind):
    weapons = {w['id']: w['name'] for w in result.data[f'{kind}_weapons']}
    return {weapons[e['weapon_id']]: e['qty'] for e in u[f'{kind}_weapons']}


def options(result, u, kind):
    weapons = {w['id']: w['name'] for w in result.data[f'{kind}_weapons']}
    return sorted(weapons[i] for i in u[f'{kind}_options'])


def reported(result, kind):
    return [(i['faction'], i['unit'], i['detail']) for i in result.report if i['kind'] == kind]


def faction_units(result, name):
    fid = faction(result, name)['id']
    return sorted(u['name'] for u in result.data['units'] if u['faction_id'] == fid)


# --- Factions ------------------------------------------------------------------

def test_factions_are_catalogues_named_without_their_prefix(result):
    assert [f['name'] for f in result.data['factions']] == ['Allies', 'Neutral Forces', 'Starfarers']
    assert faction(result, 'Starfarers')['bsdata_id'] == 'cat-star'


def test_faction_takes_its_own_and_imported_units_but_not_allies(result):
    assert faction_units(result, 'Starfarers') == [
        'Beast Pack', 'Field Bunker', 'Gun Crew', 'Scout Drone', 'Star Captain', 'Star Walker [Legends]',
        'Void Warriors']
    assert faction_units(result, 'Allies') == ['Ally Agent']  # Linked from Starfarers, but not one of theirs


def test_unclaimed_library_units_join_or_form_a_faction(result):
    # Field Bunker has no faction category: it goes with the faction that took its library
    assert 'Field Bunker' in faction_units(result, 'Starfarers')
    # Nobody imports the neutral library: it becomes a faction of its own
    assert faction_units(result, 'Neutral Forces') == ['Neutral Wall']


def test_catalogue_without_units_is_skipped_and_reported(result):
    assert ('Empty Stub', None, 'no units of its own') in reported(result, 'skipped_faction')


def test_hidden_root_entries_are_left_out(result):
    assert [u['name'] for u in result.data['units']].count('Star Captain') == 1


# --- Units: stats, keywords, abilities -------------------------------------------

def test_unit_stats_and_keywords(result):
    u = unit(result, 'Void Warriors')
    assert u['bsdata_id'] == 'u-void'
    assert (u['toughness'], u['save'], u['wounds'], u['invulnerable_save'], u['feel_no_pain']) == (4, 3, 2, None, None)
    assert (u['move'], u['oc'], u['leadership']) == ('6', 2, 6)
    assert u['keywords'] == ['Infantry', 'Battleline']  # "Faction: ..." categories left out
    assert u['legends'] is False


def test_invulnerable_save_and_feel_no_pain_from_ability_text(result):
    u = unit(result, 'Star Captain')
    assert u['invulnerable_save'] == 4
    assert u['feel_no_pain'] == 5


def test_feel_no_pain_from_core_rule_link_and_legends_flag(result):
    u = unit(result, 'Star Walker [Legends]')
    assert u['feel_no_pain'] == 6
    assert u['legends'] is True
    assert 'Deep Strike' in u['abilities']


def test_stealth_and_damage_reduction_become_unit_modifiers(result):
    assert unit(result, 'Void Warriors')['modifiers'] == ['Stealth']
    assert unit(result, 'Scout Drone')['modifiers'] == ['Damage Modifier -1']


def test_move_without_a_number_is_empty(result):
    assert unit(result, 'Field Bunker')['move'] is None
    assert unit(result, 'Field Bunker')['leadership'] is None


def test_abilities_text_lists_ability_profiles(result):
    abilities = unit(result, 'Star Captain')['abilities']
    assert 'Iron Will: This model has the Feel No Pain 5+ ability.' in abilities
    assert abilities.startswith('Leader: This model can be attached')


def test_leader_targets_matched_to_faction_units(result):
    assert unit(result, 'Star Captain')['can_lead'] == ['Void Warriors']
    assert ('Starfarers', 'Star Captain', 'can lead "Missing Squad", which is not in this faction') in reported(result, 'leader')


def test_mixed_stat_lines_use_the_most_numerous_model(result):
    u = unit(result, 'Beast Pack')
    assert (u['toughness'], u['save'], u['wounds']) == (5, 6, 2)  # 3 Beasts over 1 Beast Master
    assert any(r[1] == 'Beast Pack' for r in reported(result, 'mixed_profile'))


# --- Model counts, loadouts and points ---------------------------------------------

def test_default_size_is_the_minimum_configuration(result):
    assert unit(result, 'Void Warriors')['default_models'] == 5  # Sergeant + 4 warriors (the group default)
    assert unit(result, 'Beast Pack')['default_models'] == 4
    assert unit(result, 'Star Captain')['default_models'] == 1


def test_default_loadout_counts_models_carrying_each_weapon(result):
    u = unit(result, 'Void Warriors')
    assert loadout(result, u, 'ranged') == {
        'Laser pistol': 1, 'Laser rifle - standard': 4, 'Laser rifle - overcharge': 4}
    assert loadout(result, u, 'melee') == {'Blade': 1, 'Close combat weapon': 4}  # Blade is the group default


def test_weapon_options_list_wargear_not_in_the_default_loadout(result):
    u = unit(result, 'Void Warriors')
    assert options(result, u, 'ranged') == ['Heavy laser']
    assert options(result, u, 'melee') == ['Power axe']
    captain = unit(result, 'Star Captain')
    assert loadout(result, captain, 'ranged') == {}
    assert options(result, captain, 'ranged') == ['Plasma pistol']


def test_points_for_every_legal_size(result):
    u = unit(result, 'Void Warriors')
    sizes = {b['models']: b['points'] for b in u['points_by_size']}
    assert sizes == {5: 80, 6: 160, 7: 160, 8: 160, 9: 160, 10: 160}
    assert u['points_by_size'][0] == {'models': 5, 'points': 80}  # First entry: the datasheet size
    assert u['points_cost'] == 80


def test_repeated_datasheet_surcharge_never_applies_to_a_lone_unit(result):
    assert unit(result, 'Void Warriors')['points_cost'] == 80
    assert not any(r[1] == 'Void Warriors' for r in reported(result, 'points'))


def test_points_from_repeats_modifier_groups_and_the_army_taking_the_unit(result):
    # 40 base, -5 when not taken by the Allies army (the +100 for Allies doesn't apply),
    # +15 per gunner (a repeating modifier group), then 150 flat once the Crew group holds 4
    u = unit(result, 'Gun Crew')
    assert u['points_by_size'] == [{'models': 2, 'points': 65}, {'models': 3, 'points': 80},
                                   {'models': 4, 'points': 150}]
    assert loadout(result, u, 'ranged') == {'Crew gun': 2}


def test_unevaluable_cost_modifier_is_skipped_and_reported(result):
    assert unit(result, 'Beast Pack')['points_cost'] == 60
    assert any(r[1] == 'Beast Pack' for r in reported(result, 'points'))


# --- Weapons ------------------------------------------------------------------------

def test_weapon_profile_fields(result):
    rifle = next(w for w in result.data['ranged_weapons'] if w['name'] == 'Laser rifle - overcharge')
    assert rifle['bsdata_id'] == 'p-rifle-over'
    assert (rifle['attacks'], rifle['ballistic_skill'], rifle['strength'], rifle['armor_penetration'],
            rifle['damage'], rifle['range_inches']) == ('2', 3, 7, 2, '2', 24)
    assert rifle['abilities'] == ['Hazardous']


def test_torrent_weapon_has_no_skill_and_empty_keywords_are_dropped(result):
    flamer = next(w for w in result.data['ranged_weapons'] if w['name'] == 'Walker flamer')
    assert flamer['ballistic_skill'] is None
    assert flamer['abilities'] == ['Ignores Cover', 'Torrent']
    feet = next(w for w in result.data['melee_weapons'] if w['name'] == 'Armoured feet')
    assert feet['abilities'] == []  # Keywords "-"


def test_weapons_shared_within_a_faction_appear_once(result):
    star = faction(result, 'Starfarers')['id']
    pistols = [w for w in result.data['ranged_weapons'] if w['name'] == 'Laser pistol']
    assert [w['faction_id'] for w in pistols].count(star) == 1
    assert len(pistols) == 2  # The Allies faction has its own


def test_weapon_without_readable_strength_is_skipped_and_reported(result):
    assert not any(w['name'] == 'Vortex charge' for w in result.data['ranged_weapons'])
    assert ('Starfarers', None, 'Vortex charge: unreadable strength, skipped') in reported(result, 'weapon_stats')


def test_unknown_weapon_keywords_are_reported(result):
    assert reported(result, 'unknown_keyword') == [('Starfarers', None, 'Heavy laser: "Laser Burn"')]


def test_keyword_check_is_skipped_without_known_keywords():
    assert not [i for i in import_folder(FIXTURES).report if i['kind'] == 'unknown_keyword']


def test_source_records_revisions(result):
    assert result.source['game_system'] == {'name': 'Test System', 'revision': 7}
    assert result.source['catalogues']['Xenos - Starfarers'] == 3


def test_import_is_deterministic():
    assert import_folder(FIXTURES).data == import_folder(FIXTURES).data


# --- The result loads into the app ---------------------------------------------------

def test_imported_data_loads_into_the_app(result, make_client):
    data = {'schema_version': 1, 'source': None, **result.data}
    client, _ = make_client(data)
    assert 'Void Warriors' in {u['name'] for u in client.get('/api/units').get_json()}

    detail = client.get(f'/api/units/{unit(result, "Void Warriors")["id"]}').get_json()
    assert detail['points_by_model_count'][5:7] == [80, 160]
    assert {w['name']: w['qty'] for w in detail['ranged_weapons']} == {
        'Laser pistol': 1, 'Laser rifle - standard': 4, 'Laser rifle - overcharge': 4}
    # One "Weapon - Profile" variant is ticked by default
    ticked = {w['name']: w['default_use'] for w in detail['ranged_weapons']}
    assert [ticked['Laser rifle - standard'], ticked['Laser rifle - overcharge']].count(True) == 1


# --- Bad folders ----------------------------------------------------------------------

@pytest.fixture
def scratch(tmp_path):
    folder = tmp_path / 'bsdata'
    shutil.copytree(FIXTURES, folder)
    return folder


def test_missing_folder(tmp_path):
    with pytest.raises(BSDataError, match='is not a folder'):
        import_folder(tmp_path / 'nope')


def test_missing_game_system(scratch):
    (scratch / 'Test System.json').unlink()
    with pytest.raises(BSDataError, match='No game system file'):
        import_folder(scratch)


def test_unreadable_json(scratch):
    (scratch / 'Broken.json').write_text('{not json', encoding='utf-8')
    with pytest.raises(BSDataError, match='Broken.json: not readable JSON'):
        import_folder(scratch)


def test_not_a_catalogue(scratch):
    (scratch / 'Other.json').write_text(json.dumps({'something': 1}), encoding='utf-8')
    with pytest.raises(BSDataError, match='Other.json: not a BattleScribe catalogue'):
        import_folder(scratch)


def test_unsupported_battlescribe_version(scratch):
    path = scratch / 'Imperium - Allies.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    content['catalogue']['battleScribeVersion'] = 3.0
    path.write_text(json.dumps(content), encoding='utf-8')
    with pytest.raises(BSDataError, match='BattleScribe version 3.0 is not supported'):
        import_folder(scratch)


def test_catalogue_from_another_game_system(scratch):
    path = scratch / 'Imperium - Allies.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    content['catalogue']['gameSystemId'] = 'other-system'
    path.write_text(json.dumps(content), encoding='utf-8')
    with pytest.raises(BSDataError, match='belongs to a different game system'):
        import_folder(scratch)


# --- Detachments and army rules ---------------------------------------------------------

def test_detachments_of_the_faction_but_not_of_allies(result):
    star = faction(result, 'Starfarers')['id']
    names = {d['name']: d for d in result.data['detachments'] if d['faction_id'] == star}
    assert sorted(names) == ['Star Armada', 'Star Bastion', 'Star Citadel', 'Void Vanguard']  # Not "Allied Task Force"
    assert names['Void Vanguard']['bsdata_id'] == 'd-vanguard'
    # Detachment Points, and categories split into force dispositions and Unique Tags ("3DP Detachment" is neither)
    assert (names['Void Vanguard']['dp'], names['Void Vanguard']['dispositions'], names['Void Vanguard']['tags']) == \
        (2, ['Purge the Foe'], [])
    assert (names['Star Bastion']['dp'], names['Star Bastion']['tags']) == (1, ['Fortress Detachment'])
    assert (names['Star Armada']['dp'], names['Star Armada']['dispositions'], names['Star Armada']['tags']) == \
        (3, ['Priority Assets'], [])
    assert names['Void Vanguard']['rules'] == [{'name': 'Swift Strike', 'text': 'Units from this army can Advance and shoot.'}]
    allies = faction(result, 'Allies')['id']
    assert [d['name'] for d in result.data['detachments'] if d['faction_id'] == allies] == ['Allied Task Force']


def test_army_rules_are_the_faction_catalogues_rules(result):
    assert [r['name'] for r in faction(result, 'Starfarers')['army_rules']] == ['Star Pact']
    assert faction(result, 'Allies')['army_rules'] == []


# --- Rule text: tidied for display, plain for parsing ---------------------------------------

def test_tidy_text_keeps_markup_but_not_odd_spacing():
    from combat_sim.bsdata import tidy_text
    assert tidy_text('A ^^**Character^^**  model can re‑roll.  \n') == 'A ^^**Character^^** model can re-roll.'


def test_plain_text_drops_every_markup_style():
    from combat_sim.bsdata import plain_text
    text = ('While a ^^**Character^^** leads, **^^Infantry^^** and ^^**Mounted**^^ units gain [^^Lance^^]; '
            '*footnote*, <ins>Or:</ins> PLAGUE MARINES†')
    assert plain_text(text) == ('While a Character leads, Infantry and Mounted units gain [Lance]; '
                                'footnote, Or: PLAGUE MARINES')


def test_marked_up_and_oddly_spaced_text_is_still_parsed(tmp_path):
    import shutil
    folder = tmp_path / 'bsdata'
    shutil.copytree(FIXTURES, folder)
    path = folder / 'Xenos - Starfarers Library.json'
    content = json.loads(path.read_text(encoding='utf-8'))
    drone = next(e for e in content['catalogue']['sharedSelectionEntries'] if e['id'] == 'u-drone')
    drone['profiles'][1]['characteristics'][0]['$text'] = (
        'Each time an attack is allocated to ^^**this model^^**, subtract 1 from the **Damage** characteristic of that attack.')
    path.write_text(json.dumps(content), encoding='utf-8')
    star = folder / 'Xenos - Starfarers.json'
    star.write_text(star.read_text(encoding='utf-8').replace('**^^VOID WARRIORS^^**', '^^**VOID\u00a0WARRIORS^^**'),
                    encoding='utf-8')

    result = import_folder(folder)
    assert unit(result, 'Scout Drone')['modifiers'] == ['Damage Modifier -1']
    assert unit(result, 'Star Captain')['can_lead'] == ['Void Warriors']
    assert ' ' not in unit(result, 'Scout Drone')['abilities']
    assert '^^**this model^^**' in unit(result, 'Scout Drone')['abilities']  # Markup kept for display


# --- Leader buffs -----------------------------------------------------------------------------

from combat_sim.bsdata import parse_leader_buffs  # noqa: E402

LEADING = 'While this model is leading a unit, '


@pytest.mark.parametrize('text,expected', [
    # Real BSData phrasings
    ('melee weapons equipped by models in that unit have the [Sustained Hits 1] and [Lance] abilties.',
     [('Attacker', 'Fighting', 'Sustained Hits 1'), ('Attacker', 'Fighting', 'Lance')]),
    ('weapons equipped by models in that unit have the [LETHAL HITS] ability.', [('Attacker', 'Both', 'Lethal Hits')]),
    ('ranged weapons equipped by models in that unit have the [ASSAULT] ability.', [('Attacker', 'Shooting', 'Assault')]),
    ('each time a model in that unit makes an attack, add 1 to the Wound roll.', [('Attacker', 'Both', 'Wound Modifier +1')]),
    ('each time a model in that unit makes a melee attack, you can re-roll the Hit roll.',
     [('Attacker', 'Fighting', 'Reroll Hits')]),
    ('each time a model in that unit makes an attack, re-roll a Hit roll of 1.', [('Attacker', 'Both', 'Reroll Hits 1')]),
    ('each time a model in that unit makes a ranged attack, a successful unmodifed Hit roll of 5+ scores a Critical Hit.',
     [('Attacker', 'Shooting', 'Critical Hits 5+')]),
    ('improve the Strength and Armour Penetration characteristic of melee weapons equipped by models in that unit by 1.',
     [('Attacker', 'Fighting', 'Strength Modifier +1'), ('Attacker', 'Fighting', 'AP Modifier -1')]),
    ('models in that unit have a 4+ invulnerable save.', [('Target', 'Both', 'Invulnerable Save 4+')]),
    ('models in that unit have the Feel No Pain 5+ ability.', [('Target', 'Both', 'Feel No Pain 5+')]),
    ('each time a melee attack targets that unit, subtract 1 from the Hit roll.', [('Target', 'Fighting', 'Hit Modifier -1')]),
    # Marked up, with a second sentence
    ('^^**Melee**^^ weapons equipped by models in that unit have the **[LETHAL HITS]** ability. In addition, '
     'each time a model in that unit makes an attack, a Critical Hit is scored on an unmodified Hit roll of 5+, '
     'instead of only on a 6.', [('Attacker', 'Fighting', 'Lethal Hits'), ('Attacker', 'Both', 'Critical Hits 5+')]),
])
def test_leader_buffs_parsed(text, expected):
    buffs, skipped = parse_leader_buffs(LEADING + text)
    assert [(b['applies_to'], b['phase'], b['modifier']) for b in buffs] == expected
    assert skipped == []


@pytest.mark.parametrize('text', [
    'models in that unit have the Feel No Pain 4+ ability against Psychic Attacks.',  # Conditional
    'each time a model in that unit makes an attack which targets a unit which is Afflicted, that attack has the '
    '[Sustained Hits 1] ability.',
    'you can re-roll Charge rolls made for that unit.',  # Not damage maths
    'models in that unit have the Fights First ability.',
])
def test_leader_text_not_modelled_is_reported(text):
    buffs, skipped = parse_leader_buffs(LEADING + text)
    assert buffs == [] and skipped == [text]


def test_text_without_leading_gives_nothing():
    assert parse_leader_buffs('Models in this unit have the Feel No Pain 5+ ability.') == ([], [])


def test_leader_buffs_of_a_leader_unit(result):
    assert unit(result, 'Star Captain')['leader_buffs'] == [
        {'applies_to': 'Attacker', 'phase': 'Shooting', 'modifier': 'Lethal Hits'},
        {'applies_to': 'Target', 'phase': 'Both', 'modifier': 'Hit Modifier -1'}]
    assert unit(result, 'Void Warriors')['leader_buffs'] == []  # Not a leader
    assert ('Starfarers', 'Star Captain', 'not modelled: "You can re-roll Charge rolls made for that unit."') in \
        reported(result, 'leader_buff')
