"""
Play mode's mission actions and operation markers (src/combat_sim/play/mission_state.py): starting in the
Shooting phase (core rules 16), completing at the end of the turn, markers and their statuses, Locate and
Deny's opening markers, Surveil the Foe's marker removal and Consecrate.
"""

import copy

from combat_sim.play.battlefields import BUILTIN
from combat_sim.play.game import new_game
from combat_sim.play.mission_state import action_options
from test_play_mission_game import BLUE_HOME, CENTRE, FAR, WEST, armies, to_turn_end
from test_play_terrain import piece
from test_play_turns import act, put, refused


def game(red, blue, red_spots, blue_spots, terrain=(), red_oc=2, blue_oc=1):
    frozen = armies(red, blue, [(f'R{i}', 1, red_oc) for i in range(len(red_spots))],
                    [(f'B{i}', 1, blue_oc) for i in range(len(blue_spots))])
    battlefield = copy.deepcopy(BUILTIN['dawn-of-war'])
    battlefield['terrain'] = list(terrain)
    state = new_game(frozen, battlefield)
    for i, spot in enumerate(red_spots):
        put(state, f'p1-r{i}', spot)
    for i, spot in enumerate(blue_spots):
        put(state, f'p2-b{i}', spot)
    state, _ = act(state, {'type': 'end_deployment'})
    return state


def to_shooting(state):
    while state['turn']['phase'] != 'shooting':
        state, _ = act(state, {'type': 'next_phase'})
    return state


def options(state, unit='p1-r0'):
    return {o['key']: o for o in action_options(state, unit)}


def test_sabotage_completes_at_the_end_of_the_turn_if_you_hold_the_objective():
    state = to_shooting(game('Priority Assets', 'Priority Assets', [CENTRE], [FAR]))
    sabotage = options(state)['sabotage']
    assert sabotage['reason'] is None and [t['id'] for t in sabotage['targets']] == ['central-1']
    state, event = act(state, {'type': 'start_action', 'unit': 'p1-r0', 'action': 'sabotage', 'target': 'central-1'})
    assert event['action_started']['action'] == 'sabotage'
    assert 'started an action' in refused(state, {'type': 'shoot', 'unit': 'p1-r0', 'target': 'p2-b0'})
    assert options(state)['sabotage']['reason'] == 'It started another action this turn.'
    state, event = to_turn_end(state)
    assert [(a['action'], a['result']) for a in event['actions']] == [('sabotage', 'completed')]
    assert ('Sabotage', 3) in [(v['mission'], v['vp']) for v in event['vp']]


def test_an_action_doesnt_complete_if_the_objective_is_lost():
    state = to_shooting(game('Priority Assets', 'Priority Assets', [(30, 19.5)], [(30, 25)], blue_oc=5))  # Blue holds it
    state, _ = act(state, {'type': 'start_action', 'unit': 'p1-r0', 'action': 'sabotage', 'target': 'central-1'})
    state, event = to_turn_end(state)
    assert [(a['action'], a['result']) for a in event['actions']] == [('sabotage', 'not completed')]


def test_who_can_start_an_action():
    state = game('Priority Assets', 'Priority Assets', [CENTRE], [FAR])
    assert options(state)['sabotage']['reason'] == 'Mission actions start in your Shooting phase.'
    state = to_shooting(state)
    state['units'][0]['activity']['shot'] = True
    assert options(state)['sabotage']['reason'] == 'It has shot this turn.'  # Unless a rule says otherwise
    state['units'][0]['activity'] = {'moved': 'advance'}
    assert options(state)['sabotage']['reason'] == 'It Advanced this turn.'
    state['units'][0]['activity'] = {}
    state['units'][0]['status']['shocked'] = True
    assert options(state)['sabotage']['reason'] == 'It is battle-shocked.'


def test_booby_trap_traps_a_terrain_area_at_once():
    ruin = piece('t1', 'dense', 17, 18)  # x 17-23, y 18-22: not in a deployment zone
    state = to_shooting(game('Disruption', 'Take and Hold', [(20, 20)], [FAR], terrain=[ruin]))  # Red: Death Trap
    assert [t['id'] for t in options(state)['booby-trap']['targets']] == ['t1']
    state, event = act(state, {'type': 'start_action', 'unit': 'p1-r0', 'action': 'booby-trap', 'target': 't1'})
    assert event['action_completed']['terrain'] == 't1'
    assert [(m['player'], m['tag'], m['terrain']) for m in state['mission']['markers']] == [(0, 'trapped', 't1')]
    state, event = to_turn_end(state)
    assert ('Death Trap', 'Each terrain area you trapped this turn', 2) in [(v['mission'], v['text'], v['vp']) for v in event['vp']]


def test_locate_and_deny_places_its_markers_before_the_first_turn():
    areas = [piece(f't{i}', 'light', 4 + 9 * i, 24) for i in range(6)]  # Outside both deployment zones
    state = game('Disruption', 'Priority Assets', [FAR], [(55, 40)], terrain=areas)  # Red: Locate and Deny
    assert state['mission']['choice'] == {'player': 0, 'kind': 'locate'}
    assert 'Locate and Deny' in refused(state, {'type': 'next_phase'})
    assert '5 terrain areas' in refused(state, {'type': 'place_markers', 'terrain': ['t0', 't1']})
    state, event = act(state, {'type': 'place_markers', 'terrain': ['t0', 't1', 't2', 't3', 't4']})
    assert len(state['mission']['markers']) == 5 and state['mission']['choice'] is None


def test_surveil_the_foe_removes_enemy_markers_where_its_units_end_a_move():
    state = game('Reconnaissance', 'Disruption', [(10, 8)], [FAR])  # Red: Surveil the Foe; Blue: Smoke and Mirrors
    state['mission']['markers'] = [{'id': 'm1', 'player': 1, 'tag': 'decoyed', 'objective': 'expansion-1', 'objective_kind': 'expansion',
                                    'objective_owner': None, 'terrain': None, 'x': 11, 'y': 25}]
    state, _ = act(state, {'type': 'next_phase'})  # Movement
    state, _ = act(state, {'type': 'move', 'unit': 'p1-r0', 'mode': 'normal', 'models': [{'id': 'p1-r0-0-1', 'x': 10, 'y': 13}]})
    assert state['mission']['markers']  # Moves aren't locked yet
    state, _ = act(state, {'type': 'move', 'unit': 'p1-r0', 'mode': 'normal', 'models': [{'id': 'p1-r0-0-1', 'x': 10, 'y': 14}]})
    state['units'][0]['models'][0]['position'] = {'x': 10, 'y': 22, 'angle': 0}  # Within range of expansion-1 (10, 25)
    state, event = act(state, {'type': 'next_phase'})  # Moves lock
    assert state['mission']['markers'] == [] and event['markers_removed'][0]['markers'][0]['id'] == 'm1'


def test_consecrate_marks_the_opponents_home_objective_first():
    state = game('Purge the Foe', 'Reconnaissance', [BLUE_HOME], [FAR])  # Red: Consecrate, beside Blue's home objective
    state['mission']['consecration'] = ['p1-r0']  # It destroyed a unit
    state, event = to_turn_end(state)
    assert [(m['tag'], m['objective']) for m in state['mission']['markers']] == [('consecrated', 'home-defender')]
    assert ('Consecrate', 'One or two objectives consecrated', 3) in [(v['mission'], v['text'], v['vp']) for v in event['vp']]


def test_sensor_sweep_removes_the_marker_chosen_when_it_started():
    areas = [piece(f't{i}', 'light', 4 + 9 * i, 24) for i in range(6)]
    state = game('Disruption', 'Priority Assets', [FAR], [CENTRE], terrain=areas)
    state, _ = act(state, {'type': 'place_markers', 'terrain': ['t0', 't1', 't2', 't3', 't4']})
    # Blue (Extract Relic) sweeps in their own turn
    state, _ = to_turn_end(state)
    state = to_shooting(state)
    sweep = options(state, 'p2-b0')['sensor-sweep']
    assert sweep['reason'] is None and len(sweep['markers']) == 5
    state, _ = act(state, {'type': 'start_action', 'unit': 'p2-b0', 'action': 'sensor-sweep', 'target': 'central-1',
                           'marker': sweep['markers'][0]['id']})
    state, event = to_turn_end(state)
    assert len(state['mission']['markers']) == 4
    assert ('Extract Relic', 'A unit of yours made a sensor sweep this turn', 4) in [(v['mission'], v['text'], v['vp']) for v in event['vp']]
