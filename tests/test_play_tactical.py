"""
Tactical secondary missions in a game (src/combat_sim/play/tactical.py): drawing two cards at the start of
each of your Command phases, achieved cards discarded, holding a card back, the when-drawn rules and
choices, discarding for 1CP and the once-per-battle discard and draw.
"""

import random

from combat_sim.play.dice import Dice
from combat_sim.play.game import apply_event, resolve_action
from test_play_mission_game import CENTRE, FAR, armies
from combat_sim.play.game import new_game
from test_play_turns import put


def act(state, action):
    event = resolve_action(state, action, Dice(random.Random(7)))
    return apply_event(state, event), event


def refused(state, action):
    from combat_sim.play.game import ActionError
    try:
        resolve_action(state, action, Dice(random.Random(7)))
    except ActionError as error:
        return str(error)
    raise AssertionError('not refused')


def game(deck, red_spots=(FAR,), blue_spots=((55, 40),), red_oc=2):
    """Take and Hold vs Take and Hold, Red playing Tactical with its deck in this order, the battle begun."""
    frozen = armies('Take and Hold', 'Take and Hold', [(f'R{i}', 1, red_oc) for i in range(len(red_spots))],
                    [(f'B{i}', 1, 1) for i in range(len(blue_spots))])
    state = new_game(frozen)
    state['mission']['secondaries'] = {'0': {'mode': 'tactical', 'cards': []}, '1': None}
    state['mission']['tactical'] = {'0': {'deck': list(deck), 'active': [], 'discarded': [], 'redraw_used': False,
                                          'beacon': None, 'tempting': None, 'guards': {}}, '1': None}
    for i, spot in enumerate(red_spots):
        put(state, f'p1-r{i}', spot)
    for i, spot in enumerate(blue_spots):
        put(state, f'p2-b{i}', spot)
    state, _ = act(state, {'type': 'end_deployment'})
    return state


def cards(state, player=0):
    return [c['key'] for c in state['mission']['tactical'][str(player)]['active']]


def to_turn_end(state):
    player, round_number = state['turn']['player'], state['turn']['round']
    event = None
    while (state['turn']['player'], state['turn']['round']) == (player, round_number):
        state, event = act(state, {'type': 'next_phase'})
    return state, event


def kill(state, unit='p2-b0'):
    state, _ = act(state, {'type': 'correction', 'player': 0, 'models': {f'{unit}-0-1': {'alive': False}}, 'note': 'Shot'})
    state, _ = act(state, {'type': 'accept', 'player': 1})
    return state


def test_two_cards_are_drawn_at_the_start_of_your_command_phase_and_achieved_ones_discarded():
    state = game(['no-prisoners', 'centre-ground', 'outflank', 'secure-no-mans-land'])
    assert cards(state) == ['no-prisoners', 'centre-ground']
    state = kill(state)
    state, event = to_turn_end(state)
    assert ('No Prisoners', 2) in [(v['mission'], v['vp']) for v in event['vp']]
    assert cards(state) == ['centre-ground'] and state['mission']['tactical']['0']['discarded'] == ['no-prisoners']
    state, _ = to_turn_end(state)  # Blue's turn; then Red draws two more, keeping Centre Ground
    assert cards(state) == ['centre-ground', 'outflank', 'secure-no-mans-land']


def test_a_held_card_is_neither_scored_nor_discarded():
    state = game(['no-prisoners', 'centre-ground'])
    state, _ = act(state, {'type': 'tactical', 'player': 0, 'card': 'no-prisoners', 'op': 'hold'})
    state = kill(state)
    state, event = to_turn_end(state)
    assert not any(v['mission'] == 'No Prisoners' for v in event.get('vp', [])) and 'no-prisoners' in cards(state)


def test_defend_stronghold_drawn_in_round_1_is_shuffled_back_and_another_drawn():
    state = game(['defend-stronghold', 'no-prisoners', 'outflank'])
    assert cards(state) == ['no-prisoners', 'outflank'] and 'defend-stronghold' in state['mission']['tactical']['0']['deck']


def test_a_card_whose_rule_allows_it_can_be_redrawn_while_just_drawn():
    state = game(['forward-position', 'no-prisoners', 'outflank'])
    card = state['mission']['tactical']['0']['active'][0]
    assert card['redraw'] == 'It is the first battle round.'
    assert "can't be redrawn" in refused(state, {'type': 'tactical', 'player': 0, 'card': 'no-prisoners', 'op': 'redraw'})
    state, event = act(state, {'type': 'tactical', 'player': 0, 'card': 'forward-position', 'op': 'redraw'})
    assert event['drew'] == 'Outflank' and cards(state) == ['no-prisoners', 'outflank']


def test_discarding_at_the_end_of_your_turn_gains_1cp_and_the_paid_redraw_is_once_per_battle():
    state = game(['centre-ground', 'outflank', 'no-prisoners', 'assassination'])
    cp = state['players'][0]['cp']
    state, _ = act(state, {'type': 'tactical', 'player': 0, 'card': 'centre-ground', 'op': 'redraw_cp'})
    assert cards(state) == ['outflank', 'no-prisoners'] and state['players'][0]['cp'] == cp - 1
    assert 'once per battle' in refused(state, {'type': 'tactical', 'player': 0, 'card': 'outflank', 'op': 'redraw_cp'})
    state, _ = act(state, {'type': 'tactical', 'player': 0, 'card': 'no-prisoners', 'op': 'discard'})  # Nothing to score
    state, event = to_turn_end(state)
    assert event['discards'] == [{'player': 0, 'cards': ['No Prisoners'], 'cp': 1}] and 'no-prisoners' not in cards(state)


def test_beacon_and_a_tempting_target_ask_for_choices():
    state = game(['beacon', 'a-tempting-target'])
    assert state['mission']['choice'] == {'player': 0, 'kind': 'beacon'}
    assert state['mission']['queue'] == [{'player': 1, 'kind': 'tempting', 'for': 0}]  # The opponent chooses it
    assert 'beacon unit' in refused(state, {'type': 'next_phase'})
    state, _ = act(state, {'type': 'beacon', 'unit': 'p1-r0'})
    assert state['mission']['choice']['kind'] == 'tempting'
    state, _ = act(state, {'type': 'tempting', 'objective': 'central-1'})
    assert state['mission']['tactical']['0']['tempting'] == 'central-1' and state['mission']['choice'] is None


def test_display_of_might_held_for_the_end_of_your_opponents_turn():
    state = game(['display-of-might', 'outflank'], red_spots=((30, 20),), blue_spots=((55, 40),))  # Red in No Man's Land
    state, _ = act(state, {'type': 'tactical', 'player': 0, 'card': 'display-of-might', 'op': 'hold'})
    state, event = to_turn_end(state)
    assert not any(v['mission'] == 'Display of Might' for v in event.get('vp', []))
    state, _ = act(state, {'type': 'tactical', 'player': 0, 'card': 'display-of-might', 'op': 'unhold'})
    state, event = to_turn_end(state)  # The end of Blue's turn
    assert ('Display of Might', 5) in [(v['mission'], v['vp']) for v in event['vp']]
