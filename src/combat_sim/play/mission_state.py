"""
Missions in a game (missions.py has the cards): which primary each player plays, what happened this turn
(objective control and where each unit stood at the start of the turn, units destroyed), Punishment's
condemned units, and VP scored at each line's timing within the caps.

state['mission'] (None when either army has no force disposition: kill points instead) is
{'primaries': [key, key], 'secondaries': {'0': None | {'mode': 'fixed', 'cards': [key, key]}, '1': ...},
'start': {'control': [[ids], [ids]], 'units': {uid: {'in_range', 'central', 'terrain'}}}, 'destroyed': [...],
'destroyed_before': [...], 'models_destroyed': [...], 'condemned': {'0': [...], '1': [...]},
'choice': None | {'player', 'kind': 'condemn'}}. Each player's VP: state['players'][p]['vp'] =
{'primary': n, 'rounds': {'1': n, ...}, 'battle': n, 'secondary': n, 'secondary_rounds': {...},
'cards': {key: n}, 'adjust': n}.
game.py calls these hooks; everything here is plain data, replayed through events like the rest.
"""

from . import game as g
from .battlefields import outline_points
from .geometry import base_distance, base_shape
from .missions import (FIXED_CARD_CAP, FIXED_CARDS, PRIMARY_GAME_CAP, PRIMARY_ROUND_CAP, SECONDARIES, SECONDARY_GAME_CAP,
                       SECONDARY_ROUND_CAP, Context, card, primary_for, score_lines)

CENTRE = (30, 22)
CENTRE_CLEARANCE = 6  # Reconnaissance Sweep: units not within this of the centre
CONDEMN_MOST = 3


def new_mission(armies):
    """The game's missions from the two frozen armies' force dispositions, or None if either has none."""
    dispositions = [army.get('disposition') for army in armies]
    if len(dispositions) != 2 or not all(dispositions):
        return None
    primaries = [primary_for(dispositions[0], dispositions[1]), primary_for(dispositions[1], dispositions[0])]
    if not all(primaries):
        return None
    return {'primaries': primaries, 'secondaries': {'0': None, '1': None}, 'start': None, 'destroyed': [],
            'destroyed_before': [], 'models_destroyed': [], 'condemned': {'0': [], '1': []}, 'choice': None}


def new_vp():
    return {'primary': 0, 'rounds': {}, 'battle': 0, 'secondary': 0, 'secondary_rounds': {}, 'cards': {}, 'adjust': 0}


def total(state, player):
    vp = state['players'][player].get('vp') or new_vp()
    return vp['primary'] + vp.get('secondary', 0) + vp.get('adjust', 0)


# --- Where things are ---------------------------------------------------------------------------------------

def _unit_facts(state, unit):
    shapes = g._placed_shapes(unit)
    objectives = state.get('objectives', [])
    return {'in_range': any(g.in_range(s, o, state) for s in shapes for o in objectives),
            'central': any(g.in_range(s, o, state) for s in shapes for o in objectives if o['kind'] == 'central'),
            'terrain': any(g.within(s, area) for s in shapes for _, area in g.terrain_areas(state))}


def _side(state, point):
    """Which side of the dividing line a point is on: +1, -1 or 0 (on it)."""
    battlefield = state.get('battlefield') or {}
    (ax, ay), (bx, by) = battlefield.get('divider') or ((0, 22), (60, 22))
    cross = (bx - ax) * (point[1] - ay) - (by - ay) * (point[0] - ax)
    return 0 if abs(cross) < 1e-9 else (1 if cross > 0 else -1)


def territory_side(state, player):
    """The side of the dividing line that is `player`'s territory (the half holding their deployment zone)."""
    zone = g.deployment_zone(state, player)
    return _side(state, (sum(x for x, _ in zone) / len(zone), sum(y for _, y in zone) / len(zone)))


def wholly_in_territory(state, unit, player):
    side = territory_side(state, player)
    points = [p for s in g._placed_shapes(unit) for p in outline_points(s)]
    return bool(points) and all(_side(state, p) == side for p in points)


def _quarter(point):
    return (point[0] > CENTRE[0]) * 2 + (point[1] > CENTRE[1])


def quarters(state, player, presence=False):
    """Table quarters holding one of `player`'s units wholly within them, none of its models within 6"
    of the centre of the battlefield; for a `presence` (Engage on All Fronts), not counting Aircraft or
    battle-shocked units."""
    centre = base_shape({'length_mm': 0.001, 'width_mm': 0.001}, *CENTRE)
    held = set()
    for unit in state['units']:
        shapes = g._placed_shapes(unit)
        if unit['player'] != player or not shapes:
            continue
        if presence and ((unit.get('status') or {}).get('shocked') or any(g._has(p['keywords'], 'Aircraft') for p in unit['parts'])):
            continue
        points = [p for s in shapes for p in outline_points(s)]
        found = {_quarter(p) for p in points}
        on_line = any(abs(p[0] - CENTRE[0]) < 1e-9 or abs(p[1] - CENTRE[1]) < 1e-9 for p in points)
        if len(found) == 1 and not on_line and all(base_distance(s, centre) > CENTRE_CLEARANCE for s in shapes):
            held |= found
    return len(held)


def context(state, player):
    """The facts `player`'s primary is scored on now."""
    mission = state['mission']
    objectives = state.get('objectives', [])
    start = (mission.get('start') or {}).get('control') or [[], []]
    by_id = {o['id']: o for o in objectives}
    enemy_side = territory_side(state, 1 - player)
    return Context(
        player=player,
        held=[[o for o in objectives if o.get('controller') == p] for p in (0, 1)],
        held_at_start=[[by_id[i] for i in start[p] if i in by_id] for p in (0, 1)],
        in_their_territory={o['id'] for o in objectives if _side(state, (o['x'], o['y'])) == enemy_side},
        destroyed=mission['destroyed'], destroyed_before=mission['destroyed_before'],
        models_destroyed=mission.get('models_destroyed', []), quarters=quarters(state, player),
        presence=quarters(state, player, presence=True),
        enemy_wholly_in_my_territory=any(u['player'] != player and wholly_in_territory(state, u, player)
                                         for u in state['units'] if any(m['alive'] for m in u['models'])),
        condemned=mission['condemned'][str(player)])


# --- Hooks game.py calls -------------------------------------------------------------------------------------

def start_turn(state):
    """A turn begins: record who controls what and where every unit stands; last turn's losses become
    'the previous turn'; a Punishment player chooses whom to condemn."""
    mission = state.get('mission')
    if not mission:
        return
    mission['start'] = {'control': [[o['id'] for o in state.get('objectives', []) if o.get('controller') == p] for p in (0, 1)],
                        'units': {u['id']: _unit_facts(state, u) for u in state['units'] if g._placed_shapes(u)}}
    mission['destroyed_before'], mission['destroyed'], mission['models_destroyed'] = mission['destroyed'], [], []
    player = state['turn']['player']
    if mission['primaries'][player] == 'punishment':
        mission['condemned'][str(player)] = []
        if any(u['player'] != player and g._placed_shapes(u) for u in state['units']):
            mission['choice'] = {'player': player, 'kind': 'condemn'}


def log_destroyed(state, models_before, action):
    """Models that died in this action, and units whose last model did, with what destroyed them and where
    they stood (`models_before`: each model's fields before the action)."""
    mission = state.get('mission')
    if not mission:
        return
    by = state_unit(state, action.get('unit')) if action.get('type') in ('shoot', 'fight') else None
    for unit in state['units']:
        for model in unit['models']:
            if models_before[model['id']]['alive'] and not model['alive']:
                part = unit['parts'][model['part']]
                mission.setdefault('models_destroyed', []).append({
                    'model': model['id'], 'unit': unit['id'], 'player': unit['player'],
                    'character': g._has(part['keywords'], 'Character'), 'wounds': part['stats'].get('wounds')})
        alive_before = any(models_before[m['id']]['alive'] for m in unit['models'])
        if alive_before and not any(m['alive'] for m in unit['models']):
            facts = ((mission.get('start') or {}).get('units') or {}).get(unit['id'], {})
            mission['destroyed'].append({
                'unit': unit['id'], 'player': unit['player'], 'by': by['id'] if by else None,
                'by_player': by['player'] if by else None,
                'by_in_range': bool(by) and _unit_facts(state, by)['in_range'],
                'in_range': facts.get('in_range', False), 'central': facts.get('central', False),
                'terrain': facts.get('terrain', False), 'strength': len(unit['models'])})


def state_unit(state, unit_id):
    return next((u for u in state['units'] if u['id'] == unit_id), None)


def score(state, when, players, report):
    """Score the lines due now for each of `players` (the player whose turn it is first): their primary,
    then their secondaries, within the caps; each line goes in report['vp'] (with 'kind')."""
    mission = state.get('mission')
    if not mission:
        return
    round_number = str(state['turn']['round'])
    for player in players:
        vp = state['players'][player].setdefault('vp', new_vp())
        for key in ('secondary', 'secondary_rounds', 'cards'):
            vp.setdefault(key, new_vp()[key])
        facts = context(state, player)
        cards = [('primary', mission['primaries'][player])]
        chosen = (mission.get('secondaries') or {}).get(str(player))
        cards += [('secondary', key) for key in (chosen or {}).get('cards', [])]
        for kind, key in cards:
            name = card(key)['name']
            for entry, asked, how in score_lines(key, when, int(round_number), facts):
                if kind == 'primary':
                    room = PRIMARY_GAME_CAP - vp['primary']
                    if when != 'battle':
                        room = min(room, PRIMARY_ROUND_CAP - vp['rounds'].get(round_number, 0))
                else:
                    room = min(SECONDARY_GAME_CAP - vp['secondary'], FIXED_CARD_CAP - vp['cards'].get(key, 0))
                    if when != 'battle':
                        room = min(room, SECONDARY_ROUND_CAP - vp['secondary_rounds'].get(round_number, 0))
                got = max(0, min(asked, room))
                if kind == 'primary':
                    vp['primary'] += got
                    if when == 'battle':
                        vp['battle'] += got
                    else:
                        vp['rounds'][round_number] = vp['rounds'].get(round_number, 0) + got
                else:
                    vp['secondary'] += got
                    vp['cards'][key] = vp['cards'].get(key, 0) + got
                    if when != 'battle':
                        vp['secondary_rounds'][round_number] = vp['secondary_rounds'].get(round_number, 0) + got
                report.setdefault('vp', []).append({'player': player, 'kind': kind, 'mission': name, 'line': entry['id'],
                                                    'text': entry['text'], 'how': how, 'vp': got, 'asked': asked, 'when': when})


def choose_secondaries(state, action):
    """Setup: a player chooses Fixed secondary missions and their two cards (Tactical comes later). Once
    both have chosen, the choices are revealed and deployment begins, the defender first."""
    mission = state.get('mission')
    turn = state['turn']
    if not mission or turn['phase'] != 'setup' or turn.get('step') != 'secondaries':
        raise g.ActionError('Secondary missions are chosen during setup, after the attacker and defender are decided.')
    try:
        player = int(action.get('player'))
        state['players'][player]
    except (TypeError, ValueError, IndexError):
        raise g.ActionError('Say which player is choosing.')
    if mission['secondaries'].get(str(player)):
        raise g.ActionError(f'{state["players"][player]["name"]} has already chosen.')
    if action.get('mode') != 'fixed':
        raise g.ActionError('Tactical secondary missions come later: choose Fixed for now.')
    cards = list(dict.fromkeys(action.get('cards') or []))
    if len(cards) != FIXED_CARDS or any(key not in SECONDARIES for key in cards):
        raise g.ActionError(f'Choose {FIXED_CARDS} different Fixed secondary missions.')
    mission['secondaries'][str(player)] = {'mode': 'fixed', 'cards': cards}
    if all(mission['secondaries'].get(str(p)) for p in (0, 1)):
        state['turn'] = {'phase': 'deployment', 'round': 0, 'player': state['roles']['defender']}
        return {'secondaries_chosen': player, 'revealed': {p: mission['secondaries'][str(p)] for p in ('0', '1')}}
    return {'secondaries_chosen': player}


def require_no_choice(state):
    choice = (state.get('mission') or {}).get('choice')
    if choice:
        name = state['players'][choice['player']]['name']
        raise g.ActionError(f'{name} must first condemn 1 to {CONDEMN_MOST} enemy units (Punishment), in the Mission panel.')


def condemn_options(state):
    """{'units': [enemy unit ids that can be condemned], 'any': True when none qualify (choose any one)}."""
    mission = state.get('mission') or {}
    choice = mission.get('choice')
    if not choice:
        return None
    player = choice['player']
    on_board = [u for u in state['units'] if u['player'] != player and g._placed_shapes(u)]
    killers = {d['by'] for d in mission.get('destroyed_before', []) if d['player'] == player and d['by']}
    eligible = [u['id'] for u in on_board if _unit_facts(state, u)['in_range'] or u['id'] in killers]
    return {'units': eligible or [u['id'] for u in on_board], 'any': not eligible}


def condemn(state, action):
    """Punishment: the player whose turn it is condemns 1-3 eligible enemy units (or any one if none are)."""
    options = condemn_options(state)
    if not options:
        raise g.ActionError('There is nothing to condemn now.')
    chosen = list(dict.fromkeys(action.get('units') or []))
    most = 1 if options['any'] else CONDEMN_MOST
    if not 1 <= len(chosen) <= most:
        raise g.ActionError(f'Condemn {"one enemy unit" if most == 1 else f"1 to {most} enemy units"}.')
    if any(uid not in options['units'] for uid in chosen):
        raise g.ActionError('Condemn enemy units within range of objectives, or that destroyed one of your units last turn.')
    mission = state['mission']
    mission['condemned'][str(mission['choice']['player'])] = chosen
    mission['choice'] = None
    return {'condemned': chosen}
