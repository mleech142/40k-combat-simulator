"""
Battlefields: deployment zones, objectives and terrain, in board inches (60" wide, 44" deep, (0, 0) at
the bottom-left; the attacker's zone is drawn at the bottom or left).

BUILTIN holds the 11th edition Chapter Approved deployment maps, transcribed from their cards' printed
measurements (the cards are portrait, 44" across and 60" down; a card point (across, down) is board
point (down, across), turning the card a quarter turn). Each map is the same for both players turned
half way round the centre; tests/test_play_battlefields.py checks that.

A battlefield: {'id', 'name', 'source' ('builtin' or 'custom'), 'zones': {'attacker', 'defender'}
(polygons, [[x, y], ...]), 'divider' (the dashed line between the two territories: two points through
the centre), 'objectives': [{'id', 'kind' (home/central/expansion), 'role' (attacker/defender for a
home objective), 'x', 'y'}], 'terrain': [terrain pieces; see game.terrain_areas]}.
"""

import math

CENTRE = (30, 22)
CENTRAL_SPREAD = 6  # On a central-objective roll of 6: two objectives, this far either side along the divider


def _arc(cx, cy, r, start, end, steps=8):
    """Points on a circle from angle `start` to `end` (degrees), both included."""
    return [(round(cx + r * math.cos(math.radians(a)), 4), round(cy + r * math.sin(math.radians(a)), 4))
            for a in (start + (end - start) * i / steps for i in range(steps + 1))]


def _map(key, name, attacker, defender, divider, homes, expansions):
    objectives = [{'id': 'home-attacker', 'kind': 'home', 'role': 'attacker', 'x': homes[0][0], 'y': homes[0][1]},
                  {'id': 'home-defender', 'kind': 'home', 'role': 'defender', 'x': homes[1][0], 'y': homes[1][1]},
                  {'id': 'central-1', 'kind': 'central', 'role': None, 'x': CENTRE[0], 'y': CENTRE[1]}]
    objectives += [{'id': f'expansion-{i + 1}', 'kind': 'expansion', 'role': None, 'x': x, 'y': y}
                   for i, (x, y) in enumerate(expansions)]
    return key, {'id': key, 'name': name, 'source': 'builtin', 'zones': {'attacker': attacker, 'defender': defender},
                 'divider': divider, 'objectives': objectives, 'terrain': []}


BUILTIN = dict([
    # 12" deep along the long edges
    _map('dawn-of-war', 'Dawn of War',
         [(0, 0), (60, 0), (60, 12), (0, 12)], [(0, 32), (60, 32), (60, 44), (0, 44)],
         [(0, 22), (60, 22)], [(30, 10), (30, 34)], [(10, 25), (50, 19)]),
    # Short edges, stepped: 20" deep on one half, 12" on the other
    _map('tipping-point', 'Tipping Point',
         [(0, 0), (20, 0), (20, 22), (12, 22), (12, 44), (0, 44)],
         [(60, 44), (40, 44), (40, 22), (48, 22), (48, 0), (60, 0)],
         [(34, 0), (26, 44)], [(14, 11), (46, 33)], [(24, 38), (36, 6)]),
    # Long edges, stepped: 14" deep on one half, 8" on the other
    _map('sweeping-engagement', 'Sweeping Engagement',
         [(0, 0), (60, 0), (60, 8), (30, 8), (30, 14), (0, 14)],
         [(60, 44), (0, 44), (0, 36), (30, 36), (30, 30), (60, 30)],
         [(0, 25), (60, 19)], [(14, 10), (46, 34)], [(10, 30), (50, 14)]),
    # Quarters, less a 9" circle round the centre
    _map('search-and-destroy', 'Search and Destroy',
         [(0, 0), (30, 0)] + _arc(30, 22, 9, 270, 180) + [(0, 22)],
         [(60, 44), (30, 44)] + _arc(30, 22, 9, 90, 0) + [(60, 22)],
         [(60, 0), (0, 44)], [(14, 10), (46, 34)], [(14, 34), (46, 10)]),
    # 18" deep along the short edges
    _map('hammer-and-anvil', 'Hammer and Anvil',
         [(0, 0), (18, 0), (18, 44), (0, 44)], [(42, 0), (60, 0), (60, 44), (42, 44)],
         [(30, 0), (30, 44)], [(14, 10), (46, 34)], [(24, 36), (36, 8)]),
    # Corner triangles, 30" along the long edge by the whole short edge
    _map('crucible-of-battle', 'Crucible of Battle',
         [(0, 0), (30, 0), (0, 44)], [(60, 44), (30, 44), (60, 0)],
         [(45, 0), (15, 44)], [(12, 10), (48, 34)], [(15, 34), (45, 10)]),
])
DEFAULT = 'dawn-of-war'


def central_points(battlefield, double):
    """Where the central objective goes: the centre, or (on a roll of 6) two points 6" either side of it
    along the divider."""
    if not double:
        return [CENTRE]
    (ax, ay), (bx, by) = battlefield['divider']
    length = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / length, (by - ay) / length
    points = [(CENTRE[0] - ux * CENTRAL_SPREAD, CENTRE[1] - uy * CENTRAL_SPREAD),
              (CENTRE[0] + ux * CENTRAL_SPREAD, CENTRE[1] + uy * CENTRAL_SPREAD)]
    return sorted((round(x, 4), round(y, 4)) for x, y in points)


def inside(point, polygon):
    """Whether a point is inside a polygon (any shape; on the edge counts as inside)."""
    x, y = point
    pts = list(polygon)
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):  # On an edge
        cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        if abs(cross) < 1e-9 and min(ax, bx) - 1e-9 <= x <= max(ax, bx) + 1e-9 and min(ay, by) - 1e-9 <= y <= max(ay, by) + 1e-9:
            return True
    result = False
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            result = not result
    return result


def outline_points(shape, steps=24):
    """Points around a shape's edge (a round base's circle, or a polygon's corners)."""
    if shape.radius:
        (x, y), r = shape.points[0], shape.radius
        return [(x + r * math.cos(2 * math.pi * i / steps), y + r * math.sin(2 * math.pi * i / steps)) for i in range(steps)]
    return list(shape.points)


def wholly_inside(shape, polygon):
    """Whether a model's whole base is inside a polygon (checked round its edge)."""
    return all(inside(p, polygon) for p in outline_points(shape))


# --- Checking battlefields saved in the editor --------------------------------------------------------------

KINDS = ('home', 'central', 'expansion')
CATEGORIES = ('exposed', 'light', 'dense')


class BattlefieldError(ValueError):
    """A battlefield that doesn't make sense; the message says why."""


def _number(value, what, low=None, high=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BattlefieldError(f'{what} must be a number.')
    if (low is not None and number < low) or (high is not None and number > high):
        raise BattlefieldError(f'{what} must be on the board ({low} to {high}).')
    return int(number) if number.is_integer() else round(number, 3)


def _point(value, what):
    try:
        x, y = value
    except (TypeError, ValueError):
        raise BattlefieldError(f'{what} must be a point [x, y].')
    return [_number(x, f'{what} x', 0, BOARD[0]), _number(y, f'{what} y', 0, BOARD[1])]


BOARD = (60, 44)


def clean(data):
    """
    A battlefield from the editor, checked and tidied: {'name', 'zones', 'divider', 'objectives',
    'terrain'}. Objectives under a terrain piece become terrain objectives (as when terrain is set up over
    objective points). Raises BattlefieldError.
    """
    from .geometry import FOOTPRINTS, footprint  # (geometry doesn't import this module)
    if not isinstance(data, dict):
        raise BattlefieldError('Send the battlefield as a JSON object.')
    name = str(data.get('name') or '').strip()[:200]
    if not name:
        raise BattlefieldError('Give the battlefield a name.')
    zones = {}
    for role in ('attacker', 'defender'):
        points = (data.get('zones') or {}).get(role)
        if not isinstance(points, list) or len(points) < 3:
            raise BattlefieldError(f'The {role}\'s zone needs at least 3 corners.')
        zones[role] = [_point(p, f'A corner of the {role}\'s zone') for p in points]
    divider = data.get('divider') or [[0, 22], [60, 22]]
    if not isinstance(divider, list) or len(divider) != 2:
        raise BattlefieldError('The dividing line needs two points.')
    divider = [_point(p, 'The dividing line') for p in divider]
    terrain, ids = [], set()
    for i, piece in enumerate(data.get('terrain') or []):
        if not isinstance(piece, dict):
            raise BattlefieldError('Each terrain piece must be an object.')
        if piece.get('footprint') not in FOOTPRINTS:
            raise BattlefieldError(f'Unknown terrain footprint: {piece.get("footprint")} (one of {", ".join(FOOTPRINTS)}).')
        if piece.get('category') not in CATEGORIES:
            raise BattlefieldError(f'Terrain category must be one of {", ".join(CATEGORIES)}.')
        id_ = str(piece.get('id') or f't{i + 1}')[:40]
        while id_ in ids:
            id_ += '+'
        ids.add(id_)
        terrain.append({'id': id_, 'footprint': piece['footprint'], 'x': _number(piece.get('x'), 'Terrain x', 0, BOARD[0]),
                        'y': _number(piece.get('y'), 'Terrain y', 0, BOARD[1]),
                        'angle': _number(piece.get('angle') or 0, 'Terrain angle', -360, 360),
                        'category': piece['category'], 'height': _number(piece.get('height') or 0, 'Terrain height', 0, 30)})
    objectives, seen = [], set()
    for i, objective in enumerate(data.get('objectives') or []):
        if not isinstance(objective, dict) or objective.get('kind') not in KINDS:
            raise BattlefieldError(f'Objective kind must be one of {", ".join(KINDS)}.')
        role = objective.get('role') if objective['kind'] == 'home' else None
        if objective['kind'] == 'home' and role not in ('attacker', 'defender'):
            raise BattlefieldError('A home objective belongs to the attacker or the defender.')
        id_ = str(objective.get('id') or f'{objective["kind"]}-{i + 1}')[:40]
        while id_ in seen:
            id_ += '+'
        seen.add(id_)
        x, y = _point([objective.get('x'), objective.get('y')], 'An objective')
        under = next((t['id'] for t in terrain
                      if inside((x, y), footprint(t['footprint'], t['x'], t['y'], t['angle']).points)), None)
        objectives.append({'id': id_, 'kind': objective['kind'], 'role': role, 'x': x, 'y': y, 'terrain': under})
    return {'name': name, 'zones': zones, 'divider': divider, 'objectives': objectives, 'terrain': terrain}
