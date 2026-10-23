"""
Seats (src/combat_sim/play/seats.py): when each player's app holds one side of a game, whose each action
is, and whose input the game is waiting for.
"""

import re

import pytest

from combat_sim.play.game import ActionError
from combat_sim.play.dice import Dice
from combat_sim.play.game import resolve_action
from combat_sim.play.seats import ACTION_TYPES, actor, check_seat, waiting_on
from test_play_fight import MISS, fight, fighters, to_step
from test_play_mission_game import BLUE_HOME, FAR
from test_play_mission_game import battle as mission_battle
from test_play_mission_game import setup_game
from test_play_turns import act, at, deployed, row, two_armies


def test_a_units_actions_are_its_players():
    state = two_armies()
    assert actor(state, at(state, 'p1-squad', *row(10, 6, 3))) == 0
    assert actor(state, at(state, 'p2-mob', *row(10, 38, 3))) == 1  # Even out of turn: the engine refuses it
    assert actor(state, {'type': 'effect', 'unit': 'p2-mob', 'effect': 'cover'}) == 1


def test_actions_that_name_a_player_are_that_players():
    state = deployed()  # Red's turn
    assert actor(state, {'type': 'stratagem', 'player': 1, 'key': 'x', 'unit': 'p2-mob'}) == 1
    assert actor(state, {'type': 'orders', 'player': 1, 'casualties': 'closest'}) == 1
    assert actor(state, {'type': 'correction', 'player': 1, 'models': {}, 'note': ''}) == 1
    assert actor(state, {'type': 'accept', 'player': 0}) == 0


def test_moving_the_game_on_is_for_whoever_it_is_waiting_on():
    state = deployed()
    assert waiting_on(state) == [0] and actor(state, {'type': 'next_phase'}) == 0
    state, _ = act(state, {'type': 'next_phase'})
    state, _ = act(state, {'type': 'next_phase'})
    assert state['turn']['phase'] == 'shooting' and waiting_on(state) == [0]


def test_the_other_player_moves_the_fight_steps_on_when_their_units_move_or_pick():
    state, _ = act(fighters(), {'type': 'next_phase'})  # Pile in: Red's units, then Blue's
    assert waiting_on(state) == [0]
    state, _ = act(state, {'type': 'next_phase'})
    assert state['turn']['mover'] == 1 and waiting_on(state) == [1] and actor(state, {'type': 'next_phase'}) == 1
    state = to_step(state, 'fight')
    state, _ = act(state, fight('p1-squad'), *MISS * 3)
    assert state['turn']['fight']['picker'] == 1 and waiting_on(state) == [1]


def test_a_correction_waits_for_the_other_player():
    state, _ = act(deployed(), {'type': 'correction', 'player': 0, 'models': {'p2-mob-0-1': {'alive': False}}, 'note': 'Shot'})
    assert waiting_on(state) == [1]


def test_a_mission_choice_waits_for_the_player_making_it():
    _, state = mission_battle('Purge the Foe', 'Disruption', [FAR], [BLUE_HOME, (50, 40)])  # Red condemns
    assert waiting_on(state) == [0] and actor(state, {'type': 'condemn', 'units': ['p2-b0']}) == 0


def test_secondaries_wait_for_each_player_who_has_not_chosen():
    _, state = setup_game([('R', 1, 1)], [('B', 1, 1)])
    assert waiting_on(state) == [0, 1]
    state, _ = act(state, {'type': 'choose_secondaries', 'player': 1, 'mode': 'fixed', 'cards': ['assassination', 'bring-it-down']})
    assert waiting_on(state) == [0]


def test_setup_rolls_are_for_the_player_whose_step_it_is():
    _, state = setup_game([('R', 1, 1)], [('B', 1, 1)])
    state['turn'] = {'phase': 'setup', 'round': 0, 'player': 1, 'step': 'choose_role'}  # Blue won the roll-off
    assert actor(state, {'type': 'choose_role', 'role': 'attacker'}) == 1


def test_a_finished_game_waits_for_nobody():
    state = deployed()
    state['turn'] = {'phase': 'ended', 'round': 5, 'player': 1}
    assert waiting_on(state) == []


def test_a_seat_only_sends_its_own_players_actions():
    state = deployed()
    check_seat(state, {'type': 'next_phase'}, None)  # Hot-seat: one app plays both
    check_seat(state, {'type': 'next_phase'}, 0)
    check_seat(state, {'type': 'orders', 'player': 1, 'casualties': 'closest'}, 1)  # Out of turn, but Blue's own
    with pytest.raises(ActionError, match="Red's to do"):
        check_seat(state, {'type': 'next_phase'}, 1)
    with pytest.raises(ActionError, match="Blue's to do"):
        check_seat(state, {'type': 'stratagem', 'player': 1, 'key': 'x', 'unit': 'p2-mob'}, 0)
    check_seat(state, {'type': 'move', 'unit': 'nobody'}, 1)  # Not a unit: the engine says so


def test_every_action_the_engine_takes_has_a_seat_and_passes_the_game_file_checks():
    """ACTION_TYPES is what game files and the relay may carry: the engine's own list of actions, no more, no less."""
    import inspect
    from combat_sim.play import game
    handled = set(re.findall(r"'([a-z_]+)': lambda s", inspect.getsource(game.resolve_action)))
    assert handled == ACTION_TYPES
    with pytest.raises(ActionError, match='Unknown action'):
        resolve_action(deployed(), {'type': 'self_destruct'}, Dice())
