"""
Play mode's waypoint moves (src/combat_sim/play/game.py): a Movement-phase move can carry on from where
the unit stands ('waypoint'), so a unit can go round terrain; the whole path counts against its Move and
every leg is checked.
"""

from combat_sim.play.game import moved_distance, unit_by_id
from test_play_game import part
from test_play_terrain import BASE, BLOCK, game_on, piece, target
from test_play_turns import act, refused

WALKER = 'p1-walker'


def tank(move):
    return (part('Walker', 1, 8, base=BASE, stats={'move': str(move)}, keywords=['Vehicle']), (10, 14))


def board(move=20):
    """A Vehicle at (10, 14) below a 5" Dense ruin (x 7-13, y 18-22): climbing it costs 10" more."""
    return game_on([piece('t1', 'dense', *BLOCK, height=5)], [tank(move)], [target(50, 40)], phase='movement')


def go(x, y, waypoint=False):
    action = {'type': 'move', 'unit': WALKER, 'mode': 'normal', 'models': [{'id': f'{WALKER}-0-1', 'x': x, 'y': y}]}
    return dict(action, waypoint=True) if waypoint else action


def where(state):
    position = unit_by_id(state, WALKER)['models'][0]['position']
    return position['x'], position['y']


def round_the_ruin(move=20):
    state, _ = act(board(move), go(14, 17))  # 5"
    state, _ = act(state, go(14, 23, waypoint=True))  # 6" more
    return state


def test_a_unit_goes_round_terrain_by_way_of_waypoints():
    state = board()
    assert 'Dense' in refused(state, go(10, 26))  # Straight over the ruin: 12" and 10" climbing
    state = round_the_ruin()
    state, _ = act(state, go(10, 26, waypoint=True))  # 5" more: 16" in all
    assert where(state) == (10, 26)
    unit = unit_by_id(state, WALKER)
    assert unit['activity']['via'] == {f'{WALKER}-0-1': [{'x': 14, 'y': 17, 'angle': 0}, {'x': 14, 'y': 23, 'angle': 0}]}
    assert round(moved_distance(unit), 6) == 16  # For Heavy: the whole path


def test_the_whole_path_counts_against_the_move():
    assert '16.0' in refused(round_the_ruin(move=15), go(10, 26, waypoint=True))


def test_every_leg_is_checked():
    state, _ = act(board(), go(10, 16))
    assert 'Dense' in refused(state, go(10, 26, waypoint=True))


def test_a_move_without_a_waypoint_starts_again_from_where_the_unit_began():
    state, _ = act(round_the_ruin(), go(16, 14))  # 6" in a straight line from (10, 14)
    unit = unit_by_id(state, WALKER)
    assert where(state) == (16, 14) and unit['activity']['via'] == {} and moved_distance(unit) == 6


def test_resetting_a_move_forgets_its_waypoints():
    state, _ = act(round_the_ruin(), {'type': 'reset_move', 'unit': WALKER})
    unit = unit_by_id(state, WALKER)
    assert where(state) == (10, 14) and 'via' not in unit['activity']
