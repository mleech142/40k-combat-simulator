"""
A game's state as plain data (JSON all through): the two frozen armies and every model's wounds.

Actions become events: resolve_action rolls the dice and says what happened without changing the
state; apply_event applies an event's results without rolling. A game's log is its events, so
replaying them on new_game rebuilds the game exactly, with no dice.
"""

import copy
from dataclasses import asdict

from .. import effects
from ..simulation import AttackerProfile, BattleConditions, DefenderProfile
from .attack import roll_attack, roll_wound
from .geometry import BOARD_DEPTH, BOARD_WIDTH, MM_PER_INCH, base_shape, bounds, hull_shape, overlaps

STATE_VERSION = 1
DEPLOYMENT_DEPTH = 12  # Until missions: each player sets up within this many inches of their own long edge
MODEL_FIELDS = ('wounds_left', 'alive', 'position')  # What an event can change about a model


class ActionError(ValueError):
    """An action the rules don't allow; its message says why, for the player."""


def new_game(armies):
    """A game between frozen armies (snapshot.freeze_army), the first army's player going first."""
    state = {'version': STATE_VERSION, 'players': [], 'units': []}
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
                                   'parts': copy.deepcopy(unit['parts']), 'models': models})
    return state


def unit_by_id(state, unit_id):
    return next(u for u in state['units'] if u['id'] == unit_id)


def resolve_action(state, action, dice):
    """Roll an action and return its event ({'action', 'rolls', 'models', ...}); `state` is not changed."""
    before = {m['id']: {k: m[k] for k in MODEL_FIELDS} for u in state['units'] for m in u['models']}
    state = copy.deepcopy(state)
    start = len(dice.log)
    kind = action.get('type')
    if kind == 'shoot':
        detail = _shoot(state, action, dice)
    elif kind == 'place':
        detail = _place(state, action)
    else:
        raise ActionError(f'Unknown action: {kind}')
    changed = {}
    for unit in state['units']:
        for m in unit['models']:
            now = {k: m[k] for k in MODEL_FIELDS}
            if now != before[m['id']]:
                changed[m['id']] = now
    return {'action': action, 'rolls': [asdict(g) for g in dice.log[start:]], 'models': changed, **detail}


def apply_event(state, event):
    """The state after an event: its results applied, no dice rolled."""
    state = copy.deepcopy(state)
    for unit in state['units']:
        for model in unit['models']:
            if model['id'] in event['models']:
                model.update(event['models'][model['id']])
    return state


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
    living = {m['id']: m for m in unit['models'] if m['alive']}
    spots = {}
    for spot in action.get('models') or []:
        try:
            spots[spot['id']] = {'x': float(spot['x']), 'y': float(spot['y']), 'angle': float(spot.get('angle') or 0)}
        except (KeyError, TypeError, ValueError):
            raise ActionError('Each model needs an id, x and y.')
    if set(spots) != set(living):
        raise ActionError(f'Place every model of {unit["name"]} at once.')
    for spot in spots.values():  # Whole numbers stay whole in the saved game
        for key in ('x', 'y', 'angle'):
            if spot[key].is_integer():
                spot[key] = int(spot[key])

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
    return {}
