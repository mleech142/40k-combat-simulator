"""
Play mode's missions (src/combat_sim/play/missions.py): force dispositions to primary missions, and each
primary's lines scored at their timings, as the 2026-27 mission deck words them.
"""

from collections import Counter

from combat_sim.play.layouts import LAYOUTS
from combat_sim.play.missions import (DISPOSITIONS, MATRIX, PRIMARIES, Context, primary, primary_for,
                                      score_lines)

RED, BLUE = 0, 1
HOME_RED = {'id': 'home-attacker', 'kind': 'home', 'owner': RED}
HOME_BLUE = {'id': 'home-defender', 'kind': 'home', 'owner': BLUE}
CENTRAL = {'id': 'central-1', 'kind': 'central', 'owner': None}
EAST = {'id': 'expansion-1', 'kind': 'expansion', 'owner': None}
WEST = {'id': 'expansion-2', 'kind': 'expansion', 'owner': None}


def ctx(red=(), blue=(), start=((), ()), **extra):
    return Context(player=RED, held=[list(red), list(blue)], held_at_start=[list(start[0]), list(start[1])], **extra)


def scored(key, when, round_number, context):
    return {entry['text']: vp for entry, vp, _ in score_lines(key, when, round_number, context)}


def kill(player=BLUE, **extra):
    return dict({'unit': 'x', 'player': player, 'by': None, 'by_player': None, 'by_in_range': False,
                 'in_range': False, 'central': False, 'terrain': False}, **extra)


def test_25_primaries_five_for_each_disposition():
    assert len(PRIMARIES) == 25
    assert Counter(d for _, d, _ in PRIMARIES.values()) == {d: 5 for d in DISPOSITIONS}
    assert sorted(m for row in MATRIX.values() for m in row.values()) == sorted(PRIMARIES)
    assert all(PRIMARIES[MATRIX[mine][theirs]][1] == mine for mine in MATRIX for theirs in MATRIX[mine])


def test_the_matrix_agrees_with_every_event_companion_layout():
    for layout in LAYOUTS:
        (a, b), (mission_a, mission_b) = layout['dispositions'], layout['missions']
        assert primary(primary_for(a, b))['name'].lower() == mission_a.lower().replace('’', "'"), layout['id']
        assert primary(primary_for(b, a))['name'].lower() == mission_b.lower().replace('’', "'"), layout['id']
    assert primary_for('Take and Hold', 'Purge the Foe') == 'immovable-object'  # BSData's names work too


def test_battlefield_dominance_scores_each_objective_at_the_end_of_the_command_phase_from_round_2():
    holding = ctx(red=[HOME_RED, CENTRAL, EAST], blue=[WEST])
    assert scored('battlefield-dominance', 'command', 1, holding) == {}
    assert scored('battlefield-dominance', 'turn', 1, holding) == {'Rounds 1-2: hold more objectives than your opponent': 2}
    assert sum(scored('battlefield-dominance', 'command', 2, holding).values()) == 3 * 3 + 2 * 2  # Home held: +2 each other
    # Round 5: the end of your turn instead of the Command phase
    assert scored('battlefield-dominance', 'command', 5, holding) == {}
    assert sum(scored('battlefield-dominance', 'turn', 5, holding).values()) == 13


def test_alternative_lines_score_only_the_best():
    both = ctx(red=[CENTRAL], destroyed=[kill(in_range=True, by_player=RED, by_in_range=True)])
    assert sum(scored('purge-and-secure', 'turn', 1, both).values()) == 3  # Either of the two kill lines, not both


def marker(tag, objective='central-1', player=RED, **extra):
    return dict({'player': player, 'tag': tag, 'objective': objective, 'terrain': None}, **extra)


def test_every_line_scores_now_that_actions_have_arrived():
    assert not any(entry['needs'] for key in PRIMARIES for entry in primary(key)['lines'])


def test_marker_lines_count_your_markers():
    three = ctx(markers=[marker('consecrated'), marker('consecrated', 'expansion-1'), marker('consecrated', 'expansion-2'),
                         marker('consecrated', 'home-attacker', player=BLUE)])
    assert scored('consecrate', 'turn', 3, three) == {'Three or more objectives consecrated': 6}
    home = ctx(markers=[marker('consecrated', 'home-defender', objective_kind='home', objective_owner=BLUE)])
    assert sum(scored('consecrate', 'battle', 5, home).values()) == 5
    decoys = ctx(markers=[marker('decoyed'), marker('decoyed', 'expansion-1')], in_their_territory={'expansion-1'})
    assert sum(scored('smoke-and-mirrors', 'turn', 1, decoys).values()) == 2 * 2 + 1 * 2


def test_completed_actions_score_this_turn():
    done = ctx(completed=[{'player': RED, 'action': 'sabotage', 'unit': 'a', 'their_territory': True},
                          {'player': RED, 'action': 'sabotage', 'unit': 'b', 'their_territory': False},
                          {'player': BLUE, 'action': 'sabotage', 'unit': 'c', 'their_territory': True}])
    assert sum(scored('sabotage', 'turn', 1, done).values()) == 2 * 3 + 1 * 2
    assert sum(scored('triangulation', 'turn', 2, ctx(markers=[marker('triangulated'), marker('triangulated', 'x')])).values()) == 6


def test_kills_this_turn_against_losses_the_turn_before():
    context = ctx(destroyed=[kill(), kill()], destroyed_before=[kill(player=RED)])
    assert scored('meatgrinder', 'turn', 2, context) == {'Destroy an enemy unit this turn': 3,
                                                         'More enemy units destroyed this turn than of yours the turn before': 5}
    assert scored('meatgrinder', 'turn', 1, context) == {'Destroy an enemy unit this turn': 3}  # Round 2 onwards


def test_objectives_taken_this_turn_and_in_the_opponents_territory():
    taken = ctx(red=[CENTRAL, EAST], start=([CENTRAL], []), in_their_territory={'expansion-1'})
    assert scored('determined-acquisition', 'turn', 1, taken) == {'Each objective you took this turn (not your home objective)': 2}
    assert sum(scored('determined-acquisition', 'command', 3, taken).values()) == 2 * 3 + 1 * 3


def test_delaying_action_holds_neither_home_objective():
    assert scored('delaying-action', 'command', 2, ctx(red=[HOME_BLUE])) == {}
    assert sum(scored('delaying-action', 'command', 2, ctx(red=[EAST])).values()) == 4


def test_end_of_battle_lines():
    context = ctx(red=[HOME_BLUE, CENTRAL])
    assert scored('meatgrinder', 'battle', 5, context) == {"End of the battle: hold your opponent's home objective": 5}
    assert "End of the battle: hold your opponent's home objective" not in scored('meatgrinder', 'turn', 5, context)


def test_punishment_scores_condemned_units_destroyed_in_either_turn():
    context = ctx(destroyed=[kill(unit='p2-x')], condemned=['p2-x'])
    assert scored('punishment', 'any_turn', 3, context) == {"A condemned enemy unit was destroyed this turn (either player's turn)": 5}


# --- Fixed secondary missions ---------------------------------------------------------------------------------

def model(character=False, wounds=1, player=BLUE):
    return {'model': 'm', 'unit': 'u', 'player': player, 'character': character, 'wounds': wounds}


def test_the_four_fixed_secondaries():
    from combat_sim.play.missions import SECONDARIES
    assert sorted(SECONDARIES) == ['a-grievous-blow', 'assassination', 'bring-it-down', 'engage-on-all-fronts']


def test_a_grievous_blow_and_bring_it_down_score_up_to_5vp_a_turn():
    big = ctx(destroyed=[kill(strength=20), kill(strength=13), kill(strength=10)])
    assert scored('a-grievous-blow', 'any_turn', 2, big) == {
        'Each enemy unit of 13+ models (starting strength) destroyed this turn, up to 5VP a turn': 5}  # 2 x 4VP, capped
    one = ctx(models_destroyed=[model(wounds=12), model(wounds=9)])
    assert sum(scored('bring-it-down', 'any_turn', 1, one).values()) == 4
    assert scored('bring-it-down', 'turn', 1, one) == {}  # 'End of a turn': every turn, scored as 'any_turn'


def test_assassination_counts_character_models_and_tough_ones_again():
    killed = ctx(models_destroyed=[model(character=True, wounds=5), model(character=True, wounds=3), model(wounds=9),
                                   model(character=True, wounds=6, player=RED)])  # Red's own Character doesn't count
    assert sum(scored('assassination', 'any_turn', 3, killed).values()) == 2 * 3 + 1


def test_engage_on_all_fronts_scores_the_best_presence():
    assert sum(scored('engage-on-all-fronts', 'turn', 1, ctx(presence=4)).values()) == 4
    assert sum(scored('engage-on-all-fronts', 'turn', 1, ctx(presence=3)).values()) == 2
    assert scored('engage-on-all-fronts', 'turn', 1, ctx(presence=2)) == {}


# --- The Tactical deck ------------------------------------------------------------------------------------------

def tac(key):
    return f'tactical:{key}'


def test_the_tactical_deck_has_18_cards():
    from combat_sim.play.missions import TACTICAL
    assert len(TACTICAL) == 18


def test_tactical_values_and_their_limits():
    assert scored(tac('no-prisoners'), 'any_turn', 1, ctx(destroyed=[kill(), kill(), kill()])) == {
        'Each enemy unit destroyed this turn, up to 5VP': 5}
    assert sum(scored(tac('bring-it-down'), 'any_turn', 1, ctx(models_destroyed=[model(wounds=12)])).values()) == 5
    assert sum(scored(tac('engage-on-all-fronts'), 'turn', 1, ctx(presence=3)).values()) == 3


def test_end_of_your_opponents_turn_or_of_round_5():
    held = ctx(red=[HOME_RED])
    assert scored(tac('defend-stronghold'), 'opp_turn', 1, held) == {}  # Round 2 onwards
    assert sum(scored(tac('defend-stronghold'), 'opp_turn', 2, held).values()) == 3 + 2
    assert sum(scored(tac('defend-stronghold'), 'r5_end', 5, held).values()) == 5  # Going second in round 5: your own turn
    assert scored(tac('defend-stronghold'), 'turn', 3, held) == {}
    might = ctx(nml=(2, 1))
    assert sum(scored(tac('display-of-might'), 'turn', 1, might).values()) == 2
    assert sum(scored(tac('display-of-might'), 'opp_turn', 1, might).values()) == 5


def test_forward_position_and_assassination():
    assert scored(tac('forward-position'), 'turn', 2, ctx(red=[EAST, WEST], held_expansions_all=True))
    assert scored(tac('forward-position'), 'turn', 2, ctx(red=[HOME_BLUE]))
    assert not scored(tac('forward-position'), 'turn', 2, ctx(red=[EAST]))
    assert scored(tac('assassination'), 'any_turn', 3, ctx(all_enemy_characters_dead=True))
