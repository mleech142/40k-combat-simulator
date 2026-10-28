"""
Scouts and Infiltrators (release 2.7 step b, src/combat_sim/play/game.py; core rules 24.20, 24.31, 24.32). After
deployment and the first-turn roll-off, the players take turns resolving pre-battle abilities (the player going
first starts): a Scouts X" unit wholly within its zone moves up to X", ending more than 8" from enemies, or a Scouts
unit in reserves is set up wholly within its zone. Infiltrators set up anywhere more than 8" from the enemy zone and
enemy units.
"""

from combat_sim.play.game import new_game, unit_by_id
from combat_sim.rules_text import scouts_distance
from test_play_game import army, part
from test_play_terrain import BASE
from test_play_turns import act, at, refused

DONE = {'type': 'next_phase'}


def scout(name='Scout', distance=6, **extra):
    return part(name, 2, 1, base=BASE, scouts=distance, points=50, **extra)


def deployed(red=None, blue=None, red_spots=((10, 5), (11.5, 5)), blue_spots=((10, 39), (11.5, 39))):
    """Dawn of War (Red's zone y 0-12, Blue's y 32-44), both deployed; deployment not yet ended."""
    state = new_game([army('Red', (red or scout('Scout'),)), army('Blue', (blue or part('Line', 2, 1, base=BASE, points=50),))])
    state, _ = act(state, at(state, 'p1-' + (red or scout())['uid'], *red_spots))
    state, _ = act(state, at(state, 'p2-' + (blue or part('Line', 2, 1))['uid'], *blue_spots))
    return state


def move(unit, *spots, models=None):
    ids = models or [f'{unit}-0-1', f'{unit}-0-2']
    return {'type': 'scout', 'unit': unit, 'models': [{'id': m, 'x': x, 'y': y} for m, (x, y) in zip(ids, spots)]}


def test_scouts_is_read_from_its_line():
    assert scouts_distance('Scouts 6"\n\nDeep Strike') == 6
    assert scouts_distance('Scouts 9"') == 9
    assert scouts_distance('Scouts 6" ability: units with it move before the battle') is None
    assert scouts_distance('Infiltrators') is None


def test_ending_deployment_opens_the_pre_battle_step_when_anyone_has_scouts():
    state, _ = act(deployed(), {'type': 'end_deployment'})
    assert state['turn'] == {'phase': 'prebattle', 'round': 0, 'player': 0, 'scouted': [], 'finished': []}
    # Without Scouts the battle begins at once
    plain = deployed(red=part('Scout', 2, 1, base=BASE, points=50))
    assert act(plain, {'type': 'end_deployment'})[0]['turn']['phase'] == 'command'


def test_a_scout_move_of_up_to_x_inches():
    state, _ = act(deployed(), {'type': 'end_deployment'})
    assert 'Scouts 6"' in refused(state, move('p1-scout', (10, 11.5), (11.5, 11.5)))  # 6.5"
    after, event = act(state, move('p1-scout', (10, 11), (11.5, 11)))
    assert event['scouted'] == 'p1-scout' and event['how'] == 'moved'
    assert unit_by_id(after, 'p1-scout')['models'][0]['position']['y'] == 11
    assert after['turn']['phase'] == 'command' and after['turn']['round'] == 1  # Nobody has another: the battle begins


def test_a_scout_move_ends_more_than_8_inches_from_enemies():
    sneak = part('Line', 2, 1, base=BASE, infiltrators=True, points=50)  # Blue infiltrates to y 21 (8.4" from Red's zone)
    state, _ = act(deployed(blue=sneak, red_spots=((10, 11), (11.5, 11)), blue_spots=((10, 21), (11.5, 21))), {'type': 'end_deployment'})
    assert 'more than 8"' in refused(state, move('p1-scout', (10, 13), (11.5, 13)))  # 6.7" from Blue's models
    act(state, move('p1-scout', (15, 11), (16.5, 11)))  # About 10" away


def test_only_units_wholly_within_their_zone_scout():
    state = deployed()
    for m in unit_by_id(state, 'p1-scout')['models']:
        m['position']['y'] = 14  # Outside Red's zone (y 0-12)
    assert act(state, {'type': 'end_deployment'})[0]['turn']['phase'] == 'command'


def test_a_scout_unit_in_reserves_sets_up_in_its_zone():
    state = new_game([army('Red', (scout('Scout'),), (part('Base', 1, 1, base=BASE, points=200),)),
                      army('Blue', (part('Line', 2, 1, base=BASE, points=50),))])
    state, _ = act(state, {'type': 'reserve', 'unit': 'p1-scout'})
    state, _ = act(state, at(state, 'p2-line', (10, 39), (11.5, 39)))
    state, _ = act(state, at(state, 'p1-base', (30, 5)))
    state, _ = act(state, {'type': 'end_deployment'})
    assert state['turn']['phase'] == 'prebattle'
    assert 'wholly within your deployment zone' in refused(state, move('p1-scout', (10, 20), (11.5, 20)))
    after, event = act(state, move('p1-scout', (10, 3), (11.5, 3)))
    assert event['how'] == 'set_up' and not unit_by_id(after, 'p1-scout')['status'].get('reserve')


def test_players_take_turns_and_can_finish_early():
    red = [(scout('Scout'),), (scout('Ranger'),)]
    blue = [(scout('Outrider'),)]
    state = new_game([army('Red', *red), army('Blue', *blue)])
    state, _ = act(state, at(state, 'p1-scout', (10, 5), (11.5, 5)))
    state, _ = act(state, at(state, 'p2-outrider', (10, 39), (11.5, 39)))
    state, _ = act(state, at(state, 'p1-ranger', (30, 5), (31.5, 5)))
    state, _ = act(state, {'type': 'end_deployment'})
    assert state['turn']['player'] == 0  # Red goes first (no setup roll-off in this game)
    state, _ = act(state, move('p1-scout', (10, 8), (11.5, 8)))
    assert state['turn']['player'] == 1  # Blue's go
    assert "Blue's turn" in refused(state, move('p1-ranger', (30, 8), (31.5, 8)))
    state, _ = act(state, DONE)  # Blue uses no more
    assert state['turn']['player'] == 0 and state['turn']['finished'] == [1]
    assert "can't use Scouts" in refused(state, move('p1-scout', (10, 9), (11.5, 9)))  # Each unit once
    state, _ = act(state, move('p1-ranger', (30, 8), (31.5, 8)))
    assert state['turn']['phase'] == 'command'


def test_infiltrators_set_up_more_than_8_inches_from_the_enemy_zone_and_units():
    sneak = part('Sneak', 2, 1, base=BASE, infiltrators=True, points=50)
    state = new_game([army('Red', (sneak,)), army('Blue', (part('Line', 2, 1, base=BASE, points=50),))])
    state, _ = act(state, at(state, 'p1-sneak', (10, 22), (11.5, 22)))  # 9.4" from Blue's zone edge (y 32)
    assert unit_by_id(state, 'p1-sneak')['models'][0]['position']['y'] == 22
    plain = new_game([army('Red', (part('Sneak', 2, 1, base=BASE, points=50),)), army('Blue', (part('Line', 2, 1, base=BASE),))])
    assert 'deployment zone' in refused(plain, at(plain, 'p1-sneak', (10, 22), (11.5, 22)))
    fresh = new_game([army('Red', (sneak,)), army('Blue', (part('Line', 2, 1, base=BASE, points=50),))])
    assert 'enemy deployment zone' in refused(fresh, at(fresh, 'p1-sneak', (10, 23.5), (11.5, 23.5)))  # 7.9" away


def test_infiltrators_keep_away_from_enemy_units():
    sneak = part('Sneak', 2, 1, base=BASE, infiltrators=True, points=50)
    lurker = part('Lurker', 2, 1, base=BASE, infiltrators=True, points=50)
    state = new_game([army('Red', (part('Base', 1, 1, base=BASE, points=50),), (sneak,)), army('Blue', (lurker,))])
    state, _ = act(state, at(state, 'p1-base', (30, 5)))
    state, _ = act(state, at(state, 'p2-lurker', (10, 22), (11.5, 22)))  # 9.4" from Red's zone
    assert 'every enemy unit' in refused(state, at(state, 'p1-sneak', (10, 15), (11.5, 15)))  # 5.7" from Blue's
    act(state, at(state, 'p1-sneak', (40, 15), (41.5, 15)))
