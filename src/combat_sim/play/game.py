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

from .. import effects
from ..simulation import AttackerProfile, BattleConditions, DefenderProfile
from .attack import roll_attack, roll_wound
from . import battlefields, mission_state, tactical
from .missions import MIRRORED_PRIMARIES, NIGHT_VISIBILITY
from .geometry import clear_line
from .geometry import footprint as footprint_shape
from .geometry import (BOARD_DEPTH, BOARD_WIDTH, MM_PER_INCH, base_distance, base_shape, bounds, can_see, farther_than, hull_shape,
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
        state['players'].append({'name': army['name'], 'faction': army['faction'], 'orders': dict(DEFAULT_ORDERS), 'cp': 0,
                                 'stratagems': copy.deepcopy(army.get('stratagems', []) + army.get('rules', [])),
                                 'used': {}, 'vp': mission_state.new_vp(), 'disposition': army.get('disposition')})
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
                         **({'vp': p['vp']} if 'vp' in p else {})} for p in state['players']],
            'mission': state.get('mission'),
            'turn': state.get('turn'), 'pending': state.get('pending'), 'roles': state.get('roles'),
            'objectives': {o['id']: o.get('controller') for o in state.get('objectives', [])},
            'layout': [{k: v for k, v in o.items() if k != 'controller'} for o in state.get('objectives', [])]}


def resolve_action(state, action, dice):
    """
    Roll an action and return its event: {'action', 'rolls', 'models' (changed models), 'units'
    (changed units' activity), 'turn' (when it changed), ...}. `state` is not changed.
    """
    handlers = {'shoot': lambda s: _shoot(s, action, dice), 'place': lambda s: _place(s, action),
                'end_deployment': lambda s: _end_deployment(s, dice), 'next_phase': lambda s: _next_phase(s, dice),
                'move': lambda s: _move(s, action), 'advance': lambda s: _advance(s, action, dice),
                'stationary': lambda s: _stationary(s, action), 'reset_move': lambda s: _reset_move(s, action),
                'charge': lambda s: _charge(s, action, dice), 'charge_move': lambda s: _charge_move(s, action),
                'pile_in': lambda s: _pile_in(s, action), 'fight': lambda s: _fight(s, action, dice),
                'consolidate': lambda s: _consolidate(s, action),
                'orders': lambda s: _orders(s, action), 'stratagem': lambda s: _stratagem(s, action),
                'rule': lambda s: _rule(s, action), 'effect': lambda s: _manual_effect(s, action),
                'remove_effect': lambda s: _remove_effect(s, action), 'correction': lambda s: _correction(s, action),
                'accept': lambda s: _answer(s, action, True), 'decline': lambda s: _answer(s, action, False),
                'roll_objectives': lambda s: _roll_objectives(s, dice), 'roll_off': lambda s: _roll_off_roles(s, dice),
                'choose_role': lambda s: _choose_role(s, action), 'condemn': lambda s: mission_state.condemn(s, action),
                'choose_secondaries': lambda s: mission_state.choose_secondaries(s, action, dice),
                'tactical': lambda s: tactical.card_action(s, action, dice),
                'beacon': lambda s: tactical.choose_beacon(s, action),
                'tempting': lambda s: tactical.choose_tempting(s, action),
                'set_guards': lambda s: tactical.set_guards(s, action),
                'roll_mirrored': lambda s: _roll_mirrored(s, dice),
                'start_action': lambda s: mission_state.start_action(s, action),
                'place_markers': lambda s: mission_state.place_markers(s, action)}
    kind = action.get('type') if isinstance(action, dict) else None
    if kind not in handlers:
        raise ActionError(f'Unknown action: {kind}')
    pending = state.get('pending')
    if pending and kind not in ('accept', 'decline'):
        other = state['players'][1 - pending['player']]['name']
        raise ActionError(f'A correction is waiting for {other} to accept or decline it.')
    before = copy.deepcopy(_snapshot(state))
    state = copy.deepcopy(state)
    start = len(dice.log)
    detail = handlers[kind](state) or {}
    mission_state.log_destroyed(state, before['models'], action)
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
    return [u for u in state['units'] if u['player'] == player and any(m['alive'] and not m['position'] for m in u['models'])]


def _end_deployment(state, dice):
    _require(state, 'deployment')
    if _unplaced(state, 0) or _unplaced(state, 1):
        raise ActionError('Place every unit before deployment ends.')
    report = {'battle_shock': [], 'hazards': []}
    first = 0
    if state.get('setup'):  # Roll off for the first turn: the winner goes first
        report['first_turn'] = _roll_off(dice, 'first turn')
        first = report['first_turn']['winner']
    state['roles'] = dict(state.get('roles') or {'attacker': 0, 'defender': 1}, first=first)
    update_control(state)  # Who holds what as the battle begins
    mission_state.start_battle(state)
    _start_turn(state, 1, first, dice, report)
    _prune_effects(state)
    return {k: v for k, v in report.items() if v}


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
    lasted until then end."""
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
    # Moves lock at the end of a phase or Fight step (Surveil the Foe removes markers then)
    locking = START_KEYS.get(turn.get('step') if turn['phase'] == 'fight' else turn['phase'])
    if locking:
        mission_state.surveil_moves(state, locking, report)
    if turn['phase'] == 'command':
        mission_state.require_no_choice(state)
        mission_state.score(state, 'command', [turn['player']], report)
    if turn['phase'] == 'fight' and turn.get('step') == 'pile_in':
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
    mission_state.complete_actions(state, report)  # The end of the turn: actions complete, then scoring
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
        if unit['player'] == player and _placed_shapes(unit) and (status['shocked'] or half_strength(unit)):
            _battle_shock_roll(unit, dice, report)
    mission_state.start_turn(state, dice, report)


def _battle_shock_roll(unit, dice, report, reason=None):
    """2D6 against the unit's best Leadership: under it, the unit is battle-shocked; at or over it, it isn't (any more)."""
    status = unit.setdefault('status', {'shocked': False})
    leadership = min(unit['parts'][m['part']]['stats'].get('leadership') or 7 for m in unit['models'] if m['alive'])
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
    a battle-shocked unit. Its effect, if the app reads one, lasts the phase; otherwise its text is
    logged for the players to apply.
    """
    _in_battle(state)
    player, entry, unit = _player_entry(state, action, 'stratagem')
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
    return {'stratagem': entry['name'], 'cp': entry['cp'], 'player': player, 'unit': unit['id'], 'text': entry.get('text', '')}


def _rule(state, action):
    """Switch on one of the army's rules for a unit (free) for this phase, this turn or the rest of the game."""
    _in_battle(state)
    player, entry, unit = _player_entry(state, action, 'rule')
    _add_effect(state, unit, entry['name'], entry.get('applies_to'), entry.get('phase'), entry.get('modifier') or '',
                action.get('until') or 'phase', 'rule')  # With no modifier it's a reminder on the unit
    return {'rule': entry['name'], 'player': player, 'unit': unit['id'], 'text': entry.get('text', '')}


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


def _effect_modifiers(unit, side, kind):
    """Modifier text from a unit's effects (stratagems, rules, manual) for one side and the weapon's phase."""
    rows = [{'applies_to': e['side'], 'phase': e['phase'], 'modifier': e['modifier']} for e in unit.get('effects', [])]
    return effects.leader_modifiers(rows, side, kind)


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
            _battle_shock_roll(unit, dice, report, 'Ordered Retreat')


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
        model = _allocate(unit)
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
        if unit['models'] and not any(m['alive'] for m in unit['models']):
            points[1 - unit['player']] += sum(part.get('points') or 0 for part in unit['parts'])
    return points


def _orders(state, action):
    """A player's standing orders (any time): which models are removed first ('farthest' or 'closest'
    from the attacking unit)."""
    try:
        player = state['players'][int(action.get('player'))]
    except (TypeError, ValueError, IndexError):
        raise ActionError('Say whose orders these are.')
    casualties = action.get('casualties')
    if casualties not in ('farthest', 'closest'):
        raise ActionError('Casualties are removed farthest or closest first.')
    player['orders'] = dict(player.get('orders', DEFAULT_ORDERS), casualties=casualties)


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


def _weapon_plan(state, shooter, part_index, weapon_index, target):
    """
    Check one weapon against one target: ([models within half range], [models in range beyond it],
    extra abilities) or ActionError saying why it can't fire. A model fires if a target model it can
    see is within the weapon's range, base to base.
    """
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
        seen = []
        for target_model, other in targets:
            visible, why = sees(state, shape, other, target, target_model)
            hidden = hidden or why == 'hidden'
            dark = dark or why == 'night'
            if visible:
                seen.append(base_distance(shape, other))
        if seen and min(seen) <= reach + 1e-9:
            (near if min(seen) <= reach / 2 + 1e-9 else far).append(model)
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


def shooting_options(state, unit_id):
    """
    For the page: whether a unit can shoot now ('reason' why not, else None) and, for each of its
    ranged weapons, each enemy unit as a target: {'ok', 'models' (how many would fire), 'reason'}.
    """
    shooter = _unit_or_error(state, unit_id)
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
                    near, far, _, _ = _weapon_plan(state, shooter, part_index, weapon_index, target)
                    targets[target['id']] = {'ok': True, 'models': len(near) + len(far), 'reason': None}
                except ActionError as error:
                    targets[target['id']] = {'ok': False, 'models': 0, 'reason': str(error)}
            weapons.append({'part': part_index, 'weapon': weapon_index, 'name': weapon['name'], 'range': weapon.get('range'),
                            'default_use': weapon['default_use'], 'targets': targets})
    return {'reason': reason, 'weapons': weapons}


def _shoot(state, action, dice):
    """
    Shoot with a unit: each weapon at its target (`target` for all, or per weapon in `weapons`:
    [{'part', 'weapon', 'target'}]). Every weapon is checked first (_weapon_plan), then rolled in
    turn: models within half range roll on their own. Wounds go to models by the target player's
    standing orders; Hazardous weapons roll afterwards.
    """
    shooter = _unit_or_error(state, action.get('unit'))
    _shooter_checks(state, shooter)
    activity = shooter['activity']
    picks = action.get('weapons')
    if picks is None:  # Every ranged weapon that starts ticked
        picks = [{'part': p, 'weapon': w} for p, part in enumerate(shooter['parts'])
                 for w, weapon in enumerate(part['weapons']) if weapon['default_use'] and weapon['type'] == 'ranged']
    if not picks:
        raise ActionError(f'{shooter["name"]} has no ranged weapons to fire.')
    stationary = moved_distance(shooter) <= HEAVY_MOVE + 1e-9 and not _is_engaged(state, shooter)  # Heavy
    plans = []
    for pick in picks:
        if not isinstance(pick, dict) or 'part' not in pick or 'weapon' not in pick:
            raise ActionError('Choose weapons the unit has.')
        target = _unit_or_error(state, pick.get('target') or action.get('target'))
        near, far, extra, covered = _weapon_plan(state, shooter, pick['part'], pick['weapon'], target)
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
        groups = [([m for m in models if (m['id'] in covered) == cover], half, cover)
                  for models, half in ((near, True), (far, False)) for cover in (False, True)]
        for models, half, cover in groups:
            if not models or not any(m['alive'] for m in target['models']):
                continue
            attacker = _attacker(shooter, part_index, weapon_index, len(models))
            attacker.abilities += extra
            conditions = BattleConditions(half_range=half, stationary=stationary, cover=cover)
            first = len(dice.log)
            pool = roll_attack(attacker, _defender(target, _first_alive(target)['part']), conditions, dice)
            killed = _resolve_wounds(state, shooter, target, attacker, conditions, pool, dice)
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
    shooter.setdefault('status', {'shocked': False})['shot_seq'] = state['turn'].get('seq', 0)  # No longer hidden
    return {'attacks': attacks}


def _resolve_wounds(state, attacking, target, attacker, conditions, pool, dice):
    """Allocate and resolve a wound pool one wound at a time (normal wounds, then mortal); the models killed."""
    killed = []
    order = state['players'][target['player']].get('orders', DEFAULT_ORDERS)['casualties']
    for mortal in [False] * pool.wounds + [True] * pool.mortal:
        model = _allocate(target, attacking, order)
        if model is None:
            break
        result = roll_wound(attacker, _defender(target, model['part']), conditions, dice, model['wounds_left'], mortal)
        model['wounds_left'] -= result.damage
        model['alive'] = model['wounds_left'] > 0
        if not model['alive']:
            killed.append(model['id'])
    return killed


def _first_alive(unit):
    return next(m for m in unit['models'] if m['alive'])


def _special(unit, model):
    """Whether a model carries a weapon that not every model of its part has (a special weapon)."""
    part = unit['parts'][model['part']]
    return any(part['weapons'][w]['qty'] < part['models'] for w in model['weapons'])


def _allocate(unit, attacking=None, order='farthest'):
    """
    The model the next wound goes to: one already damaged; else bodyguard before leaders; then models
    with only the common weapons before special-weapon carriers; then the farthest from (or, by
    standing order, closest to) the attacking unit; then the last-listed model.
    """
    alive = [m for m in unit['models'] if m['alive']]
    if not alive:
        return None
    full = {m['id']: unit['parts'][m['part']]['stats']['wounds'] for m in alive}
    damaged = [m for m in alive if m['wounds_left'] < full[m['id']]]
    if damaged:
        return damaged[0]
    attackers = [shape for _, shape in _placed_models(attacking)] if attacking else []

    def distance(model):
        if not attackers or not model['position']:
            return 0
        shape = model_shape(unit['parts'][model['part']], model['position'])
        return min(base_distance(shape, other) for other in attackers)

    position = {m['id']: i for i, m in enumerate(unit['models'])}
    sign = -1 if order == 'farthest' else 1
    return min(alive, key=lambda m: (m['part'], _special(unit, m), round(sign * distance(m), 6), -position[m['id']]))


def _attacker(unit, part_index, weapon_index, models):
    """A weapon's AttackerProfile: its abilities plus the unit's leader and enhancement effects."""
    part = unit['parts'][part_index]
    weapon = part['weapons'][weapon_index]
    kind = weapon['type']
    extra = _leader_effects(unit, 'Attacker', kind) + effects.enhancement_modifiers(
        _carried(unit), 'Attacker', kind, leader_weapon=part['role'] == 'leader') + _effect_modifiers(unit, 'Attacker', kind)
    return AttackerProfile(weapon['attacks'], weapon['skill'], weapon['strength'], weapon['ap'], weapon['damage'],
                           list(weapon['abilities'] or []) + extra, models, kind)


def _defender(unit, part_index, kind='ranged'):
    """The DefenderProfile of one part of a unit, with the unit's leader and enhancement effects as a target."""
    part = unit['parts'][part_index]
    stats = part['stats']
    extra = (_leader_effects(unit, 'Target', kind) + effects.enhancement_modifiers(_carried(unit), 'Target', kind)
             + _effect_modifiers(unit, 'Target', kind))
    alive = sum(m['alive'] for m in unit['models'])
    return DefenderProfile(stats['toughness'], stats['save'], stats['wounds'], stats['invulnerable_save'],
                           stats['feel_no_pain'], list(part['keywords']) + extra, alive)


def _leader_effects(unit, side, kind):
    """Effects a unit's living leaders grant it."""
    alive_parts = {m['part'] for m in unit['models'] if m['alive']}
    return [m for i, part in enumerate(unit['parts']) if part['role'] == 'leader' and i in alive_parts
            for m in effects.leader_modifiers(part['leader_buffs'], side, kind)]


def _carried(unit):
    """[(buffs, bearer)] for enhancements carried by the unit itself or by a leader in it."""
    return [(part['enhancement']['buffs'], 'leader' if part['role'] == 'leader' else 'unit')
            for part in unit['parts'] if part.get('enhancement')]


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
    drawn = outline(part)
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
    new = not any(m['position'] for m in unit['models'] if m['alive'])
    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = _spots(unit, action, 'Place')
    shapes = {mid: model_shape(unit['parts'][living[mid]['part']], spot) for mid, spot in spots.items()}
    zone = deployment_zone(state, unit['player'])
    for shape in shapes.values():
        left, bottom, right, top = bounds(shape)
        if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
            raise ActionError('Every model must be wholly on the board.')
        if not battlefields.wholly_inside(shape, zone):
            raise ActionError('Every model must be wholly within your deployment zone.')
    others = [model_shape(u['parts'][m['part']], m['position']) for u in state['units'] if u is not unit
              for m in u['models'] if m['alive'] and m['position']]
    placed = list(shapes.values())
    for i, shape in enumerate(placed):
        if any(overlaps(shape, other) for other in placed[i + 1:] + others):
            raise ActionError("Models' bases can't overlap.")
    for mid, spot in spots.items():
        living[mid]['position'] = spot
    if new:  # Players take turns setting up units; one with nothing left to place sits out
        other = 1 - unit['player']
        if _unplaced(state, other):
            state['turn']['player'] = other
    return {}


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
        for key in ('x', 'y', 'angle'):
            if spot[key].is_integer():
                spot[key] = int(spot[key])
    return spots


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
    return any(base_distance(a, b) <= ENGAGEMENT_RANGE + 1e-9 for a in shapes for b in enemies)


def move_characteristic(part):
    """A part's Move in inches ('6', '20+' -> 20; none -> 0)."""
    found = re.match(r'\s*(\d+)', str(part['stats'].get('move') or ''))
    return int(found.group(1)) if found else 0


# Where a unit started each kind of move this turn: moves can be redone until their phase (or step) ends
START_KEYS = {'movement': 'start', 'charge': 'charge_start', 'pile_in': 'pile_start', 'consolidate': 'consolidate_start'}


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


def _advance(state, action, dice):
    """Roll to Advance: D6 inches added to the unit's moves this phase (as Advances); the roll stands."""
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit)
    if 'advance_roll' in activity:
        raise ActionError(f'{unit["name"]} has already rolled to Advance.')
    if _engaged(_start_shapes(unit), _enemy_shapes(state, unit)):
        raise ActionError(f'{unit["name"]} is in engagement range: it can only Fall Back.')
    activity['advance_roll'] = dice.roll_value('D6', 'advance')


def _stationary(state, action):
    """Remain stationary: back where the unit started the phase, if it had moved."""
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit)
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
    _back_to_start(unit, START_KEYS[phase])
    for key in ('moved', 'via') if phase == 'movement' else ('charged', 'charge_targets'):
        activity.pop(key, None)


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
    targets = {}
    if roll is not None:
        start = _start_shapes(unit, 'charge_start')
        for other in state['units']:
            if other['player'] != unit['player'] and _placed_shapes(other):
                gap = _unit_distance(start, _placed_shapes(other))
                if gap <= min(CHARGE_REACH, roll) + 1e-9:
                    targets[other['id']] = {'ok': True, 'distance': round(gap, 2)}
    return {'reason': reason, 'roll': roll, 'targets': targets}


def _charge_move(state, action):
    """
    A charge move after the roll, at `targets`: enemy units within 12" and within the roll. Each model
    moves in a straight line up to the roll, not through enemy models, and each one that moves ends
    closer to a target; the unit ends engaged with every target and with no other enemy unit, wholly
    on the board, overlapping no model and in coherency. It can be redone until the phase ends.
    """
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit, 'charge')
    roll = activity.get('charge_roll')
    if roll is None:
        raise ActionError('Roll to charge first.')
    start_shapes = _start_shapes(unit, 'charge_start')
    targets = []
    for target_id in action.get('targets') or []:
        target = _unit_or_error(state, target_id)
        if target['player'] == unit['player'] or not _placed_shapes(target):
            raise ActionError('Charge targets must be enemy units on the battlefield.')
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
        distance = math.dist((start['x'], start['y']), (spot['x'], spot['y']))
        climb = 0 if skies else _climb(state, part, [start, spot])
        _within_allowance(part, distance, climb, allowance, f'the charge roll{" less 2 for taking to the skies" if skies else ""}')
        steps = max(1, math.ceil(distance / PATH_STEP))
        for i in range(1, steps):
            t = i / steps
            here = {'x': start['x'] + t * (spot['x'] - start['x']), 'y': start['y'] + t * (spot['y'] - start['y']),
                    'angle': spot['angle']}
            if any(overlaps(model_shape(part, here), enemy) for enemy in enemies):
                raise ActionError(f'{part["name"]} can\'t move through enemy models.')
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
    for mid, spot in spots.items():
        living[mid]['position'] = spot
    activity['charged'] = True
    activity['charge_targets'] = [t['id'] for t in targets]


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
    activity = _ready_to_move(state, unit)
    mode = action.get('mode')
    if mode not in MOVE_MODES:
        raise ActionError('Choose a Normal move, Advance or Fall Back.')
    enemies = _enemy_shapes(state, unit)
    engaged = _engaged(_start_shapes(unit), enemies)  # Where it started the phase decides
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
        path = [start, *via[mid], spot]
        allowance = move_characteristic(part) + (activity.get('advance_roll', 0) if mode == 'advance' else 0) - (SKIES if skies else 0)
        distance = path_length(path)
        mobile = twist_is(state, 'ruinscape') and mode in ('normal', 'advance')  # Ruinscape: Mobile while moving
        climb = 0 if skies or mobile else _climb(state, part, path)
        _within_allowance(part, distance, climb, allowance, 'less 2 for taking to the skies' if skies else '')
        for a, b in zip(path, path[1:]):  # Every leg, waypoints included
            steps = max(1, math.ceil(math.dist(_xy(a), _xy(b)) / PATH_STEP))
            for i in range(1, steps + (b is not spot)):
                t = i / steps
                here = {'x': a['x'] + t * (b['x'] - a['x']), 'y': a['y'] + t * (b['y'] - a['y']), 'angle': spot['angle']}
                if any(overlaps(model_shape(part, here), enemy) for enemy in blocking):
                    raise ActionError(f'{part["name"]} can\'t move through enemy models.')
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
    """Whether a unit can be picked to fight now: not fought yet, engaged, and with melee weapons."""
    if unit['activity'].get('fought') or not _placed_shapes(unit):
        return False
    alive = {(m['part'], w) for m in unit['models'] if m['alive'] for w in m['weapons']}
    return bool(alive & set(_melee_weapons(unit))) and _is_engaged(state, unit)


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
        raise ActionError(f'{unit["name"]} isn\'t engaged with an enemy unit, so it can\'t fight.')
    if fight['stage'] == 'fights_first' and not _fights_first(unit):
        raise ActionError('Only Fights First units (those that charged this turn) fight now.')


def fight_options(state, unit_id):
    """
    For the page: why a unit can't fight now ('reason', None if it can), the enemy units it is engaged
    with ('targets'), and each part's melee weapons ('parts': {'part', 'name', 'choice' (the default
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
    return {'reason': reason, 'targets': [u['id'] for u in _engaged_units(state, unit)], 'parts': parts}


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
        plans.append((p, w, target, models))
    if not plans:
        raise ActionError(f'No model of {unit["name"]} is engaged with its target.')

    conditions = BattleConditions(charged=bool(unit['activity'].get('charged')))
    attacks, hazardous = [], []
    for p, w, target, models in plans:
        if not any(m['alive'] for m in target['models']):
            continue
        weapon = unit['parts'][p]['weapons'][w]
        attacker = _attacker(unit, p, w, len(models))
        first = len(dice.log)
        pool = roll_attack(attacker, _defender(target, _first_alive(target)['part'], 'melee'), conditions, dice)
        killed = _resolve_wounds(state, unit, target, attacker, conditions, pool, dice)
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
    return {'attacks': attacks}


def _pile_in(state, action):
    """A pile-in move (both players, in the Pile in step): up to 3" towards the enemy, ending engaged."""
    unit = _unit_or_error(state, action.get('unit'))
    if state['turn']['phase'] != 'fight' or state['turn'].get('step') != 'pile_in':
        raise ActionError('Units only pile in during the Fight phase\'s Pile in step.')
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
    """
    try:
        player = int(action.get('player'))
        state['players'][player]
    except (TypeError, ValueError, IndexError):
        raise ActionError('Say who proposes the correction.')
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


def _answer(state, action, accept):
    """The other player accepts (it's applied) or declines (it's dropped) the pending correction."""
    pending = state.get('pending')
    if not pending:
        raise ActionError('There is no correction to answer.')
    if action.get('player') != 1 - pending['player']:
        raise ActionError('Only the other player can accept or decline a correction.')
    state['pending'] = None
    if not accept:
        return {'declined': pending['note']}
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
    if phase == 'movement':
        return [u['id'] for u in mine if 'moved' not in u['activity'] and _placed_shapes(u)]
    if phase == 'shooting':
        def can_shoot(unit):
            options = shooting_options(state, unit['id'])
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
    if phase == 'fight' and step == 'pile_in':
        return [u['id'] for u in on_board if 'pile_start' not in u['activity'] and _placed_shapes(u)
                and (_is_engaged(state, u) or u['activity'].get('charged'))]
    if phase == 'fight' and step == 'fight':
        fight = turn.get('fight') or {}
        if fight.get('picker') is None:
            return []
        first = fight['stage'] == 'fights_first'
        return [u['id'] for u in on_board if u['player'] == fight['picker'] and _can_fight(state, u)
                and (_fights_first(u) or not first)]
    if phase == 'fight' and step == 'consolidate':
        def can_consolidate(unit):
            enemies = [e for e in on_board if e['player'] != unit['player'] and _placed_shapes(e)]
            mine_shapes = _placed_shapes(unit)
            return (any(_unit_distance(mine_shapes, _placed_shapes(e)) <= CONSOLIDATE_MOVE + 1e-9 for e in enemies)
                    or any(base_distance(s, objective_shape(o)) <= CONSOLIDATE_MOVE + 1e-9  # An Objective consolidation
                           for o in state.get('objectives', []) for s in mine_shapes))
        return [u['id'] for u in on_board if u['activity'].get('fought') and 'consolidate_start' not in u['activity']
                and can_consolidate(u)]
    return []


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
    for unit in state['units']:
        if unit.get('status', {}).get('shocked'):
            continue
        for model, shape in _placed_models(unit):
            if in_range(shape, objective, state):
                totals[unit['player']] += unit['parts'][model['part']]['stats'].get('oc') or 0
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
