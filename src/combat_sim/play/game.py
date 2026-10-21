"""
A game's state as plain data (JSON all through): the two frozen armies, every model's wounds and
position, what each unit has done this turn (`activity`) and whose turn and phase it is (`turn`).

Actions become events: resolve_action rolls the dice and says what happened without changing the
state; apply_event applies an event's results without rolling. A game's log is its events, so
replaying them on new_game rebuilds the game exactly, with no dice.
"""

import copy
import math
import re
from dataclasses import asdict

from .. import effects
from ..simulation import AttackerProfile, BattleConditions, DefenderProfile
from .attack import roll_attack, roll_wound
from .geometry import BOARD_DEPTH, BOARD_WIDTH, MM_PER_INCH, base_distance, base_shape, bounds, hull_shape, overlaps

STATE_VERSION = 1
ENGAGEMENT_RANGE = 1  # Inches, measured base to base (height is ignored on a flat board)
COHERENCY_RANGE = 2  # Each model within this of 1 other model in its unit (2 others with 7+ models)
PATH_STEP = 0.2  # Inches between the points a move's path is checked at
DEPLOYMENT_DEPTH = 12  # Until missions: each player sets up within this many inches of their own long edge
MODEL_FIELDS = ('wounds_left', 'alive', 'position')  # What an event can change about a model
PHASES = ('command', 'movement', 'shooting', 'charge', 'fight')  # Each player's turn, in order


class ActionError(ValueError):
    """An action the rules don't allow; its message says why, for the player."""


def new_game(armies):
    """A game between frozen armies (snapshot.freeze_army), the first army's player going first."""
    state = {'version': STATE_VERSION, 'players': [], 'units': [],
             'turn': {'phase': 'deployment', 'round': 0, 'player': 0}}
    for player, army in enumerate(armies):
        state['players'].append({'name': army['name'], 'faction': army['faction']})
        for unit in army['units']:
            unit_id = f'p{player + 1}-{unit["uid"]}'
            models = [{'id': f'{unit_id}-{part_index}-{n + 1}', 'part': part_index,
                       'wounds_left': part['stats']['wounds'], 'alive': True, 'position': None,
                       # The part's weapons this model carries: the first `qty` models carry each
                       'weapons': [i for i, w in enumerate(part['weapons']) if n < w['qty']]}
                      for part_index, part in enumerate(unit['parts']) for n in range(part['models'])]
            state['units'].append({'id': unit_id, 'player': player, 'name': unit['name'],
                                   'parts': copy.deepcopy(unit['parts']), 'models': models, 'activity': {}})
    return state


def unit_by_id(state, unit_id):
    return next(u for u in state['units'] if u['id'] == unit_id)


def _snapshot(state):
    """What events record: each model's MODEL_FIELDS, each unit's activity, and the turn."""
    return {'models': {m['id']: {k: m[k] for k in MODEL_FIELDS} for u in state['units'] for m in u['models']},
            'units': {u['id']: {'activity': u.get('activity', {})} for u in state['units']},
            'turn': state.get('turn')}


def resolve_action(state, action, dice):
    """
    Roll an action and return its event: {'action', 'rolls', 'models' (changed models), 'units'
    (changed units' activity), 'turn' (when it changed), ...}. `state` is not changed.
    """
    handlers = {'shoot': lambda s: _shoot(s, action, dice), 'place': lambda s: _place(s, action),
                'end_deployment': _end_deployment, 'next_phase': _next_phase,
                'move': lambda s: _move(s, action), 'advance': lambda s: _advance(s, action, dice),
                'stationary': lambda s: _stationary(s, action)}
    kind = action.get('type') if isinstance(action, dict) else None
    if kind not in handlers:
        raise ActionError(f'Unknown action: {kind}')
    before = copy.deepcopy(_snapshot(state))
    state = copy.deepcopy(state)
    start = len(dice.log)
    detail = handlers[kind](state) or {}
    after = _snapshot(state)
    event = {'action': action, 'rolls': [asdict(g) for g in dice.log[start:]],
             'models': {k: v for k, v in after['models'].items() if v != before['models'][k]},
             'units': {k: v for k, v in after['units'].items() if v != before['units'][k]}, **detail}
    if after['turn'] != before['turn']:
        event['turn'] = after['turn']
    return event


def apply_event(state, event):
    """The state after an event: its results applied, no dice rolled."""
    state = copy.deepcopy(state)
    for unit in state['units']:
        unit.update(copy.deepcopy(event.get('units', {}).get(unit['id'], {})))
        for model in unit['models']:
            if model['id'] in event['models']:
                model.update(event['models'][model['id']])
    if 'turn' in event:
        state['turn'] = dict(event['turn'])
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


def _end_deployment(state):
    _require(state, 'deployment')
    if _unplaced(state, 0) or _unplaced(state, 1):
        raise ActionError('Place every unit before deployment ends.')
    _start_turn(state, 1, 0)


def _next_phase(state):
    turn = state['turn']
    if turn['phase'] not in PHASES:
        raise ActionError('The battle has not started yet.')
    i = PHASES.index(turn['phase'])
    if i + 1 < len(PHASES):
        turn['phase'] = PHASES[i + 1]
    elif turn['player'] == 0:
        _start_turn(state, turn['round'], 1)
    else:
        _start_turn(state, turn['round'] + 1, 0)


def _start_turn(state, round_number, player):
    """A player's turn begins: Command phase, and every unit's record of this turn cleared."""
    state['turn'] = {'phase': 'command', 'round': round_number, 'player': player}
    for unit in state['units']:
        unit['activity'] = {}


def _shoot(state, action, dice):
    shooter, target = unit_by_id(state, action['unit']), unit_by_id(state, action['target'])
    chosen = action.get('weapons')
    if chosen is None:  # Every weapon that starts ticked
        chosen = [{'part': p, 'weapon': w} for p, part in enumerate(shooter['parts'])
                  for w, weapon in enumerate(part['weapons']) if weapon['default_use'] and weapon['type'] == 'ranged']
    conditions = BattleConditions()
    weapons = []
    for pick in chosen:
        carriers = [m for m in shooter['models'] if m['alive'] and m['part'] == pick['part'] and pick['weapon'] in m['weapons']]
        if not carriers or not any(m['alive'] for m in target['models']):
            continue
        attacker = _attacker(shooter, pick['part'], pick['weapon'], len(carriers))
        pool = roll_attack(attacker, _defender(target, _first_alive(target)['part']), conditions, dice)
        for mortal in [False] * pool.wounds + [True] * pool.mortal:
            model = _allocate(target)
            if model is None:
                break
            result = roll_wound(attacker, _defender(target, model['part']), conditions, dice, model['wounds_left'], mortal)
            model['wounds_left'] -= result.damage
            model['alive'] = model['wounds_left'] > 0
        weapons.append({'part': pick['part'], 'weapon': pick['weapon'], 'models': len(carriers), **asdict(pool)})
    return {'weapons': weapons}


def _first_alive(unit):
    return next(m for m in unit['models'] if m['alive'])


def _allocate(unit):
    """
    The model the next wound goes to: one already damaged, else bodyguard before leaders and, within
    each, the last-listed model first (the first models carry the special weapons; see new_game).
    """
    alive = [m for m in reversed(unit['models']) if m['alive']]
    alive.sort(key=lambda m: m['part'])  # Stable: last-listed first within each part
    if not alive:
        return None
    full = {m['id']: unit['parts'][m['part']]['stats']['wounds'] for m in alive}
    damaged = [m for m in alive if m['wounds_left'] < full[m['id']]]
    return (damaged or alive)[0]


def _attacker(unit, part_index, weapon_index, models):
    """A weapon's AttackerProfile: its abilities plus the unit's leader and enhancement effects."""
    part = unit['parts'][part_index]
    weapon = part['weapons'][weapon_index]
    kind = weapon['type']
    extra = _leader_effects(unit, 'Attacker', kind) + effects.enhancement_modifiers(
        _carried(unit), 'Attacker', kind, leader_weapon=part['role'] == 'leader')
    return AttackerProfile(weapon['attacks'], weapon['skill'], weapon['strength'], weapon['ap'], weapon['damage'],
                           list(weapon['abilities'] or []) + extra, models, kind)


def _defender(unit, part_index, kind='ranged'):
    """The DefenderProfile of one part of a unit, with the unit's leader and enhancement effects as a target."""
    part = unit['parts'][part_index]
    stats = part['stats']
    extra = _leader_effects(unit, 'Target', kind) + effects.enhancement_modifiers(_carried(unit), 'Target', kind)
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


def replay(armies, events):
    """A game rebuilt from its frozen armies and its events, in order (no dice)."""
    state = new_game(armies)
    for event in events:
        state = apply_event(state, event)
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
    zone = (0, DEPLOYMENT_DEPTH) if unit['player'] == 0 else (BOARD_DEPTH - DEPLOYMENT_DEPTH, BOARD_DEPTH)
    for shape in shapes.values():
        left, bottom, right, top = bounds(shape)
        if left < 0 or right > BOARD_WIDTH or bottom < 0 or top > BOARD_DEPTH:
            raise ActionError('Every model must be wholly on the board.')
        if bottom < zone[0] or top > zone[1]:
            raise ActionError(f'Every model must be wholly within your deployment zone '
                              f'({DEPLOYMENT_DEPTH}" from your edge).')
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


def _engaged(shapes, enemies):
    return any(base_distance(a, b) <= ENGAGEMENT_RANGE + 1e-9 for a in shapes for b in enemies)


def move_characteristic(part):
    """A part's Move in inches ('6', '20+' -> 20; none -> 0)."""
    found = re.match(r'\s*(\d+)', str(part['stats'].get('move') or ''))
    return int(found.group(1)) if found else 0


def _ready_to_move(state, unit):
    _require(state, 'movement', unit)
    activity = unit['activity']
    if activity.get('moved') == 'stationary':
        raise ActionError(f'{unit["name"]} has already remained stationary this phase.')
    if 'moved' in activity:
        raise ActionError(f'{unit["name"]} has already moved this phase.')
    return activity


def _advance(state, action, dice):
    """Roll to Advance: D6 inches added to the unit's next move (an Advance), and the roll stands."""
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit)
    if 'advance_roll' in activity:
        raise ActionError(f'{unit["name"]} has already rolled to Advance.')
    if _engaged(_placed_shapes(unit), _enemy_shapes(state, unit)):
        raise ActionError(f'{unit["name"]} is in engagement range: it can only Fall Back.')
    activity['advance_roll'] = dice.roll_value('D6', 'advance')


def _stationary(state, action):
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit)
    if 'advance_roll' in activity:
        raise ActionError(f'{unit["name"]} rolled to Advance: it must make an Advance move.')
    activity['moved'] = 'stationary'


def _move(state, action):
    """
    A Normal move, Advance or Fall Back: each model in a straight line up to its Move (+ the Advance
    roll), not through enemy models, ending wholly on the board, overlapping no model, in coherency
    and out of engagement range. Only an engaged unit Falls Back; an engaged unit can do nothing else.
    """
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit)
    mode = action.get('mode')
    if mode not in MOVE_MODES:
        raise ActionError('Choose a Normal move, Advance or Fall Back.')
    enemies = _enemy_shapes(state, unit)
    engaged = _engaged(_placed_shapes(unit), enemies)
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
    shapes = {}
    for mid, spot in spots.items():
        model, part = living[mid], unit['parts'][living[mid]['part']]
        start = model['position']
        allowance = move_characteristic(part) + (activity.get('advance_roll', 0) if mode == 'advance' else 0)
        distance = math.dist((start['x'], start['y']), (spot['x'], spot['y']))
        if distance > allowance + 1e-6:
            raise ActionError(f'{part["name"]} can move at most {allowance}" ({distance:.1f}" asked).')
        steps = max(1, math.ceil(distance / PATH_STEP))
        for i in range(1, steps):
            t = i / steps
            here = {'x': start['x'] + t * (spot['x'] - start['x']), 'y': start['y'] + t * (spot['y'] - start['y']),
                    'angle': spot['angle']}
            if any(overlaps(model_shape(part, here), enemy) for enemy in enemies):
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
    activity['moved'] = mode


def coherency_problem(shapes):
    """Why a unit's models (shapes) are out of coherency, or None: each must be within 2" of another
    model of the unit (of 2 others when the unit has 7 or more)."""
    if len(shapes) < 2:
        return None
    needed = 2 if len(shapes) >= 7 else 1
    for i, shape in enumerate(shapes):
        near = sum(1 for j, other in enumerate(shapes) if j != i and base_distance(shape, other) <= COHERENCY_RANGE + 1e-9)
        if near < needed:
            return (f'Every model must end within {COHERENCY_RANGE}" of '
                    f'{"two other models" if needed == 2 else "another model"} in its unit (unit coherency).')
    return None
