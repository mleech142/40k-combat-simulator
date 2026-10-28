"""
Strategic Reserves and Deep Strike (release 2.7 step a, src/combat_sim/play/game.py; core rules 20, 24.09, 15.07):
units put in reserves during deployment (at most half the battle's points), arriving from battle round 2 with an
ingress move (wholly within 6" of an edge, more than 8" from enemies, not in the enemy's zone before round 3;
Deep Strike: anywhere more than 8" away), Rapid Ingress at the end of the opponent's Movement phase, and units
still in reserves destroyed at the end of battle round 3.
"""

import math

from combat_sim.play.game import moved_distance, new_game, score, unit_by_id
from test_play_game import army, part
from test_play_terrain import BASE, game_on, target
from test_play_turns import act, at, next_phase, refused

NEXT = {'type': 'next_phase'}


def lander(name='Lander', points=100, deep=False, keywords=('Infantry',)):
    return part(name, 2, 2, base=BASE, points=points, deep_strike=deep, keywords=list(keywords))


def in_reserves(state, unit_id):
    unit = unit_by_id(state, unit_id)
    for model in unit['models']:
        model['position'] = None
    unit['status']['reserve'] = True
    return state


def board(round_=2, phase='movement', player=0, deep=False, blue=None, red_cp=0, blue_cp=0):
    """Red's lander in reserves and Red's target at (30, 10); Blue's target at (30, 38) (and `blue` units)."""
    state = game_on([], [target(30, 10), (lander(deep=deep), (5, 5), (6.5, 5))], [target(30, 38), *(blue or [])], phase=phase)
    state['turn'].update(round=round_, player=player)
    state['players'][0]['cp'], state['players'][1]['cp'] = red_cp, blue_cp
    return in_reserves(state, 'p1-lander')


def arrive(x, y, kind='ingress', unit='p1-lander', **extra):
    models = [m['id'] for m in unit_by_id(board(), unit)['models']] if unit == 'p1-lander' else None
    return dict({'type': kind, 'unit': unit, 'models': [{'id': models[0], 'x': x, 'y': y}, {'id': models[1], 'x': x + 1.5, 'y': y}]},
                **extra)


def placed(state, unit_id='p1-lander'):
    return [m['position'] for m in unit_by_id(state, unit_id)['models']]


# --- Deployment ----------------------------------------------------------------------------------------------

def deployment(points_limit=None, red=None):
    red = red or [(lander('Lander', 100),), (lander('Hulk', 150),)]
    blue = [(lander('Blue', 100),)]
    reds = army('Red', *red)
    reds['points_limit'] = points_limit
    return new_game([reds, army('Blue', *blue)])


def test_a_unit_put_in_reserves_counts_as_deployed():
    state, event = act(deployment(), {'type': 'reserve', 'unit': 'p1-lander'})
    assert event['reserve'] == 'p1-lander' and unit_by_id(state, 'p1-lander')['status']['reserve']
    assert state['turn']['player'] == 1  # Blue sets up next
    assert 'in reserves' in refused(dict(state, turn=dict(state['turn'], player=0)), at(state, 'p1-lander', (5, 5), (6.5, 5)))
    assert 'already set up' in refused(dict(state, turn=dict(state['turn'], player=0)), {'type': 'reserve', 'unit': 'p1-lander'})


def test_reserves_cost_at_most_half_the_battle_points():
    # No points limit: half what the army costs (250 / 2 = 125): the 100-point unit fits, the 150-point one doesn't
    assert 'at most 125 points' in refused(deployment(), {'type': 'reserve', 'unit': 'p1-hulk'})
    state = deployment(points_limit=500)  # Half of 500 is 250: both fit
    state, _ = act(state, {'type': 'reserve', 'unit': 'p1-hulk'})
    state['turn']['player'] = 0
    state, _ = act(state, {'type': 'reserve', 'unit': 'p1-lander'})
    assert all(unit_by_id(state, u)['status']['reserve'] for u in ('p1-hulk', 'p1-lander'))


def test_fortifications_cant_go_in_reserves():
    state = deployment(red=[(lander('Bunker', 10, keywords=('Fortification',)),), (lander('Hulk', 150),)])
    assert 'Fortifications' in refused(state, {'type': 'reserve', 'unit': 'p1-bunker'})


def test_deployment_ends_with_units_in_reserves():
    state, _ = act(deployment(points_limit=500), {'type': 'reserve', 'unit': 'p1-lander'})
    state, _ = act(state, at(state, 'p2-blue', (5, 40), (6.5, 40)))
    state, _ = act(state, {'type': 'reserve', 'unit': 'p1-hulk'})
    state, _ = act(state, {'type': 'end_deployment'})
    assert state['turn']['phase'] == 'command'


# --- Ingress moves -------------------------------------------------------------------------------------------

def test_reserves_arrive_from_battle_round_2():
    assert 'from battle round 2' in refused(board(round_=1), arrive(1, 20))
    state, event = act(board(), arrive(1, 20))
    assert event['arrived'] == 'p1-lander' and placed(state)[0] == {'x': 1, 'y': 20, 'angle': 0}
    assert not unit_by_id(state, 'p1-lander')['status'].get('reserve')
    assert unit_by_id(state, 'p1-lander')['activity']['moved'] == 'ingress'


def test_an_ingress_move_sets_up_wholly_within_6_inches_of_an_edge():
    assert 'within 6"' in refused(board(), arrive(20, 20))
    assert 'within 6"' in refused(board(), arrive(5.5, 20))  # The second model (at 7") sticks out
    for spot in ((1, 20), (40, 1), (57, 20)):
        act(board(), arrive(*spot))


def test_more_than_8_inches_from_enemies():
    assert 'more than 8"' in refused(board(deep=True), arrive(30, 30.5))  # 7.5" centre to centre from Blue's target
    assert 'more than 8"' in refused(board(blue=[target(10, 20)]), arrive(1, 20))


def test_not_in_the_enemy_deployment_zone_before_round_3():
    assert "opponent's deployment zone" in refused(board(), arrive(1, 40))
    act(board(round_=3), arrive(1, 40))


def test_deep_strike_arrives_anywhere_more_than_8_inches_away():
    act(board(deep=True), arrive(20, 20))
    assert 'more than 8"' in refused(board(deep=True), arrive(30, 31))
    assert "opponent's deployment zone" in refused(board(deep=True), arrive(10, 36))


def test_an_arrived_unit_cant_move_again_but_can_be_set_up_again_or_reset():
    state, _ = act(board(), arrive(1, 20))
    assert 'arrived this phase' in refused(state, {'type': 'stationary', 'unit': 'p1-lander'})
    assert 'arrived this phase' in refused(state, {'type': 'advance', 'unit': 'p1-lander'})
    again, _ = act(state, arrive(1, 25))
    assert placed(again)[0]['y'] == 25
    back, _ = act(state, {'type': 'reset_move', 'unit': 'p1-lander'})
    assert placed(back) == [None, None] and unit_by_id(back, 'p1-lander')['status']['reserve']
    assert 'moved' not in unit_by_id(back, 'p1-lander')['activity']


def test_a_unit_in_reserves_cant_move_on_the_board():
    assert 'in reserves' in refused(board(), {'type': 'stationary', 'unit': 'p1-lander'})


def test_arriving_counts_as_moving_for_heavy():
    state, _ = act(board(), arrive(1, 20))
    assert moved_distance(unit_by_id(state, 'p1-lander')) == math.inf


# --- Rapid Ingress ----------------------------------------------------------------------------------------------

def blue_reserves(round_=2, cp=1, deep=False, blue=None):
    """Red's Movement phase; Blue's lander in reserves with `cp` CP."""
    state = game_on([], [target(30, 10)], [target(30, 38), (lander(deep=deep), (5, 40), (6.5, 40)), *(blue or [])],
                    phase='movement')
    state['turn'].update(round=round_)
    state['players'][1]['cp'] = cp
    return in_reserves(state, 'p2-lander')


def rapid(x, y):
    models = ['p2-lander-0-1', 'p2-lander-0-2']
    return {'type': 'rapid_ingress', 'player': 1, 'unit': 'p2-lander',
            'models': [{'id': models[0], 'x': x, 'y': y}, {'id': models[1], 'x': x + 1.5, 'y': y}]}


def test_rapid_ingress_at_the_end_of_the_opponents_movement_phase():
    state, event = act(blue_reserves(), NEXT)
    assert state['turn']['window'] == {'kind': 'ingress', 'player': 1, 'then': 'next_phase'} and event['window'] == 'ingress'
    assert 'within 6"' in refused(state, rapid(30, 18))  # A refused set-up costs nothing
    assert "opponent's deployment zone" in refused(state, rapid(30, 1))
    after, event = act(state, rapid(1, 40))
    assert event['arrived'] == 'p2-lander' and event['stratagem'] == 'Rapid Ingress'
    assert after['players'][1]['cp'] == 0 and after['turn']['phase'] == 'shooting'
    assert placed(after, 'p2-lander')[0]['y'] == 40


def test_no_rapid_ingress_in_round_1_without_cp_or_by_standing_order():
    for state in (blue_reserves(round_=1), blue_reserves(cp=0)):
        assert act(state, NEXT)[0]['turn']['phase'] == 'shooting'
    state, _ = act(blue_reserves(), {'type': 'orders', 'player': 1, 'reactions': {'ingress': {'mode': 'never'}}})
    assert act(state, NEXT)[0]['turn']['phase'] == 'shooting'


def test_rapid_ingress_isnt_for_aircraft():
    state = blue_reserves()
    unit_by_id(state, 'p2-lander')['parts'][0]['keywords'] = ['Vehicle', 'Aircraft']
    assert act(state, NEXT)[0]['turn']['phase'] == 'shooting'


def test_overwatch_is_asked_first_then_rapid_ingress():
    from test_play_reactions import watcher
    state = blue_reserves(cp=2, blue=[watcher(x=30, y=30)])
    state, _ = act(state, NEXT)
    assert state['turn']['window']['kind'] == 'overwatch'
    state, _ = act(state, {'type': 'pass', 'player': 1})
    assert state['turn']['window']['kind'] == 'ingress'
    state, _ = act(state, {'type': 'pass', 'player': 1})
    assert state['turn']['phase'] == 'shooting'


# --- The end of battle round 3 -------------------------------------------------------------------------------------

def test_units_still_in_reserves_are_destroyed_at_the_end_of_round_3():
    state = board(round_=3, phase='fight', player=1)
    state['turn']['step'] = 'consolidate'
    state['roles'] = {'attacker': 0, 'defender': 1, 'first': 0}
    after, event = next_phase(state)
    assert event['reserves_lost'][0]['unit'] == 'p1-lander'
    assert not any(m['alive'] for m in unit_by_id(after, 'p1-lander')['models'])
    assert after['turn']['round'] == 4
    assert score(after) == [0, 0]  # Not counted as destroyed by the enemy


def test_reserves_stay_until_the_end_of_round_3():
    state = board(round_=3, phase='fight', player=0)  # Red goes first: round 3 isn't over after Red's turn
    state['turn']['step'] = 'consolidate'
    state['roles'] = {'attacker': 0, 'defender': 1, 'first': 0}
    after, event = next_phase(state)
    assert 'reserves_lost' not in event and unit_by_id(after, 'p1-lander')['status']['reserve']
