"""
The board view (play/view.py, architecture review candidate 8): what the Play page draws a game from, worked out
next to the engine so the page only reads it. Which stratagems and rules can be used now and on which units, and
each player's VP against the caps, used to be worked out by the page itself.
"""

from combat_sim.play import view
from combat_sim.play.game import stratagem_targets, usable_now
from test_play_loop import AIM, OATH, TRICK, shooting_game
from test_play_turns import act

FIGHT = {'key': 'stratagem:4', 'kind': 'stratagem', 'name': 'Strike', 'cp': 1, 'phase': 'Fighting', 'modifier': ''}
OVERWATCH = {'key': 'stratagem:9', 'kind': 'stratagem', 'name': 'Fire Overwatch', 'cp': 1, 'phase': None, 'modifier': ''}


def names(entries):
    return [e['name'] for e in entries]


def test_only_this_phases_stratagems_and_rules_and_those_without_a_phase_are_offered():
    state = shooting_game(cp=1)
    state['players'][0]['stratagems'] = [AIM, TRICK, OATH, FIGHT]
    red, blue = usable_now(state)
    assert names(red) == ['Aim', 'Trick', 'Oath'] and blue == []
    state['turn']['phase'] = 'fight'
    assert names(usable_now(state)[0]) == ['Trick', 'Oath', 'Strike']
    state['turn']['phase'] = 'deployment'
    assert usable_now(state) == [[], []]  # Outside the battle's phases


def test_each_says_whether_the_game_would_take_it_now_and_why_not():
    state = shooting_game(cp=1)
    state['players'][0]['stratagems'].append(OVERWATCH)
    offered = {e['name']: e for e in usable_now(state)[0]}
    assert offered['Aim']['ready'] and 'why' not in offered['Aim']
    assert not offered['Trick']['ready'] and '2 CP' in offered['Trick']['why']  # Red has 1
    assert offered['Oath']['ready']  # A rule is free
    assert not offered['Fire Overwatch']['ready'] and 'offers it' in offered['Fire Overwatch']['why']
    state, _ = act(state, {'type': 'stratagem', 'player': 0, 'key': 'stratagem:1', 'unit': 'p1-gunner'})
    state['players'][0]['cp'] = 5
    offered = {e['name']: e for e in usable_now(state)[0]}
    assert not offered['Aim']['ready'] and 'once per phase' in offered['Aim']['why']


def test_a_unit_targeted_by_a_stratagem_this_phase_or_battle_shocked_cant_be_again():
    state = shooting_game(cp=3)
    assert 'p1-gunner' in stratagem_targets(state)
    state, _ = act(state, {'type': 'stratagem', 'player': 0, 'key': 'stratagem:1', 'unit': 'p1-gunner'})
    assert 'p1-gunner' not in stratagem_targets(state)
    squad = next(u for u in state['units'] if u['id'] == 'p2-squad')
    squad.setdefault('status', {})['shocked'] = True
    assert 'p2-squad' not in stratagem_targets(state)


def test_the_board_view_carries_them_for_the_page():
    state = shooting_game()
    board = view.board(state, [{'stratagems': [], 'rules': []}, {'stratagems': [], 'rules': []}])
    assert names(board['usable'][0]) == ['Aim', 'Trick', 'Oath'] and 'p1-gunner' in board['stratagem_targets']
    assert {'board', 'zones', 'outlines', 'score', 'mission', 'texts'} <= set(board)


def test_vp_against_the_caps():
    vp = {'primary': 30, 'rounds': {'2': 15}, 'adjust': 2, 'secondary': 12, 'secondary_rounds': {'2': 4}}
    standing = view.standing(vp, 2)
    assert standing['primary'] == {'total': 32, 'primary': 30, 'round': 15, 'round_cap': 15, 'game_cap': 45, 'round_full': True}
    assert standing['secondary']['total'] == 12 and standing['secondary']['round'] == 4
    assert not standing['secondary']['round_full'] and standing['secondary']['card_cap']
    assert view.standing(vp, 3)['primary']['round'] == 0
    assert view.standing({'primary': 5, 'rounds': {1: 5}}, 1)['primary']['round'] == 5  # Before the state is JSON
