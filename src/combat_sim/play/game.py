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
from .geometry import (BOARD_DEPTH, BOARD_WIDTH, MM_PER_INCH, base_distance, base_shape, bounds, can_see, hull_shape,
                       overlaps)

STATE_VERSION = 1
ENGAGEMENT_RANGE = 1  # Inches, measured base to base (height is ignored on a flat board)
COHERENCY_RANGE = 2  # Each model within this of 1 other model in its unit (2 others with 7+ models)
PATH_STEP = 0.2  # Inches between the points a move's path is checked at
DEPLOYMENT_DEPTH = 12  # Until missions: each player sets up within this many inches of their own long edge
MODEL_FIELDS = ('wounds_left', 'alive', 'position')  # What an event can change about a model
PHASES = ('command', 'movement', 'shooting', 'charge', 'fight')  # Each player's turn, in order
DEFAULT_ORDERS = {'casualties': 'farthest'}  # Standing orders: which models are removed first
HAZARDOUS_DAMAGE = 3  # Mortal wounds to a model whose Hazardous roll is a 1


class ActionError(ValueError):
    """An action the rules don't allow; its message says why, for the player."""


def new_game(armies):
    """A game between frozen armies (snapshot.freeze_army), the first army's player going first."""
    state = {'version': STATE_VERSION, 'players': [], 'units': [],
             'turn': {'phase': 'deployment', 'round': 0, 'player': 0}}
    for player, army in enumerate(armies):
        state['players'].append({'name': army['name'], 'faction': army['faction'], 'orders': dict(DEFAULT_ORDERS)})
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
    """What events record: each model's MODEL_FIELDS, each unit's activity, players' orders and the turn."""
    return {'models': {m['id']: {k: m[k] for k in MODEL_FIELDS} for u in state['units'] for m in u['models']},
            'units': {u['id']: {'activity': u.get('activity', {})} for u in state['units']},
            'players': [p.get('orders', DEFAULT_ORDERS) for p in state['players']],
            'turn': state.get('turn')}


def resolve_action(state, action, dice):
    """
    Roll an action and return its event: {'action', 'rolls', 'models' (changed models), 'units'
    (changed units' activity), 'turn' (when it changed), ...}. `state` is not changed.
    """
    handlers = {'shoot': lambda s: _shoot(s, action, dice), 'place': lambda s: _place(s, action),
                'end_deployment': _end_deployment, 'next_phase': _next_phase,
                'move': lambda s: _move(s, action), 'advance': lambda s: _advance(s, action, dice),
                'stationary': lambda s: _stationary(s, action), 'reset_move': lambda s: _reset_move(s, action),
                'orders': lambda s: _orders(s, action)}
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
    if after['players'] != before['players']:
        event['orders'] = after['players']
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
    for player, orders in zip(state['players'], event.get('orders', [])):
        player['orders'] = dict(orders)
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
    pistol = _has(abilities, 'Pistol')
    engaged_with = _engaged_units(state, shooter)
    big = _big(shooter)
    if activity.get('moved') == 'advance' and not _has(abilities, 'Assault'):
        raise ActionError(f'{shooter["name"]} Advanced: it can only fire Assault weapons ({weapon["name"]} isn\'t one).')
    if engaged_with and not big and not pistol:
        raise ActionError(f'{shooter["name"]} is in engagement range: it can only fire Pistols.')
    if engaged_with and pistol and target not in engaged_with:
        raise ActionError(f'Pistols fired while engaged must target the unit {shooter["name"]} is engaged with.')
    if target not in engaged_with and not _big(target):
        friendly = [u for u in state['units'] if u['player'] == shooter['player']]
        if any(target in _engaged_units(state, u) for u in friendly):
            raise ActionError(f'{target["name"]} is in engagement range of your units: only Monsters and Vehicles can be shot then.')
    reach = weapon.get('range') or 0
    targets = _placed_models(target)
    near, far = [], []
    for model, shape in _placed_models(shooter):
        if model['part'] != int(part_index) or int(weapon_index) not in model['weapons']:
            continue
        seen = [base_distance(shape, other) for _, other in targets if can_see(shape, other, [])]
        if seen and min(seen) <= reach + 1e-9:
            (near if min(seen) <= reach / 2 + 1e-9 else far).append(model)
    if not near and not far:
        raise ActionError(f'No model carrying {weapon["name"]} has {target["name"]} in range ({reach}") and in sight.')
    extra = ['Hit Modifier -1'] if engaged_with and big and not pistol else []  # Big Guns Never Tire
    return near, far, extra


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
                    near, far, _ = _weapon_plan(state, shooter, part_index, weapon_index, target)
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
    stationary = activity.get('moved') in (None, 'stationary')
    plans = []
    for pick in picks:
        if not isinstance(pick, dict) or 'part' not in pick or 'weapon' not in pick:
            raise ActionError('Choose weapons the unit has.')
        target = _unit_or_error(state, pick.get('target') or action.get('target'))
        near, far, extra = _weapon_plan(state, shooter, pick['part'], pick['weapon'], target)
        part_index, weapon_index = int(pick['part']), int(pick['weapon'])
        plans.append((part_index, weapon_index, shooter['parts'][part_index]['weapons'][weapon_index], target, near, far, extra))

    attacks, hazardous = [], []
    for part_index, weapon_index, weapon, target, near, far, extra in plans:
        for models, half in ((near, True), (far, False)):
            if not models or not any(m['alive'] for m in target['models']):
                continue
            attacker = _attacker(shooter, part_index, weapon_index, len(models))
            attacker.abilities += extra
            conditions = BattleConditions(half_range=half, stationary=stationary)
            first = len(dice.log)
            pool = roll_attack(attacker, _defender(target, _first_alive(target)['part']), conditions, dice)
            killed = _resolve_wounds(state, shooter, target, attacker, conditions, pool, dice)
            attacks.append({'part': part_index, 'weapon': weapon_index, 'name': weapon['name'], 'target': target['id'],
                            'models': len(models), 'half_range': half, **asdict(pool), 'killed': killed,
                            'rolls': [first, len(dice.log)]})
        if _has(weapon['abilities'], 'Hazardous'):
            hazardous += near + far
    if hazardous:
        first = len(dice.log)
        roll = dice.roll(len(hazardous), 'hazardous', 2, crit_on=7)
        for model, die in zip(hazardous, roll.dice):
            if not die.success and model['alive']:
                model['wounds_left'] = max(0, model['wounds_left'] - HAZARDOUS_DAMAGE)
                model['alive'] = model['wounds_left'] > 0
        attacks.append({'name': 'Hazardous', 'target': shooter['id'], 'models': len(hazardous),
                        'killed': [m['id'] for m in hazardous if not m['alive']], 'rolls': [first, len(dice.log)]})
    activity['shot'] = True
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
    """
    The unit's activity, once it is the unit's Movement phase. A unit can be moved, reset and moved
    again as often as its player likes during the phase: every move is measured from where it
    started the phase (`activity['start']`, kept from its first move), and moves lock when the phase
    ends.
    """
    _require(state, 'movement', unit)
    activity = unit['activity']
    if 'start' not in activity:
        activity['start'] = {m['id']: m['position'] for m in unit['models'] if m['alive']}
    return activity


def _start_shapes(unit):
    """The unit's models where they started the Movement phase."""
    start = unit['activity'].get('start') or {}
    return [model_shape(unit['parts'][m['part']], start.get(m['id']) or m['position'])
            for m in unit['models'] if m['alive'] and (start.get(m['id']) or m['position'])]


def _back_to_start(unit):
    for model in unit['models']:
        if model['alive'] and model['id'] in unit['activity'].get('start', {}):
            model['position'] = unit['activity']['start'][model['id']]


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
    """Take a unit back to where it started the phase, not yet moved (an Advance roll stands)."""
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit)
    _back_to_start(unit)
    activity.pop('moved', None)


def _move(state, action):
    """
    A Normal move, Advance or Fall Back: each model in a straight line from where it started the
    phase, up to its Move (+ the Advance roll), not through enemy models, ending wholly on the board,
    overlapping no model, in coherency and out of engagement range. Only a unit that started the
    phase engaged Falls Back, and it can do nothing else.
    """
    unit = _unit_or_error(state, action.get('unit'))
    activity = _ready_to_move(state, unit)
    mode = action.get('mode')
    if mode not in MOVE_MODES:
        raise ActionError('Choose a Normal move, Advance or Fall Back.')
    enemies = _enemy_shapes(state, unit)
    engaged = _engaged(_start_shapes(unit), enemies)  # Where it started the phase decides
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
        start = activity['start'].get(mid) or model['position']
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
