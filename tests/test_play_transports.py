"""
Transports and Deadly Demise (release 2.7 step c, src/combat_sim/play/game.py; core rules 18, 24.08, 24.14): capacity
read from the datasheet, starting embarked, embarking and the three disembark moves, emergency disembarks when a
transport is destroyed, Firing Deck and Deadly Demise.
"""

from combat_sim.play.game import disembark_options, embark_options, new_game, score, shooting_options, transport_problem, unit_by_id
from combat_sim.rules_text import deadly_demise, firing_deck, parse_transport
from test_play_game import army, gun, part
from test_play_terrain import BASE, game_on
from test_play_turns import act, at, refused

HULL = {'shape': 'round', 'length_mm': 100, 'width_mm': 100}  # 1.97" from centre to edge
CAPACITY = ('Transport: This model has a transport capacity of 10 ADEPTUS ASTARTES INFANTRY models. It cannot transport '
            'JUMP PACK models. Each GRAVIS model takes up the space of 2 models.')


def rhino(wounds=10, **extra):
    return part('Rhino', 1, wounds, base=HULL, points=80, keywords=['Vehicle', 'Transport', 'Dedicated Transport'],
                transport=parse_transport(CAPACITY), **extra)


def squad(name='Squad', models=5, keywords=('Infantry',), **extra):
    return part(name, models, 1, base=BASE, points=90, keywords=list(keywords),
                weapons=[gun('Bolter', attacks='1', range=24, qty=models)], **extra)


def aboard(state, unit_id, transport_id):
    unit = unit_by_id(state, unit_id)
    for model in unit['models']:
        model['position'] = None
    unit['status']['embarked'] = transport_id
    return state


def spots(unit_id, n, x, y, gap=1.5):
    return [{'id': f'{unit_id}-0-{i + 1}', 'x': x + i * gap, 'y': y} for i in range(n)]


# --- Reading datasheets ----------------------------------------------------------------------------------------

def test_transport_capacity_is_read_from_the_datasheet():
    assert parse_transport(CAPACITY) == {'pools': [{'n': 10, 'kinds': ['ADEPTUS ASTARTES INFANTRY']}], 'excluded': ['JUMP PACK'],
                                         'costs': [{'kinds': ['GRAVIS'], 'n': 2}], 'only': []}
    two = parse_transport('This model has a transport capacity of 12 Adeptus Astartes Infantry models and 1 Dreadnought model. '
                          'Each Jump Pack, Wulfen or Terminator model takes up the space of 2 models and each Centurion model '
                          'takes up the space of 3 models.')
    assert two['pools'] == [{'n': 12, 'kinds': ['Adeptus Astartes Infantry']}, {'n': 1, 'kinds': ['Dreadnought']}]
    assert two['costs'] == [{'kinds': ['Jump Pack', 'Wulfen', 'Terminator'], 'n': 2}, {'kinds': ['Centurion'], 'n': 3}]
    only = parse_transport('This model has a transport capacity of 6 models. It can only transport Scout Squad and Sergeant Telion.')
    assert only['only'] == ['Scout Squad', 'Sergeant Telion'] and only['pools'] == [{'n': 6, 'kinds': ['']}]
    either = parse_transport('This model has a transport capacity of 10 Adeptus Astartes Infantry or Adeptus Astartes Mounted models.')
    assert either['pools'][0]['kinds'] == ['Adeptus Astartes Infantry', 'Adeptus Astartes Mounted']
    aside = parse_transport('This model has a transport capacity of 12 ^^**Heretic Astartes Infantry**^^ (excluding '
                            '^^**Obliterator^^** and ^^**Jump Pack^^** models).')
    assert aside['pools'] == [{'n': 12, 'kinds': ['Heretic Astartes Infantry']}] and aside['excluded'] == ['Obliterator', 'Jump Pack']
    assert parse_transport('Smoke') is None
    assert firing_deck('Firing Deck 2\n\nSmoke') == 2 and firing_deck('Smoke') is None
    assert deadly_demise('Deadly Demise D6+2') == 'D6+2' and deadly_demise('Deadly Demise 1') == '1'


def test_capacity_counts_models_by_keyword():
    state = new_game([army('Red', (rhino(),), (squad('Tacticals', 6),), (squad('Heavies', 3, ('Infantry', 'Gravis')),),
                           (squad('Jumpers', 2, ('Infantry', 'Jump Pack')),), (part('Tank', 1, 10, keywords=['Vehicle']),)),
                      army('Blue', (squad(),))])
    t = unit_by_id(state, 'p1-rhino')
    assert transport_problem(state, t, [unit_by_id(state, 'p1-tacticals')]) is None  # Faction words aren't keywords: Infantry is
    assert 'no room' in transport_problem(state, t, [unit_by_id(state, 'p1-tacticals'), unit_by_id(state, 'p1-heavies')])  # 6 + 3 x 2
    assert transport_problem(state, t, [unit_by_id(state, 'p1-heavies')]) is None
    assert "can't transport Jumpers" in transport_problem(state, t, [unit_by_id(state, 'p1-jumpers')])
    assert 'can only transport' in transport_problem(state, t, [unit_by_id(state, 'p1-tank')])


# --- Deployment --------------------------------------------------------------------------------------------------

def deployment(**extra):
    return new_game([army('Red', (rhino(**extra),), (squad(),)), army('Blue', (squad('Line', 2),))])


def test_a_unit_starts_embarked_and_counts_as_deployed():
    state, _ = act(deployment(), at(deployment(), 'p1-rhino', (10, 5)))
    state, _ = act(state, at(state, 'p2-line', (10, 40), (11.5, 40)))
    assert embark_options(state) == {'p1-squad': ['p1-rhino']}
    state, event = act(state, {'type': 'embark', 'unit': 'p1-squad', 'transport': 'p1-rhino'})
    assert event['embarked'] == 'p1-squad' and unit_by_id(state, 'p1-squad')['status']['embarked'] == 'p1-rhino'
    state, _ = act(state, {'type': 'end_deployment'})
    assert state['turn']['phase'] == 'command'


def test_an_empty_dedicated_transport_is_lost_when_deployment_ends():
    state = deployment()
    state, _ = act(state, at(state, 'p1-rhino', (10, 5)))
    state, _ = act(state, at(state, 'p2-line', (10, 40), (11.5, 40)))
    state, _ = act(state, at(state, 'p1-squad', *[(20 + i * 1.5, 5) for i in range(5)]))
    state, event = act(state, {'type': 'end_deployment'})
    assert event['empty_transports'][0]['unit'] == 'p1-rhino' and not unit_by_id(state, 'p1-rhino')['models'][0]['alive']
    assert score(state) == [0, 0]  # Not destroyed by the enemy


def test_reserves_count_the_units_a_transport_carries():
    state = new_game([army('Red', (rhino(),), (squad(),), (part('Base', 1, 1, base=BASE, points=50),)),
                      army('Blue', (squad('Line', 2),))])  # 220 pts: reserves up to 110
    state, _ = act(state, {'type': 'embark', 'unit': 'p1-squad', 'transport': 'p1-rhino'})
    state['turn']['player'] = 0
    assert '170 pts' in refused(state, {'type': 'reserve', 'unit': 'p1-rhino'})


# --- Embarking and disembarking ---------------------------------------------------------------------------------

def movement(transport_moved=None, passenger=True, blue=None, **extra):
    """Red's Movement phase in round 2: the Rhino at (20, 10), the squad embarked in it (or at (20, 14))."""
    state = game_on([], [(rhino(**extra), (20, 10)), (squad(), *[(17 + i * 1.5, 14) for i in range(5)])],
                    [*(blue or [(squad('Line', 2), (20, 40), (21.5, 40))])], phase='movement')
    state['turn']['round'] = 2
    if passenger:
        aboard(state, 'p1-squad', 'p1-rhino')
    if transport_moved:
        unit_by_id(state, 'p1-rhino')['activity'] = {'moved': transport_moved, 'start': {'p1-rhino-0-1': {'x': 20, 'y': 10, 'angle': 0}}}
    return state


def test_embarking_after_a_move_within_3_inches():
    state = movement(passenger=False)
    assert 'Normal move' in refused(state, {'type': 'embark', 'unit': 'p1-squad', 'transport': 'p1-rhino'})
    moved, _ = act(state, {'type': 'move', 'unit': 'p1-squad', 'mode': 'normal', 'models': spots('p1-squad', 5, 17, 13)})
    assert embark_options(moved) == {'p1-squad': ['p1-rhino']}
    after, event = act(moved, {'type': 'embark', 'unit': 'p1-squad', 'transport': 'p1-rhino'})
    assert event['embarked'] == 'p1-squad' and all(m['position'] is None for m in unit_by_id(after, 'p1-squad')['models'])
    assert 'p1-squad' not in disembark_options(after)  # Not in the phase it embarked
    back, _ = act(after, {'type': 'reset_move', 'unit': 'p1-squad'})
    assert not back['units'][1]['status'].get('embarked') and back['units'][1]['models'][0]['position']['y'] == 14
    far, _ = act(state, {'type': 'move', 'unit': 'p1-squad', 'mode': 'normal', 'models': spots('p1-squad', 5, 17, 18)})
    assert 'within 3"' in refused(far, {'type': 'embark', 'unit': 'p1-squad', 'transport': 'p1-rhino'})


def test_a_tactical_disembark_then_moves_as_usual():
    state = movement()
    assert disembark_options(state) == {'p1-squad': {'mode': 'tactical', 'reach': 3, 'transport': 'p1-rhino'}}
    assert 'wholly within 3"' in refused(state, {'type': 'disembark', 'unit': 'p1-squad', 'models': spots('p1-squad', 5, 17, 15)})
    after, event = act(state, {'type': 'disembark', 'unit': 'p1-squad', 'models': spots('p1-squad', 5, 17, 13)})
    assert event['mode'] == 'tactical' and not unit_by_id(after, 'p1-squad')['status'].get('embarked')
    moved, _ = act(after, {'type': 'move', 'unit': 'p1-squad', 'mode': 'normal', 'models': spots('p1-squad', 5, 17, 19)})  # 6" on
    assert unit_by_id(moved, 'p1-squad')['models'][0]['position']['y'] == 19
    assert 'set up this turn' in refused(moved, {'type': 'embark', 'unit': 'p1-squad', 'transport': 'p1-rhino'})


def test_a_rapid_disembark_after_the_transport_moved():
    state = movement('normal')
    after, event = act(state, {'type': 'disembark', 'unit': 'p1-squad', 'models': spots('p1-squad', 5, 17, 13)})
    assert event['mode'] == 'rapid'
    assert 'set up this phase (disembarked)' in refused(after, {'type': 'stationary', 'unit': 'p1-squad'})
    charge = dict(after, turn=dict(after['turn'], phase='charge'))
    assert "can't declare a charge" in refused(charge, {'type': 'charge', 'unit': 'p1-squad', 'targets': ['p2-line']})


def test_no_disembark_after_the_transport_advanced():
    state = movement('advance')
    assert disembark_options(state) == {}
    assert 'Advanced' in refused(state, {'type': 'disembark', 'unit': 'p1-squad', 'models': spots('p1-squad', 5, 17, 13)})


def test_a_combat_disembark_from_an_engaged_transport():
    state = movement(blue=[(squad('Line', 2), (20, 13.5), (21.5, 13.5))])  # Engaged with the Rhino (0.9" away)
    assert disembark_options(state)['p1-squad']['mode'] == 'combat'
    models = [{'id': f'p1-squad-0-{i + 1}', 'x': 16.5, 'y': 7 + i * 1.5} for i in range(5)]  # Beside the Rhino, up to 6" away
    after, event = act(state, {'type': 'disembark', 'unit': 'p1-squad', 'models': models}, 3, 3, 3, 3, 1)  # One hazard fails
    assert event['mode'] == 'combat' and event['hazards'][0]['fails'] == 1
    squad_now = unit_by_id(after, 'p1-squad')
    assert squad_now['status']['shocked'] and sum(m['alive'] for m in squad_now['models']) == 4


# --- A destroyed transport --------------------------------------------------------------------------------------

def shot_rhino(rhino_extra=None, nearby=()):
    """Red's gunner shoots Blue's 1-wound Rhino at (30, 30) with Blue's squad aboard."""
    cannon = part('Gunner', 1, 3, base=BASE, weapons=[gun('Cannon', attacks='1', skill=2, strength=10, range=36)])
    state = game_on([], [(cannon, (30, 10)), *nearby],
                    [(rhino(wounds=1, **(rhino_extra or {})), (30, 30)), (squad(), *[(10 + i * 1.5, 40) for i in range(5)])])
    return aboard(state, 'p2-squad', 'p2-rhino')


def test_passengers_make_an_emergency_disembark():
    state = shot_rhino()
    after, event = act(state, {'type': 'shoot', 'unit': 'p1-gunner', 'target': 'p2-rhino'}, 6, 6, 1, 3, 3, 3, 3, 1)
    assert not unit_by_id(after, 'p2-rhino')['models'][0]['alive']
    emergency = event['emergency'][0]
    assert emergency['unit'] == 'p2-squad' and emergency['hazard']['fails'] == 1 and len(emergency['set_up']) == 4
    squad_now = unit_by_id(after, 'p2-squad')
    assert squad_now['status']['shocked'] and not squad_now['status'].get('embarked')
    rhino_spot = (30, 30)
    for model in (m for m in squad_now['models'] if m['alive']):
        assert ((model['position']['x'] - rhino_spot[0]) ** 2 + (model['position']['y'] - rhino_spot[1]) ** 2) ** 0.5 < 6 + 2


def test_deadly_demise_on_a_6_hits_every_unit_within_6_inches():
    near = (squad('Bystanders', 1), (30, 25))  # Red's unit 3" from the Rhino's hull
    state = shot_rhino({'deadly_demise': '1'}, nearby=[near])
    aboard(state, 'p2-squad', 'p2-rhino')
    state, event = act(state, {'type': 'shoot', 'unit': 'p1-gunner', 'target': 'p2-rhino'}, 6, 6, 1, 4, 4, 4, 4, 4, 6)
    demise = event['demise'][0]
    assert demise['roll'] == 6 and {h['unit'] for h in demise['hits']} >= {'p1-bystanders', 'p2-squad'}
    assert not unit_by_id(state, 'p1-bystanders')['models'][0]['alive']
    _, quiet = act(shot_rhino({'deadly_demise': '1'}, nearby=[near]), {'type': 'shoot', 'unit': 'p1-gunner', 'target': 'p2-rhino'},
                   6, 6, 1, 4, 4, 4, 4, 4, 5)
    assert quiet['demise'][0]['roll'] == 5 and quiet['demise'][0]['hits'] == []


# --- Firing Deck ------------------------------------------------------------------------------------------------

def test_firing_deck_adds_passengers_weapons_to_the_transports_shooting():
    state = movement(firing_deck=2)
    state['turn']['phase'] = 'shooting'
    state['units'][2]['models'][0]['position'] = {'x': 20, 'y': 30, 'angle': 0}  # Blue's line within 24"
    state['units'][2]['models'][1]['position'] = {'x': 21.5, 'y': 30, 'angle': 0}
    options = shooting_options(state, 'p1-rhino')
    deck = [w for w in options['weapons'] if w.get('deck')]
    assert [w['name'] for w in deck] == ['Bolter', 'Bolter']  # Up to 2 of the 5 models carrying it
    picks = [{'part': w['part'], 'weapon': w['weapon'], 'target': 'p2-line'} for w in deck]
    after, event = act(state, {'type': 'shoot', 'unit': 'p1-rhino', 'weapons': picks}, 4, 4, 4, 4, 1, 1)
    assert event['attacks'][0]['name'] == 'Bolter' and unit_by_id(after, 'p1-squad')['activity']['shot']
    assert 'Firing Deck 2' in refused(state, {'type': 'shoot', 'unit': 'p1-rhino', 'weapons': picks + [picks[0]]})  # A third


def test_a_dedicated_transport_scouts_when_every_passenger_has_scouts():
    state = new_game([army('Red', (rhino(),), (squad(scouts=6),)), army('Blue', (squad('Line', 2),))])
    state, _ = act(state, at(state, 'p1-rhino', (10, 5)))
    state, _ = act(state, at(state, 'p2-line', (10, 40), (11.5, 40)))
    state, _ = act(state, {'type': 'embark', 'unit': 'p1-squad', 'transport': 'p1-rhino'})
    state, _ = act(state, {'type': 'end_deployment'})
    assert state['turn']['phase'] == 'prebattle'
    state, event = act(state, {'type': 'scout', 'unit': 'p1-rhino', 'models': [{'id': 'p1-rhino-0-1', 'x': 10, 'y': 11}]})
    assert event['scouted'] == 'p1-rhino' and state['turn']['phase'] == 'command'
