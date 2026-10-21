"""
Play mode's Charge and Fight phases (src/combat_sim/play/game.py), as in the 11th edition core rules:
charge roll first, then targets within the roll; pile in, fights (Fights First, then the rest,
players alternating) and consolidate.
"""

from combat_sim.play.game import new_game, unit_by_id
from test_play_game import army, gun, part
from test_play_turns import act, put, refused

BASE = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}  # 1.26" across; engagement range is 2"


def model(name, models=1, wounds=1, **extra):
    return part(name, models, wounds, base=BASE, **extra)


def battle(red, blue, phase='charge', player=0):
    """Units given as (part, *spots), Red's and Blue's; `player`'s `phase`."""
    state = new_game([army('Red', *[(u[0],) for u in red]), army('Blue', *[(u[0],) for u in blue])])
    for owner, units in (('p1', red), ('p2', blue)):
        for spec in units:
            put(state, f'{owner}-{spec[0]["uid"]}', *spec[1:])
    state['turn'] = {'phase': phase, 'round': 1, 'player': player}
    return state


def charge_move(unit, targets, *spots, state):
    models = [m['id'] for m in unit_by_id(state, unit)['models'] if m['alive']]
    return {'type': 'charge_move', 'unit': unit, 'targets': list(targets),
            'models': [{'id': m, 'x': x, 'y': y} for m, (x, y) in zip(models, spots)]}


def row(x, y, n=3, gap=1.5):
    return [(x + gap * i, y) for i in range(n)]


def positions(state, unit_id):
    return [(m['position']['x'], m['position']['y']) for m in unit_by_id(state, unit_id)['models'] if m['alive']]


SQUAD = (model('Squad', 3), *row(10, 10))  # Red
MOB = (model('Mob', 3), *row(10, 18))  # Blue, 6.74" in front of the Squad


# --- Charges ------------------------------------------------------------------------------------------

def test_a_charge_rolls_2d6_first():
    state, event = act(battle([SQUAD], [MOB]), {'type': 'charge', 'unit': 'p1-squad'}, 3, 5)
    assert [(g['purpose'], g['total']) for g in event['rolls']] == [('charge', 8)]
    assert unit_by_id(state, 'p1-squad')['activity']['charge_roll'] == 8
    assert 'already' in refused(state, {'type': 'charge', 'unit': 'p1-squad'}, 6, 6)


def test_who_can_declare_a_charge():
    far = battle([SQUAD], [(model('Mob', 3), *row(10, 30))])  # 18.7" away
    assert '12"' in refused(far, {'type': 'charge', 'unit': 'p1-squad'}, 6, 6)
    engaged = battle([SQUAD], [(model('Mob', 3), *row(10, 12))])  # 0.74" away
    assert 'engaged' in refused(engaged, {'type': 'charge', 'unit': 'p1-squad'}, 6, 6)
    for moved in ('advance', 'fall_back'):
        state = battle([SQUAD], [MOB])
        unit_by_id(state, 'p1-squad')['activity'] = {'moved': moved}
        assert "can't declare a charge" in refused(state, {'type': 'charge', 'unit': 'p1-squad'}, 6, 6)
    assert 'charge phase' in refused(battle([SQUAD], [MOB], phase='movement'), {'type': 'charge', 'unit': 'p1-squad'})
    assert "Red's turn" in refused(battle([SQUAD], [MOB]), {'type': 'charge', 'unit': 'p2-mob'}, 6, 6)


def test_charge_targets_are_enemy_units_within_the_roll():
    state, _ = act(battle([SQUAD], [MOB]), {'type': 'charge', 'unit': 'p1-squad'}, 1, 3)  # 4": the Mob is 6.74" away
    assert 'within' in refused(state, charge_move('p1-squad', ['p2-mob'], *row(10, 13), state=state))
    assert 'Roll' in refused(battle([SQUAD], [MOB]), charge_move('p1-squad', ['p2-mob'], *row(10, 13), state=battle([SQUAD], [MOB])))


def test_a_charge_move_ends_engaged_with_its_targets():
    state, _ = act(battle([SQUAD], [MOB]), {'type': 'charge', 'unit': 'p1-squad'}, 3, 5)  # 8"
    after, event = act(state, charge_move('p1-squad', ['p2-mob'], *row(10, 15.5), state=state))  # 5.5", ends 1.24" away
    assert event['rolls'] == [] and positions(after, 'p1-squad') == row(10, 15.5)
    activity = unit_by_id(after, 'p1-squad')['activity']
    assert activity['charged'] is True and activity['charge_targets'] == ['p2-mob']
    too_far = [(10, 15.5), (11.5, 15.5), (19, 15.5)]  # The last model 8.1" from where it started
    assert 'at most 8"' in refused(state, charge_move('p1-squad', ['p2-mob'], *too_far, state=state))
    assert 'engaged with' in refused(state, charge_move('p1-squad', ['p2-mob'], *row(10, 13), state=state))  # Short of it
    assert 'closer' in refused(state, charge_move('p1-squad', ['p2-mob'], (10, 15.5), (11.5, 15.5), (13, 9.5), state=state))


def test_a_charge_cant_end_engaged_with_units_it_didnt_target():
    other = (model('Other', 1), (16.5, 18))  # Beside the Mob
    state, _ = act(battle([SQUAD], [MOB, other]), {'type': 'charge', 'unit': 'p1-squad'}, 6, 6)
    spots = [(12, 15.5), (13.5, 15.5), (15, 15.5)]  # Engaged with both
    assert 'Other' in refused(state, charge_move('p1-squad', ['p2-mob'], *spots, state=state))
    act(state, charge_move('p1-squad', ['p2-mob', 'p2-other'], *spots, state=state))
    # Every target must end engaged
    assert 'every charge target' in refused(state, charge_move('p1-squad', ['p2-mob', 'p2-other'], *row(8, 15.5), state=state))


def test_a_charge_can_be_reset_until_the_phase_ends_but_the_roll_stands():
    state, _ = act(battle([SQUAD], [MOB]), {'type': 'charge', 'unit': 'p1-squad'}, 3, 5)
    state, _ = act(state, charge_move('p1-squad', ['p2-mob'], *row(10, 15.5), state=state))
    state, _ = act(state, {'type': 'reset_move', 'unit': 'p1-squad'})
    activity = unit_by_id(state, 'p1-squad')['activity']
    assert positions(state, 'p1-squad') == row(10, 10) and activity['charge_roll'] == 8 and 'charged' not in activity
    state, _ = act(state, charge_move('p1-squad', ['p2-mob'], *row(11, 15.5), state=state))
    state, _ = act(state, {'type': 'next_phase'})
    assert state['turn']['phase'] == 'fight'
    assert "hasn't moved in this step" in refused(state, {'type': 'reset_move', 'unit': 'p1-squad'})  # The charge stands
    assert positions(state, 'p1-squad') == row(11, 15.5)


# --- The Fight phase: pile in, fight, consolidate ------------------------------------------------------

SWORD = gun('Sword', attacks='2', skill=3, strength=4, kind='melee', range=None)
BOLTER_RANGED = gun('Bolter', attacks='1', qty=3)


def fighters(weapons=None):
    """Red's Squad (charged this turn) engaged with Blue's Mob; Red's Guard engaged with Blue's Brute."""
    squad = (model('Squad', 3, weapons=weapons or [dict(SWORD, qty=3)]), *row(10, 15.5))
    guard = (model('Guard', 1, weapons=[SWORD]), (40, 15.5))
    mob = (model('Mob', 3, weapons=[dict(SWORD, qty=3)]), *row(10, 18))
    brute = (model('Brute', 1, weapons=[SWORD]), (40, 18))
    state = battle([squad, guard], [mob, brute], phase='charge')
    unit_by_id(state, 'p1-squad')['activity'] = {'charge_roll': 8, 'charged': True, 'charge_targets': ['p2-mob']}
    return state


def to_step(state, step):
    """Through the Fight phase's steps up to `step` ('pile_in', 'fight', 'consolidate')."""
    for _ in range(10):
        if (state['turn']['phase'], state['turn'].get('step')) == ('fight', step):
            return state
        state, _ = act(state, {'type': 'next_phase'})
    raise AssertionError(f'never reached the {step} step')


def fight(unit, target=None, weapons=None):
    action = {'type': 'fight', 'unit': unit}
    if target:
        action['target'] = target
    if weapons is not None:
        action['weapons'] = weapons
    return action


MISS = (1, 1)  # Two attacks that miss


def test_the_fight_phase_runs_pile_in_then_fights_then_consolidate():
    state, _ = act(fighters(), {'type': 'next_phase'})
    assert state['turn']['phase'] == 'fight' and state['turn']['step'] == 'pile_in'
    state, _ = act(state, {'type': 'next_phase'})
    assert state['turn']['step'] == 'fight'
    assert 'can still fight' in refused(state, {'type': 'next_phase'})
    for unit in ('p1-squad', 'p2-mob', 'p1-guard', 'p2-brute'):
        state, _ = act(state, fight(unit), *MISS * 3)
    state, _ = act(state, {'type': 'next_phase'})
    assert state['turn']['step'] == 'consolidate'
    state, _ = act(state, {'type': 'next_phase'})
    assert state['turn'] == {'phase': 'command', 'round': 1, 'player': 1, 'seq': 1}


def test_fights_first_units_fight_first_then_players_alternate():
    state = to_step(fighters(), 'fight')
    assert state['turn']['fight'] == {'stage': 'fights_first', 'picker': 0}
    assert 'Fights First' in refused(state, fight('p1-guard'), *MISS)  # Only charged units yet
    assert "Red's" in refused(state, fight('p2-mob'), *MISS)
    state, _ = act(state, fight('p1-squad'), *MISS * 3)
    # No Fights First units left: Blue, whose pick it would have been, starts the remaining combats
    assert state['turn']['fight'] == {'stage': 'remaining', 'picker': 1}
    assert "Blue's" in refused(state, fight('p1-guard'), *MISS)
    state, _ = act(state, fight('p2-brute'), *MISS)
    state, _ = act(state, fight('p1-guard'), *MISS)
    assert 'already fought' in refused(state, fight('p1-squad'), *MISS * 3)
    state, _ = act(state, fight('p2-mob'), *MISS * 3)
    assert state['turn']['fight'] == {'stage': 'done', 'picker': None}


def test_only_models_engaged_with_the_target_attack():
    state = fighters()
    put(state, 'p1-squad', (10, 15.5), (11.5, 15.5), (13, 13))  # The last model 4.74" from the Mob
    state = to_step(state, 'fight')
    _, event = act(state, fight('p1-squad', 'p2-mob'), *MISS * 2)
    assert (event['attacks'][0]['models'], event['attacks'][0]['attacks']) == (2, 4)


def test_each_model_uses_one_melee_weapon_plus_its_extra_attacks_weapons():
    knife = gun('Knife', attacks='1', kind='melee', range=None, abilities=['Extra Attacks'], qty=3)
    axe = gun('Axe', attacks='1', kind='melee', range=None, qty=3)
    state = to_step(fighters(weapons=[dict(SWORD, qty=3), axe, knife]), 'fight')
    _, event = act(state, fight('p1-squad', 'p2-mob', [{'part': 0, 'weapon': 1}]), *(1,) * 6)
    assert [a['name'] for a in event['attacks']] == ['Axe', 'Knife']
    assert 'one melee weapon' in refused(state, fight('p1-squad', 'p2-mob', [{'part': 0, 'weapon': 0}, {'part': 0, 'weapon': 1}]))


def test_lance_wounds_more_easily_on_the_charge():
    lance = dict(gun('Lance', attacks='1', strength=4, kind='melee', range=None, abilities=['Lance']), qty=3)
    state = to_step(fighters(weapons=[lance]), 'fight')
    _, event = act(state, fight('p1-squad', 'p2-mob'), 6, 6, 6, 1, 1, 1)
    assert event['rolls'][1]['purpose'] == 'wound' and event['rolls'][1]['need'] == 3  # S4 v T4: 4+, Lance: 3+


def test_fighting_kills_models():
    state = to_step(fighters(), 'fight')
    after, event = act(state, fight('p1-squad', 'p2-mob'), 6, 6, 1, 1, 1, 1, 6, 1, 1)  # 2 hits, 1 wound, failed save
    assert event['attacks'][0]['killed'] and sum(m['alive'] for m in unit_by_id(after, 'p2-mob')['models']) == 2


def test_pile_in_moves_up_to_3_inches_closer_to_the_enemy():
    state = fighters()
    put(state, 'p1-squad', (10, 15.5), (11.5, 15.5), (13, 13.5))  # The last model 3.24" from the Mob
    state = to_step(state, 'pile_in')
    pile = lambda *spots: {'type': 'pile_in', 'unit': 'p1-squad',  # noqa: E731
                           'models': [{'id': f'p1-squad-0-{i + 1}', 'x': x, 'y': y} for i, (x, y) in enumerate(spots)]}
    state2, event = act(state, pile((10, 15.5), (11.5, 15.5), (13, 15.5)))
    assert event['rolls'] == [] and positions(state2, 'p1-squad')[2] == (13, 15.5)
    assert 'at most 3"' in refused(state, pile((10, 15.5), (11.5, 15.5), (13, 17)))
    assert 'closer' in refused(state, pile((10, 15.5), (11.5, 15.5), (14.5, 13)))
    assert 'pile in' in refused(state, {'type': 'pile_in', 'unit': 'p2-brute', 'models': [{'id': 'p2-brute-0-1', 'x': 40, 'y': 30}]})
    # Blue piles in too, in the same step; then moves lock
    act(state, {'type': 'pile_in', 'unit': 'p2-brute', 'models': [{'id': 'p2-brute-0-1', 'x': 40, 'y': 17.5}]})
    state2, _ = act(state2, {'type': 'next_phase'})
    assert 'pile in' in refused(state2, pile((10, 15.5), (11.5, 15.5), (13, 15.2)))


def test_units_that_fought_consolidate_up_to_3_inches():
    state = to_step(fighters(), 'fight')
    assert 'consolidate' in refused(state, {'type': 'consolidate', 'unit': 'p1-guard',
                                            'models': [{'id': 'p1-guard-0-1', 'x': 40, 'y': 16}]})
    state, _ = act(state, fight('p1-squad'), *MISS * 3)
    state, _ = act(state, fight('p2-brute'), *MISS)
    state, _ = act(state, fight('p1-guard'), *MISS)
    state, _ = act(state, fight('p2-mob'), *MISS * 3)
    state, _ = act(state, {'type': 'next_phase'})
    assert state['turn']['step'] == 'consolidate'
    move_guard = lambda y: {'type': 'consolidate', 'unit': 'p1-guard',  # noqa: E731
                            'models': [{'id': 'p1-guard-0-1', 'x': 40, 'y': y}]}
    after, event = act(state, move_guard(16))
    assert positions(after, 'p1-guard') == [(40, 16)] and event['rolls'] == []
    assert 'closer' in refused(state, move_guard(14))


# --- What the page offers ---------------------------------------------------------------------------------

def test_charge_options_say_whether_a_unit_can_charge_and_what_it_can_reach():
    from combat_sim.play.game import charge_options
    other = (model('Other', 1), (40, 30))  # 22" away
    state = battle([SQUAD], [MOB, other])
    assert charge_options(state, 'p1-squad') == {'reason': None, 'roll': None, 'targets': {}}
    state, _ = act(state, {'type': 'charge', 'unit': 'p1-squad'}, 3, 5)
    options = charge_options(state, 'p1-squad')
    assert options['roll'] == 8 and options['targets'] == {'p2-mob': {'ok': True, 'distance': 6.74}}
    unit_by_id(state, 'p1-squad')['activity'] = {'moved': 'advance'}
    assert 'Advanced' in charge_options(state, 'p1-squad')['reason']


def test_fight_options_list_each_parts_melee_weapons_and_the_enemies_it_can_hit():
    from combat_sim.play.game import fight_options
    knife = gun('Knife', attacks='1', kind='melee', range=None, abilities=['Extra Attacks'], qty=3)
    state = to_step(fighters(weapons=[dict(SWORD, qty=3), knife, BOLTER_RANGED]), 'fight')
    options = fight_options(state, 'p1-squad')
    assert options['reason'] is None and options['targets'] == ['p2-mob']
    assert options['parts'] == [{'part': 0, 'name': 'Squad', 'choice': 0,
                                 'weapons': [{'weapon': 0, 'name': 'Sword', 'extra': False},
                                             {'weapon': 1, 'name': 'Knife', 'extra': True}]}]
    assert 'pick' in fight_options(state, 'p2-mob')['reason']


def test_a_part_with_no_model_engaged_just_doesnt_attack():
    # The Squad's leader stands back, out of engagement: picking its weapon doesn't stop the Squad fighting
    squad = (model('Squad', 3, weapons=[dict(SWORD, qty=3)], role='bodyguard'), *row(10, 15.5))
    state = battle([squad], [(model('Mob', 3, weapons=[dict(SWORD, qty=3)]), *row(10, 18))], phase='charge')
    unit = unit_by_id(state, 'p1-squad')
    leader = model('Boss', 1, weapons=[gun('Fist', attacks='1', kind='melee', range=None)], role='leader')
    unit['parts'].append(leader)
    unit['models'].append({'id': 'p1-squad-1-1', 'part': 1, 'wounds_left': 1, 'alive': True, 'weapons': [0],
                           'position': {'x': 11.5, 'y': 11, 'angle': 0}})
    unit['activity'] = {'charge_roll': 8, 'charged': True, 'charge_targets': ['p2-mob']}
    state = to_step(state, 'fight')
    _, event = act(state, fight('p1-squad', 'p2-mob', [{'part': 0, 'weapon': 0, 'target': 'p2-mob'},
                                                        {'part': 1, 'weapon': 0, 'target': 'p2-mob'}]), *MISS * 3)
    assert [a['name'] for a in event['attacks']] == ['Sword']
