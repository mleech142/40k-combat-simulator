"""
Play mode's dragged routes (src/combat_sim/play/game.py): a Movement-phase move or a charge move can carry
each model's `route`, the bends of the path the player dragged it along, so a model steered through a gap
between terrain pieces is measured and checked along that path, not the straight line.
"""

import pytest

from combat_sim.play.game import moved_distance, unit_by_id
from test_play_fight import battle
from test_play_game import part
from test_play_terrain import BASE, game_on, piece, target
from test_play_turns import act, refused

WALKER = 'p1-walker'
MODEL = f'{WALKER}-0-1'
# Two 5" Dense ruins, x 2-8 and x 10.5-16.5 (y 18-22), with a 2.5" gap between them (x 8-10.5)
RUINS = [piece('a', 'dense', 2, 18, height=5), piece('b', 'dense', 10.5, 18, height=5)]
BEND = (9.25, 16)  # Below the middle of the gap
PAST = (9.25, 26)  # Above it: 5.62" to the bend, then 10" up the gap


def tank(move=20):
    return (part('Walker', 1, 8, base=BASE, stats={'move': str(move)}, keywords=['Vehicle']), (4, 14))


def board(move=20):
    """A Vehicle at (4, 14), below and left of the gap: the straight line to PAST crosses the left ruin."""
    return game_on(RUINS, [tank(move)], [target(50, 40)], phase='movement')


def go(x, y, *route, waypoint=False):
    model = {'id': MODEL, 'x': x, 'y': y}
    if route:
        model['route'] = [{'x': rx, 'y': ry} for rx, ry in route]
    action = {'type': 'move', 'unit': WALKER, 'mode': 'normal', 'models': [model]}
    return dict(action, waypoint=True) if waypoint else action


def via(state):
    return unit_by_id(state, WALKER)['activity'].get('via', {}).get(MODEL)


def test_a_model_steered_through_a_gap_doesnt_climb():
    state = board()
    assert 'Dense' in refused(state, go(*PAST))  # Straight: through the left ruin
    state, _ = act(state, go(*PAST, BEND))
    position = unit_by_id(state, WALKER)['models'][0]['position']
    assert (position['x'], position['y']) == PAST
    assert via(state) == [{'x': 9.25, 'y': 16, 'angle': 0}]  # Drawn as its route, and counted for Heavy
    assert moved_distance(unit_by_id(state, WALKER)) == pytest.approx(15.62, abs=0.01)


def test_the_route_counts_against_the_move():
    assert '15.6" asked' in refused(board(move=15), go(*PAST, BEND))


def test_a_route_into_terrain_still_climbs():
    assert 'Dense' in refused(board(), go(*PAST, (5, 16), (5, 20), BEND))


def test_a_route_through_enemy_models_is_refused():
    state = game_on(RUINS, [tank()], [target(9.25, 21, keywords=('Vehicle',))], phase='movement')  # In the gap
    assert 'through enemy models' in refused(state, go(*PAST, BEND))


def test_without_a_waypoint_the_route_starts_from_where_the_unit_began():
    state, _ = act(board(), go(6, 14))
    state, _ = act(state, go(*PAST, BEND))
    assert via(state) == [{'x': 9.25, 'y': 16, 'angle': 0}]
    assert moved_distance(unit_by_id(state, WALKER)) == pytest.approx(15.62, abs=0.01)


def test_a_route_after_a_waypoint_carries_on_from_where_the_unit_stands():
    state, _ = act(board(), go(6, 14))
    state, _ = act(state, go(*PAST, BEND, waypoint=True))
    assert via(state) == [{'x': 6, 'y': 14, 'angle': 0}, {'x': 9.25, 'y': 16, 'angle': 0}]


@pytest.mark.parametrize('route, why', [
    ([{'x': 'left', 'y': 1}], 'route'),
    ('not a list', 'route'),
    ([{'x': 9, 'y': 16}] * 101, 'at most 100'),
    ([{'x': -1, 'y': 16}], 'on the board'),
])
def test_a_bad_route_is_refused(route, why):
    action = go(*PAST)
    action['models'][0]['route'] = route
    assert why in refused(board(), action)


def test_a_charge_move_follows_its_route():
    tank_part = part('Tank', 1, 8, base=BASE, keywords=['Vehicle'])
    mob = part('Mob', 1, 1, base=BASE)
    state = battle([(tank_part, (6.25, 16))], [(mob, (9.25, 25.5))])
    state['battlefield'] = dict(state.get('battlefield') or {}, terrain=RUINS)
    state, _ = act(state, {'type': 'charge', 'unit': 'p1-tank'}, 6, 6)

    def charge(*route):
        model = {'id': 'p1-tank-0-1', 'x': 9.25, 'y': 24, 'route': [{'x': x, 'y': y} for x, y in route]}
        return {'type': 'charge_move', 'unit': 'p1-tank', 'targets': ['p2-mob'], 'models': [model]}

    assert 'Dense' in refused(state, charge())  # Straight: through the left ruin
    act(state, charge((9.25, 16.5)))  # 10.54" by way of the gap
