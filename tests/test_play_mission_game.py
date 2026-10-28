"""
Missions in a game (src/combat_sim/play/mission_state.py with game.py): primaries from the two armies'
force dispositions, VP scored at each line's timing within the caps, units destroyed this turn,
Punishment's condemned units, VP corrections, and replays.
"""

from combat_sim.play.game import new_game, replay, score
from test_play_game import army, part
from test_play_turns import act, put, refused, next_phase

BASE = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}
# Dawn of War: Red attacks from the bottom; home objectives (30, 10) and (30, 34), central (30, 22),
# expansions (10, 25) and (50, 19)
RED_HOME, BLUE_HOME, CENTRE, WEST, EAST, FAR = (30, 10), (30, 34), (30, 22), (10, 25), (50, 19), (55, 42)


def armies(red, blue, red_units, blue_units):
    """Two armies with force dispositions; units as (name, models, oc)."""
    def build(name, disposition, units):
        frozen = army(name, *[(part(n, models, 1, base=BASE, stats={'oc': oc}),) for n, models, oc in units])
        return dict(frozen, disposition=disposition)
    return [build('Red', red, red_units), build('Blue', blue, blue_units)]


def battle(red, blue, red_spots, blue_spots):
    """A game deployed with one model per unit at each spot, then the battle begun (Red's Command phase)."""
    frozen = armies(red, blue, [(f'R{i}', 1, 2) for i in range(len(red_spots))], [(f'B{i}', 1, 1) for i in range(len(blue_spots))])
    state = new_game(frozen)
    for i, spot in enumerate(red_spots):
        put(state, f'p1-r{i}', spot)
    for i, spot in enumerate(blue_spots):
        put(state, f'p2-b{i}', spot)
    state, _ = act(state, {'type': 'end_deployment'})
    return frozen, state


def to_turn_end(state, events=None):
    """Next phase until the turn changes (Command through Consolidate); the last event."""
    player, round_number = state['turn']['player'], state['turn']['round']
    while (state['turn']['player'], state['turn']['round']) == (player, round_number) and state['turn']['phase'] != 'ended':
        state, event = next_phase(state)
        if events is not None:
            events.append(event)
    return state, event


def vp(state):
    return [p['vp']['primary'] for p in state['players']]


def test_the_primaries_come_from_both_force_dispositions():
    frozen, state = battle('Take and Hold', 'Purge the Foe', [FAR], [FAR])
    assert state['mission']['primaries'] == ['immovable-object', 'unstoppable-force']
    plain = new_game([army('Red', (part('A', 1, 1),)), army('Blue', (part('B', 1, 1),))])
    assert plain['mission'] is None  # No force dispositions: kill points, as before


def test_battlefield_dominance_scores_at_the_end_of_the_turn_and_of_the_command_phase():
    _, state = battle('Take and Hold', 'Take and Hold', [RED_HOME, WEST], [FAR])
    state, event = to_turn_end(state)
    assert vp(state) == [2, 0]  # Round 1: more objectives than Blue
    assert [(v['player'], v['vp'], v['text']) for v in event['vp']] == [(0, 2, 'Rounds 1-2: hold more objectives than your opponent')]
    state, _ = to_turn_end(state)  # Blue's turn: Blue holds nothing
    state, event = act(state, {'type': 'next_phase'})  # Red's round 2 Command phase ends
    assert [v['vp'] for v in event['vp']] == [6, 2]  # 2 objectives x 3VP; +2VP for the other one with the home held
    assert vp(state) == [10, 0] and score(state) == [10, 0]


def test_primary_vp_are_capped_at_15_a_round():
    # Outmanoeuvre, round 1: Blue's home objective (10VP) and 4 objectives other than Red's home (4VP each)
    _, state = battle('Disruption', 'Disruption', [BLUE_HOME, CENTRE, WEST, EAST], [FAR])
    state, event = to_turn_end(state)
    assert vp(state) == [15, 0]
    assert sum(v['asked'] for v in event['vp']) == 26 and sum(v['vp'] for v in event['vp']) == 15


def test_destroyed_units_are_recorded_and_score():
    _, state = battle('Purge the Foe', 'Purge the Foe', [FAR], [CENTRE])  # Meatgrinder for both
    state, _ = act(state, {'type': 'correction', 'player': 0, 'models': {'p2-b0-0-1': {'alive': False}}, 'note': 'Shot'})
    state, _ = act(state, {'type': 'accept', 'player': 1})
    assert state['mission']['destroyed'] == [{'unit': 'p2-b0', 'player': 1, 'by': None, 'by_player': None, 'by_in_range': False,
                                              'in_range': True, 'central': True, 'terrain': False, 'strength': 1}]
    assert state['mission']['models_destroyed'] == [{'model': 'p2-b0-0-1', 'unit': 'p2-b0', 'player': 1, 'character': False,
                                                     'wounds': 1}]
    state, event = to_turn_end(state)
    assert [(v['player'], v['vp']) for v in event['vp']] == [(0, 3)]  # An enemy unit destroyed this turn
    assert state['mission']['destroyed'] == [] and len(state['mission']['destroyed_before']) == 1


def test_punishment_condemns_units_and_scores_when_one_is_destroyed():
    _, state = battle('Purge the Foe', 'Disruption', [FAR], [BLUE_HOME, (50, 40)])  # Red: Punishment
    assert state['mission']['choice'] == {'player': 0, 'kind': 'condemn'}
    assert 'condemn' in refused(state, {'type': 'next_phase'})
    assert 'within range of objectives' in refused(state, {'type': 'condemn', 'units': ['p2-b1']})  # Not on an objective
    state, _ = act(state, {'type': 'condemn', 'units': ['p2-b0']})
    assert state['mission']['condemned']['0'] == ['p2-b0'] and state['mission']['choice'] is None
    state, _ = act(state, {'type': 'correction', 'player': 0, 'models': {'p2-b0-0-1': {'alive': False}}, 'note': 'Shot'})
    state, _ = act(state, {'type': 'accept', 'player': 1})
    state, event = to_turn_end(state)
    assert (0, 5) in [(v['player'], v['vp']) for v in event['vp']]


def test_a_correction_can_add_or_take_away_vp():
    _, state = battle('Take and Hold', 'Take and Hold', [FAR], [FAR])
    state, _ = act(state, {'type': 'correction', 'player': 0, 'vp': {'1': 3}, 'note': 'Forgot to score'})
    state, _ = act(state, {'type': 'accept', 'player': 1})
    assert score(state) == [0, 3]
    assert 'whole number' in refused(state, {'type': 'correction', 'player': 0, 'vp': {'1': 'lots'}})


def test_a_replay_gives_the_same_vp_and_mission():
    frozen = armies('Take and Hold', 'Take and Hold', [('R0', 1, 2), ('R1', 1, 2)], [('B0', 1, 1)])
    state = new_game(frozen)
    events = []
    for unit, spot in (('p1-r0', RED_HOME), ('p1-r1', (10, 5))):
        state, event = act(state, {'type': 'place', 'unit': unit, 'models': [{'id': f'{unit}-0-1', 'x': spot[0], 'y': spot[1]}]})
        events.append(event)
        if unit == 'p1-r0':  # Blue places between Red's units
            state, event = act(state, {'type': 'place', 'unit': 'p2-b0', 'models': [{'id': 'p2-b0-0-1', 'x': 50, 'y': 40}]})
            events.append(event)
    state, event = act(state, {'type': 'end_deployment'})
    events.append(event)
    for _ in range(3):
        state, _ = to_turn_end(state, events)
    again = replay(frozen, events)
    assert vp(again) == vp(state) and vp(state)[0] > 0
    assert again['mission'] == state['mission']


# --- Fixed secondary missions -----------------------------------------------------------------------------------

def setup_game(red_units, blue_units):
    """A game with setup: Take and Hold vs Take and Hold, Red attacking, at the secondaries step."""
    frozen = armies('Take and Hold', 'Take and Hold', red_units, blue_units)
    state = new_game(frozen, setup=True)
    state, _ = act(state, {'type': 'roll_objectives'}, 1)
    state, _ = act(state, {'type': 'roll_off'}, 6, 1)
    state, _ = act(state, {'type': 'choose_role', 'role': 'attacker'})
    return frozen, state


def test_each_player_chooses_two_fixed_secondaries_before_deployment():
    _, state = setup_game([('R0', 1, 1)], [('B0', 1, 1)])
    assert state['turn']['step'] == 'secondaries'
    assert 'Fixed or Tactical' in refused(state, {'type': 'choose_secondaries', 'player': 0, 'mode': 'secret'})
    assert 'different' in refused(state, {'type': 'choose_secondaries', 'player': 0, 'mode': 'fixed',
                                          'cards': ['assassination', 'assassination']})
    state, event = act(state, {'type': 'choose_secondaries', 'player': 1, 'mode': 'fixed',
                               'cards': ['assassination', 'bring-it-down']})
    assert event['secondaries_chosen'] == 1 and 'revealed' not in event and state['turn']['step'] == 'secondaries'
    state, event = act(state, {'type': 'choose_secondaries', 'player': 0, 'mode': 'fixed',
                               'cards': ['a-grievous-blow', 'engage-on-all-fronts']})
    assert event['revealed']['1']['cards'] == ['assassination', 'bring-it-down']
    assert state['turn'] == {'phase': 'deployment', 'round': 0, 'player': 1}  # The defender deploys first


def test_secondaries_score_with_the_primary_and_count_towards_the_total():
    frozen = armies('Take and Hold', 'Take and Hold', [('R0', 1, 2)], [('B0', 1, 1)])
    frozen[1]['units'][0]['parts'][0]['keywords'] = ['Infantry', 'Character']
    frozen[1]['units'][0]['parts'][0]['stats']['wounds'] = 5
    state = new_game(frozen)
    state['mission']['secondaries'] = {'0': {'mode': 'fixed', 'cards': ['assassination', 'bring-it-down']}, '1': None}
    put(state, 'p1-r0', FAR)
    put(state, 'p2-b0', CENTRE)
    state, _ = act(state, {'type': 'end_deployment'})
    state, _ = act(state, {'type': 'correction', 'player': 0, 'models': {'p2-b0-0-1': {'alive': False}}, 'note': 'Shot'})
    state, _ = act(state, {'type': 'accept', 'player': 1})
    state, event = to_turn_end(state)
    lines = [(v['kind'], v['mission'], v['vp']) for v in event['vp']]
    assert ('secondary', 'Assassination', 3) in lines and ('secondary', 'Assassination', 1) in lines  # 5 wounds: +1VP
    vp_red = state['players'][0]['vp']
    assert (vp_red['secondary'], vp_red['cards']) == (4, {'assassination': 4}) and score(state)[0] == vp_red['primary'] + 4


def test_a_fixed_card_scores_at_most_20vp():
    frozen = armies('Take and Hold', 'Take and Hold', [('R0', 1, 2)], [('B0', 1, 1)])
    frozen[1]['units'][0]['parts'][0]['keywords'] = ['Character']
    state = new_game(frozen)
    state['mission']['secondaries'] = {'0': {'mode': 'fixed', 'cards': ['assassination', 'bring-it-down']}, '1': None}
    state['players'][0]['vp'].update(secondary=19, cards={'assassination': 19})
    put(state, 'p1-r0', FAR)
    put(state, 'p2-b0', FAR)
    state, _ = act(state, {'type': 'end_deployment'})
    state, _ = act(state, {'type': 'correction', 'player': 0, 'models': {'p2-b0-0-1': {'alive': False}}, 'note': 'Shot'})
    state, _ = act(state, {'type': 'accept', 'player': 1})
    state, event = to_turn_end(state)
    assert [(v['vp'], v['asked']) for v in event['vp'] if v['kind'] == 'secondary'] == [(1, 3)]
    assert state['players'][0]['vp']['cards'] == {'assassination': 20}
