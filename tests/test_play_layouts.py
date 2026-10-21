"""
The Warhammer Event Companion's terrain layouts (play/layouts.py, read from the PDF by
scripts/extract_event_layouts.py) as built-in battlefields: what every layout must hold, and checks that
catch a bad reading (each layout is laid out the same for both players, turned half way round).
"""

import math
from collections import Counter

import pytest

from combat_sim.play.battlefields import BUILTIN, MAPS, clean
from combat_sim.play.game import new_game, terrain_areas
from combat_sim.play.geometry import footprint
from combat_sim.play.layouts import LAYOUTS
from test_play_game import army, part

DISPOSITIONS = ('take-and-hold', 'purge-the-foe', 'priority-assets', 'reconnaissance', 'disruption')
LAYOUT_IDS = [entry['id'] for entry in LAYOUTS]


def test_three_layouts_for_every_pair_of_dispositions():
    pairs = Counter(frozenset(entry['dispositions']) for entry in LAYOUTS)
    assert len(pairs) == 15 and set(pairs.values()) == {3}  # 5 mirror pairs and 10 others
    assert sorted(entry['layout'] for entry in LAYOUTS) == sorted('ABC' * 15)
    assert len(set(LAYOUT_IDS)) == 45 and all(d in DISPOSITIONS for entry in LAYOUTS for d in entry['dispositions'])


@pytest.mark.parametrize('key', LAYOUT_IDS)
def test_a_layout_uses_the_16_footprints_on_one_deployment_map(key):
    layout = BUILTIN[key]
    assert layout['map'] in MAPS and layout['zones'] == clean(MAPS[layout['map']])['zones']
    assert Counter(t['footprint'] for t in layout['terrain']) == {'large': 4, 'polygon': 2, 'medium': 4, 'long': 2, 'short': 4}
    assert Counter(t['category'] for t in layout['terrain']) == {'dense': 12, 'light': 4}
    for piece in layout['terrain']:
        shape = footprint(piece['footprint'], piece['x'], piece['y'], piece['angle'], piece['flip'])
        assert all(-0.1 <= x <= 60.1 and -0.1 <= y <= 44.1 for x, y in shape.points), piece


@pytest.mark.parametrize('key', LAYOUT_IDS)
def test_a_layout_has_each_home_objective_and_its_central_ones(key):
    objectives = BUILTIN[key]['objectives']
    assert sorted(o['role'] for o in objectives if o['kind'] == 'home') == ['attacker', 'defender']
    assert 1 <= sum(o['kind'] == 'central' for o in objectives) <= 2
    assert all(0 < o['x'] < 60 and 0 < o['y'] < 44 for o in objectives)


@pytest.mark.parametrize('key', LAYOUT_IDS)
def test_a_layouts_terrain_is_the_same_turned_half_way_round(key):
    """Each piece turned about the centre lands on a piece of the same footprint (within 1.25")."""
    shapes = [(t['footprint'], footprint(t['footprint'], t['x'], t['y'], t['angle'], t['flip']).points)
              for t in BUILTIN[key]['terrain']]
    for kind, points in shapes:
        turned = [(60 - x, 44 - y) for x, y in points]
        assert min(max(min(math.dist(p, q) for q in other) for p in turned)
                   for k, other in shapes if k == kind) < 1.25, (kind, points)


def test_take_and_hold_mirror_layout_a_as_printed():
    """Spot checks against the printed page: Tipping Point, the central objective on two polygons that
    meet along their cut edges in an 11.5" x 10" block round the centre."""
    layout = BUILTIN['take-and-hold-vs-take-and-hold-a']
    assert layout['name'] == 'Take and Hold vs Take and Hold: Layout A' and layout['map'] == 'tipping-point'
    assert layout['missions'] == ['Battlefield Dominance', 'Battlefield Dominance']
    central = next(o for o in layout['objectives'] if o['kind'] == 'central')
    assert (central['x'], central['y']) == (30, 22) and central['terrain'] is not None  # A terrain objective
    block = [p for t in layout['terrain'] if t['footprint'] == 'polygon'
             for p in footprint('polygon', t['x'], t['y'], t['angle'], t['flip']).points]
    xs, ys = [p[0] for p in block], [p[1] for p in block]
    assert (round(max(xs) - min(xs), 1), round(max(ys) - min(ys), 1)) == (10, 11.5)
    assert abs((max(xs) + min(xs)) / 2 - 30) < 0.1 and abs((max(ys) + min(ys)) / 2 - 22) < 0.1


def test_a_game_on_a_layout_keeps_its_objectives_and_skips_the_central_roll():
    armies = [army('Red', (part('Squad', 1, 1),)), army('Blue', (part('Mob', 1, 1),))]
    state = new_game(armies, BUILTIN['take-and-hold-vs-take-and-hold-a'], setup=True)  # One central objective
    assert state['turn']['step'] == 'roll_off'
    assert len(terrain_areas(state)) == 16
