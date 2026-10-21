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
    assert [m['wounds_left'] for m in unit_by_id(state, 'p1-squad')['models']] == [2, 2, 1]


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
