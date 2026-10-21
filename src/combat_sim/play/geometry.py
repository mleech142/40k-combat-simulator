"""
Board geometry for Play mode: pure maths in inches, (0, 0) at the board's bottom-left corner,
models placed by the centre of their base. No game rules: which terrain blocks sight or gives
cover is decided elsewhere.

A shape is a convex polygon "inflated" by a radius: a round base is one point with its radius, an
oval or a rectangle is a polygon with radius 0. Distances between shapes are then distances between
polygons, less both radii, which is exact for circles and rectangles.
"""

import math
from dataclasses import dataclass

MM_PER_INCH = 25.4
BOARD_WIDTH, BOARD_DEPTH = 60, 44
OVAL_POINTS = 96  # An oval's edge as a polygon: within 0.002" of the true edge for the largest bases
SIGHT_POINTS = 16  # Points around each base that lines of sight are drawn between

# 11th edition's terrain footprints, (length, width) in inches: rectangles, and a polygon (a rectangle with
# a corner cut off: 8" wide at one end, POLYGON_CUT at the other), as the Event Companion draws them
FOOTPRINTS = {'large': (11.5, 7), 'polygon': (11.5, 8), 'medium': (6, 4), 'long': (10, 2.5), 'short': (6, 2)}
POLYGON_CUT = 2
FOOTPRINT_ALIASES = {'triangle': 'polygon'}  # Battlefields saved before the polygon replaced a triangle


@dataclass
class Shape:
    points: list  # [(x, y)], anticlockwise; one point for a round base
    radius: float = 0.0


def base_shape(base, x, y, angle=0):
    """A model's base (as bsdata.parse_base gives it) centred on (x, y), turned `angle` degrees."""
    length, width = base['length_mm'] / MM_PER_INCH, base['width_mm'] / MM_PER_INCH
    if length == width:
        return Shape([(x, y)], length / 2)
    steps = [2 * math.pi * i / OVAL_POINTS for i in range(OVAL_POINTS)]
    return Shape(_place([(length / 2 * math.cos(t), width / 2 * math.sin(t)) for t in steps], x, y, angle))


def hull_shape(length, width, x, y, angle=0):
    """A model with no base (a vehicle's hull), as a `length` x `width` inch rectangle centred on (x, y)."""
    half_l, half_w = length / 2, width / 2
    return Shape(_place([(-half_l, -half_w), (half_l, -half_w), (half_l, half_w), (-half_l, half_w)], x, y, angle))


def footprint_corners(kind):
    """A footprint's corners from its own corner (0, 0): its length along x, its width along y."""
    kind = FOOTPRINT_ALIASES.get(kind, kind)
    length, width = FOOTPRINTS[kind]
    if kind == 'polygon':  # Full width at the corner's end, POLYGON_CUT at the far end
        return [(0, 0), (length, 0), (length, POLYGON_CUT), (0, width)]
    return [(0, 0), (length, 0), (length, width), (0, width)]


def footprint(kind, x, y, angle=0, flip=False):
    """A terrain footprint (a FOOTPRINTS name) with its corner at (x, y), its length along the x axis
    before it is turned `angle` degrees anticlockwise about that corner; `flip` turns it over first
    (across its length), as a polygon footprint can be."""
    points = [(px, -py if flip else py) for px, py in footprint_corners(kind)]
    return Shape(_place(points, x, y, angle))


def base_distance(a, b):
    """The closest edge-to-edge distance between two shapes; 0 if they touch or overlap."""
    return max(0.0, _polygon_distance(a.points, b.points) - a.radius - b.radius)


def _place(points, x, y, angle):
    """Points turned `angle` degrees anticlockwise about (0, 0), then moved by (x, y)."""
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [(x + px * c - py * s, y + px * s + py * c) for px, py in points]


def _edges(points):
    return [(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]


def _point_segment(p, a, b):
    """Distance from point p to segment ab."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def _inside(p, polygon):
    """Whether p is inside (or on the edge of) a convex anticlockwise polygon of 3 or more points."""
    if len(polygon) < 3:
        return False
    return all(_side(a, b, p) >= -1e-9 for a, b in _edges(polygon))


def _polygon_distance(p, q):
    """The closest distance between two convex polygons (a single point counts as one); 0 if they overlap."""
    if any(_inside(v, q) for v in p) or any(_inside(v, p) for v in q):
        return 0.0
    if any(_crosses(a, b, c, d) for a, b in _edges(p) for c, d in _edges(q)):
        return 0.0
    return min(min(_point_segment(v, a, b) for v in p for a, b in _edges(q)),
               min(_point_segment(v, a, b) for v in q for a, b in _edges(p)))


def _side(a, b, p):
    """> 0 if p is left of the line a -> b, < 0 if right, 0 if on it."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _crosses(a, b, c, d):
    """Whether segments ab and cd cross at a point inside both (not just touching at an end)."""
    return (_side(a, b, c) * _side(a, b, d) < 0) and (_side(c, d, a) * _side(c, d, b) < 0)


def clear_line(p, q, blockers):
    """Whether the straight line from p to q passes through the inside of none of `blockers` (shapes).
    Touching a corner or running along an edge doesn't block."""
    return not any(_through(p, q, b.points) for b in blockers)


def _through(p, q, polygon):
    """Whether segment pq passes through the inside of a convex polygon."""
    # Where the segment meets the polygon's edge lines; between two of these it is all in or all out
    ts = {0.0, 1.0}
    for a, b in _edges(polygon):
        denominator = _side(a, b, q) - _side(a, b, p)
        if denominator:
            t = -_side(a, b, p) / denominator
            if 0 < t < 1:
                ts.add(t)
    ts = sorted(ts)
    for t0, t1 in zip(ts, ts[1:]):
        t = (t0 + t1) / 2
        point = (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))
        if all(_side(a, b, point) > 1e-9 for a, b in _edges(polygon)):
            return True
    return False


def can_see(a, b, blockers):
    """Whether any straight line from a point of base `a` to a point of base `b` is clear (sampled
    at SIGHT_POINTS around each edge, and the centres)."""
    return any(clear_line(p, q, blockers) for p in _sight_points(a) for q in _sight_points(b))


def _sight_points(shape):
    if shape.radius:
        (x, y), r = shape.points[0], shape.radius
        edge = [(x + r * math.cos(2 * math.pi * i / SIGHT_POINTS), y + r * math.sin(2 * math.pi * i / SIGHT_POINTS))
                for i in range(SIGHT_POINTS)]
        return [(x, y)] + edge
    step = max(1, len(shape.points) // SIGHT_POINTS)
    centre = (sum(x for x, _ in shape.points) / len(shape.points), sum(y for _, y in shape.points) / len(shape.points))
    return [centre] + shape.points[::step]


def overlaps(a, b):
    """Whether two shapes overlap (bases that only touch don't)."""
    eps = 1e-6
    if a.radius and b.radius:
        return math.dist(a.points[0], b.points[0]) < a.radius + b.radius - eps
    if a.radius or b.radius:
        circle, polygon = (a, b) if a.radius else (b, a)
        centre = circle.points[0]
        if _inside(centre, polygon.points):
            return True
        return min(_point_segment(centre, p, q) for p, q in _edges(polygon.points)) < circle.radius - eps
    return (any(_strictly_inside(v, b.points) for v in a.points)
            or any(_strictly_inside(v, a.points) for v in b.points)
            or any(_crosses(p, q, r, s) for p, q in _edges(a.points) for r, s in _edges(b.points)))


def _strictly_inside(v, polygon):
    return all(_side(p, q, v) > 1e-6 for p, q in _edges(polygon))


def bounds(shape):
    """(left, bottom, right, top) of a shape."""
    xs, ys = [x for x, _ in shape.points], [y for _, y in shape.points]
    return min(xs) - shape.radius, min(ys) - shape.radius, max(xs) + shape.radius, max(ys) + shape.radius
