"""
Play mode's battlefields (src/combat_sim/play/battlefields.py): the 11th edition deployment maps,
transcribed from their cards into board inches (60" wide, 44" deep, (0, 0) bottom-left).
"""

import math

import pytest

from combat_sim.play.battlefields import BUILTIN, MAPS, central_points, inside, wholly_inside
from combat_sim.play.geometry import base_shape

NAMES = ['Dawn of War', 'Tipping Point', 'Sweeping Engagement', 'Search and Destroy', 'Hammer and Anvil',
         'Crucible of Battle']


def half_turn(point):
    """A point turned half way round the board's centre (30, 22)."""
    return (round(60 - point[0], 3), round(44 - point[1], 3))


def area(polygon):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))) / 2


def test_the_six_deployment_maps():
    assert [b['name'] for b in MAPS.values()] == NAMES


@pytest.mark.parametrize('key', list(MAPS))
def test_each_map_is_the_same_for_both_players_turned_half_way_round(key):
    """Every map is symmetric about the centre: a check on the transcription, not a copy of it."""
    battlefield = BUILTIN[key]
    attacker, defender = battlefield['zones']['attacker'], battlefield['zones']['defender']
    assert sorted(half_turn(p) for p in attacker) == sorted((round(x, 3), round(y, 3)) for x, y in defender)
    by_role = {(o['kind'], o['role']): (o['x'], o['y']) for o in battlefield['objectives']}
    assert half_turn(by_role[('home', 'attacker')]) == by_role[('home', 'defender')]
    expansions = sorted((o['x'], o['y']) for o in battlefield['objectives'] if o['kind'] == 'expansion')
    assert sorted(half_turn(p) for p in expansions) == expansions
    assert (by_role[('central', None)]) == (30, 22)
    a, b = battlefield['divider']
    assert half_turn(a) == (round(b[0], 3), round(b[1], 3))  # The dashed line runs through the centre


@pytest.mark.parametrize('key', list(MAPS))
def test_home_objectives_sit_in_their_own_zone_and_zones_dont_overlap(key):
    battlefield = BUILTIN[key]
    for role in ('attacker', 'defender'):
        home = next(o for o in battlefield['objectives'] if o['kind'] == 'home' and o['role'] == role)
        assert inside((home['x'], home['y']), battlefield['zones'][role])
    assert not inside((30, 22), battlefield['zones']['attacker']) and not inside((30, 22), battlefield['zones']['defender'])
    assert 300 < area(battlefield['zones']['attacker']) < 800


def test_measurements_read_off_the_cards():
    dawn = BUILTIN['dawn-of-war']
    assert area(dawn['zones']['attacker']) == 60 * 12  # 12" deep along a long edge
    hammer = BUILTIN['hammer-and-anvil']
    assert area(hammer['zones']['attacker']) == 18 * 44  # 18" deep along a short edge
    crucible = BUILTIN['crucible-of-battle']
    assert area(crucible['zones']['attacker']) == 30 * 44 / 2  # A corner triangle 30" by 44"
    search = BUILTIN['search-and-destroy']
    quarter_less_arc = 30 * 22 - math.pi * 9 ** 2 / 4
    assert area(search['zones']['attacker']) == pytest.approx(quarter_less_arc, rel=0.01)  # 9" circle cut out


def test_the_central_objective_is_one_point_or_two_6_inches_either_side_along_the_divider():
    dawn = BUILTIN['dawn-of-war']
    assert central_points(dawn, double=False) == [(30, 22)]
    assert central_points(dawn, double=True) == [(24, 22), (36, 22)]
    tipping = central_points(BUILTIN['tipping-point'], double=True)
    assert all(math.dist(p, (30, 22)) == pytest.approx(6, abs=1e-3) for p in tipping)


def test_wholly_inside_a_zone_means_the_whole_base():
    zone = BUILTIN['dawn-of-war']['zones']['attacker']  # y 0-12
    base = {'length_mm': 32, 'width_mm': 32}
    assert wholly_inside(base_shape(base, 10, 11), zone)  # Edge at 11.63
    assert not wholly_inside(base_shape(base, 10, 11.5), zone)  # Edge at 12.13
