"""
Play mode's Measure tool (game.measure, POST /api/play/measure): the distance between two points, or from a
model's base edge, so model to model is base to base.
"""

import pytest

from combat_sim.play.game import ActionError, measure
from test_play_api import APP_DATA

ROUND = {'shape': 'round', 'length': 1.26, 'width': 1.26}  # A 32mm base
HULL = {'shape': 'rect', 'length': 4, 'width': 2}


def test_point_to_point():
    assert measure([{'x': 0, 'y': 0}, {'x': 3, 'y': 4}]) == pytest.approx(5)


def test_from_a_base_edge():
    assert measure([{'x': 0, 'y': 0, 'outline': ROUND}, {'x': 5, 'y': 0}]) == pytest.approx(5 - 0.63)


def test_base_to_base():
    assert measure([{'x': 0, 'y': 0, 'outline': ROUND}, {'x': 5, 'y': 0, 'outline': ROUND}]) == pytest.approx(5 - 1.26)


def test_a_turned_hull():
    """A 4" x 2" hull turned 90°: its edge is 1" from its centre along x, not 2"."""
    assert measure([{'x': 0, 'y': 0, 'outline': HULL, 'angle': 90}, {'x': 5, 'y': 0}]) == pytest.approx(4)


def test_touching_bases_are_0_apart():
    assert measure([{'x': 0, 'y': 0, 'outline': ROUND}, {'x': 1, 'y': 0, 'outline': ROUND}]) == 0


@pytest.mark.parametrize('ends', [[], [{'x': 0, 'y': 0}], [{'x': 'a', 'y': 0}, {'x': 0, 'y': 0}],
                                  [{'x': 0, 'y': 0, 'outline': {'shape': 'star', 'length': 1, 'width': 1}}, {'x': 1, 'y': 1}]])
def test_bad_ends_are_refused(ends):
    with pytest.raises(ActionError):
        measure(ends)


def test_the_api(make_client):
    client = make_client(APP_DATA)[0]
    answer = client.post('/api/play/measure', json={'ends': [{'x': 0, 'y': 0, 'outline': ROUND}, {'x': 5, 'y': 0}]})
    assert answer.status_code == 200 and answer.get_json() == {'distance': 4.37}
    assert client.post('/api/play/measure', json={'ends': [{'x': 0}]}).status_code == 400
