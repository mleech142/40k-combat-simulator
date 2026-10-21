"""
Play mode's terrain (src/combat_sim/play/game.py), as in the 11th edition core rules on a flat board:
terrain areas from the 5 footprints, each Exposed, Light or Dense with a height.
"""

import copy

from combat_sim.play.battlefields import BUILTIN
from combat_sim.play.game import new_game, unit_by_id
from test_play_game import army, gun, part
from test_play_turns import act, put, refused

BASE = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}  # 0.63" from centre to edge
RIFLE = gun('Rifle', attacks='1', skill=3, range=36)


def piece(id_, category, x, y, footprint='medium', height=3, angle=0):
    return {'id': id_, 'footprint': footprint, 'x': x, 'y': y, 'angle': angle, 'category': category, 'height': height}


def game_on(terrain, red, blue, phase='shooting', objectives=None):
    """Dawn of War with `terrain`; Red's and Blue's units as (part, *spots)."""
    battlefield = copy.deepcopy(BUILTIN['dawn-of-war'])
    battlefield['terrain'] = terrain
    if objectives:
        battlefield['objectives'] = objectives
    state = new_game([army('Red', *[(u[0],) for u in red]), army('Blue', *[(u[0],) for u in blue])], battlefield)
    for owner, units in (('p1', red), ('p2', blue)):
        for spec in units:
            put(state, f'{owner}-{spec[0]["uid"]}', *spec[1:])
    state['turn'] = {'phase': phase, 'round': 1, 'player': 0, 'seq': 1}
    return state


def gunner(x, y, keywords=('Infantry',)):
    return (part('Gunner', 1, 3, base=BASE, weapons=[RIFLE], keywords=list(keywords)), (x, y))


def target(x, y, keywords=('Infantry',)):
    return (part('Target', 1, 3, base=BASE, keywords=list(keywords)), (x, y))


SHOOT = {'type': 'shoot', 'unit': 'p1-gunner', 'target': 'p2-target'}
BLOCK = (7, 18)  # A medium footprint covering x 7-13, y 18-22


def hit_need(state):
    _, event = act(state, SHOOT, 1)
    return event['rolls'][0]['need']


# --- Seeing through terrain ------------------------------------------------------------------------------

def test_light_and_dense_areas_block_sight_but_exposed_ones_dont():
    for category in ('light', 'dense'):
        state = game_on([piece('t1', category, *BLOCK)], [gunner(10, 10)], [target(10, 30)])
        assert 'sight' in refused(state, SHOOT)
    assert hit_need(game_on([piece('t1', 'exposed', *BLOCK)], [gunner(10, 10)], [target(10, 30)])) == 3


def test_models_see_into_and_out_of_the_area_they_are_in():
    # A vehicle (never hidden) inside the area: seen, and fully, so no cover
    state = game_on([piece('t1', 'light', *BLOCK)], [gunner(10, 10)], [target(10, 20, keywords=['Vehicle'])])
    assert hit_need(state) == 3


# --- Cover --------------------------------------------------------------------------------------------------

def test_infantry_within_a_terrain_area_have_cover():
    state = game_on([piece('t1', 'exposed', *BLOCK)], [gunner(10, 10)], [target(10, 20)])
    assert hit_need(state) == 4  # BS3+ worsened by cover


def test_a_target_partly_hidden_behind_terrain_has_cover():
    # A light wall hides the left half of the line between them
    wall = piece('t1', 'light', 4, 18, footprint='short')  # x 4-10, y 18-20
    state = game_on([wall], [gunner(10, 10, keywords=['Vehicle'])], [target(10, 30, keywords=['Vehicle'])])
    assert hit_need(state) == 4


# --- Hidden and gone to ground ---------------------------------------------------------------------------------

def test_infantry_in_light_or_dense_terrain_are_hidden_beyond_15_inches():
    area = [piece('t1', 'light', *BLOCK)]
    near = game_on(area, [gunner(10, 10)], [target(10, 20)])  # 8.7" apart
    assert hit_need(near) == 4  # Seen, and in cover
    far = game_on(area, [gunner(40, 20)], [target(10, 20)])  # 28.7" apart
    assert 'hidden' in refused(far, SHOOT)
    unit_by_id(far, 'p2-target')['status']['shot_seq'] = 0  # Shot in the previous turn: no longer hidden
    assert hit_need(far) == 4


def test_hidden_models_behind_dense_terrain_have_gone_to_ground_12_inches():
    # In a light area; a dense wall covers part of the view from below
    area = piece('t1', 'light', *BLOCK)
    wall = piece('t2', 'dense', 4, 12, footprint='short')  # x 4-10, y 12-14
    assert hit_need(game_on([area, wall], [gunner(10, 7)], [target(10, 20)])) == 4  # 11.7": within 12"
    assert 'hidden' in refused(game_on([area, wall], [gunner(10, 6.5)], [target(10, 20)]), SHOOT)  # 12.2"


# --- Moving through terrain ------------------------------------------------------------------------------------

def mover(keywords):
    return (part('Walker', 1, 8, base=BASE, stats={'move': '20'}, keywords=list(keywords)), (10, 10))


def walk_through(state):
    return {'type': 'move', 'unit': 'p1-walker', 'mode': 'normal', 'models': [{'id': 'p1-walker-0-1', 'x': 10, 'y': 26}]}


def test_monsters_and_vehicles_cant_cross_dense_terrain_over_2_inches_high():
    tall = game_on([piece('t1', 'dense', *BLOCK, height=3)], [mover(['Vehicle'])], [target(50, 40)], phase='movement')
    assert 'Dense' in refused(tall, walk_through(tall))
    low = game_on([piece('t1', 'dense', *BLOCK, height=2)], [mover(['Vehicle'])], [target(50, 40)], phase='movement')
    act(low, walk_through(low))
    infantry = game_on([piece('t1', 'dense', *BLOCK, height=3)], [mover(['Infantry'])], [target(50, 40)], phase='movement')
    act(infantry, walk_through(infantry))
    light = game_on([piece('t1', 'light', *BLOCK, height=5)], [mover(['Vehicle'])], [target(50, 40)], phase='movement')
    act(light, walk_through(light))


# --- Terrain objectives ---------------------------------------------------------------------------------------

def test_a_terrain_objectives_range_is_its_terrain_area():
    ruin = piece('t1', 'dense', 27, 20)  # x 27-33, y 20-24: over the centre
    objectives = [dict(o, terrain='t1') if o['kind'] == 'central' else o for o in BUILTIN['dawn-of-war']['objectives']]
    inside = game_on([ruin], [(part('In', 1, 1, base=BASE, stats={'oc': 2}), (29, 21))], [target(50, 40)],
                     phase='movement', objectives=objectives)
    state, _ = act(inside, {'type': 'next_phase'})
    assert next(o for o in state['objectives'] if o['id'] == 'central-1')['controller'] == 0
    beside = game_on([ruin], [(part('Out', 1, 1, base=BASE, stats={'oc': 2}), (30, 18.5))], [target(50, 40)],
                     phase='movement', objectives=objectives)  # 2.1" from the marker, but outside the area
    state, _ = act(beside, {'type': 'next_phase'})
    assert next(o for o in state['objectives'] if o['id'] == 'central-1')['controller'] is None
