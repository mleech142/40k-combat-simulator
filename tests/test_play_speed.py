"""
Play mode's speed (testing list item 4): the work done for every action on a busy board must stay quick.
Before October 2026's fix, listing the units that can still shoot checked line of sight from every model to
every enemy model, beyond range included, and took over 10 seconds a request on a real game.

The limits are several times what a PC takes, so slow test machines pass; a return to the old checks fails.
"""

import copy
import time

from combat_sim.play.battlefields import BUILTIN
from combat_sim.play.game import new_game, ready_units, shooting_options
from test_play_game import army, gun, part
from test_play_turns import put

BASE = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}
LAYOUT = 'take-and-hold-vs-take-and-hold-a'


def busy_board():
    """Two armies of 10 ten-model units with 36" and 24" guns, facing each other across an Event Companion layout."""
    weapons = [gun('Rifle', range=36), gun('Carbine', range=24)]
    units = {player: [part(f'Squad {player}{i}', 10, 1, base=BASE, weapons=weapons) for i in range(10)] for player in 'RB'}
    state = new_game([army('Red', *[(u,) for u in units['R']]), army('Blue', *[(u,) for u in units['B']])],
                     copy.deepcopy(BUILTIN[LAYOUT]))
    for owner, player, rows in (('p1', 'R', (2, 5, 8)), ('p2', 'B', (42, 39, 36))):
        for i, unit in enumerate(units[player]):
            x, y = 2 + (i % 4) * 15, rows[i // 4]
            put(state, f'{owner}-{unit["uid"]}', *[(x + 1.5 * n, y) for n in range(10)])
    state['turn'] = {'phase': 'shooting', 'round': 1, 'player': 0, 'seq': 1}
    return state


def seconds(work):
    start = time.perf_counter()
    result = work()
    return time.perf_counter() - start, result


def test_listing_the_units_that_can_shoot_is_quick():
    state = busy_board()
    took, ready = seconds(lambda: ready_units(state))
    assert ready  # Some can shoot, so the check had to look
    assert took < 1.0, f'ready_units took {took:.2f}s'


def test_a_units_shooting_options_are_quick():
    state = busy_board()
    took, options = seconds(lambda: shooting_options(state, 'p1-squad r0'))
    assert options['weapons']
    assert took < 1.0, f'shooting_options took {took:.2f}s'
