"""
Play mode's setup sequence (src/combat_sim/play/game.py), as in the 11th edition mission sequence: the
central objective roll (two on a 6), the roll-off for attacker or defender, the defender deploying
first, and the roll-off for the first turn. Each step is a logged action.
"""

from combat_sim.play import battlefields
from combat_sim.play.game import new_game, replay
from test_play_game import army, part
from test_play_turns import act, at, refused

BASE = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}


def armies():
    return [army('Red', (part('Squad', 1, 1, base=BASE),)), army('Blue', (part('Mob', 1, 1, base=BASE),))]


def fresh():
    return new_game(armies(), setup=True)


def objectives(state):
    return {o['id']: (o['x'], o['y'], o['owner']) for o in state['objectives']}


def test_a_game_with_setup_starts_by_setting_up_the_battlefield():
    state = fresh()
    assert state['turn']['phase'] == 'setup' and state['turn']['step'] == 'objectives'
    assert 'Units can only be placed' in refused(state, at(state, 'p1-squad', (30, 5)))


def test_one_central_objective_unless_the_roll_is_a_6():
    state, event = act(fresh(), {'type': 'roll_objectives'}, 5)
    assert event['central'] == {'roll': 5, 'objectives': 1}
    assert objectives(state)['central-1'] == (30, 22, None) and 'central-2' not in objectives(state)
    assert state['turn']['step'] == 'roll_off'


def test_a_6_gives_two_central_objectives_along_the_divider():
    state, event = act(fresh(), {'type': 'roll_objectives'}, 6)
    assert event['central'] == {'roll': 6, 'objectives': 2}
    # Dawn of War's divider runs across the middle: 6" either side of the centre along it
    assert objectives(state)['central-1'][:2] == (24, 22) and objectives(state)['central-2'][:2] == (36, 22)
    again = replay(armies(), [event], None, setup=True)  # Replays without dice
    assert objectives(again) == objectives(state)


def test_the_roll_off_winner_chooses_attacker_or_defender():
    state, _ = act(fresh(), {'type': 'roll_objectives'}, 1)
    state, event = act(state, {'type': 'roll_off'}, 3, 3, 2, 5)  # A tie is rolled again
    assert event['roll_off'] == {'rolls': [[3, 3], [2, 5]], 'winner': 1}
    assert state['turn']['step'] == 'choose_role' and state['turn']['player'] == 1
    assert 'attacker or defender' in refused(state, {'type': 'choose_role', 'role': 'sideways'})
    state, _ = act(state, {'type': 'choose_role', 'role': 'attacker'})
    assert state['roles'] == {'attacker': 1, 'defender': 0}
    # The home objectives belong to whoever's zone they're in now
    assert objectives(state)['home-attacker'][2] == 1 and objectives(state)['home-defender'][2] == 0
    # The defender deploys first, in the defender's zone (Dawn of War: the top 12")
    assert state['turn'] == {'phase': 'deployment', 'round': 0, 'player': 0}
    assert 'deployment zone' in refused(state, at(state, 'p1-squad', (30, 5)))
    state, _ = act(state, at(state, 'p1-squad', (30, 40)))
    assert state['turn']['player'] == 1


def deployed(first_turn_rolls):
    state, _ = act(fresh(), {'type': 'roll_objectives'}, 1)
    state, _ = act(state, {'type': 'roll_off'}, 6, 1)
    state, _ = act(state, {'type': 'choose_role', 'role': 'attacker'})  # Red attacks, Blue deploys first
    state, _ = act(state, at(state, 'p2-mob', (30, 40)))
    state, _ = act(state, at(state, 'p1-squad', (30, 5)))
    return act(state, {'type': 'end_deployment'}, *first_turn_rolls)


def test_the_first_turn_roll_off_winner_goes_first_and_turns_follow_from_them():
    state, event = deployed((2, 4))
    assert event['first_turn'] == {'rolls': [[2, 4]], 'winner': 1}
    assert state['roles']['first'] == 1
    assert (state['turn']['round'], state['turn']['player'], state['turn']['phase']) == (1, 1, 'command')
    for _ in range(7):  # Blue's turn (Command to Consolidate), then Red's in the same round
        state, _ = act(state, {'type': 'next_phase'})
    assert (state['turn']['round'], state['turn']['player']) == (1, 0)
    for _ in range(7):
        state, _ = act(state, {'type': 'next_phase'})
    assert (state['turn']['round'], state['turn']['player']) == (2, 1)


def test_a_battlefield_with_fixed_central_objectives_skips_the_roll():
    two = dict(battlefields.BUILTIN['dawn-of-war'])
    two['objectives'] = [o for o in two['objectives'] if o['kind'] != 'central'] + [
        {'id': 'central-1', 'kind': 'central', 'role': None, 'x': 20, 'y': 22},
        {'id': 'central-2', 'kind': 'central', 'role': None, 'x': 40, 'y': 22}]
    state = new_game(armies(), two, setup=True)
    assert state['turn']['step'] == 'roll_off'


def test_games_without_setup_still_deploy_and_start_with_player_one():
    state = new_game(armies())
    assert state['turn'] == {'phase': 'deployment', 'round': 0, 'player': 0}
