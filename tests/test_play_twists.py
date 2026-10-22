"""
Play mode's Twist cards (missions.TWISTS): agreed on the new-game form, frozen into the game; Scrambled
Communications and Mirrored World change the primaries; Martial Pride, Ruinscape and Night Fighting
change the rules.
"""

import copy

import pytest

from combat_sim.play.battlefields import BUILTIN
from combat_sim.play.game import new_game
from combat_sim.play.mission_state import action_options
from conftest import BSDATA_FIXTURES
from test_play_api import APP_DATA
from test_play_mission_game import CENTRE, FAR, armies
from test_play_missions_api import saved_army
from test_play_terrain import BLOCK, piece
from test_play_turns import act, put, refused


def twisted(twist, red='Take and Hold', blue='Purge the Foe', setup=False, units=((CENTRE,), ((55, 40),)), keywords=None,
            terrain=()):
    frozen = armies(red, blue, [(f'R{i}', 1, 2) for i in range(len(units[0]))], [(f'B{i}', 1, 1) for i in range(len(units[1]))])
    if keywords:
        frozen[0]['units'][0]['parts'][0]['keywords'] = keywords
    battlefield = copy.deepcopy(BUILTIN['dawn-of-war'])
    battlefield['terrain'] = list(terrain)
    state = new_game(frozen, battlefield, setup=setup, twist=twist)
    if not setup:
        for i, spot in enumerate(units[0]):
            put(state, f'p1-r{i}', spot)
        for i, spot in enumerate(units[1]):
            put(state, f'p2-b{i}', spot)
        state, _ = act(state, {'type': 'end_deployment'})
    return state


def test_scrambled_communications_swaps_the_primaries():
    assert twisted(None)['mission']['primaries'] == ['immovable-object', 'unstoppable-force']
    assert twisted({'key': 'scrambled-communications'})['mission']['primaries'] == ['unstoppable-force', 'immovable-object']


def test_mirrored_world_agreed_or_rolled():
    assert twisted({'key': 'mirrored-world', 'primary': 'sabotage'})['mission']['primaries'] == ['sabotage', 'sabotage']
    state = twisted({'key': 'mirrored-world', 'primary': 'roll'}, setup=True)
    assert state['turn']['step'] == 'mirrored'
    state, event = act(state, {'type': 'roll_mirrored'}, 6, 3)  # A 6 is rolled again
    assert event['mirrored'] == {'rolls': [6, 3], 'primary': 'outmanoeuvre'}
    assert state['mission']['primaries'] == ['outmanoeuvre', 'outmanoeuvre'] and state['turn']['step'] == 'objectives'


def test_martial_pride_lets_battleline_units_act_after_advancing_and_shoot_after_acting():
    plain = twisted(None, red='Priority Assets', blue='Priority Assets', keywords=['Infantry', 'Battleline'])
    pride = twisted({'key': 'martial-pride'}, red='Priority Assets', blue='Priority Assets', keywords=['Infantry', 'Battleline'])
    for state in (plain, pride):
        state['turn'].update(phase='shooting')
        state['units'][0]['activity'] = {'moved': 'advance'}
    assert {o['key']: o['reason'] for o in action_options(plain, 'p1-r0')}['sabotage'] == 'It Advanced this turn.'
    assert {o['key']: o['reason'] for o in action_options(pride, 'p1-r0')}['sabotage'] is None
    pride['units'][0]['activity'] = {'action': 'sabotage'}
    assert 'started an action' not in refused(pride, {'type': 'shoot', 'unit': 'p1-r0', 'target': 'p2-b0'})


def test_ruinscape_lets_a_normal_move_cross_tall_dense_terrain():
    ruin = piece('t1', 'dense', *BLOCK, height=5)
    walk = {'type': 'move', 'unit': 'p1-r0', 'mode': 'normal', 'models': [{'id': 'p1-r0-0-1', 'x': 10, 'y': 23}]}
    for twist, crosses in ((None, False), ({'key': 'ruinscape'}, True)):
        state = twisted(twist, units=(((10, 17),), ((55, 40),)), keywords=['Vehicle'], terrain=[ruin])
        state, _ = act(state, {'type': 'next_phase'})  # Movement
        state['units'][0]['parts'][0]['stats']['move'] = '10'
        if crosses:
            act(state, walk)
        else:
            assert 'Dense terrain' in refused(state, walk)


def test_night_fighting_hides_units_beyond_18_inches():
    from combat_sim.play.game import sees, unit_by_id, model_shape
    state = twisted({'key': 'night-fighting'}, units=(((10, 5),), ((10, 30),)))
    red, blue = unit_by_id(state, 'p1-r0'), unit_by_id(state, 'p2-b0')
    shape = lambda u: model_shape(u['parts'][0], u['models'][0]['position'])  # noqa: E731
    assert sees(state, shape(red), shape(blue), blue, blue['models'][0]) == (False, 'night')
    blue['models'][0]['position'] = {'x': 10, 'y': 20, 'angle': 0}
    assert sees(state, shape(red), shape(blue), blue, blue['models'][0])[0]


# --- Through the API -----------------------------------------------------------------------------------------------

@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]


def test_a_game_is_started_with_a_twist(client):
    red, blue = saved_army(client, 'Red', ['d-vanguard']), saved_army(client, 'Blue', ['d-bastion'])
    listed = client.get('/api/play/twists').get_json()
    assert len(listed['twists']) == 6 and [m['name'] for m in listed['mirrored']][0] == 'Battlefield Dominance'
    game = client.post('/api/play/games', json={'armies': [red, blue], 'twist': {'key': 'scrambled-communications'}}).get_json()
    assert game['twist']['name'] == 'Scrambled Communications'
    assert [p['name'] for p in game['mission']['primaries']] == ['Immovable Object', 'Unstoppable Force']
    again = client.get(f'/api/play/games/{game["id"]}').get_json()
    assert again['twist']['key'] == 'scrambled-communications'  # Kept with the game
    plain = saved_army(client, 'Plain', [])
    refused_game = client.post('/api/play/games', json={'armies': [red, plain], 'twist': 'mirrored-world'})
    assert refused_game.status_code == 400 and 'force disposition' in refused_game.get_json()['error']
    night = client.post('/api/play/games', json={'armies': [red, plain], 'twist': 'night-fighting'}).get_json()
    assert night['twist']['key'] == 'night-fighting' and night['mission'] is None
