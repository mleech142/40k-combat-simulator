"""
Play mode's game loop (src/combat_sim/play/game.py): Command points, battle-shock, hazard rolls,
the end of the battle and the score.
"""

from combat_sim.play.game import new_game, score, unit_by_id
from test_play_game import army, gun, part
from test_play_turns import act, put, refused

BASE = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}


def model(name, models=1, wounds=1, **extra):
    return part(name, models, wounds, base=BASE, **extra)


def battle(red, blue, phase='command', player=0, round_=1):
    state = new_game([army('Red', *[(u[0],) for u in red]), army('Blue', *[(u[0],) for u in blue])])
    for owner, units in (('p1', red), ('p2', blue)):
        for spec in units:
            put(state, f'{owner}-{spec[0]["uid"]}', *spec[1:])
    state['turn'] = {'phase': phase, 'round': round_, 'player': player}
    return state


def row(x, y, n=3, gap=1.5):
    return [(x + gap * i, y) for i in range(n)]


def to_phase(state, phase, player, *faces):
    """Next phase until it's `player`'s `phase` (dice faces for any battle-shock rolls on the way)."""
    faces = list(faces)
    for _ in range(40):
        if state['turn']['phase'] == phase and state['turn']['player'] == player and 'step' not in state['turn']:
            return state
        state, event = act(state, {'type': 'next_phase'}, *faces)
        used = sum(len(g['dice']) for g in event['rolls'])
        faces = faces[used:]
    raise AssertionError('never got there')


def kill(state, unit_id, n):
    """Remove the last `n` living models of a unit (to arrange a test)."""
    for model_ in [m for m in unit_by_id(state, unit_id)['models'] if m['alive']][-n:]:
        model_['alive'], model_['wounds_left'] = False, 0
    return state


SQUAD = (model('Squad', 4), *row(10, 6, 4))
MOB = (model('Mob', 4), *row(10, 38, 4))


# --- Command points ----------------------------------------------------------------------------------------

def test_both_players_gain_a_command_point_in_every_command_phase():
    state = new_game([army('Red', (SQUAD[0],)), army('Blue', (MOB[0],))])
    assert [p['cp'] for p in state['players']] == [0, 0]
    state, _ = act(state, {'type': 'place', 'unit': 'p1-squad',
                           'models': [{'id': f'p1-squad-0-{i + 1}', 'x': x, 'y': y} for i, (x, y) in enumerate(SQUAD[1:])]})
    state, _ = act(state, {'type': 'place', 'unit': 'p2-mob',
                           'models': [{'id': f'p2-mob-0-{i + 1}', 'x': x, 'y': y} for i, (x, y) in enumerate(MOB[1:])]})
    state, _ = act(state, {'type': 'end_deployment'})
    assert [p['cp'] for p in state['players']] == [1, 1]  # Red's first Command phase
    state = to_phase(state, 'command', 1)
    assert [p['cp'] for p in state['players']] == [2, 2]


# --- Battle-shock -----------------------------------------------------------------------------------------

def test_units_at_half_strength_take_a_battle_shock_roll_in_their_command_phase():
    state = kill(battle([SQUAD], [MOB], phase='fight', player=1), 'p1-squad', 2)  # 2 of 4 left: half strength
    state['turn']['step'] = 'consolidate'
    state, event = act(state, {'type': 'next_phase'}, 2, 3)  # Red's Command phase: 5 against Ld 6+: fails
    assert [(g['purpose'], g['total']) for g in event['rolls']] == [('battle-shock', 5)]
    assert event['battle_shock'] == [{'unit': 'p1-squad', 'name': 'Squad', 'total': 5, 'leadership': 6,
                                      'shocked': True, 'was_shocked': False, 'rolls': [0, 1]}]
    assert unit_by_id(state, 'p1-squad')['status'] == {'shocked': True}
    assert unit_by_id(state, 'p2-mob')['status'] == {'shocked': False}  # Not rolled: the other player's unit
    state = to_phase(state, 'command', 1)
    state = to_phase(state, 'command', 0, 4, 4)  # Next time: 8, passes
    assert unit_by_id(state, 'p1-squad')['status'] == {'shocked': False}


def test_units_above_half_strength_dont_roll():
    state = kill(battle([SQUAD], [MOB], phase='fight', player=1), 'p1-squad', 1)  # 3 of 4 left
    state['turn']['step'] = 'consolidate'
    _, event = act(state, {'type': 'next_phase'})
    assert event['rolls'] == []


def test_a_single_model_rolls_once_it_has_lost_half_its_wounds():
    hero = (model('Hero', 1, 6), (20, 6))
    state = battle([hero], [MOB], phase='fight', player=1)
    state['turn']['step'] = 'consolidate'
    unit_by_id(state, 'p1-hero')['models'][0]['wounds_left'] = 3
    _, event = act(state, {'type': 'next_phase'}, 6, 6)
    assert [g['purpose'] for g in event['rolls']] == ['battle-shock']


def test_battle_shocked_units_fall_back_by_desperate_escape():
    # Engaged, and battle-shocked: it may move through the enemy, then each model makes a hazard roll
    # when the Movement phase ends (1-2 fails: the unit suffers a mortal wound)
    squad = (model('Squad', 3, 2), *row(10, 6))
    mob = (model('Mob', 3), *row(10, 8.5))
    state = battle([squad], [mob], phase='movement')
    unit_by_id(state, 'p1-squad')['status'] = {'shocked': True}
    through = [(10, 12), (11.5, 12), (13, 12)]  # Straight through the Mob, ending 2.24" beyond it
    state, _ = act(state, {'type': 'move', 'unit': 'p1-squad', 'mode': 'fall_back',
                           'models': [{'id': f'p1-squad-0-{i + 1}', 'x': x, 'y': y} for i, (x, y) in enumerate(through)]})
    state, event = act(state, {'type': 'next_phase'}, 1, 4, 6)
    assert [(g['purpose'], [d['face'] for d in g['dice']]) for g in event['rolls']] == [('hazard', [1, 4, 6])]
    assert event['hazards'] == [{'unit': 'p1-squad', 'name': 'Squad', 'reason': 'Desperate Escape', 'fails': 1,
                                 'mortal_wounds': 1, 'killed': [], 'rolls': [0, 1]}]
    assert [m['wounds_left'] for m in unit_by_id(state, 'p1-squad')['models']] == [2, 2, 1]


def test_a_unit_that_falls_back_in_ordered_retreat_takes_a_battle_shock_roll():
    """Core rules 09.07: a unit that Falls Back while not battle-shocked (Ordered Retreat) makes a battle-shock roll
    afterwards; the game rolls it as the Movement phase ends, before control of objectives is settled."""
    squad = (model('Squad', 3, 2), *row(10, 6))
    mob = (model('Mob', 3), *row(10, 8.5))
    for faces, shocked in (((3, 2), True), ((4, 3), False)):  # Leadership 7+ (the default)
        state = battle([squad], [mob], phase='movement')
        back = [(10, 2), (11.5, 2), (13, 2)]
        state, _ = act(state, {'type': 'move', 'unit': 'p1-squad', 'mode': 'fall_back',
                               'models': [{'id': f'p1-squad-0-{i + 1}', 'x': x, 'y': y} for i, (x, y) in enumerate(back)]})
        state, event = act(state, {'type': 'next_phase'}, *faces)
        assert [(b['name'], b['total'], b['shocked'], b['reason']) for b in event['battle_shock']] == [('Squad', sum(faces), shocked, 'Ordered Retreat')]
        assert unit_by_id(state, 'p1-squad')['status']['shocked'] is shocked


def test_units_out_of_coherency_lose_models_at_the_end_of_the_turn():
    """Core rules 03.03: at the end of each turn a unit out of coherency has models removed until it is back in it
    (the straggler here); they don't count as destroyed by the enemy."""
    squad = (model('Squad', 4), (10, 6), (11.5, 6), (13, 6), (30, 6))  # The last model 15.7" from the others
    state = battle([squad], [MOB], phase='fight')
    state['turn']['step'] = 'consolidate'
    state, event = act(state, {'type': 'next_phase'})
    assert [m['alive'] for m in unit_by_id(state, 'p1-squad')['models']] == [True, True, True, False]
    assert event['coherency'] == [{'unit': 'p1-squad', 'name': 'Squad', 'removed': ['p1-squad-0-4']}]
    assert score(state) == [0, 0]


# --- Hazard rolls (11th edition) --------------------------------------------------------------------------

def test_hazardous_weapons_fail_on_1_or_2_and_cost_the_unit_a_mortal_wound():
    hot = gun('Plasma', attacks='1', range=24, abilities=['Hazardous'], qty=2)
    guns = (model('Gunners', 2, 2, weapons=[hot]), (11.5, 20), (13, 20))
    state = battle([(model('Squad', 3), *row(10, 10))], [guns], phase='shooting', player=1)
    after, event = act(state, {'type': 'shoot', 'unit': 'p2-gunners', 'target': 'p1-squad'}, 1, 1, 2, 3)  # Miss, miss
    assert event['rolls'][-1]['purpose'] == 'hazard'
    assert [m['wounds_left'] for m in unit_by_id(after, 'p2-gunners')['models']] == [2, 1]  # One fail: 1 mortal wound
    tank = (model('Tank', 1, 12, weapons=[dict(hot, qty=1)], keywords=['Vehicle']), (11.5, 20))
    state = battle([(model('Squad', 3), *row(10, 10))], [tank], phase='shooting', player=1)
    after, _ = act(state, {'type': 'shoot', 'unit': 'p2-tank', 'target': 'p1-squad'}, 1, 2)
    assert unit_by_id(after, 'p2-tank')['models'][0]['wounds_left'] == 9  # Every model a Vehicle: 3


# --- The end of the battle ------------------------------------------------------------------------------------

def test_the_battle_ends_after_five_battle_rounds():
    state = battle([SQUAD], [MOB], phase='fight', player=1, round_=5)
    state['turn']['step'] = 'consolidate'
    state, _ = act(state, {'type': 'next_phase'})
    assert state['turn']['phase'] == 'ended'
    assert 'over' in refused(state, {'type': 'next_phase'})


def test_the_score_is_the_points_of_enemy_units_destroyed():
    squad = (model('Squad', 4, points=90), *row(10, 6, 4))
    mob = (model('Mob', 4, points=60), *row(10, 38, 4))
    hero = (model('Hero', 1, 4, points=50), (30, 38))
    state = battle([squad], [mob, hero])
    kill(state, 'p2-mob', 4)
    assert score(state) == [60, 0]  # The Hero still stands
    kill(state, 'p1-squad', 3)  # Not destroyed: no points
    assert score(state) == [60, 0]


# --- Stratagems, rules and manual effects ----------------------------------------------------------------------

AIM = {'key': 'stratagem:1', 'kind': 'stratagem', 'name': 'Aim', 'cp': 1, 'applies_to': 'Attacker',
       'phase': 'Shooting', 'modifier': 'Hit Modifier +1', 'detachment': None, 'text': 'Add 1 to the Hit roll.'}
TRICK = {'key': 'stratagem:2', 'kind': 'stratagem', 'name': 'Trick', 'cp': 2, 'applies_to': None,
         'phase': None, 'modifier': '', 'detachment': None, 'text': 'Something clever.'}
OATH = {'key': 'rule:3', 'kind': 'rule', 'name': 'Oath', 'cp': 0, 'applies_to': 'Attacker',
        'phase': 'Both', 'modifier': 'Reroll Hits', 'detachment': None, 'text': 'Re-roll hits.'}


def shooting_game(cp=2):
    """Red's Gunner (BS3+, one shot) facing Blue's Squad, Red's Shooting phase; Red has Aim, Trick and Oath."""
    gunner = (model('Gunner', 1, 3, weapons=[gun(attacks='1', range=24)]), (11.5, 20))
    squad = (model('Squad', 3), *row(10, 30))
    state = battle([gunner], [squad], phase='shooting')
    state['players'][0].update(cp=cp, stratagems=[AIM, TRICK, OATH])
    return state


def shoot_squad(state, *faces):
    return act(state, {'type': 'shoot', 'unit': 'p1-gunner', 'target': 'p2-squad'}, *faces)


def test_a_stratagem_spends_command_points_once_per_phase_and_its_effect_reaches_the_dice():
    state, event = act(shooting_game(), {'type': 'stratagem', 'player': 0, 'key': 'stratagem:1', 'unit': 'p1-gunner'})
    assert state['players'][0]['cp'] == 1 and event['stratagem'] == 'Aim'
    assert 'once per phase' in refused(state, {'type': 'stratagem', 'player': 0, 'key': 'stratagem:1', 'unit': 'p1-gunner'})
    _, shot = shoot_squad(state, 1)
    assert shot['rolls'][0]['need'] == 2  # BS3+ improved by 1
    state, _ = act(state, {'type': 'next_phase'})
    assert unit_by_id(state, 'p1-gunner')['effects'] == []  # It lasted the phase


def test_a_unit_can_be_targeted_by_one_stratagem_per_phase():
    """Core rules 15.01: a player can't target the same unit with more than one stratagem in a phase."""
    state, _ = act(shooting_game(cp=3), {'type': 'stratagem', 'player': 0, 'key': 'stratagem:1', 'unit': 'p1-gunner'})
    assert 'one stratagem' in refused(state, {'type': 'stratagem', 'player': 0, 'key': 'stratagem:2', 'unit': 'p1-gunner'})
    state, _ = act(state, {'type': 'next_phase'})  # Charge phase: a new phase
    state, _ = act(state, {'type': 'stratagem', 'player': 0, 'key': 'stratagem:2', 'unit': 'p1-gunner'})
    assert state['players'][0]['cp'] == 0


def test_stratagems_need_the_command_points_and_cant_target_battle_shocked_units():
    assert 'CP' in refused(shooting_game(cp=0), {'type': 'stratagem', 'player': 0, 'key': 'stratagem:1', 'unit': 'p1-gunner'})
    state = shooting_game()
    unit_by_id(state, 'p1-gunner')['status'] = {'shocked': True}
    assert 'battle-shocked' in refused(state, {'type': 'stratagem', 'player': 0, 'key': 'stratagem:1', 'unit': 'p1-gunner'})
    assert 'own' in refused(state, {'type': 'stratagem', 'player': 0, 'key': 'stratagem:1', 'unit': 'p2-squad'})


def test_a_stratagem_the_game_cant_read_is_logged_with_its_text():
    state, event = act(shooting_game(), {'type': 'stratagem', 'player': 0, 'key': 'stratagem:2', 'unit': 'p1-gunner'})
    assert state['players'][0]['cp'] == 0 and event['text'] == 'Something clever.'
    assert unit_by_id(state, 'p1-gunner')['effects'] == []


def test_army_rules_are_switched_on_for_a_unit_at_no_cost():
    state, _ = act(shooting_game(), {'type': 'rule', 'player': 0, 'key': 'rule:3', 'unit': 'p1-gunner', 'until': 'turn'})
    assert state['players'][0]['cp'] == 2
    _, shot = shoot_squad(state, 1, 6, 1)  # Missed, re-rolled to a 6
    assert shot['rolls'][0]['dice'][0]['rerolled_from'] == 1
    state, _ = act(state, {'type': 'next_phase'})
    assert len(unit_by_id(state, 'p1-gunner')['effects']) == 1  # Until the end of the turn
    state = to_phase(state, 'command', 1)
    assert unit_by_id(state, 'p1-gunner')['effects'] == []


def test_manual_effects_apply_to_the_dice_for_as_long_as_chosen():
    state, event = act(shooting_game(), {'type': 'effect', 'unit': 'p2-squad', 'effect': 'cover', 'until': 'game'})
    assert event['effect'] == 'Benefit of cover'
    _, shot = shoot_squad(state, 1)
    assert shot['rolls'][0]['need'] == 4  # BS3+ worsened by cover
    state, _ = act(state, {'type': 'effect', 'unit': 'p1-gunner', 'effect': 'hit_plus', 'until': 'phase'})
    _, shot = shoot_squad(state, 1)
    assert shot['rolls'][0]['need'] == 3  # +1 and cover cancel out
    state = to_phase(state, 'command', 1)
    assert [e['name'] for e in unit_by_id(state, 'p2-squad')['effects']] == ['Benefit of cover']
    state, _ = act(state, {'type': 'remove_effect', 'unit': 'p2-squad', 'index': 0})
    assert unit_by_id(state, 'p2-squad')['effects'] == []
    assert 'Unknown effect' in refused(state, {'type': 'effect', 'unit': 'p2-squad', 'effect': 'nonsense'})


# --- Correcting the game ---------------------------------------------------------------------------------------

def test_a_correction_needs_the_other_player_to_accept_it():
    state = shooting_game()
    fix = {'type': 'correction', 'player': 0, 'note': 'That model should have survived',
           'models': {'p2-squad-0-1': {'wounds_left': 0, 'alive': False}}, 'cp': {'1': 0}}
    state, event = act(state, fix)
    assert state['pending'] == {'player': 0, 'note': 'That model should have survived',
                                'models': {'p2-squad-0-1': {'wounds_left': 0, 'alive': False}}, 'cp': {'1': 0}}
    assert 'correction' in refused(state, {'type': 'next_phase'})  # Everything waits for an answer
    assert 'other player' in refused(state, {'type': 'accept', 'player': 0})
    declined, _ = act(state, {'type': 'decline', 'player': 1})
    assert declined['pending'] is None and unit_by_id(declined, 'p2-squad')['models'][0]['alive']
    accepted, event = act(state, {'type': 'accept', 'player': 1})
    assert accepted['pending'] is None and event['corrected'] == 'That model should have survived'
    assert not unit_by_id(accepted, 'p2-squad')['models'][0]['alive'] and accepted['players'][1]['cp'] == 0


def test_a_correction_can_only_change_what_exists():
    assert 'model' in refused(shooting_game(), {'type': 'correction', 'player': 0, 'models': {'nobody': {'alive': False}}})
    assert 'wounds' in refused(shooting_game(), {'type': 'correction', 'player': 0,
                                                 'models': {'p2-squad-0-1': {'wounds_left': 9}}})


# --- Which units can still act -------------------------------------------------------------------------------

def test_ready_units_are_those_that_can_still_act_this_phase():
    from combat_sim.play.game import ready_units
    gunner = (model('Gunner', 1, 3, weapons=[gun(attacks='1', range=24)]), (11.5, 20))
    far_gunner = (model('Far', 1, 3, weapons=[gun(attacks='1', range=6)]), (40, 6))
    squad = (model('Squad', 3), *row(10, 30))
    state = battle([gunner, far_gunner], [squad], phase='movement')
    assert ready_units(state) == ['p1-gunner', 'p1-far']  # Neither has moved
    unit_by_id(state, 'p1-gunner')['activity'] = {'moved': 'normal'}
    assert ready_units(state) == ['p1-far']
    state['turn']['phase'] = 'shooting'
    assert ready_units(state) == ['p1-gunner']  # The other has nothing in range
    unit_by_id(state, 'p1-gunner')['activity']['shot'] = True
    assert ready_units(state) == []
    state['turn']['phase'] = 'command'
    assert ready_units(state) == []


def test_ready_units_in_the_fight_follow_whose_pick_it_is():
    from combat_sim.play.game import ready_units
    from test_play_fight import fighters, to_step
    state = to_step(fighters(), 'pile_in')
    assert sorted(ready_units(state)) == ['p1-guard', 'p1-squad']  # Red piles in first...
    state, _ = act(state, {'type': 'next_phase'})
    assert sorted(ready_units(state)) == ['p2-brute', 'p2-mob']  # ...then Blue
    state = to_step(state, 'fight')
    assert ready_units(state) == ['p1-squad']  # Fights First: Red's charged Squad
