"""
Missions in a game (missions.py has the cards): which primary each player plays, what happened this turn
(objective control and where each unit stood at the start of the turn, units destroyed), Punishment's
condemned units, and VP scored at each line's timing within the caps.

state['mission'] (None when either army has no force disposition: kill points instead) is
{'primaries': [key, key], 'secondaries': {'0': None | {'mode': 'fixed', 'cards': [key, key]}, '1': ...},
'start': {'control': [[ids], [ids]], 'units': {uid: {'in_range', 'central', 'terrain'}}}, 'destroyed': [...],
'destroyed_before': [...], 'models_destroyed': [...], 'condemned': {'0': [...], '1': [...]},
'choice': None | {'player', 'kind': 'condemn' | 'locate' | 'beacon' | 'tempting'}, 'queue': [choices after it],
'tactical': {'0': None | a Tactical player's deck (tactical.py), '1': ...}, 'markers': [operation markers], 'started': [actions
started this turn], 'actions': [actions under way, to complete at the end of the turn], 'completed': [actions
completed this turn], 'surveilled': [enemy units surveilled this turn], 'consecration': [consecration units]}.
Each player's VP: state['players'][p]['vp'] =
{'primary': n, 'rounds': {'1': n, ...}, 'battle': n, 'secondary': n, 'secondary_rounds': {...},
'cards': {key: n}, 'adjust': n}.
game.py calls these hooks; everything here is plain data, replayed through events like the rest.
"""

from . import game as g
from . import tactical
from .battlefields import outline_points
from .geometry import base_distance, base_shape
from .missions import (ACTIONS, FIXED_CARD_CAP, FIXED_CARDS, LOCATE_MARKERS, PRIMARY_ACTIONS, PRIMARY_GAME_CAP,
                       PRIMARY_ROUND_CAP, SECONDARIES, SECONDARY_GAME_CAP, SECONDARY_ROUND_CAP, TACTICAL_PREFIX, Context,
                       card, primary_for, score_lines)

CENTRE = (30, 22)
CENTRE_CLEARANCE = 6  # Reconnaissance Sweep: units not within this of the centre
CONDEMN_MOST = 3
SURVEIL_RANGE = 18  # Surveil the Foe: enemy units within this of the surveilling unit, and visible to it
MARKER_SPACING = 1.2  # Operation markers by an objective are set this far apart


def new_mission(armies):
    """The game's missions from the two frozen armies' force dispositions, or None if either has none."""
    dispositions = [army.get('disposition') for army in armies]
    if len(dispositions) != 2 or not all(dispositions):
        return None
    primaries = [primary_for(dispositions[0], dispositions[1]), primary_for(dispositions[1], dispositions[0])]
    if not all(primaries):
        return None
    return {'primaries': primaries, 'secondaries': {'0': None, '1': None}, 'start': None, 'destroyed': [],
            'destroyed_before': [], 'models_destroyed': [], 'condemned': {'0': [], '1': []}, 'choice': None,
            'markers': [], 'marker_seq': 0, 'started': [], 'actions': [], 'completed': [], 'surveilled': [],
            'consecration': [], 'queue': [], 'tactical': {'0': None, '1': None}}


def push_choice(state, choice):
    """A required choice: now if none is waiting, else after the ones that are."""
    mission = state['mission']
    if mission.get('choice'):
        mission.setdefault('queue', []).append(choice)
    else:
        mission['choice'] = choice


def next_choice(state):
    """The choice waiting was made: the next one, if any."""
    mission = state['mission']
    queue = mission.get('queue') or []
    mission['choice'] = queue.pop(0) if queue else None


def new_vp():
    return {'primary': 0, 'rounds': {}, 'battle': 0, 'secondary': 0, 'secondary_rounds': {}, 'cards': {}, 'adjust': 0}


def total(state, player):
    vp = state['players'][player].get('vp') or new_vp()
    return vp['primary'] + vp.get('secondary', 0) + vp.get('adjust', 0)


# --- Where things are ---------------------------------------------------------------------------------------

def _unit_facts(state, unit):
    shapes = g._placed_shapes(unit)
    objectives = state.get('objectives', [])
    areas = _areas_within(state, unit)
    return {'in_range': any(g.in_range(s, o, state) for s in shapes for o in objectives),
            'central': any(g.in_range(s, o, state) for s in shapes for o in objectives if o['kind'] == 'central'),
            'terrain': bool(areas), 'terrain_ids': areas}


def _areas_within(state, unit):
    """The ids of the terrain areas a unit is within (any of its models)."""
    shapes = g._placed_shapes(unit)
    return [piece['id'] for piece, area in g.terrain_areas(state) if any(g.within(s, area) for s in shapes)]


def _in_range_of(state, unit, objective):
    return any(g.in_range(s, objective, state) for s in g._placed_shapes(unit))


def _area_centre(state, terrain_id):
    area = next(a for piece, a in g.terrain_areas(state) if piece['id'] == terrain_id)
    return (sum(x for x, _ in area.points) / len(area.points), sum(y for _, y in area.points) / len(area.points))


def _area_in_zone(state, terrain_id, player):
    """Whether a terrain area is within `player`'s deployment zone (its middle is)."""
    from .battlefields import inside
    return inside(_area_centre(state, terrain_id), g.deployment_zone(state, player))


def _alive(unit):
    return any(m['alive'] for m in unit['models']) and bool(g._placed_shapes(unit))


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
        condemned=mission['condemned'][str(player)], **_marker_facts(state, player), **tactical.facts(state, player))


def _marker_facts(state, player):
    """What `player`'s action and marker lines read: the markers, actions completed, areas trapped this turn,
    the lone-marker conditions, surveillance, and markers on the central objectives they hold."""
    mission = state['mission']
    markers = mission.get('markers', [])
    objectives = {o['id']: o for o in state.get('objectives', [])}
    starts = (mission.get('start') or {}).get('units') or {}
    trapped = {m['terrain'] for m in markers if m['player'] == player and m['tag'] == 'trapped'}
    killed_in_trapped = any(d['player'] != player and set(starts.get(d['unit'], {}).get('terrain_ids', [])) & trapped
                            for d in mission['destroyed'])

    def lone(owner):
        theirs = [m for m in markers if m['player'] == owner]
        if len(theirs) != 1 or not theirs[0].get('terrain'):
            return False
        area = theirs[0]['terrain']
        here = [u for u in state['units'] if _alive(u) and area in _areas_within(state, u)]
        return any(u['player'] == player for u in here) and not any(u['player'] != player for u in here)

    marked = {m['objective'] for m in markers if m.get('objective')}
    surveilled = [state_unit(state, s['unit']) for s in mission.get('surveilled', []) if s['player'] == player]
    surveil_ok = any(u is not None and _alive(u) and not any(_in_range_of(state, u, objectives[o]) for o in marked if o in objectives)
                     for u in surveilled)
    return {'markers': markers, 'completed': mission.get('completed', []),
            'trapped_now': [{'terrain': c['terrain'], 'objective': c.get('is_objective', False)}
                            for c in mission.get('completed', []) if c['player'] == player and c['action'] == 'booby-trap'],
            'killed_in_trapped': bool(killed_in_trapped), 'lone_mine': lone(player), 'lone_theirs': lone(1 - player),
            'surveilled': surveil_ok,
            'markers_on_held_central': sum(1 for m in markers if m['player'] == player and m.get('objective') in objectives
                                           and objectives[m['objective']]['kind'] == 'central'
                                           and objectives[m['objective']].get('controller') == player)}


# --- Hooks game.py calls -------------------------------------------------------------------------------------

def start_turn(state, dice=None, report=None):
    """A turn begins: record who controls what and where every unit stands; last turn's losses become
    'the previous turn'; a Punishment player chooses whom to condemn."""
    mission = state.get('mission')
    if not mission:
        return
    mission['start'] = {'control': [[o['id'] for o in state.get('objectives', []) if o.get('controller') == p] for p in (0, 1)],
                        'units': {u['id']: _unit_facts(state, u) for u in state['units'] if g._placed_shapes(u)}}
    mission['destroyed_before'], mission['destroyed'], mission['models_destroyed'] = mission['destroyed'], [], []
    for key in ('started', 'actions', 'completed', 'surveilled'):
        mission[key] = []
    player = state['turn']['player']
    if mission['primaries'][player] == 'punishment':
        mission['condemned'][str(player)] = []
        if any(u['player'] != player and g._placed_shapes(u) for u in state['units']):
            push_choice(state, {'player': player, 'kind': 'condemn'})
    if dice is not None:
        drawn = tactical.start_of_command(state, dice)
        if drawn is not None and report is not None:
            report['drawn'] = {'player': player, 'cards': drawn}


def start_battle(state):
    """Deployment is over: a Locate and Deny player places their markers first (a choice in the Mission panel)."""
    mission = state.get('mission')
    if not mission:
        return
    for player in (0, 1):
        if mission['primaries'][player] == 'locate-and-deny' and locate_options(state, player):
            push_choice(state, {'player': player, 'kind': 'locate'})
            return


def locate_options(state, player):
    """Terrain areas outside `player`'s deployment zone (where Locate and Deny's markers go)."""
    return [piece['id'] for piece, _ in g.terrain_areas(state) if not _area_in_zone(state, piece['id'], player)]


def place_markers(state, action):
    """Locate and Deny: a marker in each of five terrain areas outside your deployment zone (all, if fewer)."""
    mission = state.get('mission') or {}
    choice = mission.get('choice')
    if not choice or choice['kind'] != 'locate':
        raise g.ActionError('There are no markers to place now.')
    player = choice['player']
    options = locate_options(state, player)
    chosen = list(dict.fromkeys(action.get('terrain') or []))
    needed = min(LOCATE_MARKERS, len(options))
    if len(chosen) != needed or any(t not in options for t in chosen):
        raise g.ActionError(f'Choose {needed} terrain areas outside your deployment zone.')
    for terrain in chosen:
        _add_marker(state, player, 'asset', terrain=terrain)
    next_choice(state)
    return {'markers_placed': {'player': player, 'terrain': chosen}}


def _add_marker(state, player, tag, objective=None, terrain=None):
    mission = state['mission']
    mission['marker_seq'] = mission.get('marker_seq', 0) + 1
    if objective is not None:
        o = next(o for o in state['objectives'] if o['id'] == objective)
        others = sum(1 for m in mission['markers'] if m.get('objective') == objective)
        x, y = o['x'] + MARKER_SPACING * (others + 1), o['y'] + MARKER_SPACING * 0.6
        marker = {'objective': objective, 'objective_kind': o['kind'], 'objective_owner': o.get('owner'), 'terrain': o.get('terrain')}
    else:
        x, y = _area_centre(state, terrain)
        marker = {'objective': None, 'objective_kind': None, 'objective_owner': None, 'terrain': terrain}
    marker.update(id=f'm{mission["marker_seq"]}', player=player, tag=tag, x=round(x, 2), y=round(y, 2))
    mission['markers'].append(marker)
    return marker


def _remove_markers(state, ids, report, why):
    mission = state['mission']
    gone = [m for m in mission['markers'] if m['id'] in ids]
    mission['markers'] = [m for m in mission['markers'] if m['id'] not in ids]
    if gone:
        report.setdefault('markers_removed', []).append({'markers': gone, 'why': why})


# --- Mission actions ------------------------------------------------------------------------------------------

def _start_problem(state, unit):
    """Why a unit can't start an action now (core rules 16), or None."""
    activity = unit['activity']
    keywords = lambda name: any(g._has(p['keywords'], name) for p in unit['parts'])  # noqa: E731
    if state['turn']['phase'] != 'shooting' or unit['player'] != state['turn']['player']:
        return 'Mission actions start in your Shooting phase.'
    if not _alive(unit):
        return 'It is not on the battlefield.'
    if keywords('Aircraft') or keywords('Fortification'):
        return 'Aircraft and Fortifications can\'t start actions.'
    if (unit.get('status') or {}).get('shocked'):
        return 'It is battle-shocked.'
    if not any((unit['parts'][m['part']]['stats'].get('oc') or 0) > 0 for m in unit['models'] if m['alive']):
        return 'Its OC is 0.'
    if g._is_engaged(state, unit) and not keywords('Titanic'):
        return 'It is engaged.'
    pride = g.twist_is(state, 'martial-pride') and g._battleline(unit)  # Martial Pride: Battleline may Advance first
    if activity.get('moved') == 'fall_back' or (activity.get('moved') == 'advance' and not pride):
        return f'It {"Advanced" if activity["moved"] == "advance" else "Fell Back"} this turn.'
    if activity.get('shot'):
        return 'It has shot this turn.'
    if activity.get('action'):
        return 'It started another action this turn.'
    return None


def _targets(state, unit, key):
    """[(target id, label)] an action could be started on by `unit` now."""
    mission = state['mission']
    player = unit['player']
    spec = ACTIONS[key]
    objectives = state.get('objectives', [])
    mine = [m for m in mission['markers'] if m['player'] == player]
    taken = {s['target'] for s in mission['started'] if s['player'] == player and s['action'] == key} if spec['distinct'] else set()

    def label(o):
        return f'{o["kind"].title()} objective {o["id"].split("-")[-1]}'
    if spec['on'] in ('objective', 'central'):
        found = [o for o in objectives if _in_range_of(state, unit, o)
                 and not (o['kind'] == 'home' and o.get('owner') == player)
                 and (spec['on'] == 'objective' or o['kind'] == 'central')]
        if key == 'decoy':
            found = [o for o in found if not any(m['objective'] == o['id'] and m['tag'] == 'decoyed' for m in mine)]
        if key == 'extract-intelligence':
            found = [o for o in found if not any(m['objective'] == o['id'] for m in mine)]
        return [(o['id'], label(o)) for o in found if o['id'] not in taken]
    if spec['on'] == 'trap':
        areas = [a for a in _areas_within(state, unit) if not _area_in_zone(state, a, player)]
        areas += [o['terrain'] for o in objectives if o.get('terrain') and _in_range_of(state, unit, o)
                  and not (o['kind'] == 'home' and o.get('owner') == player)]
        trapped = {m['terrain'] for m in mine if m['tag'] == 'trapped'}
        return [(a, f'Terrain area {a}') for a in dict.fromkeys(areas) if a not in trapped and a not in taken]
    if spec['on'] == 'outside_terrain':  # Plunder: a terrain area not within your territory
        side = territory_side(state, player)
        return [(a, f'Terrain area {a}') for a in _areas_within(state, unit) if _side(state, _area_centre(state, a)) != side]
    if spec['on'] == 'their_terrain':
        side = territory_side(state, 1 - player)
        return [(a, f'Terrain area {a}') for a in _areas_within(state, unit) if _side(state, _area_centre(state, a)) == side]
    if spec['on'] == 'enemy':
        surveilled = {s['unit'] for s in mission['surveilled'] if s['player'] == player}
        found = []
        for enemy in state['units']:
            if enemy['player'] == player or not _alive(enemy) or enemy['id'] in surveilled:
                continue
            if any(base_distance(a, b) <= SURVEIL_RANGE + 1e-9 and g.sees(state, a, b, enemy, model)[0]
                   for a in g._placed_shapes(unit) for model, b in g._placed_models(enemy)):
                found.append((enemy['id'], enemy['name']))
        return found
    return []


def action_options(state, unit_id):
    """For the page: [{'key', 'name', 'targets': [{'id', 'label'}], 'markers': [...] (Sensor Sweep),
    'reason'}] for the mission actions `unit_id` could start now (empty when its primary has none)."""
    mission = state.get('mission')
    unit = state_unit(state, unit_id)
    if not mission or unit is None:
        return []
    out = []
    for key in PRIMARY_ACTIONS.get(mission['primaries'][unit['player']], []) + tactical.card_actions(state, unit['player']):
        spec = ACTIONS[key]
        reason = _start_problem(state, unit)
        if not reason and state['turn']['round'] < spec['from_round']:
            reason = f'From battle round {spec["from_round"]}.'
        if not reason and spec['limit'] and sum(1 for s in mission['started'] if s['player'] == unit['player']
                                                and s['action'] == key) >= spec['limit']:
            reason = 'Already started this turn (once per turn).'
        if not reason and key == 'sensor-sweep' and len(mission['markers']) < 2:
            reason = 'Only one operation marker (or none) is on the battlefield.'
        targets = [] if reason else _targets(state, unit, key)
        if not reason and not targets:
            reason = 'Nothing it could start this on.'
        out.append({'key': key, 'name': spec['name'], 'reason': reason,
                    'targets': [{'id': t, 'label': lbl} for t, lbl in targets],
                    'markers': [{'id': m['id'], 'player': m['player'], 'tag': m['tag']} for m in mission['markers']]
                    if key == 'sensor-sweep' else []})
    return out


def start_action(state, action):
    """A unit starts a mission action on a target; Booby Trap and Surveil the Foe complete at once."""
    unit = g._unit_or_error(state, action.get('unit'))
    g._require(state, 'shooting', unit)
    options = {o['key']: o for o in action_options(state, unit['id'])}
    key = action.get('action')
    if key not in options:
        raise g.ActionError(f'{unit["name"]} can\'t start that action.')
    if options[key]['reason']:
        raise g.ActionError(f'{unit["name"]} can\'t start {ACTIONS[key]["name"]}: {options[key]["reason"]}')
    target = action.get('target')
    if target not in {t['id'] for t in options[key]['targets']}:
        raise g.ActionError(f'Choose what {unit["name"]} starts {ACTIONS[key]["name"]} on.')
    mission = state['mission']
    entry = {'player': unit['player'], 'action': key, 'unit': unit['id'], 'target': target}
    if key == 'sensor-sweep':
        if action.get('marker') not in {m['id'] for m in mission['markers']}:
            raise g.ActionError('Choose the operation marker the sensor sweep removes.')
        entry['marker'] = action['marker']
    unit['activity']['action'] = key
    mission['started'].append(entry)
    if ACTIONS[key]['completes'] == 'now':
        return {'action_started': entry, 'action_completed': _complete(state, entry, {})}
    mission['actions'].append(entry)
    return {'action_started': entry}


def _complete(state, entry, report):
    """An action's effect: markers placed or removed, statuses, and the completion recorded."""
    mission = state['mission']
    key, player = entry['action'], entry['player']
    done = dict(entry)
    if key == 'booby-trap':
        _add_marker(state, player, 'trapped', terrain=entry['target'])
        done.update(terrain=entry['target'], is_objective=any(o.get('terrain') == entry['target'] for o in state['objectives']))
    elif key == 'surveil':
        mission['surveilled'].append({'player': player, 'unit': entry['target']})
    elif key == 'sensor-sweep':
        _remove_markers(state, {entry['marker']}, report, 'sensor sweep')
    elif ACTIONS[key]['tag']:
        _add_marker(state, player, ACTIONS[key]['tag'], objective=entry['target'])
    if key == 'sabotage':
        unit = state_unit(state, entry['unit'])
        side = territory_side(state, 1 - player)
        done['their_territory'] = any(_side(state, (o['x'], o['y'])) == side and _in_range_of(state, unit, o)
                                      for o in state['objectives'])
    mission['completed'].append(done)
    return done


def complete_actions(state, report):
    """The end of a turn: the active player's actions under way complete if their units are still there
    (not battle-shocked) and their conditions hold; Consecrate marks objectives."""
    mission = state.get('mission')
    if not mission:
        return
    player = state['turn']['player']
    for entry in [a for a in mission['actions'] if a['player'] == player]:
        unit = state_unit(state, entry['unit'])
        spec = ACTIONS[entry['action']]
        ok = unit is not None and _alive(unit) and not (unit.get('status') or {}).get('shocked')
        if ok and spec['on'] in ('objective', 'central'):
            objective = next((o for o in state['objectives'] if o['id'] == entry['target']), None)
            ok = objective is not None and objective.get('controller') == player and _in_range_of(state, unit, objective)
        if ok and spec['on'] == 'their_terrain':
            ok = not any(u['player'] != player and _alive(u) and entry['target'] in _areas_within(state, u) for u in state['units'])
        if ok:
            report.setdefault('actions', []).append(dict(_complete(state, entry, report), result='completed'))
        else:
            report.setdefault('actions', []).append(dict(entry, result='not completed'))
    mission['actions'] = [a for a in mission['actions'] if a['player'] != player]
    if mission['primaries'][player] == 'consecrate':
        _consecrate(state, player, report)


def _consecrate(state, player, report):
    """Each consecration unit of `player`'s marks an unconsecrated objective in range (not their home),
    their opponent's home objective first."""
    mission = state['mission']
    consecrated = {m['objective'] for m in mission['markers'] if m['tag'] == 'consecrated'}
    left = []
    for uid in mission.get('consecration', []):
        unit = state_unit(state, uid)
        if unit is None or unit['player'] != player or not _alive(unit):
            continue
        choices = [o for o in state['objectives'] if _in_range_of(state, unit, o) and o['id'] not in consecrated
                   and not (o['kind'] == 'home' and o.get('owner') == player)]
        if not choices:
            left.append(uid)
            continue
        objective = sorted(choices, key=lambda o: not (o['kind'] == 'home' and o.get('owner') != player))[0]
        _add_marker(state, player, 'consecrated', objective=objective['id'])
        consecrated.add(objective['id'])
        report.setdefault('actions', []).append({'player': player, 'action': 'consecrate', 'unit': uid, 'target': objective['id'],
                                                 'result': 'consecrated'})
    mission['consecration'] = left + [u for u in mission.get('consecration', []) if state_unit(state, u)
                                      and state_unit(state, u)['player'] != player]


def surveil_moves(state, start_key, report):
    """Surveil the Foe: each unit of that player's that moved this phase or step and ends within range of
    an objective removes the opponent's markers there (when moves lock)."""
    mission = state.get('mission')
    if not mission:
        return
    for player in (0, 1):
        if mission['primaries'][player] != 'surveil-the-foe':
            continue
        for unit in state['units']:
            starts = unit['activity'].get(start_key)
            if unit['player'] != player or not starts or not _alive(unit):
                continue
            if all(starts.get(m['id']) == m['position'] for m in unit['models'] if m['alive']):
                continue  # Didn't move
            here = {o['id'] for o in state['objectives'] if _in_range_of(state, unit, o)}
            gone = {m['id'] for m in mission['markers'] if m['player'] != player and m.get('objective') in here}
            _remove_markers(state, gone, report, f'surveilled by {unit["name"]}')


def log_destroyed(state, models_before, action, uncounted=()):
    """Models that died in this action, and units whose last model did, with what destroyed them and where
    they stood (`models_before`: each model's fields before the action); not those in `uncounted` (removed
    without being destroyed, as for coherency)."""
    mission = state.get('mission')
    if not mission:
        return
    by = state_unit(state, action.get('unit')) if action.get('type') in ('shoot', 'fight') else None
    for unit in state['units']:
        for model in unit['models']:
            if models_before[model['id']]['alive'] and not model['alive'] and model['id'] not in uncounted:
                part = unit['parts'][model['part']]
                mission.setdefault('models_destroyed', []).append({
                    'model': model['id'], 'unit': unit['id'], 'player': unit['player'],
                    'character': g._has(part['keywords'], 'Character'), 'wounds': part['stats'].get('wounds')})
        alive_before = any(models_before[m['id']]['alive'] for m in unit['models'])
        if alive_before and not any(m['alive'] for m in unit['models']) and by and mission['primaries'][by['player']] == 'consecrate':
            mission.setdefault('consecration', [])
            if by['id'] not in mission['consecration']:
                mission['consecration'].append(by['id'])  # It destroyed a unit: a consecration unit
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
        cards += [('secondary', TACTICAL_PREFIX + key) for key in tactical.active_keys(state, player)
                  if not tactical.held(state, player, key)]
        achieved = []
        for kind, key in cards:
            name = card(key)['name']
            for entry, asked, how in score_lines(key, when, int(round_number), facts):
                if kind == 'primary':
                    room = PRIMARY_GAME_CAP - vp['primary']
                    if when != 'battle':
                        room = min(room, PRIMARY_ROUND_CAP - vp['rounds'].get(round_number, 0))
                else:
                    room = SECONDARY_GAME_CAP - vp['secondary']
                    if not key.startswith(TACTICAL_PREFIX):
                        room = min(room, FIXED_CARD_CAP - vp['cards'].get(key, 0))
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
                if key.startswith(TACTICAL_PREFIX) and key not in achieved:
                    achieved.append(key)
        for key in achieved:  # Achieved Tactical cards are discarded
            tactical.scored(state, player, key)


def choose_secondaries(state, action, dice):
    """Setup: a player chooses Fixed secondary missions and their two cards, or Tactical (their deck is
    shuffled). Once both have chosen, the choices are revealed and deployment begins, the defender first."""
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
    if action.get('mode') == 'tactical':
        mission['secondaries'][str(player)] = {'mode': 'tactical', 'cards': []}
        mission.setdefault('tactical', {'0': None, '1': None})[str(player)] = tactical.new_deck(dice)
    elif action.get('mode') == 'fixed':
        cards = list(dict.fromkeys(action.get('cards') or []))
        if len(cards) != FIXED_CARDS or any(key not in SECONDARIES for key in cards):
            raise g.ActionError(f'Choose {FIXED_CARDS} different Fixed secondary missions.')
        mission['secondaries'][str(player)] = {'mode': 'fixed', 'cards': cards}
    else:
        raise g.ActionError('Choose Fixed or Tactical secondary missions.')
    if all(mission['secondaries'].get(str(p)) for p in (0, 1)):
        state['turn'] = {'phase': 'deployment', 'round': 0, 'player': state['roles']['defender']}
        return {'secondaries_chosen': player, 'revealed': {p: mission['secondaries'][str(p)] for p in ('0', '1')}}
    return {'secondaries_chosen': player}


def require_no_choice(state):
    choice = (state.get('mission') or {}).get('choice')
    if choice:
        name = state['players'][choice['player']]['name']
        if choice['kind'] == 'locate':
            raise g.ActionError(f'{name} must first place their Locate and Deny markers, in the Mission panel.')
        if choice['kind'] == 'beacon':
            raise g.ActionError(f'{name} must first choose their beacon unit (Beacon), in the Mission panel.')
        if choice['kind'] == 'tempting':
            raise g.ActionError(f"{name} must first choose their opponent's tempting target, in the Mission panel.")
        raise g.ActionError(f'{name} must first condemn 1 to {CONDEMN_MOST} enemy units (Punishment), in the Mission panel.')


def condemn_options(state):
    """{'units': [enemy unit ids that can be condemned], 'any': True when none qualify (choose any one)}."""
    mission = state.get('mission') or {}
    choice = mission.get('choice')
    if not choice or choice['kind'] != 'condemn':
        return None
    player = choice['player']
    on_board = [u for u in state['units'] if u['player'] != player and g._placed_shapes(u)]
    killers = {d['by'] for d in mission.get('destroyed_before', []) if d['player'] == player and d['by']}
    eligible = [u['id'] for u in on_board if _unit_facts(state, u)['in_range'] or u['id'] in killers]
    return {'units': eligible or [u['id'] for u in on_board], 'any': not eligible}


def condemn(state, action):
    """Punishment: the player whose turn it is condemns 1-3 eligible enemy units (or any one if none are)."""
    options = condemn_options(state)
    if not options or state['mission']['choice']['kind'] != 'condemn':
        raise g.ActionError('There is nothing to condemn now.')
    chosen = list(dict.fromkeys(action.get('units') or []))
    most = 1 if options['any'] else CONDEMN_MOST
    if not 1 <= len(chosen) <= most:
        raise g.ActionError(f'Condemn {"one enemy unit" if most == 1 else f"1 to {most} enemy units"}.')
    if any(uid not in options['units'] for uid in chosen):
        raise g.ActionError('Condemn enemy units within range of objectives, or that destroyed one of your units last turn.')
    mission = state['mission']
    mission['condemned'][str(mission['choice']['player'])] = chosen
    next_choice(state)
    return {'condemned': chosen}
