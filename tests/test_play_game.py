"""
Play mode's game state (src/combat_sim/play): armies frozen into a game as plain data, the game
created from them, shooting resolved into events, and replaying a game from its events.
"""

import json

import pytest

from conftest import BSDATA_FIXTURES
from test_play_dice import FixedRolls
from combat_sim.play.dice import Dice
from combat_sim.play.game import apply_event, new_game, replay, resolve_action
from combat_sim.play.snapshot import freeze_army

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}


@pytest.fixture
def client(make_client):
    return make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]


def ids(client):
    factions = {f['name']: f['id'] for f in client.get('/api/data/factions').get_json()}
    units = {u['name']: u['id'] for u in client.get(f'/api/units?faction_id={factions["Starfarers"]}').get_json()}
    return factions, units


def patrol(client):
    """Void Warriors led by a Star Captain (the Warlord), and 4 Gun Crew with Targeting Sights."""
    factions, units = ids(client)
    return {'name': 'Patrol', 'faction_id': factions['Starfarers'], 'points_limit': 2000, 'detachment_key': 'd-vanguard',
            'warlord': 'c', 'units': [
                {'uid': 'w', 'unit_id': units['Void Warriors']},
                {'uid': 'c', 'unit_id': units['Star Captain'], 'attached_to': 'w'},
                {'uid': 'g', 'unit_id': units['Gun Crew'], 'models': 4, 'enhancement': 'enh-sights'}]}


def frozen(client, army):
    with client.application.app_context():
        return freeze_army(army)


# --- Frozen armies ------------------------------------------------------------------

def test_frozen_army_keeps_each_units_stats_base_and_weapons(client):
    snapshot = frozen(client, patrol(client))
    assert snapshot['name'] == 'Patrol' and snapshot['faction'] == 'Starfarers'
    assert json.loads(json.dumps(snapshot)) == snapshot  # Plain data, ready for a game file
    warriors, crew = snapshot['units']
    bodyguard = warriors['parts'][0]
    assert (bodyguard['name'], bodyguard['role'], bodyguard['models']) == ('Void Warriors', 'bodyguard', 5)
    assert bodyguard['stats'] == {'move': '6', 'toughness': 4, 'save': 3, 'wounds': 2, 'invulnerable_save': None,
                                  'feel_no_pain': None, 'oc': 2, 'leadership': 6}
    assert bodyguard['base'] == {'shape': 'round', 'length_mm': 32, 'width_mm': 32, 'text': '32mm'}
    assert 'Infantry' in bodyguard['keywords'] and 'Stealth' in bodyguard['keywords']
    gun = next(w for w in crew['parts'][0]['weapons'] if w['name'] == 'Crew gun')
    assert {k: gun[k] for k in ('type', 'attacks', 'skill', 'strength', 'ap', 'damage', 'qty')} == {
        'type': 'ranged', 'attacks': '2', 'skill': 4, 'strength': 8, 'ap': 2, 'damage': '3', 'qty': 4}


def test_attached_leader_is_part_of_its_unit_with_its_own_profile(client):
    snapshot = frozen(client, patrol(client))
    leader = snapshot['units'][0]['parts'][1]
    assert (leader['name'], leader['role'], leader['models'], leader['warlord']) == ('Star Captain', 'leader', 1, True)
    assert (leader['stats']['wounds'], leader['stats']['invulnerable_save'], leader['stats']['feel_no_pain']) == (5, 4, 5)
    assert [w['name'] for w in leader['weapons']] == ['Master blade']
    _, units = ids(client)
    assert leader['leader_buffs'] == client.get(f'/api/units/{units["Star Captain"]}').get_json()['leader_buffs'] != []
    assert len(snapshot['units']) == 2  # The leader isn't a unit of its own


def test_enhancement_travels_with_its_bearer(client):
    crew = frozen(client, patrol(client))['units'][1]['parts'][0]
    assert crew['enhancement']['name'] == 'Targeting Sights' and crew['enhancement']['buffs'] != []
    assert crew['role'] == 'unit'


# --- A new game ---------------------------------------------------------------------
# Hand-written snapshots (the shape freeze_army gives), so the game itself needs no database.

def part(name, models, wounds, role='unit', save=3, toughness=4, weapons=(), **extra):
    stats = {'move': '6', 'toughness': toughness, 'save': save, 'wounds': wounds, 'invulnerable_save': None,
             'feel_no_pain': None, 'oc': 1, 'leadership': 6}
    stats.update(extra.pop('stats', {}))
    return dict({'uid': name.lower(), 'unit_id': 1, 'bsdata_id': None, 'name': name, 'role': role, 'models': models,
                 'stats': stats, 'keywords': ['Infantry'], 'base': None, 'weapons': list(weapons), 'leader_buffs': [],
                 'enhancement': None, 'warlord': False}, **extra)


def gun(name='Bolter', attacks='2', skill=3, strength=4, ap=0, damage='1', qty=1, abilities=(), kind='ranged'):
    return {'name': name, 'type': kind, 'attacks': attacks, 'skill': skill, 'strength': strength, 'ap': ap,
            'damage': damage, 'range': 24, 'abilities': list(abilities), 'qty': qty, 'default_use': True}


def army(name, *units):
    return {'name': name, 'faction': 'Testers', 'units': [{'uid': u[0]['uid'], 'name': u[0]['name'], 'parts': list(u)}
                                                         for u in units]}


def test_new_game_has_every_model_at_full_wounds():
    squad = (part('Squad', 3, 2, role='bodyguard'), part('Hero', 1, 5, role='leader'))
    state = new_game([army('Red', squad), army('Blue', (part('Tank', 1, 12),))])
    assert [p['name'] for p in state['players']] == ['Red', 'Blue']
    red, blue = state['units']
    assert (red['player'], blue['player']) == (0, 1)
    assert [(m['part'], m['wounds_left']) for m in red['models']] == [(0, 2), (0, 2), (0, 2), (1, 5)]
    assert len({m['id'] for u in state['units'] for m in u['models']}) == 5  # Every model has its own ID
    assert json.loads(json.dumps(state)) == state  # Plain data all through


# --- Shooting ---------------------------------------------------------------------------

def wounds(state, unit_id):
    unit = next(u for u in state['units'] if u['id'] == unit_id)
    return [m['wounds_left'] if m['alive'] else 'dead' for m in unit['models']]


def shoot(state, faces, unit='p2-gunner', target='p1-squad', weapons=None):
    action = {'type': 'shoot', 'unit': unit, 'target': target}
    if weapons is not None:
        action['weapons'] = weapons
    return resolve_action(state, action, Dice(FixedRolls(*faces)))


def duel(squad=None, gunner=None):
    """Red: 3 models W2 Sv3+ led by a Hero (W5, Sv2+). Blue: one gunner with a 4-shot bolter."""
    squad = squad or (part('Squad', 3, 2, role='bodyguard'), part('Hero', 1, 5, role='leader', save=2))
    gunner = gunner or (part('Gunner', 1, 3, weapons=[gun(attacks='4')]),)
    return new_game([army('Red', squad), army('Blue', gunner)])


def test_shooting_wounds_an_already_damaged_model_first():
    # Hits on 3+: 3, 3, 3, 1 -> 3 hits. Wounds on 4+: 4, 4, 4. Saves on 3+, one at a time: 1 (fails), 1 (fails), 6
    state = duel()
    event = shoot(state, [3, 3, 3, 1, 4, 4, 4, 1, 1, 6])
    assert wounds(apply_event(state, event), 'p1-squad') == ['dead', 2, 2, 5]


def test_leader_takes_wounds_only_when_the_bodyguard_is_gone():
    # One W1 bodyguard model; 4 hits, 4 wounds, every save fails (1s): it dies, then the Hero (Sv2+) takes 3
    squad = (part('Squad', 1, 1, role='bodyguard'), part('Hero', 1, 5, role='leader', save=2))
    state = duel(squad=squad)
    event = shoot(state, [3, 3, 3, 3, 4, 4, 4, 4, 1, 1, 1, 1], target='p1-squad')
    assert wounds(apply_event(state, event), 'p1-squad') == ['dead', 2]


def test_resolving_an_action_leaves_the_state_unchanged():
    state = duel()
    before = json.dumps(state)
    shoot(state, [3, 3, 3, 1, 4, 4, 4, 1, 1, 6])
    assert json.dumps(state) == before


def test_leader_effects_apply_to_the_shooter_and_to_the_target():
    aim = [{'applies_to': 'Attacker', 'phase': 'Shooting', 'modifier': 'Hit Modifier +1'}]
    dodge = [{'applies_to': 'Target', 'phase': 'Both', 'modifier': 'Hit Modifier -1'}]
    gunner = (part('Gunner', 1, 3, role='bodyguard', weapons=[gun(attacks='1', skill=4)]),
              part('Spotter', 1, 3, role='leader', leader_buffs=aim))
    plain = (part('Squad', 3, 2, role='bodyguard'), part('Hero', 1, 5, role='leader'))
    dodging = (part('Squad', 3, 2, role='bodyguard'), part('Hero', 1, 5, role='leader', leader_buffs=dodge))
    event = shoot(duel(squad=plain, gunner=gunner), [6, 1])
    assert event['rolls'][0]['purpose'] == 'hit' and event['rolls'][0]['need'] == 3  # 4+ improved by 1
    event = shoot(duel(squad=dodging, gunner=gunner), [6, 1])
    assert event['rolls'][0]['need'] == 4  # +1 and -1 cancel out


def test_only_living_models_carrying_a_weapon_shoot_it():
    gunners = (part('Gunners', 2, 1, weapons=[gun(attacks='1', qty=2)]),)
    state = new_game([army('Red', (part('Target', 5, 1),), (part('Snipers', 1, 1, weapons=[gun(attacks='1')]),)),
                      army('Blue', gunners)])
    # Red's sniper kills a gunner (hit 6, wound 6, save 1); the one gunner left shoots back with one attack
    first = resolve_action(state, {'type': 'shoot', 'unit': 'p1-snipers', 'target': 'p2-gunners'},
                           Dice(FixedRolls(6, 6, 1)))
    state = apply_event(state, first)
    event = shoot(state, [1], unit='p2-gunners', target='p1-target')
    assert event['weapons'][0]['models'] == 1 and event['weapons'][0]['attacks'] == 1


# --- Replaying a game from its events -----------------------------------------------------

def test_replaying_saved_events_rebuilds_the_game_without_dice():
    armies = [army('Red', (part('Squad', 3, 2, role='bodyguard'), part('Hero', 1, 5, role='leader', save=2))),
              army('Blue', (part('Gunner', 1, 3, weapons=[gun(attacks='4')]),))]
    state, events = new_game(armies), []
    for faces in ([3, 3, 3, 1, 4, 4, 4, 1, 1, 6], [6, 6, 6, 6, 4, 4, 4, 4, 1, 1, 1, 1]):
        event = resolve_action(state, {'type': 'shoot', 'unit': 'p2-gunner', 'target': 'p1-squad'},
                               Dice(FixedRolls(*faces)))
        events.append(event)
        state = apply_event(state, event)
    saved = json.loads(json.dumps(events))  # As the game file / relay would keep them
    assert replay(armies, saved) == state
    assert wounds(state, 'p1-squad') == ['dead', 'dead', 'dead', 5]
