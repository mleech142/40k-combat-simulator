"""
Death Guard's Nurgle's Gift in Play mode (effects batch 2; src/combat_sim/play/game.py, contagion.py): Contagion Range
by battle round and bonuses, Afflicted enemy units and the Plague's effects on their attacks, saves, Move, Leadership
and Objective Control, choosing the Plague, Verminous Haze's Infiltrators, Plague Marines' Infused with the Blessings
of Nurgle, and bonuses against Afflicted targets. The rule and the Simulator's side are in test_contagion.py.
"""

from combat_sim.play import game
from combat_sim.play.dice import Dice
from combat_sim.play.game import affliction_of, afflictions, contagion_ranges, infiltrators, new_game, objective_control, unit_by_id
from test_play_dice import FixedRolls
from test_play_game import army, gun, part
from test_play_terrain import BASE, RIFLE, game_on
from test_play_turns import act, at, refused

WOUND_BONUS = {'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Wound Modifier +1', 'when': 'afflicted'}


def marine(x, y, **extra):
    """A Death Guard model with a S4 rifle (BS3+)."""
    return (part('Marine', 1, 3, base=BASE, weapons=[RIFLE], contagion=True, **extra), (x, y))


def foe(x, y, save=3, **extra):
    return (part('Target', 1, 3, base=BASE, save=save, weapons=[RIFLE], **extra), (x, y))


SHOOT = {'type': 'shoot', 'unit': 'p1-marine', 'target': 'p2-target'}


# --- Contagion Range -------------------------------------------------------------------------------------------

def test_enemy_units_within_contagion_range_are_afflicted():
    state = game_on([], [marine(30, 20)], [foe(30, 24)])  # 4" apart, centre to centre: 2.74" base to base
    assert contagion_ranges(state, unit_by_id(state, 'p1-marine')) == {'p1-marine-0-1': 3}
    assert afflictions(state) == {'p2-target': 'none'}  # No Plague chosen yet
    state = game_on([], [marine(30, 20)], [foe(30, 26)])  # 4.74" apart
    assert afflictions(state) == {}
    state['turn']['round'] = 2  # 6" from the second battle round
    assert afflictions(state) == {'p2-target': 'none'}
    state['players'][0]['plague'] = 'rattlejoint'
    assert affliction_of(state, unit_by_id(state, 'p2-target')) == 'rattlejoint'
    assert affliction_of(state, unit_by_id(state, 'p1-marine')) is None  # Only the other army's units


def test_contagion_range_bonuses():
    state = game_on([], [marine(30, 20, contagion_bonus=3)], [foe(30, 26)])  # Gift of Poxes: 6" in round 1
    assert afflictions(state) == {'p2-target': 'none'}
    hero = (part('Marine', 1, 3, base=BASE, contagion=True, keywords=['Infantry', 'Character']), (30, 20))
    state = game_on([], [hero], [foe(30, 26)])
    assert afflictions(state) == {}
    state['players'][0]['contagion_bonuses'] = [{'rule': 'Hypervirulent Strains', 'keywords': 'DEATH GUARD CHARACTER', 'inches': 3}]
    assert afflictions(state) == {'p2-target': 'none'}  # "Death Guard" isn't a keyword units carry: Character is
    state['turn']['round'] = 3
    assert contagion_ranges(state, unit_by_id(state, 'p1-marine')) == {'p1-marine-0-1': 12}  # 9 + 3, never more than 12


def test_no_contagion_without_nurgles_gift():
    state = game_on([], [(part('Marine', 1, 3, base=BASE, weapons=[RIFLE]), (30, 20))], [foe(30, 24)])
    assert afflictions(state) == {} and affliction_of(state, unit_by_id(state, 'p2-target')) is None


# --- Afflicted units in battle ----------------------------------------------------------------------------------

def test_an_afflicted_target_has_minus_one_toughness():
    near = game_on([], [marine(30, 20)], [foe(30, 24)])
    far = game_on([], [marine(30, 20)], [foe(30, 35)])
    # Hit on a 4, wound on a 3: S4 needs 4+ against T4, 3+ against T3; the save fails on a 1
    assert act(near, SHOOT, 4, 3, 1)[1]['attacks'][0]['wounds'] == 1
    assert act(far, SHOOT, 4, 3, 1)[1]['attacks'][0]['wounds'] == 0


def test_rattlejoint_ague_worsens_the_save():
    state = game_on([], [marine(30, 20)], [foe(30, 24)])
    # A save of 3 saves on 3+
    assert unit_by_id(act(state, SHOOT, 4, 3, 3)[0], 'p2-target')['models'][0]['wounds_left'] == 3
    state['players'][0]['plague'] = 'rattlejoint'
    assert unit_by_id(act(state, SHOOT, 4, 3, 3)[0], 'p2-target')['models'][0]['wounds_left'] == 2  # Needs 4+ now


def test_skullsquirm_blight_gives_cover_against_the_afflicted_units_shots():
    state = game_on([], [marine(30, 20)], [foe(30, 24)])
    state['players'][0]['plague'] = 'skullsquirm'
    state['turn']['player'] = 1
    _, event = act(state, {'type': 'shoot', 'unit': 'p2-target', 'target': 'p1-marine'}, 3, 1, 1, 1)
    assert event['attacks'][0]['cover'] is True and event['attacks'][0]['hits'] == 0  # BS3+ hits on 4+ now


def test_skullsquirm_blight_minus_one_to_hit_in_melee():
    unit = unit_by_id(game_on([], [marine(30, 20)], [foe(30, 24)]), 'p2-target')
    unit['parts'][0]['weapons'] = [gun('Claw', kind='melee', range=0)]
    profile = game._attacker(unit, 0, 0, 1, ('skullsquirm', None))
    assert 'Hit Modifier -1' in profile.abilities
    assert 'Hit Modifier -1' not in game._attacker(unit, 0, 0, 1, ('rattlejoint', None)).abilities


def test_scabrous_soulrot_move_leadership_and_objective_control():
    state = game_on([], [marine(30, 20)], [foe(30, 24)], phase='movement')
    state['players'][0]['plague'] = 'scabrous'
    state['turn']['player'] = 1
    target = unit_by_id(state, 'p2-target')
    # 5.5" is within its Move of 6", but not of 5
    assert 'at most 5"' in refused(state, {'type': 'move', 'unit': 'p2-target', 'mode': 'normal',
                                     'models': [{'id': 'p2-target-0-1', 'x': 35.5, 'y': 24}]})
    report = {'battle_shock': []}
    game._battle_shock_roll(target, Dice(FixedRolls(3, 3)), report, affliction='scabrous')
    assert report['battle_shock'][0]['leadership'] == 7 and target['status']['shocked']  # 6 is under 7
    objective = {'id': 'o', 'kind': 'central', 'x': 30, 'y': 26, 'terrain': None}
    target['status']['shocked'] = False
    target['parts'][0]['stats']['oc'] = 2
    assert objective_control(state, objective) == [0, 1]  # The target's OC 2 counts as 1 (the Marine is out of range)


# --- Choosing the Plague ------------------------------------------------------------------------------------------

def deployment():
    red = army('Red', (part('Marine', 1, 3, base=BASE, contagion=True),),
               (part('Plague Marines', 2, 2, base=BASE),), (part('Plague Marines', 2, 2, base=BASE, uid='more'),),
               (part('Plague Marines', 2, 2, base=BASE, uid='third'),))
    red['grants'] = [{'rule': 'Verminous Haze', 'ability': 'Infiltrators', 'keywords': 'PLAGUE MARINES', 'count': 2}]
    return new_game([red, army('Blue', (part('Line', 1, 1, base=BASE),))])


def test_the_plague_is_chosen_in_deployment():
    state = deployment()
    assert "doesn't have Nurgle's Gift" in refused(state, {'type': 'plague', 'player': 1, 'plague': 'scabrous'})
    assert 'one of the Plagues' in refused(state, {'type': 'plague', 'player': 0, 'plague': 'bogus'})
    state, event = act(state, {'type': 'plague', 'player': 0, 'plague': 'scabrous'})
    assert event['plague'] == 'scabrous' and state['players'][0]['plague'] == 'scabrous'
    assert game.replay([army('Red', (part('Marine', 1, 3, base=BASE, contagion=True),)), army('Blue', (part('Line', 1, 1),))],
                       [event])['players'][0]['plague'] == 'scabrous'  # Recorded in the event


def test_deployment_cannot_end_before_the_plague_is_chosen():
    state = new_game([army('Red', (part('Marine', 1, 3, base=BASE, contagion=True),)), army('Blue', (part('Line', 1, 1, base=BASE),))])
    state, _ = act(state, at(state, 'p1-marine', (10, 5)))
    state, _ = act(state, at(state, 'p2-line', (10, 40)))
    assert 'choose your Plague' in refused(state, {'type': 'end_deployment'})
    state, _ = act(state, {'type': 'plague', 'player': 0, 'plague': 'rattlejoint'})
    state, _ = act(state, {'type': 'end_deployment'})
    assert state['turn']['phase'] == 'command'
    assert 'during deployment' in refused(state, {'type': 'plague', 'player': 0, 'plague': 'scabrous'})


# --- Verminous Haze -----------------------------------------------------------------------------------------------

def test_verminous_haze_gives_up_to_two_plague_marines_units_infiltrators():
    state = deployment()
    grant = {'type': 'grant', 'ability': 'Infiltrators'}
    assert 'for Plague Marines units' in refused(state, dict(grant, unit='p1-marine'))
    state, event = act(state, dict(grant, unit='p1-plague marines'))
    assert event['on'] and infiltrators(unit_by_id(state, 'p1-plague marines'))
    state, _ = act(state, dict(grant, unit='p1-more'))
    assert 'up to 2 units' in refused(state, dict(grant, unit='p1-third'))
    state, event = act(state, dict(grant, unit='p1-more'))  # Taken back
    assert not event['on'] and not infiltrators(unit_by_id(state, 'p1-more'))
    assert game.grant_options(state, 0)[0]['chosen'] == ['p1-plague marines']
    state, _ = act(state, at(state, 'p1-third', (10, 5), (11.5, 5)))
    assert 'has been set up' in refused(state, dict(grant, unit='p1-third'))


# --- Afflicting by ability ----------------------------------------------------------------------------------------

def test_infused_with_the_blessings_afflicts_the_unit_it_hit_until_its_next_turn():
    state = game_on([], [marine(30, 20, afflicts=True)], [foe(30, 35)])  # Out of Contagion Range
    state, event = act(state, SHOOT, 4, 1)  # Hits, doesn't wound: hit is enough
    assert event['afflicted'] == 'p2-target'
    assert affliction_of(state, unit_by_id(state, 'p2-target')) == 'none'
    report = {'battle_shock': [], 'hazards': []}
    game._start_turn(state, 1, 1, Dice(FixedRolls()), report)  # The opponent's turn: still Afflicted
    assert affliction_of(state, unit_by_id(state, 'p2-target')) == 'none'
    game._start_turn(state, 2, 0, Dice(FixedRolls()), report)  # The start of the shooter's next turn
    assert affliction_of(state, unit_by_id(state, 'p2-target')) is None


def test_infused_needs_a_hit_and_its_own_shooting_phase():
    state = game_on([], [marine(30, 20, afflicts=True)], [foe(30, 35)])
    _, event = act(state, SHOOT, 1)
    assert 'afflicted' not in event  # Missed
    plain = game_on([], [marine(30, 20)], [foe(30, 35)])
    assert 'afflicted' not in act(plain, SHOOT, 4, 1)[1]  # Without the ability


def test_with_several_units_hit_the_player_picks_one():
    state = game_on([], [marine(30, 20, afflicts=True)],
                    [foe(28, 35), (part('Other', 1, 3, base=BASE), (32, 35))])
    shot = {'type': 'shoot', 'unit': 'p1-marine', 'weapons': [{'part': 0, 'weapon': 0, 'target': 'p2-target'}]}
    unit_by_id(state, 'p1-marine')['parts'][0]['weapons'].append(dict(RIFLE, name='Second rifle'))
    unit_by_id(state, 'p1-marine')['models'][0]['weapons'] = [0, 1]
    shot['weapons'].append({'part': 0, 'weapon': 1, 'target': 'p2-other'})
    state, event = act(state, shot, 4, 1, 4, 1)
    assert 'afflicted' not in event
    assert unit_by_id(state, 'p1-marine')['activity']['afflict_options'] == ['p2-target', 'p2-other']
    assert 'one of the enemy units it hit' in refused(state, {'type': 'afflict', 'unit': 'p1-marine', 'target': 'p1-marine'})
    state, event = act(state, {'type': 'afflict', 'unit': 'p1-marine', 'target': 'p2-other'})
    assert event['afflicted'] == 'p2-other' and afflictions(state) == {'p2-other': 'none'}
    assert 'no enemy unit' in refused(state, {'type': 'afflict', 'unit': 'p1-marine', 'target': 'p2-target'})


# --- Bonuses against Afflicted targets ----------------------------------------------------------------------------

def test_datasheet_and_leader_bonuses_against_afflicted_targets():
    near = game_on([], [marine(30, 20, ability_buffs=[WOUND_BONUS])], [foe(30, 24)])
    far = game_on([], [marine(30, 20, ability_buffs=[WOUND_BONUS])], [foe(30, 35)])
    # Wound on a 2: 2+ only with T3 (Afflicted) and +1
    assert act(near, SHOOT, 4, 2, 1)[1]['attacks'][0]['wounds'] == 1
    assert act(far, SHOOT, 4, 2, 1)[1]['attacks'][0]['wounds'] == 0
    leader = unit_by_id(far, 'p1-marine')
    leader['parts'][0].update(role='leader', leader_buffs=[dict(WOUND_BONUS, modifier='Sustained Hits 1')])
    assert 'Sustained Hits 1' in game._attacker(leader, 0, 0, 1, (None, 'none')).abilities
    assert 'Sustained Hits 1' not in game._attacker(leader, 0, 0, 1, (None, None)).abilities


# --- Frozen into the game -------------------------------------------------------------------------------------------

PLAGUE_DATA = {
    'schema_version': 1, 'source': None,
    'factions': [{'id': 1, 'name': 'Plague'}, {'id': 2, 'name': 'Guard'}],
    'units': [
        {'id': 1, 'faction_id': 1, 'name': 'Plague Marines', 'toughness': 5, 'save': 3, 'wounds': 2, 'points_cost': 90,
         'default_models': 5, 'keywords': ['Infantry', 'Battleline'], 'modifiers': [],
         'abilities': "Nurgle's Gift (Aura)\n\nInfused with the Blessings of Nurgle: In your Shooting phase, each time this "
                      "unit is selected to shoot, after this unit has shot, select one enemy unit hit by one or more of those "
                      "attacks. Until the start of your next turn, that unit is Afflicted.",
         'ranged_weapons': [], 'melee_weapons': []},
        {'id': 2, 'faction_id': 1, 'name': 'Lord of Poxes', 'toughness': 5, 'save': 3, 'wounds': 5, 'points_cost': 70,
         'default_models': 1, 'keywords': ['Infantry', 'Character'], 'modifiers': [],
         'abilities': "Nurgle's Gift (Aura)\n\nGift of Poxes: Add 3\" to the range of this model's Contagion Range.\n\n"
                      "Diseased Malice: Each time this model makes an attack that targets an Afflicted unit, add 1 to the Wound roll.",
         'ranged_weapons': [], 'melee_weapons': []},
        {'id': 3, 'faction_id': 2, 'name': 'Guardsmen', 'toughness': 3, 'save': 5, 'wounds': 1, 'points_cost': 60,
         'default_models': 10, 'keywords': ['Infantry'], 'modifiers': [],
         'ranged_weapons': [], 'melee_weapons': []},
    ],
    'ranged_weapons': [], 'melee_weapons': [], 'faction_rules': [], 'stratagems': [], 'keywords': [], 'target_profiles': [],
}


def test_death_guard_rules_are_frozen_into_the_game(make_client):
    client, _ = make_client(PLAGUE_DATA)
    red = client.post('/api/armies', json={'name': 'Red', 'faction_id': 1, 'points_limit': 1000, 'units': [
        {'uid': 'm', 'unit_id': 1}, {'uid': 'l', 'unit_id': 2}]}).get_json()['id']
    blue = client.post('/api/armies', json={'name': 'Blue', 'faction_id': 2, 'points_limit': 1000,
                                            'units': [{'uid': 'g', 'unit_id': 3}]}).get_json()['id']
    response = client.post('/api/play/games', json={'name': 'Plague', 'armies': [red, blue], 'setup': False})
    assert response.status_code == 200, response.get_json()
    played = response.get_json()
    marines, lord, guard = (u['parts'][0] for u in played['state']['units'])
    assert marines['contagion'] and marines['afflicts'] and marines['contagion_bonus'] == 0
    assert lord['contagion_bonus'] == 3 and lord['ability_buffs'] == [
        {'applies_to': 'Attacker', 'phase': 'Both', 'modifier': 'Wound Modifier +1', 'when': 'afflicted'}]
    assert not guard['contagion']
    assert played['plagues']['players'] == [0] and [c['key'] for c in played['plagues']['choices']] == \
        ['skullsquirm', 'rattlejoint', 'scabrous']
    assert played['afflicted'] == {} and played['contagion'] == {} and played['grants'] == [[], []]


def test_detachment_rules_are_frozen_as_data(make_client):
    from combat_sim.models import Detachment, db
    from combat_sim.play.snapshot import _detachment_effects
    client, _ = make_client(PLAGUE_DATA)
    with client.application.app_context():
        db.session.add(Detachment(faction_id=1, bsdata_id='haze', name='Flyblown Host', rules=[
            {'name': 'Verminous Haze', 'text': 'In the **Declare Battle Formations** step, you can select up to two friendly '
                                                '**PLAGUE MARINES** units. Those units have **Infiltrators**.'},
            {'name': 'Hypervirulent Strains', 'text': 'Friendly **DEATH GUARD CHARACTER** units have +3" to their '
                                                       '**Contagion Range** (to a maximum of 12").'}]))
        db.session.commit()
        assert _detachment_effects({'detachment_keys': ['haze']}) == {
            'contagion_bonuses': [{'rule': 'Hypervirulent Strains', 'keywords': 'DEATH GUARD CHARACTER', 'inches': 3}],
            'grants': [{'rule': 'Verminous Haze', 'ability': 'Infiltrators', 'keywords': 'PLAGUE MARINES', 'count': 2}]}
        assert _detachment_effects({'detachment_keys': []}) == {'contagion_bonuses': [], 'grants': []}
