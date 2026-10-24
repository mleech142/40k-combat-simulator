"""
Reactions (release 2.6, src/combat_sim/play/game.py): the other player's decisions in your turn. A reaction window
opens when they could react and their standing orders don't answer it; nothing else happens until they do.
Fire Overwatch: the end of the opponent's Movement phase, one unit snap shoots (11th edition core stratagems).
"""

from combat_sim.play.game import unit_by_id
from combat_sim.play.seats import waiting_on
from test_play_game import gun, part
from test_play_terrain import BASE, game_on, piece, target
from test_play_turns import act, refused

NEXT = {'type': 'next_phase'}
PASS = {'type': 'pass', 'player': 1}


def watcher(x=10, y=30, keywords=('Infantry',), attacks='1'):
    """Blue's unit: one model with a 36" rifle hitting on 3+ (snap shooting: 6s only)."""
    return (part('Watcher', 1, 3, base=BASE, weapons=[gun('Rifle', attacks=attacks, skill=3, range=36)], keywords=list(keywords)), (x, y))


def board(cp=2, blue=None, terrain=()):
    """Red's Movement phase: Red's 3-wound target at (10, 10); Blue's watcher 20" away, with `cp` CP."""
    state = game_on(list(terrain), [target(10, 10)], [blue or watcher()], phase='movement')
    state['players'][1]['cp'] = cp
    return state


def overwatch(**extra):
    return dict({'type': 'overwatch', 'player': 1, 'unit': 'p2-watcher', 'target': 'p1-target'}, **extra)


def wounds(state):
    return unit_by_id(state, 'p1-target')['models'][0]['wounds_left']


# --- The window ------------------------------------------------------------------------------------------------

def test_the_end_of_the_movement_phase_waits_for_overwatch():
    state, event = act(board(), NEXT)
    assert state['turn']['phase'] == 'movement' and state['turn']['window'] == {'kind': 'overwatch', 'player': 1, 'then': 'next_phase'}
    assert event['window'] == 'overwatch' and waiting_on(state) == [1]
    assert 'Waiting for Blue' in refused(state, NEXT)
    assert 'Waiting for Blue' in refused(state, {'type': 'stationary', 'unit': 'p1-target'})
    assert "Blue's decision" in refused(state, {'type': 'pass', 'player': 0})
    state, event = act(state, PASS)
    assert state['turn']['phase'] == 'shooting' and 'window' not in state['turn'] and event['passed'] == 'overwatch'


def test_no_window_when_they_cant_react():
    for state in (board(cp=0),  # No CP
                  board(blue=watcher(y=40)),  # Nothing within 24"
                  board(terrain=[piece('t1', 'dense', 4, 18, footprint='large')])):  # Nothing visible
        after, _ = act(state, NEXT)
        assert after['turn']['phase'] == 'shooting', after['turn']


def test_a_standing_order_of_never_skips_the_window():
    state, _ = act(board(), {'type': 'orders', 'player': 1, 'reactions': {'overwatch': {'mode': 'never'}}})
    assert act(state, NEXT)[0]['turn']['phase'] == 'shooting'


# --- Fire Overwatch ----------------------------------------------------------------------------------------------

def test_overwatch_snap_shoots_hitting_only_on_6s():
    state, _ = act(board(), NEXT)
    after, event = act(state, overwatch(), 5)  # A 5 would hit on 3+, but snap shooting needs a 6
    assert event['overwatch'] and event['rolls'][0]['need'] == 6 and event['attacks'][0]['hits'] == 0
    assert after['players'][1]['cp'] == 1 and after['turn']['phase'] == 'shooting'  # Paid, and the phase moved on
    after, event = act(state, overwatch(), 6, 6, 1)  # Hit (not critical), wound, failed save
    assert not event['rolls'][0]['dice'][0]['critical'] and wounds(after) == 2


def test_overwatch_hits_are_never_critical():
    lethal = (part('Watcher', 1, 3, base=BASE, weapons=[gun('Rifle', attacks='1', range=36, abilities=['Lethal Hits'])]), (10, 30))
    state, _ = act(board(blue=lethal), NEXT)
    _, event = act(state, overwatch(), 6, 1)  # The hit isn't critical: it must still wound (a 1 fails)
    assert event['attacks'][0]['wounds'] == 0


def test_overwatch_checks_its_unit_and_target():
    engaged = board(blue=watcher(y=11.8))  # In engagement range of Red's target: can't
    after, _ = act(engaged, NEXT)
    assert after['turn']['phase'] == 'shooting'
    state, _ = act(board(), NEXT)
    assert 'own units' in refused(state, overwatch(unit='p1-target'))
    assert "Blue's decision" in refused(state, overwatch(player=0))


def test_a_standing_order_overwatches_with_its_unit_at_the_closest_target():
    state, _ = act(board(), {'type': 'orders', 'player': 1, 'reactions': {'overwatch': {'mode': 'unit', 'unit': 'p2-watcher'}}})
    after, event = act(state, NEXT, 6, 6, 1)
    assert event['standing'] and event['overwatch'] and wounds(after) == 2
    assert after['turn']['phase'] == 'shooting' and after['players'][1]['cp'] == 1


def test_fire_overwatch_isnt_used_from_the_stratagem_list():
    state = board()
    state['players'][1]['stratagems'] = [{'key': 'stratagem:9', 'kind': 'stratagem', 'name': 'Fire Overwatch', 'cp': 1}]
    assert 'when the game offers it' in refused(state, {'type': 'stratagem', 'player': 1, 'key': 'stratagem:9', 'unit': 'p2-watcher'})


# --- Heroic Intervention -----------------------------------------------------------------------------------------

def charged_board(cp=3, blue=None, others=()):
    """Red's Charge phase: Red's raider at (10, 20) has charged; Blue's hero 6" above it (4.74" base to base)."""
    raider = (part('Raider', 1, 3, base=BASE), (10, 20))
    hero = blue or (part('Hero', 1, 4, base=BASE, weapons=[gun('Sword', kind='melee', range=None)]), (10, 26))
    state = game_on([], [raider, *others], [hero], phase='charge')
    unit_by_id(state, 'p1-raider')['activity'] = {'charged': True, 'charge_roll': 8, 'charge_targets': []}
    state['players'][1]['cp'] = cp
    return state


def heroic(mode='leap', unit='p2-hero'):
    return {'type': 'heroic', 'player': 1, 'unit': unit, 'mode': mode}


def charge_in(targets=('p1-raider',), y=21.5):
    return {'type': 'charge_move', 'unit': 'p2-hero', 'targets': list(targets), 'models': [{'id': 'p2-hero-0-1', 'x': 10, 'y': y}]}


def test_the_end_of_the_charge_phase_waits_for_heroic_intervention():
    state, _ = act(charged_board(), NEXT)
    assert state['turn']['window']['kind'] == 'heroic' and waiting_on(state) == [1]
    state, _ = act(state, PASS)
    assert state['turn']['phase'] == 'fight'


def test_leap_to_defend_charges_a_unit_that_charged():
    state, _ = act(charged_board(), NEXT)
    state, event = act(state, heroic(), 3, 4)  # 7"
    assert event['roll'] == 7 and state['players'][1]['cp'] == 2
    assert state['turn']['window'] == {'kind': 'heroic', 'player': 1, 'then': 'next_phase', 'unit': 'p2-hero', 'mode': 'leap', 'roll': 7}
    assert 'Heroic Intervention or pass' in refused(state, NEXT)
    state, _ = act(state, charge_in())
    hero = unit_by_id(state, 'p2-hero')
    assert hero['activity']['charged'] and hero['models'][0]['position']['y'] == 21.5
    assert state['turn']['phase'] == 'fight' and 'window' not in state['turn']


def test_leap_to_defend_only_charges_units_that_charged():
    idle = (part('Idler', 1, 3, base=BASE), (13, 24.5))
    state, _ = act(charged_board(others=[idle]), NEXT)
    state, _ = act(state, heroic(), 3, 4)
    assert 'only units that charged' in refused(state, charge_in(targets=['p1-idler']))


def test_into_the_fray_costs_2_cp_and_rolls_at_most_6():
    state, _ = act(charged_board(), NEXT)
    state, event = act(state, heroic('fray'), 6, 6)
    assert event['roll'] == 6 and event['cp'] == 2 and state['players'][1]['cp'] == 1
    act(state, charge_in())


def test_into_the_fray_needs_the_extra_cp():
    state, _ = act(charged_board(cp=1), NEXT)
    assert 'costs 2 CP' in refused(state, heroic('fray'), 6, 6)
    act(state, heroic('leap'), 3, 4)


def test_a_roll_too_short_fails_and_the_phase_moves_on():
    state, _ = act(charged_board(), NEXT)
    state, event = act(state, heroic(), 1, 1)  # 2": the raider is 4.74" away
    assert event['failed'] and state['turn']['phase'] == 'fight' and state['players'][1]['cp'] == 2
    assert unit_by_id(state, 'p2-hero')['models'][0]['position']['y'] == 26


def test_who_can_intervene():
    tank = (part('Hero', 1, 10, base=BASE, keywords=['Vehicle']), (10, 26))
    assert act(charged_board(blue=tank), NEXT)[0]['turn']['phase'] == 'fight'  # No window: a plain Vehicle can't
    walker = (part('Hero', 1, 10, base=BASE, keywords=['Vehicle', 'Walker']), (10, 26))
    assert act(charged_board(blue=walker), NEXT)[0]['turn']['window']['kind'] == 'heroic'
    far = (part('Hero', 1, 4, base=BASE), (10, 34))  # 12.7" away
    assert act(charged_board(blue=far), NEXT)[0]['turn']['phase'] == 'fight'
    never, _ = act(charged_board(), {'type': 'orders', 'player': 1, 'reactions': {'heroic': {'mode': 'never'}}})
    assert act(never, NEXT)[0]['turn']['phase'] == 'fight'


def test_a_mode_with_nothing_to_charge_isnt_offered():
    quiet = charged_board(blue=(part('Hero', 1, 4, base=BASE), (10, 30)))
    unit_by_id(quiet, 'p1-raider')['activity'] = {}  # Nothing charged, and nothing within 6": no window
    assert act(quiet, NEXT)[0]['turn']['phase'] == 'fight'
    state, _ = act(charged_board(blue=(part('Hero', 1, 4, base=BASE), (10, 30))), NEXT)  # 8.7" away: Leap only
    assert state['turn']['window']['kind'] == 'heroic'
    assert 'nothing to charge' in refused(state, heroic('fray'), 6, 6)
