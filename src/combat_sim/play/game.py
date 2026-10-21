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

STATE_VERSION = 1


def new_game(armies):
    """A game between frozen armies (snapshot.freeze_army), the first army's player going first."""
    state = {'version': STATE_VERSION, 'players': [], 'units': []}
    for player, army in enumerate(armies):
        state['players'].append({'name': army['name'], 'faction': army['faction']})
        for unit in army['units']:
            unit_id = f'p{player + 1}-{unit["uid"]}'
            models = [{'id': f'{unit_id}-{part_index}-{n + 1}', 'part': part_index,
                       'wounds_left': part['stats']['wounds'], 'alive': True,
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
    state = copy.deepcopy(state)
    start = len(dice.log)
    if action['type'] == 'shoot':
        detail = _shoot(state, action, dice)
    else:
        raise ValueError(f'Unknown action: {action["type"]}')
    changed = {m['id']: {'wounds_left': m['wounds_left'], 'alive': m['alive']}
               for m in unit_by_id(state, action['target'])['models']}
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
    """The model the next wound goes to: one already damaged, else bodyguard before leaders, in listed order."""
    alive = [m for m in unit['models'] if m['alive']]
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
