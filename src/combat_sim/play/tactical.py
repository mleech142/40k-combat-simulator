"""
Tactical secondary missions in a game: each Tactical player's deck (shuffled when they choose Tactical),
two cards drawn at the start of each of their Command phases (kept until scored or discarded), the cards'
"when drawn" rules and choices (Beacon's unit, A Tempting Target's objective, Burden of Trust's guards),
holding a card back from scoring, discarding at the end of your turn for 1CP, and the once-per-battle
discard-and-draw for 1CP. Also the facts the Tactical cards are scored on (`facts`).

A player's Tactical state, state['mission']['tactical'][str(player)]: {'deck': [card keys, top first],
'active': [{'key', 'hold', 'discard', 'redraw' (why it may be redrawn now, or None)}], 'discarded': [...],
'redraw_used', 'beacon' (unit id), 'tempting' (objective id), 'guards' {objective id: unit id}}. Card keys
here are the TACTICAL keys; scoring uses missions.TACTICAL_PREFIX + key.
"""

from . import game as g
from . import mission_state as ms
from .battlefields import inside, outline_points
from .geometry import base_distance, base_shape
from .missions import BIG_MODEL_WOUNDS, BIG_UNIT, CARD_ACTIONS, TACTICAL, TACTICAL_PREFIX

DRAW = 2  # Cards drawn at the start of each of your Command phases
EDGE = 6  # Outflank: within this of a battlefield edge
CENTRE_REACH = 3  # Centre Ground: within this of the centre of the battlefield


def new_deck(dice):
    deck = list(TACTICAL)
    dice.rng.shuffle(deck)
    return {'deck': deck, 'active': [], 'discarded': [], 'redraw_used': False, 'beacon': None, 'tempting': None, 'guards': {}}


def mine(state, player):
    return ((state.get('mission') or {}).get('tactical') or {}).get(str(player))


def active_keys(state, player):
    deck = mine(state, player)
    return [c['key'] for c in deck['active']] if deck else []


# --- Drawing ---------------------------------------------------------------------------------------------------

def start_of_command(state, dice):
    """The player whose turn it is draws two cards (a Tactical player); Burden of Trust's guards are chosen
    again; the cards' when-drawn rules apply. The names of the cards drawn (None for a Fixed player)."""
    player = state['turn']['player']
    deck = mine(state, player)
    if not deck:
        return
    for card in deck['active']:
        card['redraw'] = None  # Only just-drawn cards can be redrawn
    drawn = [_draw(state, player, dice) for _ in range(DRAW)]
    if 'burden-of-trust' in active_keys(state, player):
        _auto_guards(state, player)
    return [TACTICAL[c['key']][0] for c in drawn if c]


def _draw(state, player, dice, depth=0):
    deck = mine(state, player)
    if not deck['deck']:
        return None
    key = deck['deck'].pop(0)
    rules = TACTICAL[key][2]
    if rules.get('must_redraw') == 'round_1' and state['turn']['round'] == 1 and depth < len(TACTICAL):
        _shuffle_back(deck, key, dice)
        return _draw(state, player, dice, depth + 1)
    card = {'key': key, 'hold': False, 'discard': False, 'redraw': _may_redraw(state, player, key)}
    deck['active'].append(card)
    choose = rules.get('choose')
    if choose == 'beacon' and _own_units(state, player):
        ms.push_choice(state, {'player': player, 'kind': 'beacon'})
    elif choose == 'tempting' and tempting_options(state, player):
        ms.push_choice(state, {'player': 1 - player, 'kind': 'tempting', 'for': player})
    elif choose == 'guards':
        _auto_guards(state, player)
    return card


def _shuffle_back(deck, key, dice):
    deck['deck'].insert(dice.rng.randint(0, len(deck['deck'])), key)


def _may_redraw(state, player, key):
    """Why a just-drawn card may be swapped for a new one, or None."""
    rule = TACTICAL[key][2].get('redraw')
    if not rule:
        return None
    if rule == 'round_1':
        return 'It is the first battle round.' if state['turn']['round'] == 1 else None
    if rule.startswith('with:'):
        other = rule.split(':', 1)[1]
        return f'{TACTICAL[other][0]} is active for you.' if other in active_keys(state, player) else None
    enemies = [u for u in state['units'] if u['player'] != player and ms._alive(u)]
    if rule == 'no_big_units' and not any(len(u['models']) >= BIG_UNIT for u in enemies):
        return f'No enemy unit of {BIG_UNIT}+ models is on the battlefield.'
    if rule == 'no_big_models' and not any((u['parts'][m['part']]['stats'].get('wounds') or 0) >= BIG_MODEL_WOUNDS
                                           for u in enemies for m in u['models'] if m['alive']):
        return f'No enemy model with {BIG_MODEL_WOUNDS}+ wounds is on the battlefield.'
    return None


# --- What players do with their cards ------------------------------------------------------------------------

def card_action(state, action, dice):
    """A Tactical player's choice about one of their active cards: 'hold' / 'unhold' (score it later), 'discard'
    / 'keep' (discard it at the end of your turn for 1CP), 'redraw' (a just-drawn card's own rule), or
    'redraw_cp' (once per battle, in your Command phase, 1CP: discard it and draw a new one)."""
    try:
        player = int(action.get('player'))
    except (TypeError, ValueError):
        raise g.ActionError('Say whose card it is.')
    deck = mine(state, player)
    card = next((c for c in (deck or {}).get('active', []) if c['key'] == action.get('card')), None)
    if card is None:
        raise g.ActionError('That card is not active for that player.')
    op = action.get('op')
    turn = state['turn']
    name = TACTICAL[card['key']][0]
    if op in ('hold', 'unhold'):
        card['hold'] = op == 'hold'
        return {'card': name, 'op': op, 'player': player}
    if op in ('discard', 'keep'):
        if turn['player'] != player:
            raise g.ActionError('Cards are discarded for CP at the end of your own turn.')
        card['discard'] = op == 'discard'
        return {'card': name, 'op': op, 'player': player}
    if turn['player'] != player or turn['phase'] != 'command':
        raise g.ActionError('Cards are redrawn in your own Command phase.')
    if op == 'redraw':
        if not card['redraw']:
            raise g.ActionError(f'{name} can\'t be redrawn now.')
        deck['active'].remove(card)
        _forget(deck, card['key'])
        _shuffle_back(deck, card['key'], dice)
        new = _draw(state, player, dice)
        return {'card': name, 'op': op, 'player': player, 'drew': TACTICAL[new['key']][0] if new else None}
    if op == 'redraw_cp':
        if deck['redraw_used']:
            raise g.ActionError('The 1CP discard and draw is once per battle.')
        if state['players'][player].get('cp', 0) < 1:
            raise g.ActionError('It costs 1CP.')
        state['players'][player]['cp'] -= 1
        deck['redraw_used'] = True
        discard(state, player, card['key'])
        new = _draw(state, player, dice)
        return {'card': name, 'op': op, 'player': player, 'drew': TACTICAL[new['key']][0] if new else None}
    raise g.ActionError('Choose hold, discard, redraw or redraw_cp.')


def discard(state, player, key):
    deck = mine(state, player)
    deck['active'] = [c for c in deck['active'] if c['key'] != key]
    deck['discarded'].append(key)
    _forget(deck, key)


def _forget(deck, key):
    """A card's own choices go with it."""
    if key == 'beacon':
        deck['beacon'] = None
    if key == 'a-tempting-target':
        deck['tempting'] = None
    if key == 'burden-of-trust':
        deck['guards'] = {}


def end_of_turn_discards(state, report):
    """After scoring at the end of your turn: the cards you marked are discarded, for 1CP."""
    player = state['turn']['player']
    deck = mine(state, player)
    if not deck:
        return
    marked = [c['key'] for c in deck['active'] if c['discard']]
    for key in marked:
        discard(state, player, key)
    if marked:
        state['players'][player]['cp'] = state['players'][player].get('cp', 0) + 1
        report.setdefault('discards', []).append({'player': player, 'cards': [TACTICAL[k][0] for k in marked], 'cp': 1})


def scored(state, player, key):
    """A Tactical card whose VP were gained is achieved: discarded."""
    if key.startswith(TACTICAL_PREFIX) and key[len(TACTICAL_PREFIX):] in active_keys(state, player):
        discard(state, player, key[len(TACTICAL_PREFIX):])


def held(state, player, key):
    """Whether a Tactical card is being held back from scoring."""
    deck = mine(state, player)
    return bool(deck) and any(c['key'] == key and c['hold'] for c in deck['active'])


# --- The cards' choices --------------------------------------------------------------------------------------

def _own_units(state, player):
    return [u['id'] for u in state['units'] if u['player'] == player and ms._alive(u)]


def tempting_options(state, player):
    """Objectives in No Man's Land other than home objectives (a tempting target for `player`)."""
    return [o['id'] for o in state.get('objectives', []) if o['kind'] != 'home' and in_nml(state, (o['x'], o['y']))]


def choose_beacon(state, action):
    choice = (state.get('mission') or {}).get('choice') or {}
    if choice.get('kind') != 'beacon':
        raise g.ActionError('There is no beacon to choose now.')
    if action.get('unit') not in _own_units(state, choice['player']):
        raise g.ActionError('Choose one of your units on the battlefield as the beacon.')
    mine(state, choice['player'])['beacon'] = action['unit']
    ms.next_choice(state)
    return {'beacon': action['unit'], 'player': choice['player']}


def choose_tempting(state, action):
    choice = (state.get('mission') or {}).get('choice') or {}
    if choice.get('kind') != 'tempting':
        raise g.ActionError('There is no tempting target to choose now.')
    if action.get('objective') not in tempting_options(state, choice['for']):
        raise g.ActionError("Choose an objective in No Man's Land (not a home objective).")
    mine(state, choice['for'])['tempting'] = action['objective']
    ms.next_choice(state)
    return {'tempting': action['objective'], 'player': choice['player'], 'for': choice['for']}


def set_guards(state, action):
    """Burden of Trust: the units guarding objectives (one per objective), until the start of your next turn."""
    try:
        player = int(action.get('player'))
    except (TypeError, ValueError):
        raise g.ActionError('Say whose guards these are.')
    if 'burden-of-trust' not in active_keys(state, player):
        raise g.ActionError('Burden of Trust is not active for that player.')
    guards = action.get('guards') or {}
    objectives = {o['id'] for o in state.get('objectives', [])}
    units = set(_own_units(state, player))
    if any(o not in objectives or (u is not None and u not in units) for o, u in guards.items()):
        raise g.ActionError('Each guard is one of your units on the battlefield, for an objective.')
    mine(state, player)['guards'] = {o: u for o, u in guards.items() if u}
    return {'guards': mine(state, player)['guards'], 'player': player}


def _auto_guards(state, player):
    """Each objective guarded by one of your units within its range, to start with (players can change it)."""
    guards = {}
    for objective in state.get('objectives', []):
        here = [u for u in state['units'] if u['player'] == player and ms._alive(u) and ms._in_range_of(state, u, objective)]
        if here:
            guards[objective['id']] = here[0]['id']
    mine(state, player)['guards'] = guards


def card_actions(state, player):
    """The actions `player`'s active Tactical cards offer (Plunder, Cleanse)."""
    return [CARD_ACTIONS[k] for k in active_keys(state, player) if k in CARD_ACTIONS]


# --- What the cards are scored on --------------------------------------------------------------------------------

def in_nml(state, point):
    return not any(inside(point, g.deployment_zone(state, p)) for p in (0, 1))


def _points(unit):
    return [p for s in g._placed_shapes(unit) for p in outline_points(s)]


def _counts(unit):
    """Units that count for the Tactical cards that say so: not Aircraft, not battle-shocked."""
    return ms._alive(unit) and not (unit.get('status') or {}).get('shocked') and \
        not any(g._has(p['keywords'], 'Aircraft') for p in unit['parts'])


def _partly_in_zone(state, unit, player):
    zone = g.deployment_zone(state, player)
    return any(inside(p, zone) for p in _points(unit))


def _wholly_in_zone(state, unit, player):
    zone = g.deployment_zone(state, player)
    points = _points(unit)
    return bool(points) and all(inside(p, zone) for p in points)


def _partly_in_territory(state, unit, player):
    side = ms.territory_side(state, player)
    return any(ms._side(state, p) == side for p in _points(unit))


def _edges_near(unit):
    """Which battlefield edges (0 left, 1 right, 2 bottom, 3 top) a unit is within 6" of."""
    from .geometry import BOARD_DEPTH, BOARD_WIDTH
    near = set()
    for x, y in _points(unit):
        if x <= EDGE:
            near.add(0)
        if x >= BOARD_WIDTH - EDGE:
            near.add(1)
        if y <= EDGE:
            near.add(2)
        if y >= BOARD_DEPTH - EDGE:
            near.add(3)
    return near


def facts(state, player):
    """The Tactical cards' facts for `player` (see missions.Context)."""
    deck = mine(state, player) or {}
    units = [u for u in state['units'] if ms._alive(u)]
    counting = [u for u in units if _counts(u)]
    friendly = [u for u in counting if u['player'] == player]
    nml = [sum(1 for u in counting if u['player'] == p and all(in_nml(state, pt) for pt in _points(u))) for p in (player, 1 - player)]
    # Outflank
    outside = [u for u in friendly if not _partly_in_territory(state, u, player)]
    flankers = [(u, _edges_near(u)) for u in friendly]
    level = 1 if any(_edges_near(u) for u in outside) else 0
    opposite = {0: 1, 1: 0, 2: 3, 3: 2}
    for a, near_a in flankers:
        for b, near_b in flankers:
            if a is not b and any(opposite[e] in near_b for e in near_a) and (a in outside or b in outside):
                level = 2
    # Beacon
    beacon = ms.state_unit(state, deck.get('beacon')) if deck.get('beacon') else None
    beacon_level = 0
    if beacon is not None and ms._alive(beacon) and not _partly_in_zone(state, beacon, player):
        beacon_level = 1 if _partly_in_territory(state, beacon, player) else 2
    # Centre Ground
    centre = base_shape({'length_mm': 0.001, 'width_mm': 0.001}, *ms.CENTRE)
    centre_mine = any(base_distance(s, centre) <= CENTRE_REACH + 1e-9 for u in friendly for s in g._placed_shapes(u))
    enemy_dist = min((base_distance(s, centre) for u in units if u['player'] != player for s in g._placed_shapes(u)), default=99)
    # Characters, targets, guards
    enemy_characters = [m for u in state['units'] if u['player'] != player for m in u['models']
                        if g._has(u['parts'][m['part']]['keywords'], 'Character')]
    objectives = {o['id']: o for o in state.get('objectives', [])}
    tempting = objectives.get(deck.get('tempting'))
    guarded = 0
    for objective_id, unit_id in (deck.get('guards') or {}).items():
        unit, objective = ms.state_unit(state, unit_id), objectives.get(objective_id)
        if unit and objective and ms._alive(unit) and objective.get('controller') == player and ms._in_range_of(state, unit, objective):
            guarded += 1
    expansions = [o for o in objectives.values() if o['kind'] == 'expansion']
    return {'nml': tuple(nml), 'nml_objectives': {o['id'] for o in objectives.values() if in_nml(state, (o['x'], o['y']))},
            'outflank': level, 'beacon': beacon_level,
            'enemy_in_my_zone': any(u['player'] != player and _partly_in_zone(state, u, player) for u in units),
            'centre_mine': centre_mine, 'enemy_from_centre': enemy_dist,
            'all_enemy_characters_dead': bool(enemy_characters) and not any(m['alive'] for m in enemy_characters),
            'tempting_held': bool(tempting) and tempting.get('controller') == player,
            'behind_lines': sum(1 for u in friendly if _wholly_in_zone(state, u, 1 - player)),
            'guarded': guarded,
            'held_expansions_all': bool(expansions) and all(o.get('controller') == player for o in expansions)}
