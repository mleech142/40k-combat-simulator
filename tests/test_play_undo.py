"""
Undo against someone on another PC (testing list item 11): a Correction asking to take back an action, dice
included, which puts the game back as it was before that action once the other player accepts
(game._correction with 'undo', game._answer with `rewind`; the routes are tested in test_play_online.py).
"""

from combat_sim.play.dice import Dice
from test_play_dice import FixedRolls
from combat_sim.play.game import apply_event, resolve_action, unit_by_id
from test_play_terrain import SHOOT, game_on, gunner, target
from test_play_turns import act, refused


def wounds(state):
    return unit_by_id(state, 'p2-target')['models'][0]['wounds_left']


def shot_at():
    """Red's gunner wounds Blue's 3-wound target once (hit 6, wound 6, save 1)."""
    before = game_on([], [gunner(10, 10)], [target(10, 20)])
    after, event = act(before, SHOOT, 6, 6, 1)
    assert event['rolls'] and wounds(after) == 2
    return before, after


def ask(state):
    return act(state, {'type': 'correction', 'player': 0, 'undo': {'event': 0, 'note': 'Undo: take back the last action (shooting)'}})


def test_an_accepted_undo_takes_back_an_attack_and_its_dice():
    before, after = shot_at()
    asked, _ = ask(after)
    assert asked['pending']['undo'] == {'event': 0} and 'waiting' in refused(asked, SHOOT)
    event = resolve_action(asked, {'type': 'accept', 'player': 1}, Dice(FixedRolls()), rewind=before)
    taken_back = apply_event(asked, event)
    assert wounds(taken_back) == 3 and taken_back['pending'] is None
    assert 'shot_seq' not in (unit_by_id(taken_back, 'p1-gunner').get('status') or {})  # It can shoot again
    assert event['undone'] == 0


def test_a_declined_undo_keeps_the_attack():
    _, after = shot_at()
    asked, _ = ask(after)
    declined, _ = act(asked, {'type': 'decline', 'player': 1})
    assert wounds(declined) == 2 and declined['pending'] is None


def test_accepting_needs_the_game_before_the_action():
    _, after = shot_at()
    asked, _ = ask(after)
    assert 'needed' in refused(asked, {'type': 'accept', 'player': 1})


def test_an_undo_names_its_action():
    _, after = shot_at()
    assert 'which action' in refused(after, {'type': 'correction', 'player': 0, 'undo': {'event': -1}})
