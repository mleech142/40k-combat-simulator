"""
Play mode's board geometry (src/combat_sim/play/geometry.py): inches, (0, 0) at the board's
bottom-left corner, models placed by the centre of their base. Pure maths, no game rules.
"""

import pytest

from combat_sim.play.geometry import FOOTPRINTS, base_distance, base_shape, can_see, clear_line, farther_than, footprint, hull_shape

ROUND_32 = {'shape': 'round', 'length_mm': 32, 'width_mm': 32, 'text': '32mm'}


# --- Distance between bases ----------------------------------------------------------

def test_distance_between_round_bases_is_edge_to_edge():
    # 32mm = 1.26": centres 3" apart leave 3 - 1.26 = 1.74" between the edges
    a, b = base_shape(ROUND_32, 10, 10), base_shape(ROUND_32, 13, 10)
    assert base_distance(a, b) == pytest.approx(3 - 32 / 25.4, abs=0.001)
    assert base_distance(a, base_shape(ROUND_32, 10.5, 10)) == 0  # Overlapping


OVAL = {'shape': 'oval', 'length_mm': 120, 'width_mm': 92, 'text': '120x92mm'}


def test_oval_base_measured_along_its_long_and_short_axes():
    # 120x92mm oval: 2.362" from its centre along the long axis, 1.811" along the short one
    target = base_shape(ROUND_32, 15, 10)
    along = base_distance(base_shape(OVAL, 10, 10), target)
    turned = base_distance(base_shape(OVAL, 10, 10, angle=90), target)
    assert along == pytest.approx(5 - 60 / 25.4 - 16 / 25.4, abs=0.01)
    assert turned == pytest.approx(5 - 46 / 25.4 - 16 / 25.4, abs=0.01)


def test_rectangle_hull_measured_from_its_side_and_its_corner():
    # A 4" x 2" hull centred on (10, 10): its right side is at x = 12, its top-right corner at (12, 11)
    hull = hull_shape(4, 2, 10, 10)
    assert base_distance(hull, base_shape(ROUND_32, 14, 10)) == pytest.approx(2 - 16 / 25.4, abs=0.001)
    assert base_distance(hull, base_shape(ROUND_32, 13, 13)) == pytest.approx(5 ** 0.5 - 16 / 25.4, abs=0.001)


def test_crossing_shapes_overlap_even_with_no_corner_inside_the_other():
    assert base_distance(hull_shape(4, 0.5, 10, 10), hull_shape(4, 0.5, 10, 10, angle=90)) == 0


# --- Terrain footprints ---------------------------------------------------------------

def corners(shape):
    return [(round(x, 6), round(y, 6)) for x, y in shape.points]


def test_the_five_11th_edition_footprints():
    assert {name: size for name, size in FOOTPRINTS.items()} == {
        'large': (11.5, 7), 'polygon': (11.5, 8), 'medium': (6, 4), 'long': (10, 2.5), 'short': (6, 2)}


def test_footprint_placed_by_its_corner_and_turned_about_it():
    assert corners(footprint('medium', 10, 10)) == [(10, 10), (16, 10), (16, 14), (10, 14)]
    assert corners(footprint('medium', 10, 10, angle=90)) == [(10, 10), (10, 16), (6, 16), (6, 10)]
    # The polygon: 11.5" long, 8" wide at its corner's end and 2" at the other (a corner cut off)
    assert corners(footprint('polygon', 0, 0)) == [(0, 0), (11.5, 0), (11.5, 2), (0, 8)]
    assert corners(footprint('polygon', 0, 0, flip=True)) == [(0, 0), (11.5, 0), (11.5, -2), (0, -8)]  # Turned over
    assert corners(footprint('triangle', 0, 0)) == corners(footprint('polygon', 0, 0))  # Its old name


# --- Lines of sight -------------------------------------------------------------------
# A 6" x 4" footprint covering x 10-16, y 10-14.

BLOCK = footprint('medium', 10, 10)


def test_a_line_through_a_footprint_is_blocked():
    assert not clear_line((8, 12), (18, 12), [BLOCK])
    assert clear_line((8, 16), (18, 16), [BLOCK])  # Passing above it


def test_grazing_a_corner_or_running_along_an_edge_is_clear():
    assert clear_line((8, 12), (12, 8), [BLOCK])  # Touches only the corner at (10, 10)
    assert clear_line((8, 10), (18, 10), [BLOCK])  # Along the bottom edge


def test_a_model_can_see_another_if_any_line_between_their_bases_is_clear():
    # A wall 2.5" thick covering x 17.5-20, y 0-10. Bases 32mm (0.63" radius).
    wall = [footprint('long', 20, 0, angle=90)]
    assert not can_see(base_shape(ROUND_32, 10, 5), base_shape(ROUND_32, 25, 5), wall)  # Fully behind it
    # Centres just below the wall's end, but the tops of both bases (y 10.43) see over it
    assert can_see(base_shape(ROUND_32, 15, 9.8), base_shape(ROUND_32, 25, 9.8), wall)
    assert can_see(base_shape(OVAL, 10, 5), base_shape(ROUND_32, 25, 5), [])


def test_farther_than_is_a_quick_check_that_never_rules_out_shapes_within_the_gap():
    """Range checks skip the exact distance for shapes whose bounding boxes are already farther apart."""
    round32 = {'length_mm': 32, 'width_mm': 32}
    model = base_shape(round32, 10, 10)
    assert farther_than(model, base_shape(round32, 20, 10), 3)  # About 8.7" apart, edge to edge
    assert not farther_than(model, base_shape(round32, 14, 10), 3)  # About 2.7"
    area = footprint('large', 10.5, 9)  # Overlaps the model's base
    assert not farther_than(model, area, 0) and base_distance(model, area) == 0
    # Corner to corner on a diagonal: the boxes are closer than the shapes, so it's left to the exact check
    a, b = base_shape(round32, 0, 0), base_shape(round32, 1, 1)
    assert not farther_than(a, b, 0) and base_distance(a, b) > 0
    for x in range(0, 30, 3):
        other = footprint('polygon', x, 12, angle=37)
        if farther_than(model, other, 3):
            assert base_distance(model, other) > 3
