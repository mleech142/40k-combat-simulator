"""
Play mode's turn order and movement (src/combat_sim/play/game.py): deployment taking turns, battle
rounds and phases, and the Movement phase's moves, all through resolve_action / apply_event.
"""

import pytest

from combat_sim.play.dice import Dice
from combat_sim.play.game import ActionError, apply_event, new_game, resolve_action, unit_by_id
from test_play_dice import FixedRolls
from test_play_game import army, part


def act(state, action, *faces):
    """Resolve and apply one action (dice faces given in order); the new state and the event."""
    event = resolve_action(state, action, Dice(FixedRolls(*faces)))
    return apply_event(state, event), event


def refused(state, action, *faces):
    with pytest.raises(ActionError) as error:
        resolve_action(state, action, Dice(FixedRolls(*faces)))
    return str(error.value)


def at(state, unit_id, *spots):
    """A place action putting a unit's living models at the spots, in order."""
    models = [m['id'] for m in unit_by_id(state, unit_id)['models'] if m['alive']]
    return {'type': 'place', 'unit': unit_id, 'models': [{'id': m, 'x': x, 'y': y} for m, (x, y) in zip(models, spots)]}


def row(x, y, n, gap=1.5):
    return [(x + gap * i, y) for i in range(n)]


def two_armies():
    """Red: Squad (3 models) and Hero (1). Blue: Mob (3 models). All on 32mm bases, M6"."""
    base = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}
    return new_game([army('Red', (part('Squad', 3, 1, base=base),), (part('Hero', 1, 4, base=base),)),
                     army('Blue', (part('Mob', 3, 1, base=base),))])


def deployed():
    """Both armies set up, deployment over: battle round 1, Red's Command phase."""
    state = two_armies()
    state, _ = act(state, at(state, 'p1-squad', *row(10, 6, 3)))
    state, _ = act(state, at(state, 'p2-mob', *row(10, 38, 3)))
    state, _ = act(state, at(state, 'p1-hero', (20, 6)))
    state, _ = act(state, {'type': 'end_deployment'})
    return state


# --- Deployment ---------------------------------------------------------------------------

def test_a_game_starts_with_player_one_deploying():
    assert two_armies()['turn'] == {'phase': 'deployment', 'round': 0, 'player': 0}


def test_players_take_turns_placing_units():
    state = two_armies()
    state, _ = act(state, at(state, 'p1-squad', *row(10, 6, 3)))
    assert state['turn']['player'] == 1
    assert 'Blue' in refused(state, at(state, 'p1-hero', (20, 6)))  # Blue's turn to place
    state, _ = act(state, at(state, 'p2-mob', *row(10, 38, 3)))
    # Blue has nothing left to place: Red places the rest, and can still adjust a placed unit
    state, _ = act(state, at(state, 'p1-hero', (20, 6)))
    state, _ = act(state, at(state, 'p1-squad', *row(12, 6, 3)))
    assert state['turn']['player'] == 0


def test_deployment_ends_once_every_unit_is_placed():
    state = two_armies()
    assert 'every unit' in refused(state, {'type': 'end_deployment'})
    state = deployed()
    assert state['turn'] == {'phase': 'command', 'round': 1, 'player': 0, 'seq': 1}  # The battle's first turn
    assert 'deployment' in refused(state, at(state, 'p1-hero', (22, 6)))


# --- Battle rounds and phases --------------------------------------------------------------

PHASES = ['command', 'movement', 'shooting', 'charge', 'fight']


def test_phases_and_turns_follow_in_order():
    state = deployed()
    seen = []
    for _ in range(15):
        turn = state['turn']
        seen.append((turn['round'], turn['player'], turn['phase'] + ('/' + turn['step'] if 'step' in turn else '')))
        state, _ = act(state, {'type': 'next_phase'})
    steps = PHASES[:-1] + ['fight/pile_in', 'fight/fight', 'fight/consolidate']
    assert seen == [(1, 0, p) for p in steps] + [(1, 1, p) for p in steps] + [(2, 0, 'command')]


# --- Movement ---------------------------------------------------------------------------------
# Red's Squad starts at (10, 6), (11.5, 6), (13, 6); 32mm bases are 1.26" across (0.63" from centre to edge).

def movement():
    state, _ = act(deployed(), {'type': 'next_phase'})
    assert state['turn']['phase'] == 'movement'
    return state


def put(state, unit_id, *spots):
    """Set a unit's models straight onto the board (to arrange a test; not an action)."""
    for model, (x, y) in zip([m for m in unit_by_id(state, unit_id)['models'] if m['alive']], spots):
        model['position'] = {'x': x, 'y': y, 'angle': 0}
    return state


def move(unit_id, mode, *spots, state):
    models = [m['id'] for m in unit_by_id(state, unit_id)['models'] if m['alive']]
    return {'type': 'move', 'unit': unit_id, 'mode': mode,
            'models': [{'id': m, 'x': x, 'y': y} for m, (x, y) in zip(models, spots)]}


def test_a_normal_move_goes_up_to_the_move_characteristic():
    state = movement()
    after, event = act(state, move('p1-squad', 'normal', *row(10, 12, 3), state=state))
    assert unit_by_id(after, 'p1-squad')['activity']['moved'] == 'normal' and event['rolls'] == []
    assert 'at most 6"' in refused(state, move('p1-squad', 'normal', *row(10, 12.1, 3), state=state))


def test_only_the_active_player_moves_and_only_in_the_movement_phase():
    state = movement()
    assert "Red's turn" in refused(state, move('p2-mob', 'normal', *row(10, 36, 3), state=state))
    command = deployed()
    assert 'movement phase' in refused(command, move('p1-squad', 'normal', *row(10, 8, 3), state=command))


def positions(state, unit_id):
    return [(m['position']['x'], m['position']['y']) for m in unit_by_id(state, unit_id)['models'] if m['alive']]


def test_a_unit_can_move_again_measured_from_where_it_started_the_phase():
    state = movement()
    state, _ = act(state, move('p1-squad', 'normal', *row(10, 10, 3), state=state))  # 4"
    state, _ = act(state, move('p1-squad', 'normal', *row(10, 12, 3), state=state))  # 6" from the start
    assert positions(state, 'p1-squad') == row(10, 12, 3)
    assert 'at most 6"' in refused(state, move('p1-squad', 'normal', *row(10, 12.5, 3), state=state))  # 6.5"


def test_reset_puts_a_unit_back_where_it_started_the_phase():
    state = movement()
    state, _ = act(state, move('p1-squad', 'normal', *row(10, 10, 3), state=state))
    state, event = act(state, {'type': 'reset_move', 'unit': 'p1-squad'})
    assert positions(state, 'p1-squad') == row(10, 6, 3) and event['rolls'] == []
    assert 'moved' not in unit_by_id(state, 'p1-squad')['activity']
    # Remaining stationary after a move puts it back too
    state, _ = act(state, move('p1-squad', 'normal', *row(10, 10, 3), state=state))
    state, _ = act(state, {'type': 'stationary', 'unit': 'p1-squad'})
    assert positions(state, 'p1-squad') == row(10, 6, 3)
    assert unit_by_id(state, 'p1-squad')['activity']['moved'] == 'stationary'
    state, _ = act(state, move('p1-squad', 'normal', *row(10, 9, 3), state=state))  # And it can still move after all
    assert unit_by_id(state, 'p1-squad')['activity']['moved'] == 'normal'


def test_moves_lock_when_the_movement_phase_ends():
    state = movement()
    state, _ = act(state, move('p1-squad', 'normal', *row(10, 10, 3), state=state))
    state, _ = act(state, {'type': 'next_phase'})
    assert 'movement phase' in refused(state, move('p1-squad', 'normal', *row(10, 11, 3), state=state))
    assert 'movement phase' in refused(state, {'type': 'reset_move', 'unit': 'p1-squad'})
    assert positions(state, 'p1-squad') == row(10, 10, 3)


def test_whether_a_unit_must_fall_back_is_judged_where_it_started():
    state = put(movement(), 'p2-mob', (10, 7.8), (11.5, 7.8), (13, 7.8))  # Engaged at the start of the phase
    state, _ = act(state, move('p1-squad', 'fall_back', *row(10, 4, 3), state=state))  # 2.54" away: unengaged
    assert 'Fall Back' in refused(state, move('p1-squad', 'normal', *row(10, 3, 3), state=state))
    state, _ = act(state, move('p1-squad', 'fall_back', *row(10, 3, 3), state=state))


def test_an_advance_roll_survives_a_reset():
    state = movement()
    state, _ = act(state, {'type': 'advance', 'unit': 'p1-squad'}, 3)
    state, _ = act(state, move('p1-squad', 'advance', *row(10, 14, 3), state=state))
    state, _ = act(state, {'type': 'reset_move', 'unit': 'p1-squad'})
    assert unit_by_id(state, 'p1-squad')['activity']['advance_roll'] == 3
    assert 'Advance' in refused(state, move('p1-squad', 'normal', *row(10, 8, 3), state=state))
    assert 'already rolled' in refused(state, {'type': 'advance', 'unit': 'p1-squad'}, 5)


def test_advance_rolls_a_d6_and_adds_it_to_the_move():
    state = movement()
    assert 'Advance' in refused(state, move('p1-squad', 'advance', *row(10, 14, 3), state=state))  # Roll first
    state, event = act(state, {'type': 'advance', 'unit': 'p1-squad'}, 4)
    assert [(g['purpose'], [d['face'] for d in g['dice']]) for g in event['rolls']] == [('advance', [4])]
    assert unit_by_id(state, 'p1-squad')['activity']['advance_roll'] == 4
    assert 'at most 10"' in refused(state, move('p1-squad', 'advance', *row(10, 16.1, 3), state=state))
    assert 'Advance' in refused(state, move('p1-squad', 'normal', *row(10, 8, 3), state=state))  # Rolled: it advances
    state, _ = act(state, move('p1-squad', 'advance', *row(10, 16, 3), state=state))
    assert unit_by_id(state, 'p1-squad')['activity']['moved'] == 'advance'


def test_a_unit_must_end_its_move_in_coherency():
    state = movement()
    assert 'coherency' in refused(state, move('p1-squad', 'normal', (10, 8), (12, 8), (16, 8), state=state))
    act(state, move('p1-squad', 'normal', *row(10, 8, 3, gap=2.5), state=state))  # 1.24" gaps: fine
    base = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}
    # 7 models in a line: one neighbour each is enough in 11th edition
    big = new_game([army('Red', (part('Horde', 7, 1, base=base),)), army('Blue', (part('Mob', 1, 1, base=base),))])
    big['turn'] = {'phase': 'movement', 'round': 1, 'player': 0}
    put(big, 'p2-mob', (30, 38))
    put(big, 'p1-horde', *row(10, 6, 7))
    act(big, move('p1-horde', 'normal', *row(10, 8, 7, gap=1.5), state=big))
    # Every model also within 9" of every other: 10 models 1.6" apart stretch 13" end to end
    long = new_game([army('Red', (part('Line', 10, 1, base=base),)), army('Blue', (part('Mob', 1, 1, base=base),))])
    long['turn'] = {'phase': 'movement', 'round': 1, 'player': 0}
    put(long, 'p2-mob', (50, 38))
    put(long, 'p1-line', *row(10, 6, 10))
    assert '9"' in refused(long, move('p1-line', 'normal', *row(10, 8, 10, gap=1.6), state=long))


def test_moves_dont_end_in_engagement_range_and_engaged_units_fall_back():
    # Engagement range is 2": the Squad's 32mm bases (1.26" across) at y 6
    state = put(movement(), 'p2-mob', (10, 10), (11.5, 10), (13, 10))  # 2.74" from the Squad's front
    assert 'engagement range' in refused(state, move('p1-squad', 'normal', *row(10, 7, 3), state=state))  # 1.74"
    assert 'Fall Back' in refused(state, move('p1-squad', 'fall_back', *row(10, 5, 3), state=state))  # Not engaged
    engaged = put(state, 'p2-mob', (10, 8.5), (11.5, 8.5), (13, 8.5))  # 1.24" away
    assert 'Fall Back' in refused(engaged, move('p1-squad', 'normal', *row(10, 4, 3), state=engaged))
    assert 'engagement range' in refused(engaged, move('p1-squad', 'fall_back', *row(10, 5.8, 3), state=engaged))
    after, _ = act(engaged, move('p1-squad', 'fall_back', *row(10, 4, 3), state=engaged))  # 3.24": out
    assert unit_by_id(after, 'p1-squad')['activity']['moved'] == 'fall_back'


def fast_squad(keywords=('Infantry',), enemy_keywords=('Infantry',)):
    """Red's 3-model Squad with Move 12" at y 6; one Blue model at (11.5, 9.6), 2.34" in front of its middle."""
    base = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}
    state = new_game([army('Red', (part('Squad', 3, 1, base=base, stats={'move': '12'}, keywords=list(keywords)),)),
                      army('Blue', (part('Mob', 3, 1, base=base, keywords=list(enemy_keywords)),))])
    state['turn'] = {'phase': 'movement', 'round': 1, 'player': 0}
    put(state, 'p1-squad', *row(10, 6, 3))
    return put(state, 'p2-mob', (11.5, 9.6), (40, 38), (41.5, 38))


def test_models_cant_move_through_enemy_models():
    state = fast_squad()
    # Straight up the board, the middle model would pass through the enemy model
    assert 'through' in refused(state, move('p1-squad', 'normal', *row(10, 14, 3), state=state))
    act(state, move('p1-squad', 'normal', *row(18, 10, 3), state=state))  # Past it, to the right: fine


def test_monsters_and_vehicles_move_through_enemy_models_but_not_other_monsters_or_vehicles():
    act(fast_squad(keywords=['Vehicle']), move('p1-squad', 'normal', *row(10, 14, 3), state=fast_squad(keywords=['Vehicle'])))
    walkers = fast_squad(keywords=['Vehicle'], enemy_keywords=['Monster'])
    assert 'through' in refused(walkers, move('p1-squad', 'normal', *row(10, 14, 3), state=walkers))


def test_monsters_and_vehicles_fall_back_through_enemy_models_too():
    """Core rules 17.01 and its FAQ: Monsters and Vehicles pass through models on every kind of move."""
    base = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}
    for keywords, through in ((['Vehicle'], True), (['Infantry'], False)):
        state = new_game([army('Red', (part('Tank', 1, 1, base=base, stats={'move': '12'}, keywords=keywords),)),
                          army('Blue', (part('Mob', 2, 1, base=base),))])
        state['turn'] = {'phase': 'movement', 'round': 1, 'player': 0}
        put(state, 'p1-tank', (10, 10))
        put(state, 'p2-mob', (10, 11.9), (10, 7))  # One engaged with it, one behind it
        back = move('p1-tank', 'fall_back', (10, 3), state=state)
        if through:
            act(state, back)
        else:
            assert 'through' in refused(state, back)


def test_remaining_stationary_is_recorded():
    state, _ = act(movement(), {'type': 'stationary', 'unit': 'p1-squad'})
    assert unit_by_id(state, 'p1-squad')['activity']['moved'] == 'stationary'


def test_a_new_turn_clears_what_units_did():
    state, _ = act(movement(), {'type': 'stationary', 'unit': 'p1-squad'})
    for _ in range(6):  # Shooting, Charge, then the Fight phase's three steps, to Blue's turn
        state, _ = act(state, {'type': 'next_phase'})
    assert state['turn']['player'] == 1 and unit_by_id(state, 'p1-squad')['activity'] == {}
