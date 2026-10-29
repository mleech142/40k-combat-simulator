"""
A game's state as plain data (JSON all through): the two frozen armies, every model's wounds and
position, what each unit has done this turn (`activity`) and whose turn and phase it is (`turn`).

Actions become events: resolve_action rolls the dice and says what happened without changing the
state; apply_event applies an event's results without rolling. A game's log is its events, so
replaying them on new_game rebuilds the game exactly, with no dice.
"""

import copy
import functools
import json
import math
import re
from dataclasses import asdict

from .. import contagion
from ..engagement import Sources, attack_modifiers, target_modifiers
from ..simulation import AttackerProfile, BattleConditions, DefenderProfile
from .attack import roll_attack, roll_wound
from . import battlefields, mission_state, tactical
from .missions import MIRRORED_PRIMARIES, NIGHT_VISIBILITY
from .geometry import clear_line
from .geometry import footprint as footprint_shape
from .geometry import (BOARD_DEPTH, BOARD_WIDTH, MM_PER_INCH, Shape, base_distance, base_shape, between, bounds, can_see, farther_than, hull_shape,
                       overlaps)

STATE_VERSION = 1
ENGAGEMENT_RANGE = 2  # Inches, measured base to base (11th edition; height is ignored on a flat board)
COHERENCY_RANGE = 2  # Each model within this of at least one other model in its unit...
COHERENCY_SPREAD = 9  # ...and within this of every other model in it
HEAVY_MOVE = 3  # Heavy: +1 to hit if no model in the unit moved more than this
CHARGE_REACH = 12  # A unit can declare a charge only with an enemy unit this close
FIGHT_STEPS = ('pile_in', 'fight', 'consolidate')  # The Fight phase, in order
PILE_IN_MOVE = 3  # Inches, for pile-in and consolidation moves
CONSOLIDATE_MOVE = 3
PILE_IN_REACH = 5  # An unengaged unit that charged piles in towards enemy units this close
PATH_STEP = 0.2  # Inches between the points a move's path is checked at
MODEL_FIELDS = ('wounds_left', 'alive', 'position')  # What an event can change about a model
PHASES = ('command', 'movement', 'shooting', 'charge', 'fight')  # Each player's turn, in order
DEFAULT_ORDERS = {'casualties': 'farthest'}  # Standing orders: which models are removed first
HAZARD_FAILS_ON = 2  # A hazard roll of this or less fails...
HAZARD_MORTALS = 1  # ...and the unit suffers this many mortal wounds (3 if every model is a Monster or Vehicle)
BATTLE_ROUNDS = 5
OBJECTIVE_RANGE = 3  # Inches from an objective marker's edge (11th edition; 5" vertically, ignored on a flat board)
MARKER_MM = 40  # An objective marker's diameter
DETECTION_RANGE = 15  # A hidden model can only be seen from this close...
GONE_TO_GROUND = 3  # ...or this much closer when it has gone to ground behind dense terrain
DENSE_CLIMB = 2  # Models that aren't Infantry, Beasts, Swarm or Mobile cross Dense terrain this high or lower freely, else climb it
SKIES = 2  # Inches off the maximum distance when a Fly unit takes to the skies (core rules 21.03)
SNEAKY = ('Infantry', 'Beasts', 'Beast', 'Swarm')  # Take cover inside terrain areas and can be hidden (BSData says 'Beast')
DURATIONS = ('phase', 'turn', 'game')  # How long an effect lasts
RESERVE_SHARE = 0.5  # Strategic Reserves: at most this share of the battle's points (core rules 20.01)
INGRESS_EDGE = 6  # An ingress move sets a unit up wholly within this of a battlefield edge...
INGRESS_GAP = 8  # ...and more than this horizontally from every enemy unit (core rules 20.04)
INGRESS_FROM = 2  # Reserves arrive from this battle round...
ENEMY_ZONE_FROM = 3  # ...and outside the opponent's deployment zone before this one
RESERVES_LOST = 3  # Units still in reserves at the end of this battle round are destroyed (20.03)
SCOUT_GAP = 8  # A scout move ends more than this from every enemy unit (24.32)
INFILTRATE_GAP = 8  # Infiltrators set up more than this from the enemy deployment zone and enemy units (24.20)
EMBARK_REACH = 3  # Every model of a unit embarking is within this of its transport (18.02)
DISEMBARK_REACH = {'rapid': 3, 'tactical': 3, 'combat': 6}  # Disembark moves: set up wholly within this of the transport (18.04)
DISEMBARK_NAMES = {'rapid': 'Rapid disembark', 'tactical': 'Tactical disembark', 'combat': 'Combat disembark'}
EMERGENCY_REACH = 6  # Emergency disembark: as close as possible to the destroyed transport, within this (18.05)
DEMISE_REACH = 6  # Deadly Demise: on a 6, each unit within this suffers X mortal wounds (24.08)
SET_UP_MOVES = ('ingress', 'disembark')  # Set up this phase: no other move until the Charge phase
# Manual effects: key -> (name, the side it helps or hinders, the modifier text the dice use)
MANUAL_EFFECTS = {
    'hit_plus': ('+1 to hit', 'Attacker', 'Hit Modifier +1'),
    'hit_minus': ('-1 to be hit', 'Target', 'Hit Modifier -1'),
    'wound_plus': ('+1 to wound', 'Attacker', 'Wound Modifier +1'),
    'wound_minus': ('-1 to be wounded', 'Target', 'Wound Modifier -1'),
    'reroll_hit_ones': ('Re-roll hit rolls of 1', 'Attacker', 'Reroll Hits 1'),
    'reroll_hits': ('Re-roll hit rolls', 'Attacker', 'Reroll Hits'),
    'reroll_wound_ones': ('Re-roll wound rolls of 1', 'Attacker', 'Reroll Wounds 1'),
    'reroll_wounds': ('Re-roll wound rolls', 'Attacker', 'Reroll Wounds'),
    'ap_plus': ('Improve AP by 1', 'Attacker', 'AP Modifier -1'),
    'damage_plus': ('+1 Damage', 'Attacker', 'Damage Modifier +1'),
    'damage_minus': ('-1 Damage taken', 'Target', 'Damage Modifier -1'),
    'fnp_4': ('Feel No Pain 4+', 'Target', 'Feel No Pain 4+'),
    'fnp_5': ('Feel No Pain 5+', 'Target', 'Feel No Pain 5+'),
    'fnp_6': ('Feel No Pain 6+', 'Target', 'Feel No Pain 6+'),
    'cover': ('Benefit of cover', 'Target', 'Stealth'),
    'lethal': ('Lethal Hits', 'Attacker', 'Lethal Hits'),
    'sustained': ('Sustained Hits 1', 'Attacker', 'Sustained Hits 1'),
    'devastating': ('Devastating Wounds', 'Attacker', 'Devastating Wounds'),
}


class ActionError(ValueError):
    """An action the rules don't allow; its message says why, for the player."""


def new_game(armies, battlefield=None, roles=None, setup=False, twist=None):
    """
    A game between frozen armies (snapshot.freeze_army) on a battlefield (battlefields.BUILTIN's default
    if none), the first army's player attacking unless `roles` ({'attacker', 'defender'}: players) says.
    With `setup`, the game starts with the mission sequence (SETUP_STEPS): the central objective roll,
    the roll-off for attacker or defender, and (when deployment ends) the roll-off for the first turn;
    without it (games saved before setup existed), player 1 attacks, deploys first and goes first.
    `twist` ({'key', 'primary'}; missions.TWISTS) is the Twist card the players agreed on, if any: Scrambled
    Communications swaps the primaries, Mirrored World gives both its `primary` (or 'roll': a setup step).
    """
    battlefield = json.loads(json.dumps(battlefield or battlefields.BUILTIN[battlefields.DEFAULT]))  # Plain data
    roles = dict(roles or {'attacker': 0, 'defender': 1})
    turn = {'phase': 'deployment', 'round': 0, 'player': 0}
    if setup:
        # The central objective roll, unless the battlefield fixes its central objectives (an Event layout)
        roll = sum(o['kind'] == 'central' for o in battlefield['objectives']) == 1 and not battlefield.get('fixed_objectives')
        turn = {'phase': 'setup', 'round': 0, 'player': 0, 'step': 'objectives' if roll else 'roll_off'}
    state = {'version': STATE_VERSION, 'players': [], 'units': [], 'setup': bool(setup),
             'turn': turn, 'pending': None,
             'battlefield': {k: v for k, v in battlefield.items() if k != 'objectives'}, 'roles': roles,
             'objectives': [{'id': o['id'], 'kind': o['kind'], 'role': o.get('role'),
                             'owner': roles[o['role']] if o.get('role') else None,
                             'x': o['x'], 'y': o['y'], 'terrain': o.get('terrain'), 'controller': None}
                            for o in battlefield['objectives']]}
    for player, army in enumerate(armies):
        # The battle's points: the army's points limit, else what the army costs (armies frozen without one)
        battle_points = army.get('points_limit') or sum(p.get('points') or 0 for u in army['units'] for p in u['parts'])
        state['players'].append({'name': army['name'], 'faction': army['faction'], 'orders': dict(DEFAULT_ORDERS), 'cp': 0,
                                 'reserve_limit': int(battle_points * RESERVE_SHARE),
                                 'stratagems': copy.deepcopy(army.get('stratagems', []) + army.get('rules', [])),
                                 'used': {}, 'vp': mission_state.new_vp(), 'disposition': army.get('disposition'),
                                 # Rules that add to Contagion Range, and that let the player pick units to gain an
                                 # ability (snapshot.freeze_army)
                                 'contagion_bonuses': copy.deepcopy(army.get('contagion_bonuses', [])),
                                 'grants': copy.deepcopy(army.get('grants', []))})
        for unit in army['units']:
            unit_id = f'p{player + 1}-{unit["uid"]}'
            models = [{'id': f'{unit_id}-{part_index}-{n + 1}', 'part': part_index,
                       'wounds_left': part['stats']['wounds'], 'alive': True, 'position': None,
                       # The part's weapons this model carries: the first `qty` models carry each
                       'weapons': [i for i, w in enumerate(part['weapons']) if n < w['qty']]}
                      for part_index, part in enumerate(unit['parts']) for n in range(part['models'])]
            state['units'].append({'id': unit_id, 'player': player, 'name': unit['name'],
                                   'parts': copy.deepcopy(unit['parts']), 'models': models, 'activity': {},
                                   'status': {'shocked': False}, 'effects': []})
    state['mission'] = mission_state.new_mission(armies)  # None: no force dispositions, kill points instead
    state['twist'] = dict(twist) if twist else None
    if twist and state['mission']:
        if twist['key'] == 'scrambled-communications':
            state['mission']['primaries'].reverse()
        if twist['key'] == 'mirrored-world' and twist.get('primary') in MIRRORED_PRIMARIES:
            state['mission']['primaries'] = [twist['primary'], twist['primary']]
        if twist['key'] == 'mirrored-world' and twist.get('primary') == 'roll' and setup:
            state['turn'].update(step='mirrored', next_step=state['turn']['step'])
    return state


def twist_is(state, key):
    """Whether the game is played with this Twist card."""
    return (state.get('twist') or {}).get('key') == key


def _battleline(unit):
    return any(_has(p['keywords'], 'Battleline') for p in unit['parts'])


def _roll_mirrored(state, dice):
    """Mirrored World, when the players didn't agree: a D6 (a 6 is rolled again) picks both players' primary."""
    _require_setup(state, 'mirrored')
    rolls = []
    while True:
        face = dice.roll_value('D6', 'Mirrored World')
        rolls.append(face)
        if face <= len(MIRRORED_PRIMARIES):
            break
    key = MIRRORED_PRIMARIES[face - 1]
    state['mission']['primaries'] = [key, key]
    state['turn']['step'] = state['turn'].pop('next_step')
    return {'mirrored': {'rolls': rolls, 'primary': key}}


def unit_by_id(state, unit_id):
    return next(u for u in state['units'] if u['id'] == unit_id)


def _snapshot(state):
    """What events record: each model's MODEL_FIELDS, each unit's activity and status, players' orders
    and Command points, and the turn."""
    return {'models': {m['id']: {k: m[k] for k in MODEL_FIELDS} for u in state['units'] for m in u['models']},
            'units': {u['id']: {'activity': u.get('activity', {}), 'status': u.get('status', {'shocked': False}),
                                'effects': u.get('effects', [])} for u in state['units']},
            'players': [{'orders': p.get('orders', DEFAULT_ORDERS), 'cp': p.get('cp', 0), 'used': p.get('used', {}),
                         **({'vp': p['vp']} if 'vp' in p else {}),
                         **({'plague': p['plague']} if 'plague' in p else {})} for p in state['players']],
            'mission': state.get('mission'),
            'turn': state.get('turn'), 'pending': state.get('pending'), 'roles': state.get('roles'),
            'objectives': {o['id']: o.get('controller') for o in state.get('objectives', [])},
            'layout': [{k: v for k, v in o.items() if k != 'controller'} for o in state.get('objectives', [])]}


def resolve_action(state, action, dice, rewind=None, redo=None):
    """
    Roll an action and return its event: {'action', 'rolls', 'models' (changed models), 'units'
    (changed units' activity), 'turn' (when it changed), ...}. `state` is not changed. Accepting an Undo request
    needs `rewind`: the game as it was before the action being taken back. A Command Re-roll needs `redo`
    (redo_history: the action it works out again, the game before it and the re-rolls already made on it).
    """
    kind = action.get('type') if isinstance(action, dict) else None
    handlers = _handlers(action, dice, rewind, redo)
    if kind not in handlers:
        raise ActionError(f'Unknown action: {kind}')
    return _resolve(state, action, dice, handlers[kind])


def _handlers(action, dice, rewind=None, redo=None):
    """Each action type's handler, for `action` rolling with `dice`."""
    return {'shoot': lambda s: _shoot(s, action, dice), 'place': lambda s: _place(s, action),
                'end_deployment': lambda s: _end_deployment(s, dice), 'next_phase': lambda s: _next_phase(s, dice),
                'move': lambda s: _move(s, action), 'advance': lambda s: _advance(s, action, dice),
                'stationary': lambda s: _stationary(s, action), 'reset_move': lambda s: _reset_move(s, action),
                'charge': lambda s: _charge(s, action, dice), 'charge_move': lambda s: _charge_move(s, action, dice),
                'pile_in': lambda s: _pile_in(s, action), 'fight': lambda s: _fight(s, action, dice),
                'overrun': lambda s: _overrun(s, action),
                'consolidate': lambda s: _consolidate(s, action),
                'orders': lambda s: _orders(s, action), 'stratagem': lambda s: _stratagem(s, action),
                'rule': lambda s: _rule(s, action), 'effect': lambda s: _manual_effect(s, action),
                'remove_effect': lambda s: _remove_effect(s, action), 'correction': lambda s: _correction(s, action),
                'accept': lambda s: _answer(s, action, True, rewind), 'decline': lambda s: _answer(s, action, False),
                'roll_objectives': lambda s: _roll_objectives(s, dice), 'roll_off': lambda s: _roll_off_roles(s, dice),
                'choose_role': lambda s: _choose_role(s, action), 'condemn': lambda s: mission_state.condemn(s, action),
                'choose_secondaries': lambda s: mission_state.choose_secondaries(s, action, dice),
                'tactical': lambda s: tactical.card_action(s, action, dice),
                'beacon': lambda s: tactical.choose_beacon(s, action),
                'tempting': lambda s: tactical.choose_tempting(s, action),
                'set_guards': lambda s: tactical.set_guards(s, action),
                'roll_mirrored': lambda s: _roll_mirrored(s, dice),
                'start_action': lambda s: mission_state.start_action(s, action),
                'pass': lambda s: _pass(s, action, dice), 'overwatch': lambda s: _overwatch(s, action, dice),
                'heroic': lambda s: _heroic(s, action, dice),
                'reserve': lambda s: _reserve(s, action), 'ingress': lambda s: _ingress(s, action),
                'rapid_ingress': lambda s: _rapid_ingress(s, action, dice), 'scout': lambda s: _scout(s, action, dice),
                'embark': lambda s: _embark(s, action), 'disembark': lambda s: _disembark(s, action, dice),
                'plague': lambda s: _plague(s, action), 'grant': lambda s: _grant(s, action),
                'afflict': lambda s: _afflict_choice(s, action),
                'command_reroll': lambda s: _command_reroll(s, action, dice, redo),
                'place_markers': lambda s: mission_state.place_markers(s, action)}


def _resolve(state, action, dice, handler):
    kind = action.get('type')
    pending = state.get('pending')
    if pending and kind not in ('accept', 'decline'):
        other = state['players'][1 - pending['player']]['name']
        raise ActionError(f'A correction is waiting for {other} to accept or decline it.')
    window = state['turn'].get('window')
    if window and not pending and kind not in WINDOW_ACTIONS[window['kind']] + ('orders', 'correction'):
        who = state['players'][window['player']]['name']
        raise ActionError(f'Waiting for {who}: {REACTIONS[window["kind"]]} or pass.')
    before = copy.deepcopy(_snapshot(state))
    state = copy.deepcopy(state)
    start = len(dice.log)
    detail = handler(state) or {}
    # Models removed to restore coherency don't count as destroyed (core rules 03.03)
    _aftermath(state, before, dice, detail)
    mission_state.log_destroyed(state, before['models'], action, _uncounted(detail))
    after = _snapshot(state)
    event = {'action': action, 'rolls': [asdict(g) for g in dice.log[start:]],
             'models': {k: v for k, v in after['models'].items() if v != before['models'][k]},
             'units': {k: v for k, v in after['units'].items() if v != before['units'][k]}, **detail}
    if after['turn'] != before['turn']:
        event['turn'] = after['turn']
    if after['players'] != before['players']:
        event['players'] = after['players']
    if after['pending'] != before['pending']:
        event['pending'] = after['pending']
    if after['roles'] != before['roles']:
        event['roles'] = after['roles']
    if after['mission'] != before['mission']:
        event['mission'] = after['mission']
    if after['layout'] != before['layout']:  # Objectives added, moved or changing hands (setup)
        event['layout'] = after['layout']
    changed = {k: v for k, v in after['objectives'].items() if before['objectives'].get(k) != v}
    if changed:
        event['objectives'] = changed
    return event


def apply_event(state, event):
    """The state after an event: its results applied, no dice rolled."""
    return _apply(copy.deepcopy(state), event)


def _apply(state, event):
    """Apply an event's results to `state` itself (copying what it takes from the event) and return it."""
    for unit in state['units']:
        unit.update(copy.deepcopy(event.get('units', {}).get(unit['id'], {})))
        for model in unit['models']:
            if model['id'] in event['models']:
                model.update(event['models'][model['id']])
    if 'turn' in event:
        state['turn'] = dict(event['turn'])
    for player, orders in zip(state['players'], event.get('orders', [])):  # Games saved before Command points
        player['orders'] = dict(orders)
    for player, changes in zip(state['players'], event.get('players', [])):
        player.update(copy.deepcopy(changes))
    if 'pending' in event:
        state['pending'] = copy.deepcopy(event['pending'])
    if 'roles' in event:
        state['roles'] = dict(event['roles'])
    if 'mission' in event:
        state['mission'] = copy.deepcopy(event['mission'])
    if 'layout' in event:
        held = {o['id']: o.get('controller') for o in state.get('objectives', [])}
        state['objectives'] = [dict(o, controller=held.get(o['id'])) for o in copy.deepcopy(event['layout'])]
    for objective in state.get('objectives', []):
        if objective['id'] in event.get('objectives', {}):
            objective['controller'] = event['objectives'][objective['id']]
    return state


# --- Turn order -------------------------------------------------------------------------------

def _require(state, phase, unit=None):
    """Refuse an action outside `phase`, or for a unit that isn't the active player's."""
    turn = state['turn']
    if turn['phase'] != phase:
        raise ActionError(f'That can only be done in the {phase} phase (it is the {turn["phase"]} phase).')
    if unit is not None and unit['player'] != turn['player']:
        raise ActionError(f'It is {state["players"][turn["player"]]["name"]}\'s turn.')


def _unplaced(state, player):
    """A player's units still to be set up in deployment (not those put in reserves)."""
    return [u for u in state['units'] if u['player'] == player and not in_reserve(u) and not is_embarked(u)
            and any(m['alive'] and not m['position'] for m in u['models'])]


def in_reserve(unit):
    """Whether a unit is in Strategic Reserves (off the board, to arrive with an ingress move)."""
    return bool((unit.get('status') or {}).get('reserve'))


def unit_points(unit):
    return sum(part.get('points') or 0 for part in unit['parts'])


def reserve_room(state, player):
    """The points a player can still put in reserves (their limit less the units already there)."""
    limit = state['players'][player].get('reserve_limit')
    if limit is None:  # Games started before reserves: half of what the army costs
        limit = int(sum(unit_points(u) for u in state['units'] if u['player'] == player) * RESERVE_SHARE)
    return limit - sum(unit_points(u) for u in state['units'] if u['player'] == player
                       and (in_reserve(u) or (is_embarked(u) and in_reserve(unit_by_id(state, u['status']['embarked'])))))


def _reserve(state, action):
    """
    Deployment: put a unit in Strategic Reserves instead of setting it up (it counts as that unit's turn to
    deploy). Not Fortifications; all of a player's reserves together cost at most half the battle's points.
    """
    unit = _unit_or_error(state, action.get('unit'))
    if state['turn']['phase'] != 'deployment':
        raise ActionError('Units are put in reserves during deployment.')
    _require(state, 'deployment', unit)
    if in_reserve(unit) or any(m['position'] for m in unit['models'] if m['alive']):
        raise ActionError(f'{unit["name"]} is already set up.')
    if any(_has(p['keywords'], 'Fortification') for p in unit['parts']):
        raise ActionError('Fortifications can\'t be put in reserves.')
    room = reserve_room(state, unit['player'])
    cost = unit_points(unit) + sum(unit_points(u) for u in embarked_in(state, unit))  # With the units it carries
    if cost > room:
        raise ActionError(f'Reserves can cost at most {state["players"][unit["player"]].get("reserve_limit", "half the")} points '
                          f'in all: {unit["name"]} ({cost} pts) leaves too little room ({max(room, 0)} pts left).')
    unit.setdefault('status', {'shocked': False})['reserve'] = True
    _next_deployer(state, unit)
    return {'reserve': unit['id']}


def _next_deployer(state, unit):
    """Players take turns setting up units; one with nothing left to set up sits out."""
    other = 1 - unit['player']
    if _unplaced(state, other):
        state['turn']['player'] = other


def _end_deployment(state, dice):
    _require(state, 'deployment')
    if _unplaced(state, 0) or _unplaced(state, 1):
        raise ActionError('Place every unit before deployment ends.')
    for player, owner in enumerate(state['players']):
        if has_contagion(state, player) and not owner.get('plague'):
            raise ActionError(f'{owner["name"]}: choose your Plague (Nurgle\'s Gift) before deployment ends.')
    report = {'battle_shock': [], 'hazards': []}
    for unit in state['units']:  # A Dedicated Transport must carry a unit, or it is lost (18.01)
        if any(_has(p['keywords'], 'Dedicated Transport') for p in unit['parts']) and any(m['alive'] for m in unit['models']) \
                and not embarked_in(state, unit):
            lost = [m['id'] for m in unit['models'] if m['alive']]
            for model in unit['models']:
                model['alive'], model['wounds_left'] = False, 0
            unit['status'].pop('reserve', None)
            unit['status']['lost_empty'] = True
            report.setdefault('empty_transports', []).append({'unit': unit['id'], 'name': unit['name'], 'removed': lost})
    first = 0
    if state.get('setup'):  # Roll off for the first turn: the winner goes first
        report['first_turn'] = _roll_off(dice, 'first turn')
        first = report['first_turn']['winner']
    state['roles'] = dict(state.get('roles') or {'attacker': 0, 'defender': 1}, first=first)
    # Resolve pre-battle abilities (Scouts): the players take turns, the one who goes first starting
    order = [first, 1 - first]
    waiting = [p for p in order if scout_units(state, p)]
    if waiting:
        state['turn'] = {'phase': 'prebattle', 'round': 0, 'player': waiting[0], 'scouted': [], 'finished': []}
        return {k: v for k, v in report.items() if v}
    return _begin_battle(state, dice, report)


def _begin_battle(state, dice, report):
    """Deployment and pre-battle abilities are done: battle round 1 begins with the player who goes first."""
    update_control(state)  # Who holds what as the battle begins
    mission_state.start_battle(state)
    _start_turn(state, 1, first_player(state), dice, report)
    _prune_effects(state)
    return {k: v for k, v in report.items() if v}


# --- Pre-battle abilities: Scouts (core rules 24.31, 24.32) ---------------------------------------------------

def scouts(unit, state=None):
    """X from Scouts X" if every living model in the unit has it (the least of its parts' X), else None. With `state`,
    a Dedicated Transport has the least X of its passengers if every one of them has Scouts (24.31)."""
    if state is not None and any(_has(p['keywords'], 'Dedicated Transport') for p in unit['parts']):
        passengers = [scouts(u) for u in embarked_in(state, unit)]
        if passengers and all(passengers):
            return min(passengers)
    living = {m['part'] for m in unit['models'] if m['alive']}
    values = [unit['parts'][p].get('scouts') for p in living]
    return min(values) if values and all(values) else None


def _wholly_in_zone(state, unit):
    zone = deployment_zone(state, unit['player'])
    placed = _placed_shapes(unit)
    return bool(placed) and all(battlefields.wholly_inside(shape, zone) for shape in placed)


def scout_units(state, player):
    """A player's units that can still use Scouts in the pre-battle step: in reserves, or wholly within their zone."""
    done = (state['turn'].get('scouted') or []) if state['turn']['phase'] == 'prebattle' else []
    return [u['id'] for u in state['units'] if u['player'] == player and u['id'] not in done and scouts(u, state)
            and any(m['alive'] for m in u['models']) and (in_reserve(u) or _wholly_in_zone(state, u))]


def _next_scout(state, dice):
    """After a player's pre-battle ability (or when they finish): the other player's go if they have one left,
    else this player's; once neither has, the battle begins."""
    turn = state['turn']
    left = [p for p in (1 - turn['player'], turn['player']) if p not in turn['finished'] and scout_units(state, p)]
    if left:
        turn['player'] = left[0]
        return {}
    return _begin_battle(state, dice, {'battle_shock': [], 'hazards': []})


def _scout(state, action, dice):
    """
    A pre-battle Scouts ability: a unit in reserves is set up wholly within its deployment zone; a unit on the
    board (wholly within its zone) makes a scout move of up to X", as a move (not through enemy models; climbing
    counts), ending wholly on the board, more than 8" from every enemy unit, in coherency and overlapping nothing.
    """
    unit = _unit_or_error(state, action.get('unit'))
    turn = state['turn']
    if turn['phase'] != 'prebattle':
        raise ActionError('Scouts is used in the pre-battle step, after deployment.')
    _require(state, 'prebattle', unit)
    if unit['id'] not in scout_units(state, unit['player']):
        raise ActionError(f'{unit["name"]} can\'t use Scouts now: every model needs it, and the unit must be in reserves '
                          'or wholly within your deployment zone (each unit once).')
    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = _spots(unit, action, 'Move')
    shapes = {mid: model_shape(unit['parts'][living[mid]['part']], spot) for mid, spot in spots.items()}
    if in_reserve(unit):  # Set up wholly within the deployment zone
        zone = deployment_zone(state, unit['player'])
        if not all(battlefields.wholly_inside(shape, zone) for shape in shapes.values()):
            raise ActionError('A unit in reserves with Scouts is set up wholly within your deployment zone.')
        unit['status'].pop('reserve', None)
        how = 'set_up'
    else:
        reach = scouts(unit, state)
        blocking = _blocking(state, unit)
        for mid, spot in spots.items():
            part = unit['parts'][living[mid]['part']]
            path = [living[mid]['position'], *_route(action, mid, spot), spot]
            _within_allowance(part, path_length(path), _climb(state, part, path), reach, f'its Scouts {reach}"')
            _check_path(part, path, blocking, spot)
        how = 'moved'
    placed = list(shapes.values())
    for shape in placed:
        left, bottom, right, top = bounds(shape)
        if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
            raise ActionError('Every model must end wholly on the board.')
    if how == 'moved' and any(not farther_than(shape, enemy, SCOUT_GAP) for shape in placed for enemy in _enemy_shapes(state, unit)):
        raise ActionError(f'A scout move ends more than {SCOUT_GAP}" from every enemy unit.')
    others = [shape for u in state['units'] if u is not unit for shape in _placed_shapes(u)]
    for i, shape in enumerate(placed):
        if any(overlaps(shape, other) for other in placed[i + 1:] + others):
            raise ActionError("Models' bases can't overlap.")
    problem = coherency_problem(placed)
    if problem:
        raise ActionError(problem)
    for mid, spot in spots.items():
        living[mid]['position'] = spot
    turn['scouted'].append(unit['id'])
    return dict({'scouted': unit['id'], 'how': how}, **_next_scout(state, dice))


def _end_prebattle(state, dice):
    """The player whose go it is uses no more pre-battle abilities."""
    turn = state['turn']
    turn['finished'].append(turn['player'])
    return _next_scout(state, dice)


# --- Setup: the mission sequence -----------------------------------------------------------------------------

def _require_setup(state, step):
    turn = state['turn']
    if turn['phase'] != 'setup' or turn.get('step') != step:
        raise ActionError(f'That isn\'t this step of setting up the game{" (it is over)" if turn["phase"] != "setup" else ""}.')


def _roll_off(dice, purpose):
    """Both players roll a D6 (player 1's die first), again on a tie: {'rolls': [[a, b], ...], 'winner'}."""
    rolls = []
    while True:
        pair = [dice.roll_value('D6', f'{purpose}: {name}') for name in ('player 1', 'player 2')]
        rolls.append(pair)
        if pair[0] != pair[1]:
            return {'rolls': rolls, 'winner': 0 if pair[0] > pair[1] else 1}


def _roll_objectives(state, dice):
    """The central objective: one in the centre, or on a D6 roll of 6, two along the dividing line."""
    _require_setup(state, 'objectives')
    face = dice.roll_value('D6', 'central objective')
    if face == 6:
        rest = [o for o in state['objectives'] if o['kind'] != 'central']
        pieces = terrain_areas(state)
        central = [{'id': f'central-{i + 1}', 'kind': 'central', 'role': None, 'owner': None, 'x': x, 'y': y,
                    'terrain': next((p['id'] for p, area in pieces if battlefields.inside((x, y), area.points)), None),
                    'controller': None}
                   for i, (x, y) in enumerate(battlefields.central_points(state['battlefield'], True))]
        state['objectives'] = rest[:2] + central + rest[2:]  # Home objectives, then central, then expansion
    state['turn']['step'] = 'roll_off'
    return {'central': {'roll': face, 'objectives': 2 if face == 6 else 1}}


def _roll_off_roles(state, dice):
    """The roll-off for attacker or defender: the winner chooses (choose_role)."""
    _require_setup(state, 'roll_off')
    result = _roll_off(dice, 'attacker or defender')
    state['turn'].update(step='choose_role', player=result['winner'])
    return {'roll_off': result}


def _choose_role(state, action):
    """The roll-off winner is the attacker or the defender; the defender deploys first."""
    _require_setup(state, 'choose_role')
    role = action.get('role')
    if role not in ('attacker', 'defender'):
        raise ActionError('Choose to be the attacker or defender.')
    chooser = state['turn']['player']
    state['roles'] = {role: chooser, ('defender' if role == 'attacker' else 'attacker'): 1 - chooser}
    for objective in state['objectives']:
        if objective.get('role'):
            objective['owner'] = state['roles'][objective['role']]
    state['turn'] = {'phase': 'deployment', 'round': 0, 'player': state['roles']['defender']}
    if state.get('mission'):  # Then each player chooses their secondary missions
        state['turn'] = {'phase': 'setup', 'round': 0, 'player': state['roles']['defender'], 'step': 'secondaries'}
    return {'chose': {'player': chooser, 'role': role}}


def _next_phase(state, dice):
    """On to the next phase, or the Fight phase's next step (pile in, fight, consolidate); effects that
    lasted until then end. First, the other player may react at the end of a phase (reaction windows)."""
    if state['turn']['phase'] == 'prebattle':
        return _end_prebattle(state, dice)
    reaction = _end_of_phase_window(state, dice)
    if reaction is not None:
        return reaction
    report = {'battle_shock': [], 'hazards': [], 'vp': []}  # Which units rolled and what scored, for the log
    if state['turn']['phase'] == 'movement':
        _fall_backs(state, dice, report)  # Before control is settled: a battle-shocked unit has no OC
    if state['turn']['phase'] in PHASES:
        update_control(state)  # The end of this phase (and, after the Fight phase, of the turn)
    _change_phase(state, dice, report)
    _prune_effects(state)
    return {k: v for k, v in report.items() if v}


def _change_phase(state, dice, report):
    turn = state['turn']
    if turn['phase'] == 'ended':
        raise ActionError('The battle is over.')
    if turn['phase'] not in PHASES:
        raise ActionError('The battle has not started yet.')
    if turn['phase'] == 'fight' and turn.get('step') in ('pile_in', 'consolidate'):
        # The active player's units move first; then the other player's, if any can (core rules 12.03, 12.08)
        if mover(turn) == turn['player'] and any(u['player'] != turn['player'] for u in _short_movers(state, turn['step'])):
            turn['mover'] = 1 - turn['player']
            return
        turn.pop('mover', None)
    # Moves lock at the end of a phase or Fight step (Surveil the Foe removes markers then)
    locking = START_KEYS.get(turn.get('step') if turn['phase'] == 'fight' else turn['phase'])
    if locking:
        mission_state.surveil_moves(state, locking, report)
    if turn['phase'] == 'command':
        mission_state.require_no_choice(state)
        mission_state.score(state, 'command', [turn['player']], report)
    if turn['phase'] == 'fight' and turn.get('step') == 'pile_in':
        for unit in state['units']:  # Engaged as the Fight step begins: eligible to fight all step (core rules 12.04)
            if _is_engaged(state, unit):
                unit['activity']['engaged_at_fight'] = True
        turn['step'] = 'fight'
        turn['fight'] = {'stage': 'fights_first', 'picker': turn['player']}
        _settle_picker(state)
        return
    if turn['phase'] == 'fight' and turn.get('step') == 'fight':
        waiting = [u['name'] for u in state['units'] if _can_fight(state, u)]
        if turn['fight']['picker'] is not None and waiting:
            raise ActionError(f'Units can still fight: {", ".join(waiting)}.')
        turn['step'] = 'consolidate'
        turn.pop('fight', None)
        return
    i = PHASES.index(turn['phase'])
    if i + 1 < len(PHASES):
        turn['phase'] = PHASES[i + 1]
        if turn['phase'] == 'fight':
            turn['step'] = 'pile_in'
        return
    _restore_coherency(state, report)  # The end of the turn: units back in coherency, actions complete, then scoring
    mission_state.complete_actions(state, report)
    # Each player scores, the player whose turn it is first; then they may discard Tactical cards for 1CP
    active, other = turn['player'], 1 - turn['player']
    last = turn['round'] >= BATTLE_ROUNDS and active != first_player(state)  # This turn ends battle round 5
    mission_state.score(state, 'turn', [active], report)
    mission_state.score(state, 'any_turn', [active], report)
    if last:
        mission_state.score(state, 'r5_end', [active], report)
    mission_state.score(state, 'any_turn', [other], report)
    mission_state.score(state, 'opp_turn', [other], report)
    tactical.end_of_turn_discards(state, report)
    if turn['round'] == RESERVES_LOST and turn['player'] != first_player(state):
        _reserves_lost(state, report)
    if turn['player'] == first_player(state):
        _start_turn(state, turn['round'], 1 - turn['player'], dice, report)
    elif turn['round'] >= BATTLE_ROUNDS:
        mission_state.score(state, 'battle', [first_player(state), 1 - first_player(state)], report)
        state['turn'] = {'phase': 'ended', 'round': turn['round'], 'player': turn['player']}
    else:
        _start_turn(state, turn['round'] + 1, first_player(state), dice, report)


def first_player(state):
    """Who takes the first turn of each battle round (player 1 in games without setup)."""
    return (state.get('roles') or {}).get('first', 0)


def _start_turn(state, round_number, player, dice, report):
    """
    A player's turn begins in the Command phase: every unit's record of this turn is cleared, both
    players gain 1 Command point, and the player makes a battle-shock roll for each of their units
    that is battle-shocked or at or below half strength.
    """
    seq = (state.get('turn') or {}).get('seq', 0) + 1  # Turns played so far, counting this one
    state['turn'] = {'phase': 'command', 'round': round_number, 'player': player, 'seq': seq}
    for unit in state['units']:
        unit['activity'] = {}
    for each in state['players']:
        each['cp'] = each.get('cp', 0) + 1
    for unit in state['units']:
        status = unit.setdefault('status', {'shocked': False})
        if (status.get('afflicted') or {}).get('by') == player:  # "Until the start of your next turn"
            del status['afflicted']
    for unit in state['units']:
        status = unit['status']
        if unit['player'] == player and _placed_shapes(unit) and (status['shocked'] or half_strength(unit)):
            _battle_shock_roll(unit, dice, report, affliction=affliction_of(state, unit))
    mission_state.start_turn(state, dice, report)


def _battle_shock_roll(unit, dice, report, reason=None, affliction=None):
    """2D6 against the unit's best Leadership (worse while Afflicted by Scabrous Soulrot): under it, the unit is
    battle-shocked; at or over it, it isn't (any more)."""
    status = unit.setdefault('status', {'shocked': False})
    leadership = contagion.worsen_leadership(
        min(unit['parts'][m['part']]['stats'].get('leadership') or 7 for m in unit['models'] if m['alive']), affliction)
    first, was = len(dice.log), status['shocked']
    total = dice.roll_value('2D6', 'battle-shock')
    status['shocked'] = total < leadership
    report['battle_shock'].append({'unit': unit['id'], 'name': unit['name'], 'total': total, 'leadership': leadership,
                                   'shocked': status['shocked'], 'was_shocked': was, 'rolls': [first, len(dice.log)],
                                   **({'reason': reason} if reason else {})})


def _phase_id(turn):
    return f'{turn["round"]}.{turn["player"]}.{turn["phase"]}.{turn.get("step", "")}'


def _turn_id(turn):
    return f'{turn["round"]}.{turn["player"]}'


def _prune_effects(state):
    """Effects whose phase or turn is over end; those for the game stay."""
    turn = state['turn']
    for unit in state['units']:
        unit['effects'] = [e for e in unit.get('effects', []) if e['until'] == 'game'
                           or (e['until'] == 'turn' and e['turn_id'] == _turn_id(turn))
                           or (e['until'] == 'phase' and e['phase_id'] == _phase_id(turn))]


def _add_effect(state, unit, name, side, phase, modifier, until, source):
    if until not in DURATIONS:
        raise ActionError('Choose how long it lasts: this phase, this turn or the rest of the game.')
    turn = state['turn']
    unit.setdefault('effects', []).append({'name': name, 'side': side or 'Attacker', 'phase': phase or 'Both',
                                           'modifier': modifier, 'until': until, 'source': source,
                                           'phase_id': _phase_id(turn), 'turn_id': _turn_id(turn)})


def _in_battle(state):
    if state['turn']['phase'] not in PHASES:
        raise ActionError('Stratagems, rules and effects are used during the battle.')


def _player_entry(state, action, kind):
    try:
        player = int(action.get('player'))
        entries = state['players'][player].get('stratagems', [])
    except (TypeError, ValueError, IndexError):
        raise ActionError('Say which player uses it.')
    entry = next((e for e in entries if e['key'] == action.get('key') and e['kind'] == kind), None)
    if entry is None:
        raise ActionError(f'That {kind} isn\'t one of {state["players"][player]["name"]}\'s.')
    unit = _unit_or_error(state, action.get('unit'))
    if unit['player'] != player:
        raise ActionError(f'Use it on one of your own units ({unit["name"]} is the other player\'s).')
    return player, entry, unit


def _stratagem(state, action):
    """
    Use a stratagem on one of the player's units (either player, any phase): it costs its CP, each
    stratagem once per phase per player, one stratagem per unit per phase (core rules 15.01), and not on
    a battle-shocked unit. Its effect, if the app reads one, lasts the phase; otherwise the players apply it
    (the event names its key, and the page shows its text from this PC's data: snapshot.rule_texts).
    """
    _in_battle(state)
    player, entry, unit = _player_entry(state, action, 'stratagem')
    reaction = next((k for k, name in REACTIONS.items() if entry['name'].lower() == name.lower()), None)
    if reaction:
        raise ActionError(f'{entry["name"]} is used when the game offers it: {REACTION_TIMING[reaction]}.')
    status = unit.setdefault('status', {'shocked': False})
    if status.get('shocked'):
        raise ActionError(f'{unit["name"]} is battle-shocked: stratagems can\'t target it.')
    owner = state['players'][player]
    if owner.get('cp', 0) < entry['cp']:
        raise ActionError(f'{entry["name"]} costs {entry["cp"]} CP; {owner["name"]} has {owner.get("cp", 0)} CP.')
    used = owner.setdefault('used', {})
    if used.get(entry['key']) == _phase_id(state['turn']):
        raise ActionError(f'{entry["name"]} has already been used this phase (once per phase).')
    if status.get('stratagem_phase') == _phase_id(state['turn']):
        raise ActionError(f'{unit["name"]} has already been targeted by a stratagem this phase (one stratagem per unit per phase).')
    owner['cp'] -= entry['cp']
    used[entry['key']] = _phase_id(state['turn'])
    status['stratagem_phase'] = _phase_id(state['turn'])
    if entry.get('modifier'):
        _add_effect(state, unit, entry['name'], entry.get('applies_to'), entry.get('phase'), entry['modifier'], 'phase', 'stratagem')
    return {'stratagem': entry['name'], 'cp': entry['cp'], 'player': player, 'unit': unit['id'], 'key': entry['key']}


PHASE_FITS = {'shooting': ('Shooting', 'Both'), 'fight': ('Fighting', 'Both')}  # The phases the app reads for effects


def usable_now(state):
    """
    Each player's stratagems and rules to offer in this phase, with whether `_stratagem` would take them now: those
    for the phase ('Shooting', 'Fighting' or 'Both') and those the app has no phase for. Each is the entry with
    'ready' and, when not, 'why'. Which unit it goes on is the player's choice (stratagem_targets). Outside the
    battle's phases: none.
    """
    turn = state['turn']
    if turn['phase'] not in PHASES:
        return [[], []]
    fits = PHASE_FITS.get(turn['phase'], ())
    out = []
    for owner in state['players']:
        used, offered = owner.get('used') or {}, []
        for entry in owner.get('stratagems', []):
            if entry.get('phase') and entry['phase'] not in fits:
                continue
            why = None
            if entry['kind'] == 'stratagem':
                reaction = next((k for k, name in REACTIONS.items() if entry['name'].lower() == name.lower()), None)
                if reaction:
                    why = f'Used when the game offers it: {REACTION_TIMING[reaction]}.'
                elif owner.get('cp', 0) < entry['cp']:
                    why = f'Costs {entry["cp"]} CP; {owner["name"]} has {owner.get("cp", 0)} CP.'
                elif used.get(entry['key']) == _phase_id(turn):
                    why = 'Already used this phase (once per phase).'
            offered.append(dict(entry, ready=why is None, **({'why': why} if why else {})))
        out.append(offered)
    return out


def stratagem_targets(state):
    """The units a stratagem can target now: not battle-shocked, and not targeted by one this phase (core rules 15.01)."""
    phase = _phase_id(state['turn'])
    return [u['id'] for u in state['units']
            if not (u.get('status') or {}).get('shocked') and (u.get('status') or {}).get('stratagem_phase') != phase]


def _rule(state, action):
    """Switch on one of the army's rules for a unit (free) for this phase, this turn or the rest of the game."""
    _in_battle(state)
    player, entry, unit = _player_entry(state, action, 'rule')
    _add_effect(state, unit, entry['name'], entry.get('applies_to'), entry.get('phase'), entry.get('modifier') or '',
                action.get('until') or 'phase', 'rule')  # With no modifier it's a reminder on the unit
    return {'rule': entry['name'], 'player': player, 'unit': unit['id'], 'key': entry['key']}


def _manual_effect(state, action):
    """A manual effect on a unit (from MANUAL_EFFECTS), for this phase, this turn or the rest of the game."""
    _in_battle(state)
    unit = _unit_or_error(state, action.get('unit'))
    if action.get('effect') not in MANUAL_EFFECTS:
        raise ActionError(f'Unknown effect: {action.get("effect")}.')
    name, side, modifier = MANUAL_EFFECTS[action['effect']]
    _add_effect(state, unit, name, side, 'Both', modifier, action.get('until') or 'phase', 'manual')
    return {'effect': name, 'unit': unit['id']}


def _remove_effect(state, action):
    unit = _unit_or_error(state, action.get('unit'))
    try:
        removed = unit['effects'].pop(int(action.get('index')))
    except (TypeError, ValueError, IndexError, KeyError):
        raise ActionError('That effect is not on the unit.')
    return {'removed': removed['name'], 'unit': unit['id']}


def half_strength(unit):
    """At or below half strength: half its starting models left, or (one model) half its wounds."""
    alive = [m for m in unit['models'] if m['alive']]
    if len(unit['models']) > 1:
        return len(alive) * 2 <= len(unit['models'])
    return bool(alive) and alive[0]['wounds_left'] * 2 <= unit['parts'][alive[0]['part']]['stats']['wounds']


def _fall_backs(state, dice, report):
    """
    As the Movement phase ends (moves can be redone until then), each unit that Fell Back rolls for it
    (core rules 09.07): a battle-shocked one (Desperate Escape) makes a hazard roll for each of its
    models; any other (Ordered Retreat) makes a battle-shock roll.
    """
    for unit in state['units']:
        if unit['activity'].get('moved') != 'fall_back' or not _placed_shapes(unit):
            continue
        if unit['activity'].get('desperate'):
            first = len(dice.log)
            result = _hazard(state, unit, sum(m['alive'] for m in unit['models']), dice)
            report['hazards'].append({'unit': unit['id'], 'name': unit['name'], 'reason': 'Desperate Escape', **result,
                                      'rolls': [first, len(dice.log)]})
        else:
            _battle_shock_roll(unit, dice, report, 'Ordered Retreat', affliction_of(state, unit))


def _hazard(state, unit, rolls, dice):
    """Hazard rolls: each that fails (1-2) costs the unit 1 mortal wound, or 3 if every model is a Monster
    or Vehicle. {'fails', 'mortal_wounds', 'killed' (model ids)}."""
    group = dice.roll(rolls, 'hazard', HAZARD_FAILS_ON + 1, crit_on=7)
    every_big = all(_has(unit['parts'][m['part']]['keywords'], 'Monster') or _has(unit['parts'][m['part']]['keywords'], 'Vehicle')
                    for m in unit['models'] if m['alive'])
    fails = sum(not d.success for d in group.dice)
    mortal = fails * (3 if every_big else HAZARD_MORTALS)
    return {'fails': fails, 'mortal_wounds': mortal, 'killed': _mortal_wounds(state, unit, mortal, dice)}


def _mortal_wounds(state, unit, count, dice):
    """Mortal wounds allocated one at a time like any wound (no saves; Feel No Pain still rolls)."""
    killed = []
    for _ in range(count):
        model = _allocate(unit, priority=_allocation_priority(state, unit))
        if model is None:
            break
        fnp = unit['parts'][model['part']]['stats'].get('feel_no_pain')
        if fnp and dice.roll(1, 'feel no pain', fnp, crit_on=7).successes:
            continue
        model['wounds_left'] -= 1
        model['alive'] = model['wounds_left'] > 0
        if not model['alive']:
            killed.append(model['id'])
    return killed


def score(state):
    """Each player's VP when the game has missions, else their kill points (the points of the enemy units
    they have destroyed)."""
    if state.get('mission'):
        return [mission_state.total(state, p) for p in (0, 1)]
    points = [0, 0]
    for unit in state['units']:
        if unit['models'] and not any(m['alive'] for m in unit['models']) \
                and not (unit['status'].get('lost_in_reserve') or unit['status'].get('lost_empty')):
            points[1 - unit['player']] += sum(part.get('points') or 0 for part in unit['parts'])
    return points


def _orders(state, action):
    """
    A player's standing orders (any time): which models are removed first ('casualties': 'farthest' or
    'closest' from the attacking unit) and, for units of theirs, the order their allocation groups take
    wounds ('allocation': {unit id: [part indices]}; core rules 05.03).
    """
    try:
        index = int(action.get('player'))
        player = state['players'][index]
    except (TypeError, ValueError, IndexError):
        raise ActionError('Say whose orders these are.')
    orders = dict(player.get('orders', DEFAULT_ORDERS))
    if 'casualties' in action:
        if action['casualties'] not in ('farthest', 'closest'):
            raise ActionError('Casualties are removed farthest or closest first.')
        orders['casualties'] = action['casualties']
    for kind, order in (action.get('reactions') or {}).items():
        orders['reactions'] = dict(orders.get('reactions') or {}, **{kind: _clean_reaction_order(state, index, kind, order)})
    for unit_id, parts in (action.get('allocation') or {}).items():
        unit = _unit_or_error(state, unit_id)
        if unit['player'] != index:
            raise ActionError(f'Set the allocation order of your own units ({unit["name"]} isn\'t).')
        if sorted(int(p) for p in parts) != list(range(len(unit['parts']))):
            raise ActionError(f'List every part of {unit["name"]} once.')
        orders['allocation'] = dict(orders.get('allocation') or {}, **{unit_id: [int(p) for p in parts]})
    player['orders'] = orders


# --- Shooting -------------------------------------------------------------------------------------

def _has(keywords, name):
    """Whether a keyword list has `name` (any case, ignoring a value after it: 'Rapid Fire 1')."""
    name = name.lower()
    return any(k.lower() == name or k.lower().startswith(name + ' ') for k in keywords or [])


def _big(unit):
    """Monsters and Vehicles can shoot while in engagement range and be shot while fighting."""
    return any(_has(part['keywords'], 'Monster') or _has(part['keywords'], 'Vehicle') for part in unit['parts'])


def _close_quarters(weapon):
    """Close-Quarters weapons (Pistol is the same thing in 11th edition)."""
    return _has(weapon['abilities'], 'Pistol') or _has(weapon['abilities'], 'Close-Quarters')


def _is_engaged(state, unit):
    return bool(_engaged_units(state, unit))


def moved_distance(unit):
    """The farthest any model of the unit has moved in this turn's Movement phase, along its path (from
    where it started, by way of any waypoints)."""
    if unit['activity'].get('moved') in SET_UP_MOVES:  # Set up this phase: it counts as having moved
        return math.inf
    start = unit['activity'].get('start') or {}
    via = unit['activity'].get('via') or {}
    return max((path_length([start[m['id']], *via.get(m['id'], []), m['position']])
                for m in unit['models'] if m['alive'] and m['position'] and start.get(m['id'])), default=0)


def path_length(points):
    """The length of a path through positions ({'x', 'y'}), in order."""
    return sum(math.dist(_xy(a), _xy(b)) for a, b in zip(points, points[1:]))


def _xy(position):
    return position['x'], position['y']


def _engaged_units(state, unit):
    """The enemy units in engagement range of `unit`."""
    mine = _placed_shapes(unit)
    return [u for u in state['units'] if u['player'] != unit['player'] and _engaged(mine, _placed_shapes(u))]


def _placed_models(unit):
    return [(m, model_shape(unit['parts'][m['part']], m['position'])) for m in unit['models'] if m['alive'] and m['position']]


def _shooter_checks(state, shooter):
    """Refuse a unit that can't shoot now (phase, turn, already shot, Fell Back)."""
    _require(state, 'shooting', shooter)
    activity = shooter['activity']
    if activity.get('shot'):
        raise ActionError(f'{shooter["name"]} has already shot this phase.')
    if activity.get('moved') == 'fall_back':
        raise ActionError(f'{shooter["name"]} Fell Back this turn, so it can\'t shoot.')
    if activity.get('action') and not any(_has(p['keywords'], 'Titanic') for p in shooter['parts']) \
            and not (twist_is(state, 'martial-pride') and _battleline(shooter)):
        raise ActionError(f'{shooter["name"]} started an action this turn, so it can\'t shoot.')


def _weapon_plan(state, shooter, part_index, weapon_index, target, sight=None):
    """
    Check one weapon against one target: ([models within half range], [models in range beyond it],
    extra abilities) or ActionError saying why it can't fire. A model fires if a target model it can
    see is within the weapon's range, base to base. `sight` keeps what one model can see of another
    for further checks of the same board ({(model id, model id): (visible, why not)}).
    """
    sight = {} if sight is None else sight
    try:
        weapon = shooter['parts'][int(part_index)]['weapons'][int(weapon_index)]
    except (TypeError, ValueError, IndexError):
        raise ActionError('Choose weapons the unit has.')
    if weapon['type'] != 'ranged':
        raise ActionError(f'{weapon["name"]} is a melee weapon.')
    if target['player'] == shooter['player']:
        raise ActionError('Choose an enemy unit to shoot at.')
    if not _placed_models(target):
        raise ActionError(f'{target["name"]} is not on the battlefield.')
    activity = shooter['activity']
    abilities = weapon['abilities'] or []
    close = _close_quarters(weapon)
    engaged_with = _engaged_units(state, shooter)
    big = _big(shooter)
    advanced = activity.get('moved') == 'advance'
    target_engaged = _is_engaged(state, target)
    extra = []
    if engaged_with:  # Close-quarters shooting
        if advanced:
            raise ActionError(f'{shooter["name"]} Advanced, so it can\'t shoot while engaged.')
        if not big and not close:
            raise ActionError(f'{shooter["name"]} is engaged: it can only fire Close-Quarters weapons (Pistols).')
        if not big and target not in engaged_with:
            raise ActionError(f'Close-Quarters weapons fired while engaged must target a unit engaged with {shooter["name"]}.')
        if big and not (close and target in engaged_with):
            extra = ['Hit Modifier -1']
    elif advanced and not _has(abilities, 'Assault'):  # Assault shooting
        raise ActionError(f'{shooter["name"]} Advanced: it can only fire Assault weapons ({weapon["name"]} isn\'t one).')
    if target_engaged and target not in engaged_with:
        if not _big(target):
            raise ActionError(f'{target["name"]} is engaged: only engaged Monsters and Vehicles can be shot.')
        extra = ['Hit Modifier -1']
    if target_engaged and _has(abilities, 'Blast'):
        raise ActionError(f'Blast weapons can\'t target an engaged unit ({target["name"]}).')
    reach = weapon.get('range') or 0
    targets = _placed_models(target)
    near, far, covered, hidden, dark = [], [], set(), False, False
    for model, shape in _placed_models(shooter):
        if model['part'] != int(part_index) or int(weapon_index) not in model['weapons']:
            continue
        # The closest target model it can see, looking only at those in range (sight is the slow part)
        in_range = sorted((d, i) for i, (_, other) in enumerate(targets)
                          if (d := base_distance(shape, other)) <= reach + 1e-9)
        closest = None
        for distance, i in in_range:
            target_model, other = targets[i]
            key = (model['id'], target_model['id'])
            if key not in sight:
                sight[key] = sees(state, shape, other, target, target_model)
            visible, why = sight[key]
            hidden = hidden or why == 'hidden'
            dark = dark or why == 'night'
            if visible:
                closest = distance
                break
        if closest is not None:
            (near if closest <= reach / 2 + 1e-9 else far).append(model)
            if has_cover(state, shape, target):
                covered.add(model['id'])
    if not near and not far:
        if dark and not hidden:
            raise ActionError(f'Night Fighting: {target["name"]} can\'t be seen from more than {NIGHT_VISIBILITY}" away.')
        if hidden:
            raise ActionError(f'{target["name"]} is hidden in terrain: no model carrying {weapon["name"]} is close '
                              f'enough to see it (within {DETECTION_RANGE}", or less if it has gone to ground).')
        raise ActionError(f'No model carrying {weapon["name"]} has {target["name"]} in range ({reach}") and in sight.')
    return near, far, extra, covered


def shooting_options(state, unit_id, first_only=False):
    """
    For the page: whether a unit can shoot now ('reason' why not, else None) and, for each of its
    ranged weapons, each enemy unit as a target: {'ok', 'models' (how many would fire), 'reason'}.
    With `first_only`, it stops at the first target a weapon can shoot (enough to know the unit can shoot).
    """
    shooter = _with_deck(state, copy.deepcopy(_unit_or_error(state, unit_id)))
    sight = {}
    try:
        _shooter_checks(state, shooter)
        reason = None
    except ActionError as error:
        reason = str(error)
    enemies = [u for u in state['units'] if u['player'] != shooter['player'] and _placed_models(u)]
    weapons = []
    for part_index, part in enumerate(shooter['parts']):
        for weapon_index, weapon in enumerate(part['weapons']):
            if weapon['type'] != 'ranged':
                continue
            targets = {}
            for target in enemies:
                try:
                    near, far, _, _ = _weapon_plan(state, shooter, part_index, weapon_index, target, sight)
                    targets[target['id']] = {'ok': True, 'models': len(near) + len(far), 'reason': None}
                except ActionError as error:
                    targets[target['id']] = {'ok': False, 'models': 0, 'reason': str(error)}
                    continue
                if first_only:
                    break
            weapons.append({'part': part_index, 'weapon': weapon_index, 'name': weapon['name'], 'range': weapon.get('range'),
                            'default_use': weapon['default_use'], 'targets': targets, **({'deck': True} if weapon.get('deck') else {})})
            if first_only and any(t['ok'] for t in targets.values()):
                return {'reason': reason, 'weapons': weapons}
    return {'reason': reason, 'weapons': weapons}


def _shoot(state, action, dice, snap=False):
    """
    Shoot with a unit: each weapon at its target (`target` for all, or per weapon in `weapons`:
    [{'part', 'weapon', 'target'}]). Every weapon is checked first (_weapon_plan), then rolled in
    turn: models within half range roll on their own. Wounds go to models by the target player's
    standing orders; Hazardous weapons roll afterwards. `snap`: snap shooting (Fire Overwatch, which
    checks the unit itself).
    """
    shooter = _unit_or_error(state, action.get('unit'))
    if not snap:
        _shooter_checks(state, shooter)
        _with_deck(state, shooter)  # Passengers' weapons (Firing Deck); parts aren't recorded, so this lasts the action
    activity = shooter['activity']
    picks = action.get('weapons')
    if picks is None:  # Every ranged weapon that starts ticked
        picks = [{'part': p, 'weapon': w} for p, part in enumerate(shooter['parts'])
                 for w, weapon in enumerate(part['weapons']) if weapon['default_use'] and weapon['type'] == 'ranged']
    if not picks:
        raise ActionError(f'{shooter["name"]} has no ranged weapons to fire.')
    stationary = moved_distance(shooter) <= HEAVY_MOVE + 1e-9 and not _is_engaged(state, shooter)  # Heavy
    deck = _deck_picks(state, shooter, picks)
    afflicted = afflictions(state)  # Death Guard's Nurgle's Gift (contagion.py), as the volley starts
    plans = []
    for pick in picks:
        if not isinstance(pick, dict) or 'part' not in pick or 'weapon' not in pick:
            raise ActionError('Choose weapons the unit has.')
        target = _unit_or_error(state, pick.get('target') or action.get('target'))
        near, far, extra, covered = _weapon_plan(state, shooter, pick['part'], pick['weapon'], target)
        if contagion.gives_cover(afflicted.get(shooter['id']), 'ranged'):  # Skullsquirm Blight: its targets have cover
            covered = {m['id'] for m in near + far}
        part_index, weapon_index = int(pick['part']), int(pick['weapon'])
        if not _big(shooter):  # Each model fires either its Close-Quarters weapons or its others
            for model in shooter['models']:
                chosen = [shooter['parts'][int(p['part'])]['weapons'][int(p['weapon'])] for p in picks
                          if int(p['part']) == model['part'] and int(p['weapon']) in model['weapons']]
                if any(_close_quarters(w) for w in chosen) and not all(_close_quarters(w) for w in chosen):
                    raise ActionError('Each model fires either its Close-Quarters weapons (Pistols) or its other '
                                      'ranged weapons, not both.')
        plans.append((part_index, weapon_index, shooter['parts'][part_index]['weapons'][weapon_index], target, near, far,
                      extra, covered))

    attacks, hazardous = [], []
    for part_index, weapon_index, weapon, target, near, far, extra, covered in plans:
        # Within half range or not, and with the target in cover from it or not: each its own roll
        # ...and damaged or not (24.39)
        groups = [([m for m in models if (m['id'] in covered) == cover and _damaged(shooter, m) == damaged], half, cover, damaged)
                  for models, half in ((near, True), (far, False)) for cover in (False, True) for damaged in (False, True)]
        for models, half, cover, damaged in groups:
            if not models or not any(m['alive'] for m in target['models']):
                continue
            pair = (afflicted.get(shooter['id']), afflicted.get(target['id']))
            attacker = _attacker(shooter, part_index, weapon_index, len(models), pair)
            attacker.abilities += extra
            conditions = BattleConditions(half_range=half, stationary=stationary, cover=cover, damaged=damaged)
            first = len(dice.log)
            pool = roll_attack(attacker, _defender(target, _first_alive(target)['part'], 'ranged', pair), conditions, dice,
                               snap=snap)
            killed = _resolve_wounds(state, shooter, target, attacker, conditions, pool, dice, pair)
            attacks.append({'part': part_index, 'weapon': weapon_index, 'name': weapon['name'], 'target': target['id'],
                            'models': len(models), 'half_range': half, 'cover': cover, **asdict(pool),
                            'killed': killed, 'rolls': [first, len(dice.log)]})
        if _has(weapon['abilities'], 'Hazardous'):
            hazardous += near + far
    if hazardous:  # A hazard roll for each Hazardous weapon fired
        first = len(dice.log)
        killed = _hazard(state, shooter, len(hazardous), dice)['killed']
        attacks.append({'name': 'Hazardous', 'target': shooter['id'], 'models': len(hazardous),
                        'killed': killed, 'rolls': [first, len(dice.log)]})
    activity['shot'] = True
    if deck:  # Units embarked in it can't shoot for the rest of the turn
        for passenger in embarked_in(state, shooter):
            passenger['activity']['shot'] = True
    shooter.setdefault('status', {'shocked': False})['shot_seq'] = state['turn'].get('seq', 0)  # No longer hidden
    detail = {'attacks': attacks}
    if not snap:
        detail.update(_afflict_after_shooting(state, shooter, attacks))
    if not snap:  # (Fire Overwatch opens it itself, before carrying on with the phase change)
        _save_window(state, attacks, dice)
    return detail


# --- Reactions: the other player's decisions in your turn (11th edition core stratagems) ------------------------

REACTIONS = {'overwatch': 'Fire Overwatch', 'heroic': 'Heroic Intervention', 'reroll': 'Command Re-roll',
             'ingress': 'Rapid Ingress'}
END_OF_PHASE = {'movement': ('overwatch', 'ingress'), 'charge': ('heroic',)}  # Windows at the end of a phase, in order
WINDOW_ACTIONS = {'overwatch': ('pass', 'overwatch'), 'ingress': ('pass', 'rapid_ingress'), 'heroic': ('pass', 'heroic', 'charge_move', 'command_reroll'),
                  'reroll': ('pass', 'command_reroll')}
REROLL_PURPOSES = ('hit', 'wound', 'save', 'damage', 'attacks', 'hazard', 'advance', 'charge')  # Command Re-roll's rolls
REROLL_ACTIONS = ('shoot', 'fight', 'overwatch', 'charge', 'advance', 'heroic')  # Actions whose rolls it can take
HEROIC_MODES = {'leap': 'Leap to Defend', 'fray': 'Into the Fray'}  # Into the Fray costs 1 CP more
HEROIC_REACH = 12  # Units within this of an enemy unit can intervene
FRAY_REACH = 6  # Into the Fray: any enemy unit within this can be charged
FRAY_ROLL = 6  # Into the Fray: the charge roll is at most this
REACTION_ORDERS = {'overwatch': ('ask', 'never', 'unit'), 'heroic': ('ask', 'never'), 'ingress': ('ask', 'never'), 'reroll': ('ask', 'never', 'deaths')}
OVERWATCH_RANGE = 24  # Snap shooting targets one visible enemy unit within this
REACTION_TIMING = {'overwatch': "at the end of the opponent's Movement phase",
                   'ingress': "at the end of the opponent's Movement phase, from battle round 2",
                   'heroic': "at the end of the opponent's Charge phase",
                   'reroll': 'just after the roll, from the Re-roll button on it'}


def reaction_order(state, player, kind):
    """A player's standing order for a reaction ({'mode', ...}); 'ask' when they set none."""
    return ((state['players'][player].get('orders') or {}).get('reactions') or {}).get(kind) or {'mode': 'ask'}


def _clean_reaction_order(state, player, kind, order):
    if kind not in REACTION_ORDERS or not isinstance(order, dict) or order.get('mode') not in REACTION_ORDERS[kind]:
        raise ActionError('Standing orders for reactions: Overwatch (ask, never, with a unit), Heroic Intervention '
                          '(ask, never), Command Re-roll of saves (ask, never, only when a model dies).')
    if order['mode'] == 'unit':
        unit = _unit_or_error(state, order.get('unit'))
        if unit['player'] != player:
            raise ActionError(f'Overwatch with one of your own units ({unit["name"]} isn\'t).')
        return {'mode': 'unit', 'unit': unit['id']}
    return {'mode': order['mode']}


def reaction_entry(state, player, kind):
    """The army's stratagem for a reaction (by name), else the core one at 1 CP (armies frozen without Wahapedia)."""
    name = REACTIONS[kind]
    entry = next((e for e in state['players'][player].get('stratagems', [])
                  if e['kind'] == 'stratagem' and e['name'].lower() == name.lower()), None)
    return entry or {'key': f'core:{kind}', 'kind': 'stratagem', 'name': name, 'cp': 1}


def _reaction_ready(state, player, kind, extra_cp=0):
    """Why a player can't use a reaction's stratagem now (not enough CP, used this phase), else None."""
    entry, owner = reaction_entry(state, player, kind), state['players'][player]
    if owner.get('cp', 0) < entry['cp'] + extra_cp:
        return f'{entry["name"]} costs {entry["cp"] + extra_cp} CP; {owner["name"]} has {owner.get("cp", 0)} CP.'
    if (owner.get('used') or {}).get(entry['key']) == _phase_id(state['turn']):
        return f'{entry["name"]} has already been used this phase.'
    return None


def _spend_reaction(state, player, kind, unit, extra_cp=0):
    """Pay for a reaction's stratagem on `unit`: its CP, once per phase, one stratagem per unit per phase."""
    problem = _reaction_ready(state, player, kind, extra_cp)
    if problem:
        raise ActionError(problem)
    status = unit.setdefault('status', {'shocked': False})
    if status.get('shocked'):
        raise ActionError(f'{unit["name"]} is battle-shocked: stratagems can\'t target it.')
    if status.get('stratagem_phase') == _phase_id(state['turn']):
        raise ActionError(f'{unit["name"]} has already been targeted by a stratagem this phase.')
    entry, owner = reaction_entry(state, player, kind), state['players'][player]
    owner['cp'] -= entry['cp'] + extra_cp
    owner.setdefault('used', {})[entry['key']] = _phase_id(state['turn'])
    status['stratagem_phase'] = _phase_id(state['turn'])
    return entry


def _end_of_phase_window(state, dice):
    """
    The window at the end of the active player's Movement phase (Fire Overwatch) or Charge phase (Heroic
    Intervention), for the other player: opened if they could react and their standing order doesn't answer it
    (then the phase changes once it closes); None if there is nothing to wait for.
    """
    turn = state['turn']
    player = 1 - turn['player']
    for kind in END_OF_PHASE.get(turn['phase'], ()):
        if (turn.get('reacted') or {}).get(kind) == _phase_id(turn):
            continue
        turn.setdefault('reacted', {})[kind] = _phase_id(turn)  # Asked once a phase
        order = reaction_order(state, player, kind)
        if kind == 'overwatch':
            eligible = overwatch_units(state, player)
        elif kind == 'ingress':
            eligible = ingress_units(state, player) if turn['round'] >= INGRESS_FROM else []
        else:
            eligible = [u for u in heroic_units(state, player) if any(heroic_modes(state, player, unit_by_id(state, u)).values())]
        if order['mode'] == 'never' or _reaction_ready(state, player, kind) or not eligible:
            continue
        if order['mode'] == 'unit':  # The standing order answers: that unit at the closest target it can shoot
            unit = next((u for u in state['units'] if u['id'] == order['unit']), None)
            target = _overwatch_target(state, unit) if unit and unit['id'] in overwatch_units(state, player) else None
            if target is None:
                continue
            detail = _overwatch(state, {'player': player, 'unit': unit['id'], 'target': target['id']}, dice, standing=True)
            return dict(detail, **_next_phase(state, dice)) if not turn.get('window') else detail
        turn['window'] = {'kind': kind, 'player': player, 'then': 'next_phase'}
        return {'window': kind}
    return None


def window_options(state):
    """For the page: the open reaction window, if any: {kind, player, name, cp, units: {unit id: [target ids]}}."""
    window = state['turn'].get('window')
    if not window:
        return None
    entry = reaction_entry(state, window['player'], window['kind'])
    units = {}
    if window['kind'] == 'overwatch':
        units = {u: [t['id'] for t in overwatch_targets(state, unit_by_id(state, u))] for u in overwatch_units(state, window['player'])}
    if window['kind'] == 'ingress':
        units = {u: {'deep_strike': deep_strike(unit_by_id(state, u))} for u in ingress_units(state, window['player'])}
    if window['kind'] == 'heroic' and not window.get('unit'):
        units = {u: heroic_modes(state, window['player'], unit_by_id(state, u)) for u in heroic_units(state, window['player'])}
        units = {u: modes for u, modes in units.items() if any(modes.values())}
    return {'kind': window['kind'], 'player': window['player'], 'name': entry['name'], 'cp': entry['cp'], 'units': units,
            **{k: window[k] for k in ('unit', 'mode', 'roll') if k in window}}


def _close_window(state, dice):
    """The window is answered: carry on with what it held up (the phase change)."""
    window = state['turn'].pop('window')
    if window.get('then') == 'next_phase':
        return _next_phase(state, dice)
    return {}


def _window_for(state, action, kind):
    window = state['turn'].get('window')
    if not window or window['kind'] != kind:
        raise ActionError(f'{REACTIONS[kind]} is used when the game offers it.')
    if action.get('player') != window['player']:
        raise ActionError(f'It is {state["players"][window["player"]]["name"]}\'s decision.')
    return window


def _pass(state, action, dice):
    """The reacting player lets the window go (no reaction)."""
    window = state['turn'].get('window')
    if not window:
        raise ActionError('There is nothing to pass on.')
    _window_for(state, action, window['kind'])
    detail = _close_window(state, dice)
    return dict(detail, passed=window['kind'], player=window['player'])


def overwatch_units(state, player):
    """The ids of a player's units that could Fire Overwatch now: on the board, unengaged, not Titanic or
    battle-shocked, not yet targeted by a stratagem this phase, with an enemy they could snap shoot."""
    phase = _phase_id(state['turn'])
    return [u['id'] for u in state['units'] if u['player'] == player and _placed_models(u) and not _is_engaged(state, u)
            and not any(_has(p['keywords'], 'Titanic') for p in u['parts'])
            and not (u.get('status') or {}).get('shocked') and (u.get('status') or {}).get('stratagem_phase') != phase
            and _overwatch_target(state, u) is not None]


def overwatch_targets(state, unit):
    """The enemy units `unit` could snap shoot: visible, within 24", and a target for at least one ranged weapon."""
    targets = []
    for target in state['units']:
        if target['player'] == unit['player'] or not _placed_models(target):
            continue
        if _unit_distance(_placed_shapes(unit), _placed_shapes(target)) > OVERWATCH_RANGE + 1e-9:
            continue
        for p, part in enumerate(unit['parts']):
            if any(_plan_ok(state, unit, p, w, target) for w, weapon in enumerate(part['weapons']) if weapon['type'] == 'ranged'):
                targets.append(target)
                break
    return targets


def _plan_ok(state, unit, part, weapon, target):
    try:
        _weapon_plan(state, unit, part, weapon, target)
        return True
    except ActionError:
        return False


def _overwatch_target(state, unit):
    """The closest enemy unit `unit` could snap shoot, or None."""
    targets = overwatch_targets(state, unit)
    return min(targets, key=lambda t: _unit_distance(_placed_shapes(unit), _placed_shapes(t))) if targets else None


def heroic_units(state, player):
    """The ids of a player's units that could make a Heroic Intervention: on the board, unengaged, within 12" of an enemy
    unit, not a Vehicle unless a Character or Walker, not battle-shocked or already targeted by a stratagem this phase."""
    phase = _phase_id(state['turn'])

    def vehicle_ok(unit):
        return all(not _keyword(p, ('Vehicle',)) or _keyword(p, ('Character', 'Walker')) for p in unit['parts'])
    enemies = [u for u in state['units'] if u['player'] != player and _placed_models(u)]
    return [u['id'] for u in state['units'] if u['player'] == player and _placed_models(u) and not _is_engaged(state, u)
            and vehicle_ok(u) and not (u.get('status') or {}).get('shocked') and (u.get('status') or {}).get('stratagem_phase') != phase
            and any(_unit_distance(_placed_shapes(u), _placed_shapes(e)) <= HEROIC_REACH + 1e-9 for e in enemies)]


def heroic_modes(state, player, unit):
    """{mode: whether it's possible}: affordable, with an enemy unit it could reach (Leap to Defend: one that charged
    this phase within 12"; Into the Fray: any within 6")."""
    mine = _placed_shapes(unit)
    enemies = [(u, _unit_distance(mine, _placed_shapes(u))) for u in state['units'] if u['player'] != player and _placed_models(u)]
    reach = {'leap': any(u['activity'].get('charged') and d <= CHARGE_REACH + 1e-9 for u, d in enemies),
             'fray': any(d <= FRAY_REACH + 1e-9 for _, d in enemies)}
    return {mode: reach[mode] and _reaction_ready(state, player, 'heroic', extra) is None for mode, extra in (('leap', 0), ('fray', 1))}


def heroic_targets(state, unit, mode, roll):
    """The enemy units a Heroic Intervention can charge: within the roll (and 12"), and for Leap to Defend only those
    that made a charge move this phase, for Into the Fray any within 6" of the unit."""
    start = _start_shapes(unit, 'charge_start')
    targets = []
    for other in state['units']:
        if other['player'] == unit['player'] or not _placed_shapes(other):
            continue
        gap = _unit_distance(start, _placed_shapes(other))
        if gap > min(CHARGE_REACH, roll) + 1e-9:
            continue
        if mode == 'leap' and not other['activity'].get('charged'):
            continue
        if mode == 'fray' and gap > FRAY_REACH + 1e-9:
            continue
        targets.append(other)
    return targets


def _heroic(state, action, dice):
    """
    Heroic Intervention (end of the opponent's Charge phase): one of the reacting player's units declares a charge in a
    mode chosen before the roll: Leap to Defend (1 CP) or Into the Fray (2 CP, the roll at most 6). With no enemy unit
    the mode allows within reach the charge fails; otherwise the player makes the charge move (charge_move).
    """
    window = _window_for(state, action, 'heroic')
    if window.get('unit'):
        raise ActionError('The Heroic Intervention is declared: make its charge move, or pass.')
    player = window['player']
    unit = _unit_or_error(state, action.get('unit'))
    if unit['player'] != player or unit['id'] not in heroic_units(state, player):
        raise ActionError(f'{unit["name"]} can\'t make a Heroic Intervention: it must be unengaged and within '
                          f'{HEROIC_REACH}" of an enemy unit (a Vehicle only if it\'s a Character or Walker).')
    mode = action.get('mode')
    if mode not in HEROIC_MODES:
        raise ActionError('Choose Leap to Defend or Into the Fray.')
    if not heroic_modes(state, player, unit)[mode] and _reaction_ready(state, player, 'heroic', 1 if mode == 'fray' else 0) is None:
        raise ActionError(f'{HEROIC_MODES[mode]} has nothing to charge: ' + ('no enemy unit that charged this phase is within 12".'
                          if mode == 'leap' else f'no enemy unit is within {FRAY_REACH}".'))
    entry = _spend_reaction(state, player, 'heroic', unit, extra_cp=1 if mode == 'fray' else 0)
    roll = dice.roll_value('2D6', 'charge')
    if mode == 'fray':
        roll = min(roll, FRAY_ROLL)
    activity = unit['activity']
    activity['charge_start'] = {m['id']: m['position'] for m in unit['models'] if m['alive']}
    activity['charge_roll'] = roll
    activity['heroic'] = mode
    detail = {'stratagem': entry['name'], 'cp': entry['cp'] + (1 if mode == 'fray' else 0), 'player': player, 'unit': unit['id'],
              'heroic': mode, 'roll': roll}
    if not heroic_targets(state, unit, mode, roll):  # The charge fails: on to the Fight phase
        return dict(detail, failed=True, **_close_window(state, dice))
    window.update({'unit': unit['id'], 'mode': mode, 'roll': roll})
    return detail


def _overwatch(state, action, dice, standing=False):
    """
    Fire Overwatch (1 CP; end of the opponent's Movement phase): one of the reacting player's units snap shoots one
    enemy unit (`target`) with its ranged weapons (`weapons`, as for shooting, else those that start ticked).
    `standing`: a standing order fired it, with no window open.
    """
    player = action.get('player')
    if not standing:
        _window_for(state, action, 'overwatch')
    unit = _unit_or_error(state, action.get('unit'))
    if unit['player'] != player:
        raise ActionError(f'Overwatch with one of your own units ({unit["name"]} isn\'t).')
    if unit['id'] not in overwatch_units(state, player):
        raise ActionError(f'{unit["name"]} can\'t Fire Overwatch: it must be unengaged, not Titanic, and have a visible '
                          f'enemy unit within {OVERWATCH_RANGE}" to shoot.')
    target = _unit_or_error(state, action.get('target'))
    if target['id'] not in [t['id'] for t in overwatch_targets(state, unit)]:
        raise ActionError(f'{unit["name"]} can only snap shoot a visible enemy unit within {OVERWATCH_RANGE}" '
                          f'({target["name"]} isn\'t one it can shoot).')
    picks = action.get('weapons')
    if picks is not None and any(isinstance(p, dict) and p.get('target') not in (None, target['id']) for p in picks):
        raise ActionError('Snap shooting targets one enemy unit.')
    entry = _spend_reaction(state, player, 'overwatch', unit)
    detail = _shoot(state, {'unit': unit['id'], 'target': target['id'], 'weapons': picks}, dice, snap=True)
    result = dict(detail, stratagem=entry['name'], cp=entry['cp'], player=player, unit=unit['id'], overwatch=True,
                  **({'standing': True} if standing else {}))
    then = None if standing else state['turn'].pop('window').get('then')
    if _save_window(state, detail['attacks'], dice, then):  # The moving player may re-roll a save first
        return result
    if then == 'next_phase':
        result.update(_next_phase(state, dice))
    return result


def _save_window(state, attacks, dice, then=None):
    """
    After an attack: the re-roll window for the player whose units failed a save in it, if they could use Command
    Re-roll and their standing order doesn't answer it ('never'; 'deaths': only if a model of theirs died). True if
    it opened (`then`: what to carry on with once it closes).
    """
    failed, died = {}, set()
    for attack in attacks:
        if attack.get('name') == 'Hazardous':
            continue
        target = unit_by_id(state, attack['target'])
        for group in dice.log[attack['rolls'][0]:attack['rolls'][1]]:
            if getattr(group, 'need', None) is not None and group.purpose == 'save' and any(not d.success for d in group.dice):
                failed.setdefault(target['player'], set()).add(target['id'])
        if attack['killed']:
            died.add(target['player'])
    phase = _phase_id(state['turn'])
    for player, units in failed.items():
        mode = reaction_order(state, player, 'reroll')['mode']
        if mode == 'never' or (mode == 'deaths' and player not in died) or _reaction_ready(state, player, 'reroll'):
            continue
        if not any((unit_by_id(state, u).get('status') or {}).get('stratagem_phase') != phase
                   and not (unit_by_id(state, u).get('status') or {}).get('shocked') for u in units):
            continue
        state['turn']['window'] = {'kind': 'reroll', 'player': player, **({'then': then} if then else {})}
        return True
    return False


def roll_owners(state, event):
    """
    {roll index: (player, unit id)} for the rolls of an event a Command Re-roll could take: the acting unit's hit, wound,
    attacks, damage and hazard rolls, the target's saves, and charge and Advance rolls.
    """
    action = event['action']
    kind = action.get('type')
    if kind == 'command_reroll':
        kind = (event.get('redo') or {}).get('type')
        action = event.get('redo') or {}
    owners = {}
    if kind not in REROLL_ACTIONS:
        return owners
    if kind in ('charge', 'advance', 'heroic'):
        unit = unit_by_id(state, event.get('unit') or action.get('unit'))
        for g, roll in enumerate(event['rolls']):
            if roll['purpose'] in ('charge', 'advance'):
                owners[g] = (unit['player'], unit['id'])
        return owners
    shooter = unit_by_id(state, action['unit'])
    for attack in event.get('attacks') or []:
        target = unit_by_id(state, attack['target'])
        for g in range(*attack['rolls']):
            purpose = event['rolls'][g]['purpose']
            if purpose == 'save':
                owners[g] = (target['player'], target['id'])
            elif purpose in REROLL_PURPOSES:
                owners[g] = (shooter['player'], shooter['id'])
    return owners


def rerollable(state, event):
    """
    For the page: {player: [[roll, die], ...]}: the dice of the last event each player could Command Re-roll now
    (failed dice of success rolls, every die of a rolled value; never one already re-rolled).
    """
    if not event or state['turn']['phase'] not in PHASES or state.get('pending'):
        return {}
    window = state['turn'].get('window')
    if window and 'command_reroll' not in WINDOW_ACTIONS[window['kind']]:
        return {}
    phase, out = _phase_id(state['turn']), {}
    for g, (player, unit_id) in roll_owners(state, event).items():
        status = unit_by_id(state, unit_id).get('status') or {}
        if _reaction_ready(state, player, 'reroll') or status.get('shocked') or status.get('stratagem_phase') == phase:
            continue
        roll = event['rolls'][g]
        for i, die in enumerate(roll['dice']):
            if die.get('rerolled_from') is None and (roll.get('need') is None or not die['success']):
                out.setdefault(str(player), []).append([g, i])
    return out


class _ScriptedRolls:
    """Dice that come up as `faces` in order (a face too big for the die is folded into it), then at random."""

    def __init__(self, faces, rng):
        self.faces, self.rng = list(faces), rng

    def randint(self, low, high):
        if not self.faces:
            return self.rng.randint(low, high)
        face = self.faces.pop(0)
        return (face - 1) % high + 1


def _roll_sequence(event, swap=None):
    """
    The faces an event's rolls came up, in the order they were rolled (a group's first faces, then any it re-rolled
    by its own rules), each a [face, (roll, die)] pair; `swap` ((roll, die) -> face) replaces some.
    """
    commanded = {tuple(c) for c in event.get('commanded', [])}
    sequence = []
    for g, roll in enumerate(event['rolls']):
        rerolls = []
        for i, die in enumerate(roll['dice']):
            own = roll.get('need') is not None and die.get('rerolled_from') is not None and (g, i) not in commanded
            sequence.append([die['rerolled_from'] if own else die['face'], (g, i)])
            if own:
                rerolls.append([die['face'], (g, i)])
        sequence += rerolls
    if swap:
        for item in sequence:
            if item[1] in swap:
                item[0] = swap[item[1]]
                swap = {k: v for k, v in swap.items() if k != item[1]}  # Only its first face (the one rolled first)
    return sequence


def _command_reroll(state, action, dice, redo):
    """
    Command Re-roll (1 CP, once a phase): re-roll one die of the last event's rolls for one of your units (`roll`, `die`:
    its place in the event's rolls), a charge roll in full. The action is worked out again from just before it, with
    the same dice but that one (redo: from redo_history).
    """
    if not redo:
        raise ActionError('Command Re-roll needs the last action to re-roll.')
    last = redo['last']
    try:
        g, i = int(action.get('roll')), int(action.get('die'))
        roll = last['rolls'][g]
        die = roll['dice'][i]
    except (TypeError, ValueError, IndexError, KeyError):
        raise ActionError('Choose one of the dice of the last roll.')
    player = action.get('player')
    owner = roll_owners(state, last).get(g)
    if owner is None or [g, i] not in rerollable(state, last).get(str(player), []):
        if owner is not None and owner[0] != player:
            raise ActionError('Re-roll one of your own dice.')
        if die.get('rerolled_from') is not None:
            raise ActionError('A die can only be re-rolled once.')
        raise ActionError(_reaction_ready(state, player, 'reroll') if owner is not None and _reaction_ready(state, player, 'reroll')
                          else 'Command Re-roll takes a hit, wound, save, damage, attacks, hazard, Advance or charge roll of '
                               'one of your units not yet targeted by a stratagem this phase.')
    # Back to before the action, with the re-rolls already made on it paid for again, and this one paid
    state.clear()
    state.update(copy.deepcopy(redo['rewind']))
    for earlier in redo['chain']:
        _spend_reaction(state, earlier['player'], 'reroll', unit_by_id(state, earlier['unit']))
    entry = _spend_reaction(state, player, 'reroll', unit_by_id(state, owner[1]))
    swap = {(g, k): dice.rng.randint(1, roll.get('size') or 6) for k in (range(len(roll['dice'])) if roll['purpose'] == 'charge' else [i])}
    sequence = _roll_sequence(last, swap)
    base = len(dice.log)
    scripted = dice.rng
    dice.rng = _ScriptedRolls([face for face, _ in sequence], scripted)
    try:
        detail = _handlers(redo['action'], dice)[redo['action']['type']](state) or {}
    finally:
        dice.rng = scripted
    # The re-rolled dice show their first face; those re-rolled before stay marked if rolled before this one
    order = {key: n for n, (_, key) in enumerate(_roll_sequence(last))}
    first = min(order[key] for key in swap)
    marks = {tuple(c) for c in last.get('commanded', []) if order.get(tuple(c), first + 1) < first} | set(swap)
    for mg, mi in sorted(marks):
        if base + mg < len(dice.log) and mi < len(dice.log[base + mg].dice):
            old = last['rolls'][mg]['dice'][mi]
            dice.log[base + mg].dice[mi].rerolled_from = old['rerolled_from'] if (mg, mi) not in swap else old['face']
    return dict(detail, stratagem=entry['name'], cp=entry['cp'], player=player, unit=owner[1], redo=redo['action'],
                base=redo['base'], commanded=sorted([list(m) for m in marks]), rerolled={'roll': g, 'die': i, 'from': die['face']})


def redo_history(events, replay_to):
    """
    What a Command Re-roll of the last event needs (resolve_action's `redo`): the action it works out again (the
    last event's own, or the one an earlier re-roll redid), the game just before that action (`replay_to(n)`
    replays the first n events), the re-rolls already made on it, and the last event. None if there are no events.
    """
    if not events:
        return None
    last = events[-1]
    base = last['base'] if last['action'].get('type') == 'command_reroll' else len(events) - 1
    chain = [{'player': e['player'], 'unit': e['unit']} for e in events[base + 1:] if e['action'].get('type') == 'command_reroll']
    action = events[base]['action']
    return {'action': action, 'rewind': replay_to(base), 'chain': chain, 'last': last, 'base': base}


def _resolve_wounds(state, attacking, target, attacker, conditions, pool, dice, afflicted=(None, None)):
    """Allocate and resolve a wound pool one wound at a time (normal wounds, then mortal); the models killed.
    `afflicted`: the attacking unit's and the target's afflictions (Death Guard; afflictions)."""
    killed = []
    order = state['players'][target['player']].get('orders', DEFAULT_ORDERS)['casualties']
    precision = _has(attacker.abilities, 'Precision')
    eyes = [shape for _, shape in _placed_models(attacking)]

    def visible(model):  # Precision picks out a Character the attacking unit can see
        shape = model_shape(target['parts'][model['part']], model['position'])
        return any(sees(state, eye, shape, target, model)[0] for eye in eyes)
    for mortal in [False] * pool.wounds + [True] * pool.mortal:
        model = _allocate(target, attacking, order, precision, _allocation_priority(state, target), visible)
        if model is None:
            break
        kind = 'melee' if attacker.is_melee else 'ranged'
        result = roll_wound(attacker, _defender(target, model['part'], kind, afflicted), conditions, dice,
                            model['wounds_left'], mortal)
        model['wounds_left'] -= result.damage
        model['alive'] = model['wounds_left'] > 0
        if not model['alive']:
            killed.append(model['id'])
    return killed


def _damaged(unit, model):
    """Whether a model is damaged (core rules 24.39): its part has Damaged X and it has X or fewer wounds left."""
    threshold = unit['parts'][model['part']].get('damaged')
    return bool(threshold) and model['wounds_left'] <= threshold


def _first_alive(unit):
    return next(m for m in unit['models'] if m['alive'])


def _special(unit, model):
    """Whether a model carries a weapon that not every model of its part has (a special weapon)."""
    part = unit['parts'][model['part']]
    return any(part['weapons'][w]['qty'] < part['models'] for w in model['weapons'])


def _is_character(part):
    return part.get('role') == 'leader' or _keyword(part, ('Character',))


def allocation_groups(unit):
    """A unit's non-Character allocation groups (core rules 05.03): its parts with the same W, Sv and
    invulnerable save, as lists of part indices in datasheet order."""
    groups = {}
    for i, part in enumerate(unit['parts']):
        if not _is_character(part):
            groups.setdefault(tuple(part['stats'].get(k) for k in ('wounds', 'save', 'invulnerable_save')), []).append(i)
    return list(groups.values())


def _allocation_priority(state, unit):
    """The defender's order for a unit's allocation groups (its parts, by index), a standing order."""
    orders = state['players'][unit['player']].get('orders') or {}
    return (orders.get('allocation') or {}).get(unit['id'])


def _allocate(unit, attacking=None, order='farthest', precision=False, priority=None, visible=None):
    """
    The model the next wound goes to (core rules 05.03). Models are grouped: each Character on its own,
    the rest by the same W, Sv and invulnerable save. A non-Character group with a wounded model comes
    first; then the other non-Character groups in the defender's order (`priority`: part indices, else
    the datasheet's order); Characters last, wounded ones first. A Precision attack can go to a visible
    Character first (24.28). Within a group: a wounded model, then models with only the common weapons
    before special-weapon carriers, then the farthest from (or, by standing order, closest to) the
    attacking unit, then the last-listed model.
    """
    alive = [m for m in unit['models'] if m['alive']]
    if not alive:
        return None
    parts = unit['parts']
    wounded = lambda m: m['wounds_left'] < parts[m['part']]['stats']['wounds']  # noqa: E731
    character = lambda m: _is_character(parts[m['part']])  # noqa: E731
    profile = lambda m: tuple(parts[m['part']]['stats'].get(k) for k in ('wounds', 'save', 'invulnerable_save'))  # noqa: E731
    rank = {int(p): i for i, p in enumerate(priority or [])}
    part_rank = lambda m: rank.get(m['part'], len(rank) + m['part'])  # noqa: E731
    attackers = [shape for _, shape in _placed_models(attacking)] if attacking else []

    def distance(model):
        if not attackers or not model['position']:
            return 0
        shape = model_shape(unit['parts'][model['part']], model['position'])
        return min(base_distance(shape, other) for other in attackers)

    position = {m['id']: i for i, m in enumerate(unit['models'])}
    sign = -1 if order == 'farthest' else 1

    def pick(group):
        hurt = [m for m in group if wounded(m)] or group
        return min(hurt, key=lambda m: (_special(unit, m), round(sign * distance(m), 6), -position[m['id']]))

    def first_character(group):
        hurt = [m for m in group if wounded(m)] or group
        return min(hurt, key=lambda m: (part_rank(m), position[m['id']]))

    if precision:
        seen = [m for m in alive if character(m) and m['position'] and (visible is None or visible(m))]
        if seen:
            return first_character(seen)
    others = [m for m in alive if not character(m)]
    if not others:
        return first_character(alive)
    hurt = [m for m in others if wounded(m)]
    if hurt:
        return pick([m for m in others if profile(m) == profile(hurt[0])])
    groups = {}
    for m in others:
        groups.setdefault(profile(m), []).append(m)
    return pick(min(groups.values(), key=lambda group: min(part_rank(m) for m in group)))


def _attacker(unit, part_index, weapon_index, models, afflicted=(None, None)):
    """A weapon's AttackerProfile: its abilities plus the unit's leader, enhancement and datasheet effects.
    `afflicted`: the unit's and the target's afflictions (Death Guard; afflictions)."""
    part = unit['parts'][part_index]
    weapon = part['weapons'][weapon_index]
    kind = weapon['type']
    extra = attack_modifiers(_sources(unit, afflicted[0], part), afflicted[1], kind, leader_weapon=part['role'] == 'leader')
    return AttackerProfile(weapon['attacks'], weapon['skill'], weapon['strength'], weapon['ap'], weapon['damage'],
                           list(weapon['abilities'] or []) + extra, models, kind)


def _defender(unit, part_index, kind='ranged', afflicted=(None, None)):
    """The DefenderProfile of one part of a unit, with the unit's leader and enhancement effects as a target.
    `afflicted`: the attacking unit's and this unit's afflictions (Death Guard; afflictions)."""
    part = unit['parts'][part_index]
    stats = part['stats']
    extra = target_modifiers(_sources(unit, afflicted[1]), afflicted[0], kind)
    alive = sum(m['alive'] for m in unit['models'])
    return DefenderProfile(stats['toughness'], stats['save'], stats['wounds'], stats['invulnerable_save'],
                           stats['feel_no_pain'], list(part['keywords']) + extra, alive)


def _sources(unit, affliction, part=None):
    """One side of an attack's sources for engagement.py: the unit's game effects, its living leaders' buffs, the
    enhancements it carries, the attacking part's own datasheet abilities, and its affliction."""
    alive_parts = {m['part'] for m in unit['models'] if m['alive']}
    own = list((part or {}).get('ability_buffs') or [])
    return Sources(rows=[{'applies_to': e['side'], 'phase': e['phase'], 'modifier': e['modifier']} for e in unit.get('effects', [])],
                   leader_buffs=[b for i, p in enumerate(unit['parts']) if p['role'] == 'leader' and i in alive_parts
                                 for b in p['leader_buffs'] or []],
                   carried=_carried(unit), unit_buffs=own, leader_own_buffs=own, affliction=affliction)


def _carried(unit):
    """[(buffs, bearer)] for enhancements carried by the unit itself or by a leader in it."""
    return [(part['enhancement']['buffs'], 'leader' if part['role'] == 'leader' else 'unit')
            for part in unit['parts'] if part.get('enhancement')]


# --- Death Guard: Nurgle's Gift (contagion.py) --------------------------------------------------------------------

def has_contagion(state, player):
    """Whether a player's army has Nurgle's Gift (models with the ability, in an army of their faction)."""
    return any(p.get('contagion') for u in state['units'] if u['player'] == player for p in u['parts'])


def _has_keywords(state, unit, phrase):
    """Whether a unit has every keyword a phrase names ('DEATH GUARD CHARACTER' names Character), counting the keywords
    of all its living models (an attached unit led by a Character is a Character unit), or the phrase is its name."""
    if phrase.strip().lower() in (unit['name'].lower(), unit['parts'][0]['name'].lower()):
        return True
    universe = {k.lower() for u in state['units'] for p in u['parts'] for k in p['keywords']}
    needed = _phrase_keywords(phrase, universe)
    mine = {k.lower() for m in unit['models'] if m['alive'] for k in unit['parts'][m['part']]['keywords']}
    return bool(needed) and all(k in mine for k in needed)


def contagion_ranges(state, unit):
    """{model id: Contagion Range in inches} for a unit's models on the battlefield that have Nurgle's Gift: by battle
    round, plus the model's own bonus (Gift of Poxes) and its army's rules for units like it (Hypervirulent Strains)."""
    if not any(p.get('contagion') for p in unit['parts']):
        return {}
    bonus = sum(rule['inches'] for rule in state['players'][unit['player']].get('contagion_bonuses') or []
                if _has_keywords(state, unit, rule['keywords']))
    return {m['id']: contagion.contagion_range(state['turn'].get('round'), bonus + (part.get('contagion_bonus') or 0))
            for m, _ in _placed_models(unit) if (part := unit['parts'][m['part']]).get('contagion')}


def _auras(state, player):
    """[(shape, Contagion Range)] for a player's models with Nurgle's Gift on the battlefield."""
    found = []
    for unit in state['units']:
        if unit['player'] == player:
            ranges = contagion_ranges(state, unit)
            found += [(shape, ranges[m['id']]) for m, shape in _placed_models(unit) if m['id'] in ranges]
    return found


def _within_auras(shapes, auras):
    return any(not farther_than(source, shape, reach + 1e-9) and base_distance(source, shape) <= reach + 1e-9
               for source, reach in auras for shape in shapes)


def affliction_of(state, unit, shapes=None):
    """
    A unit's affliction (contagion.py): None, or the other player's Plague (contagion.NO_PLAGUE before one is
    chosen) while it is within Contagion Range of one of their Death Guard models, or an ability made it Afflicted
    (status 'afflicted'). `shapes`: where its models count as being (where they stand, if not given).
    """
    other = 1 - unit['player']
    if not has_contagion(state, other) or not any(m['alive'] for m in unit['models']):
        return None
    shapes = [shape for _, shape in _placed_models(unit)] if shapes is None else shapes
    if (unit.get('status') or {}).get('afflicted') or (shapes and _within_auras(shapes, _auras(state, other))):
        return state['players'][other].get('plague') or contagion.NO_PLAGUE
    return None


def afflictions(state):
    """{unit id: affliction} for every Afflicted unit (affliction_of)."""
    found = {}
    for player in (0, 1):
        if not has_contagion(state, player):
            continue
        auras = _auras(state, player)
        plague = state['players'][player].get('plague') or contagion.NO_PLAGUE
        for unit in state['units']:
            if unit['player'] == player or not any(m['alive'] for m in unit['models']):
                continue
            shapes = [shape for _, shape in _placed_models(unit)]
            if (unit.get('status') or {}).get('afflicted') or (shapes and _within_auras(shapes, auras)):
                found[unit['id']] = plague
    return found


def _plague(state, action):
    """Nurgle's Gift: the Death Guard player chooses the army's Plague in the Declare Battle Formations step (here,
    during deployment)."""
    if state['turn']['phase'] != 'deployment':
        raise ActionError('The Plague is chosen in the Declare Battle Formations step, during deployment.')
    try:
        player = int(action.get('player'))
        owner = state['players'][player]
    except (TypeError, ValueError, IndexError):
        raise ActionError('Say which player chooses the Plague.')
    if not has_contagion(state, player):
        raise ActionError(f'{owner["name"]}\'s army doesn\'t have Nurgle\'s Gift.')
    if action.get('plague') not in contagion.PLAGUES:
        raise ActionError('Choose one of the Plagues.')
    owner['plague'] = action['plague']
    return {'plague': action['plague'], 'player': player}


def grant_options(state, player):
    """[{'ability', 'rule', 'count', 'units': [ids it can go to], 'chosen': [ids]}] for the rules that let a player
    pick units to gain an ability in the Declare Battle Formations step (Verminous Haze)."""
    options = []
    for grant in state['players'][player].get('grants') or []:
        units = [u for u in state['units'] if u['player'] == player and any(m['alive'] for m in u['models'])
                 and _has_keywords(state, u, grant['keywords'])]
        options.append(dict(grant, units=[u['id'] for u in units],
                            chosen=[u['id'] for u in units if grant['ability'] in (u['status'].get('granted') or [])]))
    return options


def _grant(state, action):
    """
    Pick a unit for a rule that grants an ability (Verminous Haze: up to two Plague Marines units have
    Infiltrators), or take the pick back: in deployment, before the unit is set up.
    """
    unit = _unit_or_error(state, action.get('unit'))
    if state['turn']['phase'] != 'deployment':
        raise ActionError('Units are picked for it in the Declare Battle Formations step, during deployment.')
    grant = next((g for g in grant_options(state, unit['player']) if g['ability'] == action.get('ability')), None)
    if grant is None:
        raise ActionError(f'No rule of {state["players"][unit["player"]]["name"]}\'s gives units {action.get("ability")}.')
    if unit['id'] not in grant['units']:
        raise ActionError(f'{grant["rule"]} is for {grant["keywords"].title()} units.')
    status = unit['status']
    if any(m['position'] for m in unit['models']) or in_reserve(unit) or is_embarked(unit):
        raise ActionError(f'{unit["name"]} has been set up: pick units for {grant["rule"]} before setting them up.')
    granted = list(status.get('granted') or [])
    on = grant['ability'] not in granted
    if on and len(grant['chosen']) >= grant['count']:
        raise ActionError(f'{grant["rule"]}: up to {grant["count"]} units (take one back first).')
    granted = granted + [grant['ability']] if on else [a for a in granted if a != grant['ability']]
    if granted:
        status['granted'] = granted
    else:
        status.pop('granted', None)
    return {'grant': grant['ability'], 'rule': grant['rule'], 'unit': unit['id'], 'on': on}


def _afflict(state, unit, target):
    target['status']['afflicted'] = {'by': unit['player'], 'source': unit['name']}  # Until the start of their turn


def _afflict_after_shooting(state, shooter, attacks):
    """
    Infused with the Blessings of Nurgle (Plague Marines): in its own Shooting phase, after the unit has shot, one enemy
    unit its attacks hit is Afflicted until the start of its next turn. With one unit hit, that one; with several,
    the player picks (activity 'afflict_options', the afflict action).
    """
    turn = state['turn']
    if turn['phase'] != 'shooting' or turn['player'] != shooter['player'] or \
            not any(shooter['parts'][m['part']].get('afflicts') for m in shooter['models'] if m['alive']):
        return {}
    hit = []
    for attack in attacks:
        target = attack.get('target')
        if target != shooter['id'] and target not in hit and (attack.get('hits') or attack.get('wounds') or attack.get('mortal')):
            hit.append(target)
    hit = [t for t in hit if any(m['alive'] for m in unit_by_id(state, t)['models'])]
    if len(hit) == 1:
        _afflict(state, shooter, unit_by_id(state, hit[0]))
        return {'afflicted': hit[0]}
    if hit:
        shooter['activity']['afflict_options'] = hit
    return {}


def _afflict_choice(state, action):
    """Pick which of the enemy units a unit hit is Afflicted (Infused with the Blessings of Nurgle, several hit)."""
    unit = _unit_or_error(state, action.get('unit'))
    options = unit['activity'].get('afflict_options') or []
    if state['turn']['phase'] != 'shooting' or not options:
        raise ActionError(f'{unit["name"]} has no enemy unit to make Afflicted now.')
    if action.get('target') not in options:
        raise ActionError('Choose one of the enemy units it hit.')
    target = unit_by_id(state, action['target'])
    _afflict(state, unit, target)
    del unit['activity']['afflict_options']
    return {'afflicted': target['id'], 'unit': unit['id']}


def replay(armies, events, battlefield=None, setup=False, twist=None):
    """A game rebuilt from its frozen armies (on its battlefield, with its twist) and its events, in order (no dice)."""
    state = new_game(armies, battlefield, setup=setup, twist=twist)
    # Applied in place (copying the whole state for every event made long games slow), from a copy of the
    # events so the state never shares their objects
    for event in copy.deepcopy(events):
        _apply(state, event)
    return state


def outline(part):
    """How a part's models are drawn and measured: {'shape': 'round'|'oval'|'rect', 'length', 'width'}
    in inches, 'estimated' when the size is a guess (a hull with no size yet, a missing or approximate
    base: a rectangle by the model's wounds, or 32mm round)."""
    base = part.get('base')
    if base is None:
        return {'shape': 'round', 'length': 32 / MM_PER_INCH, 'width': 32 / MM_PER_INCH, 'estimated': True}
    if base['shape'] == 'hull':
        length, width = estimated_hull(part['stats']['wounds'])
        return {'shape': 'rect', 'length': length, 'width': width, 'estimated': True}
    return {'shape': base['shape'], 'length': base['length_mm'] / MM_PER_INCH, 'width': base['width_mm'] / MM_PER_INCH,
            'estimated': bool(base.get('approx'))}


def model_shape(part, position):
    """A model's base (or hull) at its position, for measuring."""
    return _outline_shape(outline(part), position)


def measure(ends):
    """
    The Measure tool: the distance in inches between two ends, each a point {'x', 'y'} or a model's base (a point
    with the page's 'outline' and 'angle'), measured from the base's edge, so model to model is base to base.
    """
    try:
        shapes = []
        for end in ends:
            position = {'x': float(end['x']), 'y': float(end['y']), 'angle': float(end.get('angle') or 0)}
            drawn = end.get('outline')
            if drawn:
                drawn = {'shape': str(drawn['shape']), 'length': float(drawn['length']), 'width': float(drawn['width'])}
                if drawn['shape'] not in ('round', 'oval', 'rect') or not (0 < drawn['width'] < 100 and 0 < drawn['length'] < 100):
                    raise ValueError
            shapes.append(_outline_shape(drawn, position) if drawn else Shape([(position['x'], position['y'])]))
        a, b = shapes
    except (KeyError, TypeError, ValueError):
        raise ActionError("Measure between two points, each an x and y (and a model's outline and angle).")
    return base_distance(a, b)


def _outline_shape(drawn, position):
    x, y, angle = position['x'], position['y'], position.get('angle') or 0
    if drawn['shape'] == 'rect':
        return hull_shape(drawn['length'], drawn['width'], x, y, angle)
    return base_shape({'length_mm': drawn['length'] * MM_PER_INCH, 'width_mm': drawn['width'] * MM_PER_INCH}, x, y, angle)


def estimated_hull(wounds):
    """A vehicle's hull size (length, width) in inches, until its real size is entered."""
    if wounds <= 8:
        return 4, 2.5
    if wounds <= 14:
        return 5, 3.5
    return 7, 4.5


def role_of(state, player):
    """'attacker' or 'defender'."""
    roles = state.get('roles') or {'attacker': 0, 'defender': 1}
    return 'attacker' if roles['attacker'] == player else 'defender'


def deployment_zone(state, player):
    """A player's deployment zone (polygon) on the game's battlefield."""
    battlefield = state.get('battlefield') or battlefields.BUILTIN[battlefields.DEFAULT]
    return battlefield['zones'][role_of(state, player)]


def _unit_or_error(state, unit_id):
    unit = next((u for u in state['units'] if u['id'] == unit_id), None)
    if unit is None:
        raise ActionError('That unit is not in this game.')
    return unit


def _place(state, action):
    """Set up a unit: every living model, wholly on the board and in its player's deployment zone,
    overlapping no other model."""
    unit = _unit_or_error(state, action.get('unit'))
    if state['turn']['phase'] != 'deployment':
        raise ActionError('Units can only be placed during deployment.')
    _require(state, 'deployment', unit)
    if in_reserve(unit):
        raise ActionError(f'{unit["name"]} is in reserves (undo to set it up instead).')
    new = not any(m['position'] for m in unit['models'] if m['alive'])
    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = _spots(unit, action, 'Place')
    shapes = {mid: model_shape(unit['parts'][living[mid]['part']], spot) for mid, spot in spots.items()}
    zone = deployment_zone(state, unit['player'])
    in_zone = all(battlefields.wholly_inside(shape, zone) for shape in shapes.values())
    for shape in shapes.values():
        left, bottom, right, top = bounds(shape)
        if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
            raise ActionError('Every model must be wholly on the board.')
    if not in_zone and infiltrators(unit):  # Anywhere more than 8" from the enemy zone and enemy units (24.20)
        problem = infiltrate_problem(state, unit, shapes.values())
        if problem:
            raise ActionError(problem)
    elif not in_zone:
        raise ActionError('Every model must be wholly within your deployment zone.')
    others = [model_shape(u['parts'][m['part']], m['position']) for u in state['units'] if u is not unit
              for m in u['models'] if m['alive'] and m['position']]
    placed = list(shapes.values())
    for i, shape in enumerate(placed):
        if any(overlaps(shape, other) for other in placed[i + 1:] + others):
            raise ActionError("Models' bases can't overlap.")
    for mid, spot in spots.items():
        living[mid]['position'] = spot
    if new:
        _next_deployer(state, unit)
    return {}


def infiltrators(unit):
    """Whether every model in the unit has Infiltrators (its datasheets, or a rule granting it: _grant)."""
    living = {m['part'] for m in unit['models'] if m['alive']}
    granted = 'Infiltrators' in ((unit.get('status') or {}).get('granted') or [])
    return bool(living) and (granted or all(unit['parts'][p].get('infiltrators') for p in living))


def _zone_distance(shape, polygon):
    """How far a model's base is from a polygon (0 if it is partly inside it)."""
    if _partly_inside(shape, polygon):
        return 0
    edge = Shape([tuple(p) for p in polygon])
    pts = list(edge.points)
    centre = _centre(shape)
    nearest = min(math.dist(centre, _closest_on_segment(centre, a, b)) for a, b in zip(pts, pts[1:] + pts[:1]))
    return max(0, nearest - _reach(shape, centre))


def _closest_on_segment(p, a, b):
    ax, ay = a
    bx, by = b
    length = (bx - ax) ** 2 + (by - ay) ** 2
    t = 0 if not length else max(0, min(1, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / length))
    return (ax + t * (bx - ax), ay + t * (by - ay))


def _reach(shape, centre):
    """How far a base reaches from its centre (its radius, or its farthest corner for a polygon: a little generous)."""
    return shape.radius or max(math.dist(centre, p) for p in shape.points)


def infiltrate_problem(state, unit, shapes):
    """Why Infiltrators can't be set up with these shapes outside its zone (more than 8" from the enemy zone and units)."""
    shapes = list(shapes)
    enemy_zone = deployment_zone(state, 1 - unit['player'])
    if any(_zone_distance(shape, enemy_zone) <= INFILTRATE_GAP + 1e-9 for shape in shapes):
        return f'Infiltrators are set up wholly within your deployment zone, or more than {INFILTRATE_GAP}" from the enemy deployment zone.'
    if any(not farther_than(shape, enemy, INFILTRATE_GAP) for shape in shapes for enemy in _enemy_shapes(state, unit)):
        return f'Infiltrators outside your deployment zone are set up more than {INFILTRATE_GAP}" from every enemy unit.'
    return None


def _spots(unit, action, verb):
    """{model id: position} for every living model of `unit`, from an action's 'models'."""
    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = {}
    for spot in action.get('models') or []:
        try:
            current = living[spot['id']]['position'] or {}
            angle = spot.get('angle', current.get('angle', 0))
            spots[spot['id']] = {'x': float(spot['x']), 'y': float(spot['y']), 'angle': float(angle or 0)}
        except (KeyError, TypeError, ValueError):
            raise ActionError('Each model needs an id, x and y.')
    if set(spots) != set(living):
        raise ActionError(f'{verb} every model of {unit["name"]} at once.')
    for spot in spots.values():  # Whole numbers stay whole in the saved game
        _whole(spot)
    return spots


def _whole(position):
    for key in ('x', 'y', 'angle'):
        if float(position[key]).is_integer():
            position[key] = int(position[key])
    return position


MAX_ROUTE = 100  # Bends in a dragged route


def _route(action, mid, spot):
    """
    The positions a model passes through on its way to `spot`: the bends of the path the player dragged it
    along (the action's models[].route), so a model steered through a gap is checked along that path.
    """
    entry = next((m for m in action.get('models') or [] if m.get('id') == mid), {})
    raw = entry.get('route') or []
    if not isinstance(raw, list):
        raise ActionError('A route is a list of points.')
    if len(raw) > MAX_ROUTE:
        raise ActionError(f'A route has at most {MAX_ROUTE} points.')
    route = []
    for point in raw:
        try:
            x, y = float(point['x']), float(point['y'])
        except (KeyError, TypeError, ValueError):
            raise ActionError('Each point of a route needs an x and y.')
        if not (0 <= x <= BOARD_WIDTH and 0 <= y <= BOARD_DEPTH):
            raise ActionError('A route must stay on the board.')
        route.append(_whole({'x': x, 'y': y, 'angle': spot['angle']}))
    return route


def _check_path(part, path, blocking, spot):
    """Refuse a path (positions) on which the model would pass through any of `blocking`, every leg checked."""
    for a, b in zip(path, path[1:]):
        steps = max(1, math.ceil(math.dist(_xy(a), _xy(b)) / PATH_STEP))
        for i in range(1, steps + (b is not path[-1])):
            t = i / steps
            here = {'x': a['x'] + t * (b['x'] - a['x']), 'y': a['y'] + t * (b['y'] - a['y']), 'angle': spot['angle']}
            if any(overlaps(model_shape(part, here), enemy) for enemy in blocking):
                raise ActionError(f'{part["name"]} can\'t move through enemy models.')


# --- Movement ---------------------------------------------------------------------------------

MOVE_MODES = {'normal': 'Normal move', 'advance': 'Advance', 'fall_back': 'Fall Back'}


def _placed_shapes(unit):
    return [model_shape(unit['parts'][m['part']], m['position']) for m in unit['models'] if m['alive'] and m['position']]


def _enemy_shapes(state, unit):
    return [shape for u in state['units'] if u['player'] != unit['player'] for shape in _placed_shapes(u)]


def _blocking(state, unit, through_all=False):
    """
    The enemy models a unit's models can't move through: all of them, but a Monster or Vehicle passes
    through any that aren't Monsters or Vehicles on every kind of move (core rules 17.01 and its FAQ),
    and none block a unit taking to the skies or making a Desperate Escape.
    """
    if through_all:
        return []
    big = _big(unit)
    return [shape for u in state['units'] if u['player'] != unit['player'] and (not big or _big(u)) for shape in _placed_shapes(u)]


def _engaged(shapes, enemies):
    return any(not farther_than(a, b, ENGAGEMENT_RANGE + 1e-9) and base_distance(a, b) <= ENGAGEMENT_RANGE + 1e-9
               for a in shapes for b in enemies)


def move_characteristic(part, affliction=None):
    """A part's Move in inches ('6', '20+' -> 20; none -> 0), worse while Afflicted by Scabrous Soulrot."""
    found = re.match(r'\s*(\d+)', str(part['stats'].get('move') or ''))
    return contagion.worsen_move(int(found.group(1)) if found else 0, affliction)


# Where a unit started each kind of move this turn: moves can be redone until their phase (or step) ends
START_KEYS = {'movement': 'start', 'charge': 'charge_start', 'pile_in': 'pile_start', 'fight': 'overrun_start',
              'consolidate': 'consolidate_start'}


def _ready_to_move(state, unit, phase='movement'):
    """
    The unit's activity, once it is the unit's `phase`. A unit can be moved, reset and moved again
    as often as its player likes during the phase: every move is measured from where it started the
    phase (kept from its first move, in START_KEYS[phase]), and moves lock when the phase ends.
    """
    _require(state, phase, unit)
    activity = unit['activity']
    key = START_KEYS[phase]
    if key not in activity:
        activity[key] = {m['id']: m['position'] for m in unit['models'] if m['alive']}
    return activity


def _start_shapes(unit, key='start'):
    """The unit's models where they started a kind of move this turn (where they are, if they haven't)."""
    start = unit['activity'].get(key) or {}
    return [model_shape(unit['parts'][m['part']], start.get(m['id']) or m['position'])
            for m in unit['models'] if m['alive'] and (start.get(m['id']) or m['position'])]


def _back_to_start(unit, key='start'):
    for model in unit['models']:
        if model['alive'] and model['id'] in unit['activity'].get(key, {}):
            model['position'] = unit['activity'][key][model['id']]


def _board_mover(state, unit):
    """The unit's activity for a move on the board: not one in reserves or one that arrived this phase."""
    if in_reserve(unit):
        raise ActionError(f'{unit["name"]} is in reserves: it arrives with an ingress move (from battle round {INGRESS_FROM}).')
    if is_embarked(unit):
        raise ActionError(f'{unit["name"]} is embarked: it disembarks first.')
    activity = _ready_to_move(state, unit)
    if activity.get('moved') in SET_UP_MOVES:
        raise ActionError(f'{unit["name"]} was set up this phase ({"arrived" if activity["moved"] == "ingress" else "disembarked"}): '
                          'it can\'t make another move until the Charge phase.')
    return activity


def _advance(state, action, dice):
    """Roll to Advance: D6 inches added to the unit's moves this phase (as Advances); the roll stands."""
    unit = _unit_or_error(state, action.get('unit'))
    activity = _board_mover(state, unit)
    if 'advance_roll' in activity:
        raise ActionError(f'{unit["name"]} has already rolled to Advance.')
    if _engaged(_start_shapes(unit), _enemy_shapes(state, unit)):
        raise ActionError(f'{unit["name"]} is in engagement range: it can only Fall Back.')
    activity['advance_roll'] = dice.roll_value('D6', 'advance')


def _stationary(state, action):
    """Remain stationary: back where the unit started the phase, if it had moved."""
    unit = _unit_or_error(state, action.get('unit'))
    activity = _board_mover(state, unit)
    if 'advance_roll' in activity:
        raise ActionError(f'{unit["name"]} rolled to Advance: it must make an Advance move.')
    _back_to_start(unit)
    activity['moved'] = 'stationary'


def _reset_move(state, action):
    """Take a unit back to where it started the phase, not yet moved (an Advance or charge roll stands)."""
    unit = _unit_or_error(state, action.get('unit'))
    phase = state['turn']['phase']
    if phase == 'fight' and state['turn'].get('step') in ('pile_in', 'consolidate'):
        step = state['turn']['step']
        activity = unit['activity']
        if START_KEYS[step] not in activity:
            raise ActionError(f'{unit["name"]} hasn\'t moved in this step.')
        _back_to_start(unit, START_KEYS[step])
        activity.pop(START_KEYS[step])
        return
    if phase not in ('movement', 'charge'):
        raise ActionError('Moves can only be reset in the movement phase or charge phase they were made in '
                          '(or the pile in and consolidate steps).')
    activity = _ready_to_move(state, unit, phase)
    if phase == 'movement' and activity.get('moved') == 'disembark':
        raise ActionError(f'{unit["name"]} disembarked: that can\'t be reset (use Undo).')
    _back_to_start(unit, START_KEYS[phase])
    if phase == 'movement' and activity.get('moved') == 'ingress':  # Back into reserves
        unit['status']['reserve'] = True
    if phase == 'movement' and activity.pop('embarked', None):  # Back out of the transport, where it moved to
        unit['status'].pop('embarked', None)
    for key in ('moved', 'via') if phase == 'movement' else ('charged', 'charge_targets'):
        activity.pop(key, None)


# --- Strategic Reserves: arriving ---------------------------------------------------------------------

def deep_strike(unit):
    """Whether every model in the unit has Deep Strike (it can arrive anywhere more than 8" from enemies)."""
    living = {m['part'] for m in unit['models'] if m['alive']}
    return bool(living) and all(unit['parts'][p].get('deep_strike') for p in living)


def ingress_rules(state, player):
    """
    Where a player's reserves can arrive now, for the page to shade: within `edge` inches of a battlefield edge
    (anywhere for the `deep_strike` units), more than `gap` inches from enemy models, and outside `enemy_zone`
    (a polygon) when it is closed to them; `units`: their units in reserves.
    """
    units = [u['id'] for u in state['units'] if u['player'] == player and in_reserve(u) and any(m['alive'] for m in u['models'])]
    closed = state['turn'].get('round', 0) < ENEMY_ZONE_FROM
    return {'units': units, 'deep_strike': [u for u in units if deep_strike(unit_by_id(state, u))],
            'edge': INGRESS_EDGE, 'gap': INGRESS_GAP, 'enemy_zone': deployment_zone(state, 1 - player) if closed else None}


def _partly_inside(shape, polygon):
    """Whether any of a model's base is inside a polygon."""
    return battlefields.inside(_centre(shape), polygon) or any(battlefields.inside(p, polygon) for p in battlefields.outline_points(shape))


def ingress_problem(state, unit, shapes):
    """Why a reserve unit can't be set up with these model shapes (core rules 20.04, Deep Strike 24.09), else None."""
    shapes = list(shapes)
    strike = deep_strike(unit)
    for shape in shapes:
        left, bottom, right, top = bounds(shape)
        if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
            return 'Every model must be wholly on the board.'
        if not strike and not (right <= INGRESS_EDGE + 1e-9 or left >= BOARD_WIDTH - INGRESS_EDGE - 1e-9
                               or top <= INGRESS_EDGE + 1e-9 or bottom >= BOARD_DEPTH - INGRESS_EDGE - 1e-9):
            return f'Every model must be set up wholly within {INGRESS_EDGE}" of a battlefield edge.'
    if state['turn']['round'] < ENEMY_ZONE_FROM:
        zone = deployment_zone(state, 1 - unit['player'])
        if any(_partly_inside(shape, zone) for shape in shapes):
            return f'Before battle round {ENEMY_ZONE_FROM}, no model can be set up in the opponent\'s deployment zone.'
    enemies = _enemy_shapes(state, unit)
    if any(not farther_than(shape, enemy, INGRESS_GAP) for shape in shapes for enemy in enemies):
        return f'Every model must be set up more than {INGRESS_GAP}" from every enemy unit.'
    others = [shape for u in state['units'] if u is not unit for shape in _placed_shapes(u)]
    for i, shape in enumerate(shapes):
        if any(overlaps(shape, other) for other in shapes[i + 1:] + others):
            return "Models' bases can't overlap."
    return coherency_problem(shapes)


def _arrive(state, unit, action):
    """Set a reserve unit up on the board (an ingress move), checked by ingress_problem."""
    if not in_reserve(unit):
        raise ActionError(f'{unit["name"]} isn\'t in reserves.')
    if state['turn']['round'] < INGRESS_FROM:
        raise ActionError(f'Reserves can arrive from battle round {INGRESS_FROM}.')
    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = _spots(unit, action, 'Set up')
    shapes = {mid: model_shape(unit['parts'][living[mid]['part']], spot) for mid, spot in spots.items()}
    problem = ingress_problem(state, unit, shapes.values())
    if problem:
        raise ActionError(problem)
    for mid, spot in spots.items():
        living[mid]['position'] = spot
    unit['status'].pop('reserve', None)


def _ingress(state, action):
    """
    The Movement phase: a unit in reserves arrives (an ingress move; core rules 20.04). It can be set up again
    until the phase ends (or reset back into reserves), and can't make another move until the Charge phase.
    """
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit)
    if activity.get('moved') == 'ingress':  # Setting it up again: back into reserves first
        _back_to_start(unit)
        unit['status']['reserve'] = True
    elif 'moved' in activity:
        raise ActionError(f'{unit["name"]} has already moved this phase.')
    _arrive(state, unit, action)
    activity['moved'] = 'ingress'
    return {'arrived': unit['id']}


def _rapid_ingress(state, action, dice):
    """Rapid Ingress (1 CP; end of the opponent's Movement phase, not battle round 1): a unit in reserves arrives."""
    player = action.get('player')
    _window_for(state, action, 'ingress')
    unit = _unit_or_error(state, action.get('unit'))
    if unit['player'] != player:
        raise ActionError(f'Rapid Ingress brings in one of your own units ({unit["name"]} isn\'t).')
    if unit['id'] not in ingress_units(state, player):
        raise ActionError(f'{unit["name"]} can\'t make a Rapid Ingress: it must be in reserves, and not an Aircraft.')
    entry = _spend_reaction(state, player, 'ingress', unit)  # A refused set-up below undoes this (the action fails)
    _arrive(state, unit, action)
    detail = {'stratagem': entry['name'], 'cp': entry['cp'], 'player': player, 'unit': unit['id'], 'arrived': unit['id']}
    return dict(detail, **_close_window(state, dice))


def ingress_units(state, player):
    """A player's units that Rapid Ingress could bring in: in reserves, not Aircraft, no stratagem on them this phase."""
    phase = _phase_id(state['turn'])
    return [u['id'] for u in state['units'] if u['player'] == player and in_reserve(u) and any(m['alive'] for m in u['models'])
            and not any(_has(p['keywords'], 'Aircraft') for p in u['parts'])
            and (u.get('status') or {}).get('stratagem_phase') != phase]


def _reserves_lost(state, report):
    """The end of battle round 3: units still in reserves are destroyed (not counted as destroyed by the enemy)."""
    for unit in state['units']:
        if in_reserve(unit) and any(m['alive'] for m in unit['models']):
            lost = [m['id'] for m in unit['models'] if m['alive']]
            for model in unit['models']:
                model['alive'], model['wounds_left'] = False, 0
            unit['status'].pop('reserve', None)
            unit['status']['lost_in_reserve'] = True
            report.setdefault('reserves_lost', []).append({'unit': unit['id'], 'name': unit['name'], 'removed': lost})
            for passenger in embarked_in(state, unit):
                aboard = [m['id'] for m in passenger['models'] if m['alive']]
                for model in passenger['models']:
                    model['alive'], model['wounds_left'] = False, 0
                passenger['status'].pop('embarked', None)
                passenger['status']['lost_in_reserve'] = True
                report['reserves_lost'].append({'unit': passenger['id'], 'name': passenger['name'], 'removed': aboard})


# --- Transports (core rules 18) ------------------------------------------------------------------------------

def transport_info(unit):
    """The unit's transport capacity (rules_text.parse_transport), {} if it is a TRANSPORT whose capacity wasn't read,
    None if it isn't one."""
    for part in unit['parts']:
        if part.get('transport') is not None:
            return part['transport']
    return {} if any(_has(p['keywords'], 'Transport') for p in unit['parts']) else None


def is_embarked(unit):
    return bool((unit.get('status') or {}).get('embarked'))


def embarked_in(state, transport):
    """The units embarked within a transport."""
    return [u for u in state['units'] if (u.get('status') or {}).get('embarked') == transport['id']]


def _phrase_keywords(phrase, universe):
    """The keywords (from `universe`, lower case) a phrase such as 'Adeptus Astartes Infantry' names, longest first;
    words that aren't keywords (faction names, which units don't carry) are left out."""
    text, found = ' ' + phrase.lower() + ' ', []
    for keyword in sorted(universe, key=len, reverse=True):
        if f' {keyword} ' in text:
            found.append(keyword)
            text = text.replace(f' {keyword} ', ' | ')
    return found


def _is_kind(unit, part, kind, universe, open_ended=False):
    """Whether a model (its part) is of a kind a transport names: its unit's or part's name, or every keyword the
    phrase names. A phrase naming no keyword matches every model when `open_ended` (a capacity of 'N DEATH GUARD
    INFANTRY' names Infantry; 'N models' names nothing), else none."""
    kind = kind.strip().lower()
    if not kind or kind in (unit['name'].lower(), part['name'].lower()):
        return True
    needed = _phrase_keywords(kind, universe)
    if not needed:
        return open_ended
    mine = {k.lower() for k in part['keywords']}
    return all(k in mine for k in needed)


def transport_problem(state, transport, units):
    """Why `units` can't all be embarked within `transport` together (its capacity), else None."""
    info = transport_info(transport)
    if info is None:
        return f'{transport["name"]} isn\'t a transport.'
    if not info:  # Its capacity wasn't read from the datasheet: the players judge it
        return None
    universe = {k.lower() for u in state['units'] for p in u['parts'] for k in p['keywords']}
    used = [0] * len(info['pools'])
    for unit in units:
        for model in (m for m in unit['models'] if m['alive']):
            part = unit['parts'][model['part']]
            if info['only'] and not any(_is_kind(unit, part, k, universe) and k.strip() for k in info['only']):
                return f'{transport["name"]} can only transport {", ".join(info["only"])}.'
            if any(_is_kind(unit, part, k, universe) for k in info['excluded']):
                return f'{transport["name"]} can\'t transport {part["name"]}.'
            cost = next((c['n'] for c in info['costs'] if any(_is_kind(unit, part, k, universe) for k in c['kinds'])), 1)
            kinds = [i for i, pool in enumerate(info['pools']) if any(_is_kind(unit, part, k, universe, True) for k in pool['kinds'])]
            if not kinds:
                return f'{transport["name"]} can only transport {" or ".join(k for p in info["pools"] for k in p["kinds"])} models.'
            pool = next((i for i in kinds if used[i] + cost <= info['pools'][i]['n']), None)
            if pool is None:
                return f'{transport["name"]} has no room: its capacity is {" and ".join(str(p["n"]) for p in info["pools"])} models.'
            used[pool] += cost
    return None


def _embark(state, action):
    """
    Embark a unit within a friendly transport: in deployment it starts embarked (counting as its turn to deploy);
    in its Movement phase, after a Normal move, Advance or Fall Back, with every model within 3" of the transport,
    unless it was set up this turn (core rules 18.01, 18.02). Its capacity is checked with the units already aboard.
    """
    unit = _unit_or_error(state, action.get('unit'))
    transport = _unit_or_error(state, action.get('transport'))
    if transport is unit or transport['player'] != unit['player'] or transport_info(transport) is None:
        raise ActionError('Choose one of your own transports.')
    if not any(m['alive'] for m in transport['models']):
        raise ActionError(f'{transport["name"]} has been destroyed.')
    if is_embarked(unit):
        raise ActionError(f'{unit["name"]} is already embarked.')
    problem = transport_problem(state, transport, embarked_in(state, transport) + [unit])
    if problem:
        raise ActionError(problem)
    if state['turn']['phase'] == 'deployment':
        _require(state, 'deployment', unit)
        if in_reserve(unit) or any(m['position'] for m in unit['models'] if m['alive']):
            raise ActionError(f'{unit["name"]} is already set up.')
        if in_reserve(transport) and unit_points(unit) > reserve_room(state, unit['player']):
            raise ActionError(f'{transport["name"]} is in reserves, and {unit["name"]} ({unit_points(unit)} pts) would take '
                              'your reserves over half your points.')
        unit.setdefault('status', {'shocked': False})['embarked'] = transport['id']
        _next_deployer(state, unit)
        return {'embarked': unit['id'], 'transport': transport['id']}
    _require(state, 'movement', unit)
    activity = unit['activity']
    if activity.get('moved') not in MOVE_MODES:
        raise ActionError(f'{unit["name"]} embarks after a Normal move, Advance or Fall Back this phase: move it within '
                          f'{EMBARK_REACH}" of {transport["name"]} first.')
    if activity.get('disembarked'):
        raise ActionError(f'{unit["name"]} was set up this turn, so it can\'t embark.')
    carriers = _placed_shapes(transport)
    if not carriers:
        raise ActionError(f'{transport["name"]} is not on the battlefield.')
    if any(min(base_distance(shape, c) for c in carriers) > EMBARK_REACH + 1e-9 for shape in _placed_shapes(unit)):
        raise ActionError(f'Every model of {unit["name"]} must be within {EMBARK_REACH}" of {transport["name"]} to embark.')
    for model in unit['models']:
        model['position'] = None
    unit['status']['embarked'] = transport['id']
    activity['embarked'] = True  # It can't disembark this phase; reset_move takes it back out
    return {'embarked': unit['id'], 'transport': transport['id']}


def disembark_mode(state, unit):
    """
    How an embarked unit disembarks now (core rules 18.04): Rapid if its transport made a Normal or ingress move,
    Combat if the transport is engaged, else Tactical. ActionError if it can't (embarked this phase, the transport
    Advanced, Fell Back or isn't on the battlefield).
    """
    transport = unit_by_id(state, unit['status']['embarked'])
    if not _placed_shapes(transport):
        raise ActionError(f'{transport["name"]} is not on the battlefield.')
    if unit['activity'].get('embarked'):
        raise ActionError(f'{unit["name"]} embarked this phase.')
    moved = transport['activity'].get('moved')
    if moved in ('advance', 'fall_back'):
        raise ActionError(f'{transport["name"]} {"Advanced" if moved == "advance" else "Fell Back"} this phase, so no unit can disembark.')
    if moved in ('normal', 'ingress'):
        return 'rapid'
    return 'combat' if _is_engaged(state, transport) else 'tactical'


def disembark_options(state):
    """For the page: the active player's embarked units that can disembark now: {unit id: {mode, reach, transport}}."""
    turn = state['turn']
    if turn['phase'] != 'movement':
        return {}
    options = {}
    for unit in state['units']:
        if unit['player'] != turn['player'] or not is_embarked(unit) or 'moved' in unit['activity']:
            continue
        try:
            mode = disembark_mode(state, unit)
        except ActionError:
            continue
        options[unit['id']] = {'mode': mode, 'reach': DISEMBARK_REACH[mode], 'transport': unit['status']['embarked']}
    return options


def embark_options(state):
    """For the page: units the active player could embark now, {unit id: [transport ids]}: in deployment, units not
    yet set up (transports with room); in the Movement phase, units that moved and stand within 3" of one."""
    turn = state['turn']
    transports = [u for u in state['units'] if u['player'] == turn['player'] and transport_info(u) is not None
                  and any(m['alive'] for m in u['models'])]
    if not transports or turn['phase'] not in ('deployment', 'movement'):
        return {}
    options = {}
    for unit in state['units']:
        if unit['player'] != turn['player'] or transport_info(unit) is not None or is_embarked(unit):
            continue
        if turn['phase'] == 'deployment':
            if unit not in _unplaced(state, turn['player']):
                continue
            fits = [t['id'] for t in transports if not transport_problem(state, t, embarked_in(state, t) + [unit])]
        else:
            if unit['activity'].get('moved') not in MOVE_MODES or unit['activity'].get('disembarked'):
                continue
            mine = _placed_shapes(unit)
            fits = [t['id'] for t in transports if _placed_shapes(t)
                    and all(min(base_distance(s, c) for c in _placed_shapes(t)) <= EMBARK_REACH + 1e-9 for s in mine)
                    and not transport_problem(state, t, embarked_in(state, t) + [unit])]
        if fits:
            options[unit['id']] = fits
    return options


def _wholly_within(shape, others, reach):
    """Whether every part of a base is within `reach` of one of `others` (measured to its far edge)."""
    centre = _centre(shape)
    return min(base_distance(shape, o) for o in others) + 2 * _reach(shape, centre) <= reach + 1e-9


def _disembark(state, action, dice):
    """
    A disembark move (core rules 18.04): every model set up wholly within 3" of its transport (6" for a Combat
    disembark), on the board, overlapping nothing, in coherency and unengaged (a Combat disembark may engage enemies
    its transport is engaged with). Rapid: no more moving or charging this turn. Tactical: the unit then makes its
    move from there as usual. Combat: each model makes a hazard roll, and the unit is battle-shocked and can't charge.
    """
    unit = _unit_or_error(state, action.get('unit'))
    _require(state, 'movement', unit)
    if not is_embarked(unit):
        raise ActionError(f'{unit["name"]} isn\'t embarked.')
    if 'moved' in unit['activity']:
        raise ActionError(f'{unit["name"]} has already moved this phase.')
    transport = unit_by_id(state, unit['status']['embarked'])
    mode = disembark_mode(state, unit)
    reach = DISEMBARK_REACH[mode]
    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = _spots(unit, action, 'Set up')
    shapes = [model_shape(unit['parts'][living[mid]['part']], spot) for mid, spot in spots.items()]
    carriers = _placed_shapes(transport)
    for shape in shapes:
        left, bottom, right, top = bounds(shape)
        if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
            raise ActionError('Every model must be wholly on the board.')
        if not _wholly_within(shape, carriers, reach):
            raise ActionError(f'{DISEMBARK_NAMES[mode]}: every model is set up wholly within {reach}" of {transport["name"]}.')
    others = [shape for u in state['units'] if u is not unit for shape in _placed_shapes(u)]
    for i, shape in enumerate(shapes):
        if any(overlaps(shape, other) for other in shapes[i + 1:] + others):
            raise ActionError("Models' bases can't overlap.")
    problem = coherency_problem(shapes)
    if problem:
        raise ActionError(problem)
    allowed = _engaged_units(state, transport) if mode == 'combat' else []
    near = [shape for u in state['units'] if u['player'] != unit['player'] and u not in allowed for shape in _placed_shapes(u)]
    if _engaged(shapes, near):
        raise ActionError(f'{DISEMBARK_NAMES[mode]}: models can\'t be set up in engagement range of enemies'
                          f'{" other than those engaged with " + transport["name"] if mode == "combat" else ""}.')
    for mid, spot in spots.items():
        living[mid]['position'] = spot
    unit['status'].pop('embarked', None)
    activity = unit['activity']
    activity['start'] = {mid: dict(spot) for mid, spot in spots.items()}  # A Tactical disembark's move starts here
    activity['disembarked'] = mode
    detail = {'disembarked': unit['id'], 'transport': transport['id'], 'mode': mode}
    if mode in ('rapid', 'combat'):
        activity['moved'] = 'disembark'
        activity['no_charge'] = mode
    if mode == 'combat':
        detail['hazards'] = [dict(_hazard(state, unit, len(living), dice), unit=unit['id'], name=unit['name'])]
        unit['status']['shocked'] = True
    return detail


# --- Destroyed models: emergency disembarks and Deadly Demise ----------------------------------------------------------

def _uncounted(detail):
    """Models an action removed without the enemy destroying them (coherency, lost in reserves, empty transports)."""
    return {m for key in ('coherency', 'reserves_lost', 'empty_transports') for c in detail.get(key, []) for m in c['removed']}


def _aftermath(state, before, dice, detail):
    """
    After any action: for each model destroyed in it, a destroyed transport's passengers make emergency disembarks
    first, then Deadly Demise X rolls a D6 (on a 6 each unit within 6" suffers X mortal wounds), repeated for what
    those destroy (core rules 18.05, 24.08). Recorded in `detail` ('emergency', 'demise').
    """
    skip, done = _uncounted(detail), set()
    while True:
        dead = [(u, m) for u in state['units'] for m in u['models']
                if before['models'][m['id']]['alive'] and not m['alive'] and m['id'] not in skip and m['id'] not in done]
        if not dead:
            return
        done.update(m['id'] for _, m in dead)
        for unit in {u['id']: u for u, _ in dead}.values():
            if transport_info(unit) is not None and not any(m['alive'] for m in unit['models']) and embarked_in(state, unit):
                _emergency(state, unit, before, dice, detail)
        for unit, model in dead:
            part = unit['parts'][model['part']]
            where = model['position'] or before['models'][model['id']]['position']
            if not part.get('deadly_demise') or not where:
                continue
            roll = dice.roll_value('D6', 'deadly demise')
            entry = {'unit': unit['id'], 'name': part['name'], 'roll': roll, 'hits': []}
            if roll == 6:
                blast = model_shape(part, where)
                for other in state['units']:
                    if any(base_distance(blast, s) <= DEMISE_REACH + 1e-9 for s in _placed_shapes(other)):
                        count = dice.roll_value(part['deadly_demise'], 'deadly demise')
                        entry['hits'].append({'unit': other['id'], 'name': other['name'], 'mortal_wounds': count,
                                              'killed': _mortal_wounds(state, other, count, dice)})
            detail.setdefault('demise', []).append(entry)


def _emergency(state, transport, before, dice, detail):
    """
    Emergency disembark (core rules 18.05): each unit in a destroyed transport makes a hazard roll per model, then its
    models are set up as close as possible to the transport (within 6"), unengaged if they can be; any that can't be
    set up are destroyed. The unit is battle-shocked and can't charge this turn.
    """
    hulls = [model_shape(transport['parts'][m['part']], m['position'] or before['models'][m['id']]['position'])
             for m in transport['models'] if m['position'] or before['models'][m['id']]['position']]
    for unit in embarked_in(state, transport):
        unit['status'].pop('embarked', None)
        hazard = _hazard(state, unit, sum(m['alive'] for m in unit['models']), dice)
        set_up, lost = [], []
        for model in [m for m in unit['models'] if m['alive']]:
            spot = _closest_spot(state, unit, unit['parts'][model['part']], hulls)
            if spot is None:
                model['alive'], model['wounds_left'] = False, 0
                lost.append(model['id'])
            else:
                model['position'] = spot
                set_up.append(model['id'])
        unit['status']['shocked'] = True
        unit['activity']['no_charge'] = 'emergency'
        detail.setdefault('emergency', []).append({'unit': unit['id'], 'name': unit['name'], 'transport': transport['id'],
                                                   'hazard': hazard, 'lost': lost, 'set_up': set_up})


def _closest_spot(state, unit, part, hulls):
    """The free spot nearest the destroyed transport (`hulls`) for a model, wholly within 6" of it and on the board,
    unengaged if possible; None if there is none."""
    others = [shape for u in state['units'] for shape in _placed_shapes(u)]
    enemies = _enemy_shapes(state, unit)
    size = model_shape(part, {'x': 0, 'y': 0, 'angle': 0})
    reach = _reach(size, (0, 0))
    candidates = []
    for hull in hulls:
        cx, cy = _centre(hull)
        around = _reach(hull, (cx, cy))
        ring = 0.05
        while ring <= EMERGENCY_REACH:
            radius = around + reach + ring
            steps = max(12, int(2 * math.pi * radius / (reach or 0.5)))
            for i in range(steps):
                t = 2 * math.pi * i / steps
                candidates.append((ring, {'x': round(cx + radius * math.cos(t), 2), 'y': round(cy + radius * math.sin(t), 2), 'angle': 0}))
            ring += max(0.5, reach)
    candidates.sort(key=lambda c: c[0])
    for unengaged in (True, False):
        for _, spot in candidates:
            shape = model_shape(part, spot)
            left, bottom, right, top = bounds(shape)
            if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
                continue
            if not _wholly_within(shape, hulls, EMERGENCY_REACH) or any(overlaps(shape, o) for o in others):
                continue
            if unengaged and _engaged([shape], enemies):
                continue
            return spot
    return None


def _with_deck(state, shooter):
    """
    Firing Deck X (core rules 24.14): a transport shooting with units embarked adds their ranged weapons (not One
    Shot) to its own, each as many times as up to X of their models carry it, fired by one of its models and marked
    'deck'. Changes `shooter`: a copy, or the shooting action's own state (parts aren't recorded in events).
    """
    deck = next((p.get('firing_deck') for p in shooter['parts'] if p.get('firing_deck')), None)
    passengers = [u for u in embarked_in(state, shooter) if not u['activity'].get('shot')]
    firers = [m for m in shooter['models'] if m['alive'] and m['position']]
    if not deck or not passengers or not firers:
        return shooter
    carried = {}
    for unit in passengers:
        for model in (m for m in unit['models'] if m['alive']):
            for index in model['weapons']:
                weapon = unit['parts'][model['part']]['weapons'][index]
                if weapon['type'] == 'ranged' and not _has(weapon['abilities'] or [], 'One Shot'):
                    key = json.dumps({k: weapon.get(k) for k in ('name', 'attacks', 'skill', 'strength', 'ap', 'damage', 'range')}, sort_keys=True)
                    carried.setdefault(key, [weapon, 0])[1] += 1
    firer = firers[0]
    host = shooter['parts'][firer['part']]
    for weapon, carriers in carried.values():
        for _ in range(min(deck, carriers)):
            firer['weapons'].append(len(host['weapons']))
            host['weapons'].append(dict(weapon, qty=1, default_use=False, deck=deck))
    return shooter


def _deck_picks(state, shooter, picks):
    """The Firing Deck weapons picked; refused if more than X, or more than the embarked models that could fire them."""
    chosen = [shooter['parts'][int(p['part'])]['weapons'][int(p['weapon'])] for p in picks
              if isinstance(p, dict) and 'part' in p and 'weapon' in p
              and int(p['part']) < len(shooter['parts']) and int(p['weapon']) < len(shooter['parts'][int(p['part'])]['weapons'])]
    deck = [w for w in chosen if w.get('deck')]
    if not deck:
        return []
    models = sum(1 for u in embarked_in(state, shooter) for m in u['models'] if m['alive'] and m['weapons'])
    if len(deck) > min(deck[0]['deck'], models):
        raise ActionError(f'Firing Deck {deck[0]["deck"]}: up to {min(deck[0]["deck"], models)} embarked models each fire one weapon.')
    return deck


# --- Charges --------------------------------------------------------------------------------------

def _unit_distance(shapes, others):
    return min((base_distance(a, b) for a in shapes for b in others), default=math.inf)


def _charge(state, action, dice):
    """Declare a charge: roll 2D6 for the most the unit can move; its targets are chosen when it moves."""
    unit = _unit_or_error(state, action.get('unit'))
    _charge_checks(state, unit)
    unit['activity']['charge_roll'] = dice.roll_value('2D6', 'charge')


def _charge_checks(state, unit):
    """Refuse a unit that can't declare a charge now."""
    _require(state, 'charge', unit)
    activity = unit['activity']
    if activity.get('moved') in ('advance', 'fall_back'):
        mode = 'Advanced' if activity['moved'] == 'advance' else 'Fell Back'
        raise ActionError(f'{unit["name"]} {mode} this turn, so it can\'t declare a charge.')
    if 'charge_roll' in activity:
        raise ActionError(f'{unit["name"]} has already declared a charge this phase.')
    if activity.get('no_charge'):
        raise ActionError(f'{unit["name"]} disembarked ({DISEMBARK_NAMES.get(activity["no_charge"], "emergency")}) this turn, '
                          'so it can\'t declare a charge.')
    if activity.get('action'):
        raise ActionError(f'{unit["name"]} started an action this turn, so it can\'t declare a charge.')
    if not _placed_shapes(unit):
        raise ActionError(f'{unit["name"]} is not on the battlefield.')
    if _is_engaged(state, unit):
        raise ActionError(f'{unit["name"]} is engaged, so it can\'t declare a charge.')
    mine = _placed_shapes(unit)
    if not any(_unit_distance(mine, _placed_shapes(u)) <= CHARGE_REACH + 1e-9
               for u in state['units'] if u['player'] != unit['player'] and _placed_shapes(u)):
        raise ActionError(f'{unit["name"]} has no enemy unit within {CHARGE_REACH}" to charge.')


def charge_options(state, unit_id):
    """
    For the page: why a unit can't declare a charge ('reason', None if it can or has), its charge roll,
    and once rolled, the enemy units it can charge: {id: {'ok': True, 'distance'}}.
    """
    unit = _unit_or_error(state, unit_id)
    roll = unit['activity'].get('charge_roll')
    reason = None
    if roll is None:
        try:
            _charge_checks(state, unit)
        except ActionError as error:
            reason = str(error)
    targets, allowed = {}, None
    if roll is not None:
        start = _start_shapes(unit, 'charge_start')
        allowed = [t['id'] for t in heroic_targets(state, unit, unit['activity']['heroic'], roll)] if unit['activity'].get('heroic') else None
        for other in state['units']:
            if other['player'] != unit['player'] and _placed_shapes(other) and (allowed is None or other['id'] in allowed):
                gap = _unit_distance(start, _placed_shapes(other))
                if gap <= min(CHARGE_REACH, roll) + 1e-9:
                    targets[other['id']] = {'ok': True, 'distance': round(gap, 2)}
    return {'reason': None if allowed is not None else reason, 'roll': roll, 'targets': targets}


def _charge_move(state, action, dice=None):
    """
    A charge move after the roll, at `targets`: enemy units within 12" and within the roll. Each model
    moves in a straight line up to the roll, not through enemy models, and each one that moves ends
    closer to a target; the unit ends engaged with every target and with no other enemy unit, wholly
    on the board, overlapping no model and in coherency. It can be redone until the phase ends; a Heroic
    Intervention's move can't (it ends the reaction window, and the phase moves on).
    """
    unit = _unit_or_error(state, action.get('unit'))
    window = state['turn'].get('window')
    heroic = bool(window and window['kind'] == 'heroic')
    if heroic:
        if unit['id'] != window.get('unit'):
            raise ActionError(f'Waiting for {state["players"][window["player"]]["name"]}: Heroic Intervention or pass.')
        activity = unit['activity']
    else:
        activity = _ready_to_move(state, unit, 'charge')
    roll = activity.get('charge_roll')
    if roll is None:
        raise ActionError('Roll to charge first.')
    start_shapes = _start_shapes(unit, 'charge_start')
    allowed = [t['id'] for t in heroic_targets(state, unit, window['mode'], roll)] if heroic else None
    targets = []
    for target_id in action.get('targets') or []:
        target = _unit_or_error(state, target_id)
        if target['player'] == unit['player'] or not _placed_shapes(target):
            raise ActionError('Charge targets must be enemy units on the battlefield.')
        if allowed is not None and target['id'] not in allowed:
            raise ActionError(f'{HEROIC_MODES[window["mode"]]} can\'t charge {target["name"]}: '
                              + ('only units that charged this phase.' if window['mode'] == 'leap' else f'only units within {FRAY_REACH}".'))
        gap = _unit_distance(start_shapes, _placed_shapes(target))
        if gap > min(CHARGE_REACH, roll) + 1e-9:
            raise ActionError(f'{target["name"]} is {gap:.1f}" away: charge targets must be within {CHARGE_REACH}" '
                              f'and within the charge roll ({roll}").')
        targets.append(target)
    if not targets:
        raise ActionError('Choose at least one enemy unit within the charge roll to charge.')

    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = _spots(unit, action, 'Move')
    skies = _skies(unit, action)
    enemies = _blocking(state, unit, through_all=skies)
    target_shapes = [shape for t in targets for shape in _placed_shapes(t)]
    shapes = {}
    for mid, spot in spots.items():
        model, part = living[mid], unit['parts'][living[mid]['part']]
        start = activity['charge_start'].get(mid) or model['position']
        allowance = roll - (SKIES if skies else 0)
        path = [start, *_route(action, mid, spot), spot]
        distance = path_length(path)
        climb = 0 if skies else _climb(state, part, path)
        _within_allowance(part, distance, climb, allowance, f'the charge roll{" less 2 for taking to the skies" if skies else ""}')
        _check_path(part, path, enemies, spot)
        shapes[mid] = model_shape(part, spot)
        if distance > 1e-6:
            before = min(base_distance(model_shape(part, start), t) for t in target_shapes)
            if min(base_distance(shapes[mid], t) for t in target_shapes) >= before - 1e-9:
                raise ActionError(f'Each model that moves must end closer to a charge target ({part["name"]} doesn\'t).')

    _check_end_of_move(state, unit, list(shapes.values()))
    placed = list(shapes.values())
    for target in targets:
        if not _engaged(placed, _placed_shapes(target)):
            raise ActionError(f'The unit must end engaged with every charge target ({target["name"]} isn\'t).')
    for other in state['units']:
        if other['player'] != unit['player'] and other not in targets and _engaged(placed, _placed_shapes(other)):
            raise ActionError(f'The unit can\'t end engaged with {other["name"]}: it is not a charge target.')
    others = [shape for u in state['units'] if u is not unit for shape in _placed_shapes(u)]
    for mid, spot in spots.items():  # Models that can end within 1" of a target, or engaged with one, must (11.04)
        part = unit['parts'][living[mid]['part']]
        start = activity['charge_start'].get(mid) or living[mid]['position']
        gap = min(base_distance(shapes[mid], t) for t in target_shapes)
        around = others + [shape for other, shape in shapes.items() if other != mid]
        allowance = roll - (SKIES if skies else 0)
        for need, what in ((1, 'within 1" of'), (ENGAGEMENT_RANGE, 'engaged with')):
            if gap > need + 1e-6 and _could_end_near(part, start, target_shapes, need, allowance, around, enemies, spot['angle']):
                raise ActionError(f'{part["name"]} can end {what} a charge target, so it must (it ends {gap:.1f}" away).')
            if gap <= need + 1e-6:
                break
    for mid, spot in spots.items():
        living[mid]['position'] = spot
    activity['charged'] = True
    activity['charge_targets'] = [t['id'] for t in targets]
    if heroic:  # The Heroic Intervention is made: on to the Fight phase
        return dict(_close_window(state, dice), heroic_move=unit['id'])


def _could_end_near(part, start, target_shapes, need, allowance, obstacles, blocking, angle):
    """
    Whether a model could end a charge move within `need` inches of a target model: a spot round one,
    within `allowance` of where it started in a straight line not through `blocking` models, wholly on
    the board and overlapping none of `obstacles`. Spots are tried every 15 degrees round each target
    model, so a gap the search misses never makes a move fail.
    """
    def extent(shape):
        cx = sum(x for x, _ in shape.points) / len(shape.points)
        cy = sum(y for _, y in shape.points) / len(shape.points)
        return (cx, cy), max(math.dist((cx, cy), pt) for pt in shape.points) + shape.radius
    _, mine = extent(model_shape(part, {'x': 0, 'y': 0, 'angle': angle}))
    for target in target_shapes:
        (cx, cy), theirs = extent(target)
        for step in range(24):
            a = math.radians(15 * step)
            for slack in (0.05, need / 2):
                spot = {'x': cx + math.cos(a) * (mine + theirs + slack), 'y': cy + math.sin(a) * (mine + theirs + slack), 'angle': angle}
                if math.dist(_xy(start), _xy(spot)) > allowance + 1e-6:
                    continue
                shape = model_shape(part, spot)
                left, bottom, right, top = bounds(shape)
                if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
                    continue
                if base_distance(shape, target) > need + 1e-6 or any(overlaps(shape, o) for o in obstacles):
                    continue
                steps = max(1, math.ceil(math.dist(_xy(start), _xy(spot)) / PATH_STEP))
                path = ({'x': start['x'] + i / steps * (spot['x'] - start['x']), 'y': start['y'] + i / steps * (spot['y'] - start['y']),
                         'angle': angle} for i in range(1, steps))
                if any(overlaps(model_shape(part, here), b) for here in path for b in blocking):
                    continue
                return True
    return False


def _check_end_of_move(state, unit, placed):
    """Every model wholly on the board, overlapping no other model, and the unit in coherency."""
    for shape in placed:
        left, bottom, right, top = bounds(shape)
        if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
            raise ActionError('Every model must end wholly on the board.')
    others = [shape for u in state['units'] if u is not unit for shape in _placed_shapes(u)]
    for i, shape in enumerate(placed):
        if any(overlaps(shape, other) for other in placed[i + 1:] + others):
            raise ActionError("Models' bases can't overlap.")
    problem = coherency_problem(placed)
    if problem:
        raise ActionError(problem)


def _move(state, action):
    """
    A Normal move, Advance or Fall Back: each model in a straight line from where it started the
    phase, up to its Move (+ the Advance roll), not through enemy models, ending wholly on the board,
    overlapping no model, in coherency and out of engagement range. Only a unit that started the
    phase engaged Falls Back, and it can do nothing else.

    With 'waypoint', the move carries on from where the unit stands, which becomes a waypoint (activity
    'via'): the whole path counts against its Move, and every leg is checked.
    """
    unit = _unit_or_error(state, action.get('unit'))
    activity = _board_mover(state, unit)
    mode = action.get('mode')
    if mode not in MOVE_MODES:
        raise ActionError('Choose a Normal move, Advance or Fall Back.')
    enemies = _enemy_shapes(state, unit)
    engaged = _engaged(_start_shapes(unit), enemies)  # Where it started the phase decides
    affliction = affliction_of(state, unit, _start_shapes(unit))  # Where it started too, so redoing it is the same
    skies = _skies(unit, action)
    desperate = mode == 'fall_back' and unit.get('status', {}).get('shocked')  # Desperate Escape: through enemy models
    blocking = _blocking(state, unit, through_all=skies or desperate)
    if mode == 'fall_back' and not engaged:
        raise ActionError('Only a unit in engagement range can Fall Back.')
    if mode != 'fall_back' and engaged:
        raise ActionError(f'{unit["name"]} is in engagement range: it can only Fall Back.')
    if mode == 'advance' and 'advance_roll' not in activity:
        raise ActionError('Roll to Advance first.')
    if mode != 'advance' and 'advance_roll' in activity:
        raise ActionError(f'{unit["name"]} rolled to Advance: it must make an Advance move.')

    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = _spots(unit, action, 'Move')
    shapes, via = {}, {}
    for mid, spot in spots.items():
        model, part = living[mid], unit['parts'][living[mid]['part']]
        start = activity['start'].get(mid) or model['position']
        via[mid] = []
        if action.get('waypoint'):  # Where the model stands becomes a waypoint (unless it hasn't moved)
            via[mid] = list((activity.get('via') or {}).get(mid, []))
            here = model['position']
            if _xy(here) not in (_xy(start), _xy(spot)) and not (via[mid] and _xy(via[mid][-1]) == _xy(here)):
                via[mid].append(dict(here))
        via[mid] += _route(action, mid, spot)  # The bends it was dragged along, kept as waypoints
        path = [start, *via[mid], spot]
        allowance = move_characteristic(part, affliction) + (activity.get('advance_roll', 0) if mode == 'advance' else 0) - (SKIES if skies else 0)
        distance = path_length(path)
        mobile = twist_is(state, 'ruinscape') and mode in ('normal', 'advance')  # Ruinscape: Mobile while moving
        climb = 0 if skies or mobile else _climb(state, part, path)
        _within_allowance(part, distance, climb, allowance, 'less 2 for taking to the skies' if skies else '')
        _check_path(part, path, blocking, spot)
        shapes[mid] = model_shape(part, spot)

    for shape in shapes.values():
        left, bottom, right, top = bounds(shape)
        if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
            raise ActionError('Every model must end wholly on the board.')
    others = [shape for u in state['units'] if u is not unit for shape in _placed_shapes(u)]
    placed = list(shapes.values())
    for i, shape in enumerate(placed):
        if any(overlaps(shape, other) for other in placed[i + 1:] + others):
            raise ActionError("Models' bases can't overlap.")
    if _engaged(placed, enemies):
        if mode == 'fall_back':
            raise ActionError(f'A unit that Falls Back must end out of engagement range ({ENGAGEMENT_RANGE}") of enemies.')
        raise ActionError(f'A {MOVE_MODES[mode]} can\'t end in engagement range ({ENGAGEMENT_RANGE}") of an enemy.')
    problem = coherency_problem(placed)
    if problem:
        raise ActionError(problem)
    for mid, spot in spots.items():
        living[mid]['position'] = spot
    activity['via'] = {mid: points for mid, points in via.items() if points}
    activity['moved'] = mode
    if mode == 'fall_back' and unit.get('status', {}).get('shocked'):
        activity['desperate'] = True  # Hazard rolls when the Movement phase ends
    else:
        activity.pop('desperate', None)


def _restore_coherency(state, report):
    """
    At the end of each turn, a unit out of coherency has models removed one at a time until it is back in it
    (core rules 03.03). Its player would choose; the game removes the model with the fewest others of its unit
    within 2", then the one farthest from the rest, then the last-listed.
    """
    for unit in state['units']:
        removed = []
        while True:
            placed = _placed_models(unit)
            if not coherency_problem([shape for _, shape in placed]):
                break
            def badness(item):
                model, shape = item
                others = [base_distance(shape, s) for m, s in placed if m is not model]
                return (-sum(d <= COHERENCY_RANGE + 1e-9 for d in others), sum(others), unit['models'].index(model))
            model, _ = max(placed, key=badness)
            model['alive'], model['wounds_left'] = False, 0
            removed.append(model['id'])
        if removed:
            report.setdefault('coherency', []).append({'unit': unit['id'], 'name': unit['name'], 'removed': removed})


def coherency_problem(shapes):
    """Why a unit's models (shapes) are out of coherency, or None: each must be within 2" of at least
    one other model of the unit, and within 9" of every other model of it."""
    if len(shapes) < 2:
        return None
    for i, shape in enumerate(shapes):
        gaps = [base_distance(shape, other) for j, other in enumerate(shapes) if j != i]
        if min(gaps) > COHERENCY_RANGE + 1e-9:
            return f'Every model must end within {COHERENCY_RANGE}" of another model in its unit (unit coherency).'
        if max(gaps) > COHERENCY_SPREAD + 1e-9:
            return f'Every model must end within {COHERENCY_SPREAD}" of every other model in its unit (unit coherency).'
    return None


# --- The Fight phase ---------------------------------------------------------------------------------

def _require_step(state, step, unit=None):
    """Refuse an action outside a Fight phase step (both players act in pile in and consolidate)."""
    turn = state['turn']
    if turn['phase'] != 'fight' or turn.get('step') != step:
        names = {'pile_in': 'Pile in', 'fight': 'Fight', 'consolidate': 'Consolidate'}
        raise ActionError(f'That can only be done in the Fight phase\'s {names[step]} step.')


def _melee_weapons(unit):
    return [(p, w) for p, part in enumerate(unit['parts']) for w, weapon in enumerate(part['weapons']) if weapon['type'] == 'melee']


def _can_fight(state, unit):
    """
    Whether a unit can be picked to fight now (core rules 12.04): not fought yet, with melee weapons, and
    engaged, or engaged when the Fight step began, or it charged this turn. One that isn't engaged makes
    an overrun fight (_overrun).
    """
    activity = unit['activity']
    if activity.get('fought') or not _placed_shapes(unit):
        return False
    alive = {(m['part'], w) for m in unit['models'] if m['alive'] for w in m['weapons']}
    return bool(alive & set(_melee_weapons(unit))) and (
        activity.get('charged') or activity.get('engaged_at_fight') or _is_engaged(state, unit))


def _overrun_targets(state, unit, key='overrun_start'):
    """Enemy units within 5" of where the unit began its overrun pile-in (where it is, if it hasn't)."""
    start = _start_shapes(unit, key)
    return [u for u in state['units'] if u['player'] != unit['player'] and _placed_shapes(u)
            and _unit_distance(start, _placed_shapes(u)) <= PILE_IN_REACH + 1e-9]


def _overrun(state, action):
    """
    An overrun fight's pile-in (core rules 12.06), when it's the unit's pick: an eligible unit that isn't
    engaged moves up to 3" towards enemy units within 5", ending engaged; then it fights (_fight). It
    can be redone until the unit fights.
    """
    unit = _unit_or_error(state, action.get('unit'))
    _fight_checks(state, unit)
    if _is_engaged(state, unit) and 'overrun_start' not in unit['activity']:
        raise ActionError(f'{unit["name"]} is engaged: it fights where it is.')
    targets = _overrun_targets(state, unit)
    if not targets:
        raise ActionError(f'{unit["name"]} has no enemy unit within {PILE_IN_REACH}" to pile in towards.')

    def finish(placed):
        if not _engaged(placed, _enemy_shapes(state, unit)):
            raise ActionError(f'{unit["name"]} must end its overrun pile-in engaged with an enemy unit.')
    _short_move(state, unit, action, 'overrun_start', PILE_IN_MOVE, 'pile in', _shapes_of(targets), 'the closest enemy', finish)


def _fights_first(unit):
    """A unit that charged this turn, or whose models all have Fights First (core rules 24.13): every part
    of it, leaders included, has the ability on its datasheet (frozen with the army)."""
    if unit['activity'].get('charged'):
        return True
    living = {m['part'] for m in unit['models'] if m['alive']}
    return bool(living) and all(unit['parts'][p].get('fights_first') for p in living)


def _settle_picker(state):
    """
    Whose pick it is in the Fight step: Fights First units first, players alternating from the
    active player; when none is left, the remaining units, alternating from the player who would have
    picked next. A player with nothing to pick is skipped; with nothing left, the step is done.
    """
    fight = state['turn']['fight']
    while True:
        if fight['stage'] == 'done':
            fight['picker'] = None
            return
        first = fight['stage'] == 'fights_first'
        ready = [u for u in state['units'] if _can_fight(state, u) and (_fights_first(u) or not first)]
        if not ready:
            fight['stage'] = 'remaining' if first else 'done'
            continue
        if not any(u['player'] == fight['picker'] for u in ready):
            fight['picker'] = 1 - fight['picker']
        return


def _fight(state, action, dice):
    """
    Fight with a unit, when it's its player's pick: each part of it uses one melee weapon (and every
    Extra Attacks weapon) against an enemy unit it is engaged with; only models engaged with the
    target attack. Lance counts if the unit charged this turn. Wounds go by the target's standing
    orders; Hazardous weapons roll afterwards.
    """
    unit = _unit_or_error(state, action.get('unit'))
    _fight_checks(state, unit)
    fight = state['turn']['fight']
    engaged_with = _engaged_units(state, unit)
    if not engaged_with:  # An overrun fight: its pile-in first, unless no enemy is in reach (then it passes)
        if _overrun_targets(state, unit):
            raise ActionError(f'{unit["name"]} isn\'t engaged: make its overrun pile-in first (drag it up to {PILE_IN_MOVE}" '
                              f'to engage an enemy unit within {PILE_IN_REACH}").')
        unit['activity']['fought'] = True
        fight['picker'] = 1 - unit['player']
        _settle_picker(state)
        return {'attacks': [], 'passed': unit['id']}
    default_target = action.get('target') or engaged_with[0]['id']
    picks = action.get('weapons')
    explicit = picks is not None
    if not explicit:  # The first ticked melee weapon of each part
        picks = [{'part': p, 'weapon': w} for p, w in _default_melee(unit)]
    return _fight_with(state, unit, picks, explicit, default_target, engaged_with, fight, dice)


def _default_melee(unit):
    """(part, weapon) for the first ticked plain melee weapon of each part (not Extra Attacks ones)."""
    out = []
    for p, part in enumerate(unit['parts']):
        plain = [w for pp, w in _melee_weapons(unit) if pp == p and not _has(part['weapons'][w]['abilities'], 'Extra Attacks')]
        ticked = [w for w in plain if part['weapons'][w]['default_use']] or plain
        if ticked:
            out.append((p, ticked[0]))
    return out


def _fight_checks(state, unit):
    """Refuse a unit that can't be picked to fight now."""
    _require_step(state, 'fight')
    fight = state['turn']['fight']
    if unit['activity'].get('fought'):
        raise ActionError(f'{unit["name"]} has already fought this phase.')
    if fight['picker'] is None:
        raise ActionError('Every unit that can fight has fought.')
    if unit['player'] != fight['picker']:
        raise ActionError(f'It is {state["players"][fight["picker"]]["name"]}\'s pick.')
    if not _can_fight(state, unit):
        raise ActionError(f'{unit["name"]} isn\'t engaged and didn\'t charge this turn, so it can\'t fight.')
    if fight['stage'] == 'fights_first' and not _fights_first(unit):
        raise ActionError('Only Fights First units (those that charged this turn, or with the ability) fight now.')


def fight_options(state, unit_id):
    """
    For the page: why a unit can't fight now ('reason', None if it can), the enemy units it is engaged
    with ('targets'), whether it must make its overrun pile-in first ('overrun'), and each part's melee weapons ('parts': {'part', 'name', 'choice' (the default
    plain weapon), 'weapons': [{'weapon', 'name', 'extra' (Extra Attacks: used as well)}]}).
    """
    unit = _unit_or_error(state, unit_id)
    try:
        _fight_checks(state, unit)
        reason = None
    except ActionError as error:
        reason = str(error)
    defaults = dict(_default_melee(unit))
    parts = []
    for p, part in enumerate(unit['parts']):
        weapons = [{'weapon': w, 'name': part['weapons'][w]['name'], 'extra': _has(part['weapons'][w]['abilities'], 'Extra Attacks')}
                   for pp, w in _melee_weapons(unit) if pp == p]
        if weapons:
            parts.append({'part': p, 'name': part['name'], 'choice': defaults.get(p), 'weapons': weapons})
    targets = [u['id'] for u in _engaged_units(state, unit)]
    return {'reason': reason, 'targets': targets, 'overrun': not reason and not targets and bool(_overrun_targets(state, unit)),
            'parts': parts}


def _fight_with(state, unit, picks, explicit, default_target, engaged_with, fight, dice):
    """Roll a unit's fight (see _fight) with the weapons picked."""
    chosen = []
    for pick in picks:
        try:
            p, w = int(pick['part']), int(pick['weapon'])
            weapon = unit['parts'][p]['weapons'][w]
        except (KeyError, TypeError, ValueError, IndexError):
            raise ActionError('Choose weapons the unit has.')
        if weapon['type'] != 'melee':
            raise ActionError(f'{weapon["name"]} is not a melee weapon.')
        if _has(weapon['abilities'], 'Extra Attacks'):
            continue  # Used anyway, below
        if any(c[0] == p for c in chosen):
            raise ActionError(f'Each model fights with one melee weapon (plus any Extra Attacks weapons): '
                              f'choose one for {unit["parts"][p]["name"]}.')
        chosen.append((p, w, pick.get('target') or default_target))
    for p, w in _melee_weapons(unit):  # Extra Attacks weapons, in addition
        if _has(unit['parts'][p]['weapons'][w]['abilities'], 'Extra Attacks'):
            target = next((c[2] for c in chosen if c[0] == p), default_target)
            chosen.append((p, w, target))

    plans = []
    for p, w, target_id in chosen:
        target = _unit_or_error(state, target_id)
        if target not in engaged_with:
            raise ActionError(f'{target["name"]} isn\'t engaged with {unit["name"]}.')
        target_shapes = _placed_shapes(target)
        models = [m for m, shape in _placed_models(unit) if m['part'] == p and w in m['weapons']
                  and any(base_distance(shape, t) <= ENGAGEMENT_RANGE + 1e-9 for t in target_shapes)]
        if not models:  # No model with it is engaged with the target: it doesn't attack
            continue
        for damaged in (False, True):  # Damaged models (24.39) roll on their own
            group = [m for m in models if _damaged(unit, m) == damaged]
            if group:
                plans.append((p, w, target, group, damaged))
    if not plans:
        raise ActionError(f'No model of {unit["name"]} is engaged with its target.')

    attacks, hazardous = [], []
    for p, w, target, models, damaged in plans:
        conditions = BattleConditions(charged=bool(unit['activity'].get('charged')), damaged=damaged)
        if not any(m['alive'] for m in target['models']):
            continue
        weapon = unit['parts'][p]['weapons'][w]
        pair = (affliction_of(state, unit), affliction_of(state, target))
        attacker = _attacker(unit, p, w, len(models), pair)
        first = len(dice.log)
        pool = roll_attack(attacker, _defender(target, _first_alive(target)['part'], 'melee', pair), conditions, dice)
        killed = _resolve_wounds(state, unit, target, attacker, conditions, pool, dice, pair)
        attacks.append({'part': p, 'weapon': w, 'name': weapon['name'], 'target': target['id'], 'models': len(models),
                        'half_range': False, **asdict(pool), 'killed': killed, 'rolls': [first, len(dice.log)]})
        if _has(weapon['abilities'], 'Hazardous'):
            hazardous += models
    if hazardous:  # A hazard roll for each Hazardous weapon used
        first = len(dice.log)
        killed = _hazard(state, unit, len(hazardous), dice)['killed']
        attacks.append({'name': 'Hazardous', 'target': unit['id'], 'models': len(hazardous),
                        'killed': killed, 'rolls': [first, len(dice.log)]})
    unit['activity']['fought'] = True
    fight['picker'] = 1 - unit['player']
    _settle_picker(state)
    _save_window(state, attacks, dice)
    return {'attacks': attacks}


def _pile_in(state, action):
    """A pile-in move (both players, in the Pile in step): up to 3" towards the enemy, ending engaged."""
    unit = _unit_or_error(state, action.get('unit'))
    if state['turn']['phase'] != 'fight' or state['turn'].get('step') != 'pile_in':
        raise ActionError('Units only pile in during the Fight phase\'s Pile in step.')
    _require_mover(state, unit, 'piles in')
    activity = unit['activity']
    start = _start_shapes(unit, 'pile_start')
    engaged = [u for u in state['units'] if u['player'] != unit['player'] and _engaged(start, _placed_shapes(u))]
    if not engaged and not activity.get('charged'):
        raise ActionError(f'{unit["name"]} can\'t pile in: it isn\'t engaged and didn\'t charge this turn.')
    targets = engaged or [u for u in state['units'] if u['player'] != unit['player'] and _placed_shapes(u)
                          and _unit_distance(start, _placed_shapes(u)) <= PILE_IN_REACH + 1e-9]

    def finish(placed):
        if not _engaged(placed, _enemy_shapes(state, unit)):
            raise ActionError(f'{unit["name"]} must end its pile in move engaged with an enemy unit.')
    _short_move(state, unit, action, 'pile_start', PILE_IN_MOVE, 'pile in', _shapes_of(targets), 'the closest enemy', finish)


def _consolidate(state, action):
    """
    A consolidation move (both players, in the Consolidate step) by a unit that fought: up to 3", in the
    first of these modes that applies (core rules 12.08). Ongoing: engaged, it ends engaged with every
    enemy unit it was engaged with. Engaging: with enemy units within 3", it ends engaged with at least
    one of them. Objective: with an objective within 3" (`objective`, else the closest), it ends within
    range of it. With none of these, it can't consolidate.
    """
    unit = _unit_or_error(state, action.get('unit'))
    if state['turn']['phase'] != 'fight' or state['turn'].get('step') != 'consolidate':
        raise ActionError('Units only consolidate during the Fight phase\'s Consolidate step.')
    _require_mover(state, unit, 'consolidates')
    if not unit['activity'].get('fought'):
        raise ActionError(f'Only units that fought this phase consolidate ({unit["name"]} didn\'t).')
    start = _start_shapes(unit, 'consolidate_start')
    enemies = [u for u in state['units'] if u['player'] != unit['player'] and _placed_shapes(u)]
    engaged = [u for u in enemies if _engaged(start, _placed_shapes(u))]
    near = [u for u in enemies if _unit_distance(start, _placed_shapes(u)) <= CONSOLIDATE_MOVE + 1e-9]
    if engaged:  # Ongoing
        def finish(placed):
            for other in engaged:
                if not _engaged(placed, _placed_shapes(other)):
                    raise ActionError(f'{unit["name"]} must end its consolidation still engaged with {other["name"]}.')
        _short_move(state, unit, action, 'consolidate_start', CONSOLIDATE_MOVE, 'consolidate', _shapes_of(engaged), 'the closest enemy', finish)
    elif near:  # Engaging
        def finish(placed):
            if not any(_engaged(placed, _placed_shapes(other)) for other in near):
                raise ActionError(f'{unit["name"]} must end its consolidation engaged with an enemy unit within {CONSOLIDATE_MOVE}".')
        _short_move(state, unit, action, 'consolidate_start', CONSOLIDATE_MOVE, 'consolidate', _shapes_of(near), 'the closest enemy', finish)
    else:  # Objective
        objectives = [(min(base_distance(s, objective_shape(o)) for s in start), o) for o in state.get('objectives', [])]
        objectives = [(d, o) for d, o in objectives if d <= CONSOLIDATE_MOVE + 1e-9]
        if not objectives:
            raise ActionError(f'{unit["name"]} has nothing to consolidate towards: no enemy unit or objective within {CONSOLIDATE_MOVE}".')
        chosen = next((o for _, o in objectives if o['id'] == action.get('objective')), min(objectives, key=lambda pair: pair[0])[1])

        def finish(placed):
            if not any(in_range(shape, chosen, state) for shape in placed):
                raise ActionError(f'{unit["name"]} must end its consolidation within range of objective {chosen["id"]}.')
        _short_move(state, unit, action, 'consolidate_start', CONSOLIDATE_MOVE, 'consolidate', [objective_shape(chosen)],
                    f'objective {chosen["id"]}', finish)


def _shapes_of(units):
    return [shape for u in units for shape in _placed_shapes(u)]


def _short_move(state, unit, action, key, limit, verb, target_shapes, toward, finish):
    """
    Pile in or consolidate: each model up to `limit` inches in a straight line from where it started
    the step, not through enemy models; models in base contact with an enemy stay put; each model that
    moves ends closer to the closest of `target_shapes` (`toward` says what they are). The unit ends in
    coherency, and `finish(shapes)` checks the rest. It can be redone until the step ends.
    """
    activity = unit['activity']
    if key not in activity:
        activity[key] = {m['id']: m['position'] for m in unit['models'] if m['alive']}
    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = _spots(unit, action, verb.capitalize())
    enemies = _enemy_shapes(state, unit)
    blocking = _blocking(state, unit)
    shapes = {}
    for mid, spot in spots.items():
        part = unit['parts'][living[mid]['part']]
        start = activity[key].get(mid) or living[mid]['position']
        distance = math.dist((start['x'], start['y']), (spot['x'], spot['y']))
        shapes[mid] = model_shape(part, spot)
        if distance <= 1e-6:
            continue
        start_shape = model_shape(part, start)
        if any(base_distance(start_shape, e) <= 1e-6 for e in enemies):
            raise ActionError(f'{part["name"]} is in base contact with an enemy model, so it can\'t {verb}.')
        climb = _climb(state, part, [start, spot])
        if distance + climb > limit + 1e-6:
            raise ActionError(f'{part["name"]} can {verb} at most {limit}" ({distance:.1f}" asked'
                              + (f', plus {climb:.1f}" climbing Dense terrain' if climb else '') + ').')
        steps = max(1, math.ceil(distance / PATH_STEP))
        for i in range(1, steps):
            t = i / steps
            here = {'x': start['x'] + t * (spot['x'] - start['x']), 'y': start['y'] + t * (spot['y'] - start['y']),
                    'angle': spot['angle']}
            if any(overlaps(model_shape(part, here), enemy) for enemy in blocking):
                raise ActionError(f'{part["name"]} can\'t move through enemy models.')
        if target_shapes:
            before = min(base_distance(start_shape, t) for t in target_shapes)
            if min(base_distance(shapes[mid], t) for t in target_shapes) >= before - 1e-9:
                raise ActionError(f'Each model that moves must end closer to {toward} ({part["name"]} doesn\'t).')
    placed = list(shapes.values())
    _check_end_of_move(state, unit, placed)
    finish(placed)
    for mid, spot in spots.items():
        living[mid]['position'] = spot


# --- Correcting the game ---------------------------------------------------------------------------------

def _correction(state, action):
    """
    Propose a correction (either player, any time): models' wounds, whether they're alive and where
    they are, players' CP, and VP to add to or take from a player's score. Nothing else can happen until
    the other player accepts or declines it.

    Or, with 'undo' ({'event': its index, 'note'}), ask to take back an action (Undo in a game against someone on
    another PC); the routes make this one, for the player's own last action.
    """
    try:
        player = int(action.get('player'))
        state['players'][player]
    except (TypeError, ValueError, IndexError):
        raise ActionError('Say who proposes the correction.')
    undo = action.get('undo')
    if undo is not None:
        if not isinstance(undo, dict) or not isinstance(undo.get('event'), int) or isinstance(undo['event'], bool) or undo['event'] < 0:
            raise ActionError('Say which action to take back.')
        state['pending'] = {'player': player, 'note': str(undo.get('note') or 'Take back the last action')[:300],
                            'models': {}, 'cp': {}, 'undo': {'event': undo['event']}}
        return {}
    models = {m['id']: (u, m) for u in state['units'] for m in u['models']}
    changes = {}
    for model_id, change in (action.get('models') or {}).items():
        if model_id not in models or not isinstance(change, dict):
            raise ActionError(f'There is no model {model_id} in this game.')
        unit, model = models[model_id]
        clean = {}
        if 'wounds_left' in change:
            full = unit['parts'][model['part']]['stats']['wounds']
            try:
                wounds = int(change['wounds_left'])
            except (TypeError, ValueError):
                raise ActionError('Wounds must be a number.')
            if not 0 <= wounds <= full:
                raise ActionError(f'{unit["name"]} models have {full} wounds at most.')
            clean['wounds_left'] = wounds
        if 'alive' in change:
            clean['alive'] = bool(change['alive'])
        if 'position' in change:
            pos = change['position']
            clean['position'] = None if pos is None else {'x': float(pos['x']), 'y': float(pos['y']),
                                                          'angle': float(pos.get('angle') or 0)}
        if clean:
            changes[model_id] = clean
    cp = {}
    for key, value in (action.get('cp') or {}).items():
        if str(key) not in ('0', '1') or not isinstance(value, int) or value < 0:
            raise ActionError('Command points are a whole number for player 0 or 1.')
        cp[str(key)] = value
    vp = {}
    for key, value in (action.get('vp') or {}).items():
        if str(key) not in ('0', '1') or not isinstance(value, int) or isinstance(value, bool):
            raise ActionError('VP are a whole number (more or less) for player 0 or 1.')
        if value:
            vp[str(key)] = value
    if not changes and not cp and not vp:
        raise ActionError('A correction must change something.')
    state['pending'] = {'player': player, 'note': str(action.get('note') or '')[:300], 'models': changes, 'cp': cp,
                        **({'vp': vp} if vp else {})}


def _answer(state, action, accept, rewind=None):
    """
    The other player accepts (it's applied) or declines (it's dropped) the pending correction. Accepting an Undo
    request puts the game back as it was before that action (`rewind`, which the routes replay); the event
    records the difference, so the log keeps the action and shows it taken back.
    """
    pending = state.get('pending')
    if not pending:
        raise ActionError('There is no correction to answer.')
    if action.get('player') != 1 - pending['player']:
        raise ActionError('Only the other player can accept or decline a correction.')
    state['pending'] = None
    if not accept:
        return {'declined': pending['note']}
    if pending.get('undo'):
        if rewind is None:
            raise ActionError('The game before that action is needed to take it back.')
        state.clear()
        state.update(copy.deepcopy(rewind))
        state['pending'] = None
        return {'corrected': pending['note'], 'undone': pending['undo']['event']}
    for unit in state['units']:
        for model in unit['models']:
            change = pending['models'].get(model['id'])
            if change:
                model.update(copy.deepcopy(change))
                if change.get('alive') is False:
                    model['wounds_left'] = 0
                elif change.get('alive') and model['wounds_left'] <= 0:
                    model['wounds_left'] = unit['parts'][model['part']]['stats']['wounds']
    for key, value in pending['cp'].items():
        state['players'][int(key)]['cp'] = value
    for key, value in (pending.get('vp') or {}).items():
        vp = state['players'][int(key)].setdefault('vp', mission_state.new_vp())
        vp['adjust'] = vp.get('adjust', 0) + value
    return {'corrected': pending['note']}


# --- Which units can still act -------------------------------------------------------------------------------

def ready_units(state):
    """
    For the page: the units that can still act in the current phase (or Fight step), in order. Units
    that already moved, shot, charged or fought this phase aren't listed, nor units that can't (no
    target in range, nothing to charge, not engaged).
    """
    turn = state['turn']
    phase, step, active = turn['phase'], turn.get('step'), turn['player']
    on_board = [u for u in state['units'] if any(m['alive'] for m in u['models'])]
    mine = [u for u in on_board if u['player'] == active]

    def quietly(check, unit):
        try:
            return check(unit)
        except ActionError:
            return False

    if phase == 'deployment':
        return [u['id'] for u in _unplaced(state, active)]
    if phase == 'prebattle':
        return scout_units(state, active)
    if phase == 'movement':
        return [u['id'] for u in mine if 'moved' not in u['activity']
                and (_placed_shapes(u) or (in_reserve(u) and turn['round'] >= INGRESS_FROM) or u['id'] in disembark_options(state))]
    if phase == 'shooting':
        def can_shoot(unit):
            try:
                _shooter_checks(state, unit)
            except ActionError:
                return False
            options = shooting_options(state, unit['id'], first_only=True)
            return options['reason'] is None and any(t['ok'] for w in options['weapons'] for t in w['targets'].values())
        return [u['id'] for u in mine if can_shoot(u)]
    if phase == 'charge':
        def can_charge(unit):
            if unit['activity'].get('charged'):
                return False
            if 'charge_roll' in unit['activity']:
                return bool(charge_options(state, unit['id'])['targets'])
            _charge_checks(state, unit)
            return True
        return [u['id'] for u in mine if quietly(can_charge, u)]
    if phase == 'fight' and step in ('pile_in', 'consolidate'):
        return [u['id'] for u in _short_movers(state, step) if u['player'] == mover(turn)]
    if phase == 'fight' and step == 'fight':
        fight = turn.get('fight') or {}
        if fight.get('picker') is None:
            return []
        first = fight['stage'] == 'fights_first'
        return [u['id'] for u in on_board if u['player'] == fight['picker'] and _can_fight(state, u)
                and (_fights_first(u) or not first)]
    return []


def mover(turn):
    """Whose units pile in or consolidate now: the active player's, then (Next step) the other's (core rules 12.03, 12.08)."""
    return turn.get('mover', turn['player'])


def _short_movers(state, step):
    """Both players' units that can still pile in (step 'pile_in') or consolidate ('consolidate') this step."""
    on_board = [u for u in state['units'] if any(m['alive'] for m in u['models']) and _placed_shapes(u)]
    if step == 'pile_in':
        return [u for u in on_board if 'pile_start' not in u['activity']
                and (_is_engaged(state, u) or u['activity'].get('charged'))]

    def can_consolidate(unit):
        enemies = [e for e in on_board if e['player'] != unit['player']]
        mine_shapes = _placed_shapes(unit)
        return (any(_unit_distance(mine_shapes, _placed_shapes(e)) <= CONSOLIDATE_MOVE + 1e-9 for e in enemies)
                or any(base_distance(s, objective_shape(o)) <= CONSOLIDATE_MOVE + 1e-9  # An Objective consolidation
                       for o in state.get('objectives', []) for s in mine_shapes))
    return [u for u in on_board if u['activity'].get('fought') and 'consolidate_start' not in u['activity'] and can_consolidate(u)]


def _require_mover(state, unit, verb):
    """Refuse a pile-in or consolidation by the player who isn't moving now."""
    turn = state['turn']
    if unit['player'] != mover(turn):
        name = state['players'][mover(turn)]['name']
        if mover(turn) == turn['player']:
            raise ActionError(f'{name} {verb} first: {state["players"][unit["player"]]["name"]} moves after Next step.')
        raise ActionError(f'{name} {verb} now: {state["players"][unit["player"]]["name"]}\'s moves are done.')


# --- Objectives ----------------------------------------------------------------------------------------------

def objective_shape(objective):
    return base_shape({'length_mm': MARKER_MM, 'width_mm': MARKER_MM}, objective['x'], objective['y'])


def in_range(model_shape_, objective, state=None):
    """Whether a model (its base) is within range of an objective: 3" from a marker's edge, or within the
    terrain area of a terrain objective."""
    if objective.get('terrain') and state is not None:
        area = next((a for piece, a in terrain_areas(state) if piece['id'] == objective['terrain']), None)
        if area is not None:
            return within(model_shape_, area)
    marker = objective_shape(objective)
    if farther_than(model_shape_, marker, OBJECTIVE_RANGE + 1e-9):
        return False
    return base_distance(model_shape_, marker) <= OBJECTIVE_RANGE + 1e-9


def objective_control(state, objective):
    """[player 0's, player 1's] Objective Control within range of an objective (battle-shocked units add nothing)."""
    totals = [0, 0]
    afflicted = afflictions(state)  # Scabrous Soulrot worsens OC
    for unit in state['units']:
        if unit.get('status', {}).get('shocked'):
            continue
        for model, shape in _placed_models(unit):
            if in_range(shape, objective, state):
                totals[unit['player']] += contagion.worsen_oc(unit['parts'][model['part']]['stats'].get('oc') or 0,
                                                              afflicted.get(unit['id']))
    return totals


def objectives_in_range(state, unit):
    """The ids of the objectives a unit has a model within range of."""
    shapes = _placed_shapes(unit)
    return [o['id'] for o in state.get('objectives', []) if any(in_range(s, o, state) for s in shapes)]


def update_control(state):
    """Settle who controls each objective: the player with more OC in range; a tie is nobody's."""
    for objective in state.get('objectives', []):
        red, blue = objective_control(state, objective)
        objective['controller'] = 0 if red > blue else 1 if blue > red else None


# --- Terrain (11th edition, ground level) ---------------------------------------------------------------------

def terrain_areas(state):
    """[(piece, shape)] for the battlefield's terrain: each piece one of the 5 footprints, with its category
    ('exposed', 'light' or 'dense') and height (inches)."""
    pieces = (state.get('battlefield') or {}).get('terrain') or []
    return [(p, _terrain_shape(p['footprint'], p['x'], p['y'], p.get('angle') or 0, p.get('flip', False))) for p in pieces]


@functools.lru_cache(maxsize=512)
def _terrain_shape(footprint, x, y, angle, flip):
    """A terrain piece's area, worked out once: range and visibility checks ask for it again and again."""
    return footprint_shape(footprint, x, y, angle, flip)


def within(shape, area):
    """Whether a model (any part of its base) is within a terrain area."""
    if farther_than(shape, area, 1e-9):
        return False
    return overlaps(shape, area) or base_distance(shape, area) <= 1e-9


def _keyword(part, names):
    """Whether a part has one of these keywords exactly (any case): 'Beast Snagga' isn't 'Beast'."""
    names = {n.lower() for n in names}
    return any(k.lower() in names for k in part['keywords'] or [])


def _view_blockers(state, a, b, categories=('light', 'dense')):
    """Obscuring areas between two models: those of `categories` that neither model is within."""
    return [area for piece, area in terrain_areas(state)
            if piece['category'] in categories and not within(a, area) and not within(b, area)]


def fully_visible(a, b, blockers):
    """Whether every part of model `b` can be seen from model `a`'s centre past `blockers`."""
    blockers = between(a, b, blockers)
    if not blockers:
        return True
    eye = _centre(a)
    return all(clear_line(eye, point, blockers) for point in battlefields.outline_points(b) + [_centre(b)])


def _centre(shape):
    return (sum(x for x, _ in shape.points) / len(shape.points), sum(y for _, y in shape.points) / len(shape.points))


def sees(state, a, b, unit_b, model_b):
    """
    (visible, why not) for a model `a` looking at `model_b` of `unit_b`: a line not crossing obscuring
    terrain (light or dense areas neither model is in), and, if the target is hidden (Infantry, Beasts or
    Swarm within a light or dense area, its unit not having shot this turn or last), within 15" (12" if it
    has gone to ground: not fully visible past dense terrain).
    """
    if twist_is(state, 'night-fighting') and base_distance(a, b) > NIGHT_VISIBILITY + 1e-9:
        return False, 'night'
    if not can_see(a, b, _view_blockers(state, a, b)):
        return False, 'sight'
    part = unit_b['parts'][model_b['part']]
    in_cover = any(within(b, area) for piece, area in terrain_areas(state) if piece['category'] in ('light', 'dense'))
    shot = (unit_b.get('status') or {}).get('shot_seq')
    recently_shot = shot is not None and state['turn'].get('seq', 0) - shot <= 1
    if _keyword(part, SNEAKY) and in_cover and not recently_shot:
        detection = DETECTION_RANGE
        if not fully_visible(a, b, _view_blockers(state, a, b, ('dense',))):
            detection -= GONE_TO_GROUND
        if base_distance(a, b) > detection + 1e-9:
            return False, 'hidden'
    return True, None


def has_cover(state, a, unit):
    """Whether a unit has the benefit of cover against a ranged attack from model `a`: every model is
    Infantry, Beasts or Swarm within a terrain area, or not fully visible past terrain."""
    areas = [area for _, area in terrain_areas(state)]
    for model, shape in _placed_models(unit):
        part = unit['parts'][model['part']]
        if _keyword(part, SNEAKY) and any(within(shape, area) for area in areas):
            continue
        if not fully_visible(a, shape, _view_blockers(state, a, shape)):
            continue
        return False
    return bool(_placed_models(unit))


def _climb(state, part, path):
    """
    How far a model climbs along a path (positions), to add to the distance it moves (core rules 13.06):
    models that aren't Infantry, Beasts, Swarm or Mobile cross Dense terrain over 2" high by climbing it, up
    and down. On this flat board each such area the path goes into or out of is one climb: twice its height.
    """
    if _keyword(part, SNEAKY + ('Mobile',)):
        return 0
    tall = [(piece, area) for piece, area in terrain_areas(state)
            if piece['category'] == 'dense' and (piece.get('height') or 0) > DENSE_CLIMB]
    if not tall:
        return 0
    crossed = set()
    for a, b in zip(path, path[1:]):
        steps = max(1, math.ceil(math.dist(_xy(a), _xy(b)) / PATH_STEP))
        for i in range(steps + 1):
            t = i / steps
            here = model_shape(part, {'x': a['x'] + t * (b['x'] - a['x']), 'y': a['y'] + t * (b['y'] - a['y']), 'angle': b.get('angle', 0)})
            for piece, area in tall:
                if piece['id'] not in crossed and overlaps(here, area) != overlaps(model_shape(part, path[0]), area):
                    crossed.add(piece['id'])
    return sum(2 * piece['height'] for piece, _ in tall if piece['id'] in crossed)


def _within_allowance(part, distance, climb, allowance, why=''):
    """Refuse a move longer than its allowance, counting the climbing."""
    if distance + climb > allowance + 1e-6:
        asked = f'{distance:.1f}" asked' + (f', plus {climb:.1f}" climbing Dense terrain' if climb else '')
        raise ActionError(f'{part["name"]} can move at most {allowance}"{f" ({why})" if why else ""} ({asked}).')


def can_fly(unit):
    """Whether a unit has Fly models, so it can take to the skies (core rules 21.03)."""
    return any(_keyword(part, ('Fly',)) for part in unit['parts'])


def _skies(unit, action):
    """Whether a unit takes to the skies for this move (core rules 21.03); only a unit with Fly models can."""
    if not action.get('skies'):
        return False
    if not can_fly(unit):
        raise ActionError(f'{unit["name"]} can\'t take to the skies: it has no Fly models.')
    return True
