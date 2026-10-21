"""
Play mode's objectives (src/combat_sim/play/game.py), as in the 11th edition core rules: 40mm markers,
in range within 3", control by OC at the end of every phase and turn (ties: nobody), not sticky.
"""

from combat_sim.play.game import OBJECTIVE_RANGE, new_game, unit_by_id
from test_play_game import army, part
from test_play_turns import act, put

BASE = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}  # 0.63" from centre to edge
MARKER = 20 / 25.4  # A 40mm marker: 0.79" from centre to edge


def model(name, models=1, oc=1, **extra):
    return part(name, models, 1, base=BASE, stats={'oc': oc}, **extra)


def battle(red, blue, phase='movement', player=0):
    state = new_game([army('Red', *[(u[0],) for u in red]), army('Blue', *[(u[0],) for u in blue])])
    for owner, units in (('p1', red), ('p2', blue)):
        for spec in units:
            put(state, f'{owner}-{spec[0]["uid"]}', *spec[1:])
    state['turn'] = {'phase': phase, 'round': 1, 'player': player}
    return state


def control(state):
    return {o['id']: o['controller'] for o in state['objectives']}


FAR = (50, 6)  # Nowhere near any objective


def test_a_new_game_has_the_default_objectives_and_nobody_controls_them():
    state = new_game([army('Red', (model('A'),)), army('Blue', (model('B'),))])
    assert [(o['id'], o['kind'], o['owner'], o['x'], o['y']) for o in state['objectives']] == [
        ('home-1', 'home', 0, 30, 6), ('home-2', 'home', 1, 30, 38), ('central-1', 'central', None, 30, 22),
        ('expansion-1', 'expansion', None, 12, 22), ('expansion-2', 'expansion', None, 48, 22)]
    assert all(o['controller'] is None for o in state['objectives'])
    assert OBJECTIVE_RANGE == 3


def test_control_goes_to_the_most_objective_control_in_range_at_the_end_of_a_phase():
    # Red's 2 models (OC 2) beside the central objective; Blue's one (OC 1) on its other side
    reach = 3 + 0.63 + MARKER  # Centre-to-centre distance at the edge of range
    red = (model('Squad', 2, oc=2), (30 - reach + 0.05, 22), (30, 22 - reach + 0.05))
    blue = (model('Mob', 1, oc=1), (30 + reach - 0.05, 22))
    state, event = act(battle([red], [blue]), {'type': 'next_phase'})
    assert control(state)['central-1'] == 0
    assert event['objectives'] == {'central-1': 0}  # Only the changes, for the log and replays


def test_models_just_out_of_range_dont_count():
    reach = 3 + 0.63 + MARKER
    red = (model('Squad', 1, oc=5), (30 - reach - 0.05, 22))
    state, _ = act(battle([red], [(model('Mob'), FAR)]), {'type': 'next_phase'})
    assert control(state)['central-1'] is None


def test_a_tie_is_nobodys_and_battle_shocked_units_add_nothing():
    red = (model('Squad', 1, oc=2), (28, 22))
    blue = (model('Mob', 2, oc=1), (32, 22), (31.5, 23.5))
    state, _ = act(battle([red], [blue]), {'type': 'next_phase'})
    assert control(state)['central-1'] is None  # 2 against 2
    shocked = battle([red], [blue])
    unit_by_id(shocked, 'p2-mob')['status'] = {'shocked': True}
    state, _ = act(shocked, {'type': 'next_phase'})
    assert control(state)['central-1'] == 0


def test_control_isnt_kept_once_the_units_leave():
    red = (model('Squad', 1, oc=2), (28, 22))
    state, _ = act(battle([red], [(model('Mob'), FAR)]), {'type': 'next_phase'})
    assert control(state)['central-1'] == 0
    put(state, 'p1-squad', (20, 10))
    state, _ = act(state, {'type': 'next_phase'})
    assert control(state)['central-1'] is None


def test_one_model_can_count_for_two_objectives_in_range():
    objectives = [{'id': 'a', 'kind': 'expansion', 'owner': None, 'x': 10, 'y': 20},
                  {'id': 'b', 'kind': 'expansion', 'owner': None, 'x': 14, 'y': 20}]
    state = battle([(model('Squad', 1, oc=1), (12, 20))], [(model('Mob'), FAR)])
    state['objectives'] = [dict(o, controller=None) for o in objectives]
    state, _ = act(state, {'type': 'next_phase'})
    assert control(state) == {'a': 0, 'b': 0}
